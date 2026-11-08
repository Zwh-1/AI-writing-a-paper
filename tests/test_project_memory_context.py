import pytest
from fastapi import HTTPException
from test_workbench import client,project,save,manual_package
from test_writing_continuity import chapter
from app import db,security,tasks,documents
from app import project_memory as memory
from app.writing_continuity import collect
from app.ai_runtime.policy import frozen


def remember(client,pid,**kwargs):
    result=client.post(f'/api/v1/projects/{pid}/memory',json={'kind':'author_decision','subject':'研究范围','text':'限定为单校样本，不推断其他学校。',**kwargs})
    assert result.status_code==200,result.text
    return result.json()


def test_memory_is_versioned_project_scoped_and_explicitly_not_evidence(client):
    pid=project(client);other=project(client,'其他论文')
    row=remember(client,pid)
    assert row['data']['evidence'] is False and row['data']['use_for_writing'] is False
    assert client.delete(f'/api/v1/projects/{other}/memory/{row["id"]}?expected_revision=1').status_code==404
    duplicate=remember(client,pid);assert duplicate['id']==row['id']
    edited=remember(client,pid,id=row['id'],expected_revision=1,text='只分析作者确认的单校结果。')
    assert edited['revision']==2
    assert client.post(f'/api/v1/projects/{pid}/memory',json={'id':row['id'],'expected_revision':1,'kind':'author_decision','subject':'范围','text':'冲突修改'}).status_code==409


def test_memory_conflicts_require_explicit_supersession(client):
    pid=project(client);first=remember(client,pid);second=remember(client,pid,text='改为跨校样本。')
    assert len(memory.view(pid)['conflicts'])==1
    third=remember(client,pid,text='仍只使用当前学校样本。',supersedes=[first['id'],second['id']])
    assert not memory.view(pid)['conflicts']
    assert {r['id'] for r in memory.view(pid)['entries'] if r['state']=='current'}=={third['id']}


def test_frozen_memory_does_not_pick_up_new_decisions_and_revalidates_edits(client):
    pid=project(client);first=remember(client,pid)
    task=tasks.enqueue(pid,'support_check',{})
    remember(client,pid,subject='另一个决定',text='后加入的决定。')
    context=memory.task_context(pid,task=task)
    assert [x['id'] for x in context['items']]==[first['id']]
    remember(client,pid,id=first['id'],expected_revision=1,text='新版本范围。')
    with pytest.raises(HTTPException):memory.validate_context(pid,context)
    assert not memory.task_context(pid,task=task)['items']


def test_chapter_memory_expires_on_revision_and_rebuild_keeps_history(client):
    pid=project(client);sid=chapter(client,pid,'绪论','研究范围为本校。\n本文尚未实测其他学校。')
    result=client.post(f'/api/v1/projects/{pid}/memory/rebuild-chapters',json={})
    assert result.status_code==200,result.text
    old=memory.view(pid)['entries'][0]
    rev=documents.current(security.require_section(sid))
    assert save(client,sid,'新的范围只包含本系。',rev['id']).status_code==200
    assert memory.view(pid)['entries'][0]['state']=='stale'
    client.post(f'/api/v1/projects/{pid}/memory/rebuild-chapters',json={})
    rows=memory.view(pid)['entries']
    assert len(rows)==2 and any(x['id']==old['id'] and x['state']=='superseded' for x in rows)
    assert len([x for x in rows if x['state']=='current'])==1


def test_hierarchical_context_loads_current_and_dependencies_but_versions_all(client):
    pid=project(client);a=chapter(client,pid,'研究背景','这是不应整篇反复外发的背景。'*1000)
    b=chapter(client,pid,'方法','本文仅采用静态分析。');c=chapter(client,pid,'结果','没有执行性能实验。')
    db.execute('UPDATE sections SET dependencies=? WHERE id=?',(db.dumps([b]),c))
    remember(client,pid)
    payload,manifest,_=collect(pid,c,mode='hierarchical_v1')
    assert {x['section_id'] for x in payload['related_sections']}=={b}
    assert {x['section_id'] for x in payload['section_catalogue']}=={a}
    assert set(manifest['related_revisions'])=={a,b}
    assert not payload['paper_context']['full_manuscript_provided']
    assert payload['current_section_context']['text']=='没有执行性能实验。'
    assert payload['project_memory']['items'][0]['evidence'] is False
    full,_,_=collect(pid,c,mode='full_manuscript')
    assert len(db.dumps(payload))<len(db.dumps(full))


def test_context_mode_is_frozen_for_task_resume(client):
    pid=project(client);sid=chapter(client,pid,'正文','文本')
    client.put(f'/api/v1/projects/{pid}/writing-flow/settings',json={'context_mode':'hierarchical_v1'})
    task=tasks.enqueue(pid,'write',{'section_id':sid})
    client.put(f'/api/v1/projects/{pid}/writing-flow/settings',json={'context_mode':'full_manuscript'})
    assert frozen(task)['writing_flow_policy']['context_mode']=='hierarchical_v1'
    assert collect(pid,sid,task=task)[0]['paper_context']['scope']=='current_and_dependencies'


def test_history_uses_only_messages_through_current_and_keeps_complete_suffix():
    rows=[{'id':str(i),'data':{'role':'user' if i%2==0 else 'assistant','text':'内容'+str(i)}} for i in range(10)]
    policy={**memory.DEFAULT,'recent_messages':4,'history_input_bytes':2000}
    result=memory.history_context(rows,'8',policy)
    assert [x['id'] for x in result['messages']]==['6','7','8']
    assert '9' not in result['history_scope']['provided_message_ids']
    assert not result['history_scope']['complete']
    assert result['history_scope']['omitted_message_ids']==['0','1','2','3','4','5']


def test_memory_whole_items_budget_and_deleted_notes(client):
    pid=project(client)
    for i in range(4):remember(client,pid,subject='主题'+str(i),text='完整决定。'*180)
    snap=memory.snapshot(pid);snap['policy']['max_input_bytes']=1000
    value=memory.context(pid,snap)
    assert value['bytes']<=1000 and value['deferred']
    assert all(x['text'].endswith('完整决定。') for x in value['items'])


def test_revoked_memory_source_is_excluded_and_invalidates_prepared_context(client):
    from test_workbench import file
    from app.resource_refs import make
    pid=project(client);source=file(client,pid)
    row=remember(client,pid,source_refs=[make(pid,'file',source['id'])])
    snap=memory.snapshot(pid);prepared=memory.context(pid,snap)
    assert prepared['items']
    assert client.patch('/api/v1/files/'+source['id'],json={'egress':'local_only'}).status_code==200
    assert not memory.context(pid,snap)['items']
    with pytest.raises(HTTPException):memory.validate_context(pid,prepared)


def test_dependency_closure_keeps_indirect_premises(client):
    pid=project(client);a=chapter(client,pid,'定义','不可遗漏的前提。');b=chapter(client,pid,'方法','基于定义的方法。');c=chapter(client,pid,'结果','基于方法的结果。')
    db.execute('UPDATE sections SET dependencies=? WHERE id=?',(db.dumps([a]),b))
    db.execute('UPDATE sections SET dependencies=? WHERE id=?',(db.dumps([b]),c))
    assert {x['section_id'] for x in collect(pid,c,mode='hierarchical_v1')[0]['related_sections']}=={a,b}


def test_editing_memory_preserves_source_and_section_provenance(client):
    pid=project(client);sid=chapter(client,pid,'研究范围','仅研究本系的样本。')
    first=remember(client,pid,section_id=sid)
    edited=remember(client,pid,id=first['id'],expected_revision=1,text='限定本系，并保留未完成事项。')
    assert edited['data']['source_refs']==first['data']['source_refs']
    assert edited['data']['section_versions']==first['data']['section_versions']
    revision=documents.current(security.require_section(sid))
    assert save(client,sid,'改为其他范围。',revision['id']).status_code==200
    assert next(row for row in memory.view(pid)['entries'] if row['id']==first['id'])['state']=='stale'


def test_duplicate_memory_can_resolve_conflicting_decisions(client):
    pid=project(client);first=remember(client,pid);other=remember(client,pid,text='另一个范围。')
    same=remember(client,pid,supersedes=[first['id'],other['id']])
    assert same['id']==first['id'] and not memory.view(pid)['conflicts']
    assert next(row for row in memory.view(pid)['entries'] if row['id']==other['id'])['state']=='superseded'


def test_chat_validates_only_loaded_history_but_rechecks_it_before_send(client,monkeypatch):
    from app import assistant_service as assistant,research_records as records
    from test_workbench import file
    pid=project(client);source=file(client,pid,'未发送的旧消息来源。')
    conversation=records.save(pid,'conversation',{'title':'有界历史'})
    old=records.save(pid,'message',{'role':'assistant','text':'这条旧回复不在本次上下文。','file_ids':[source['id']]},owner=conversation['id'])
    for index in range(4):
        records.save(pid,'message',{'role':'user' if index%2==0 else 'assistant','text':'完整轮次'+str(index)},owner=conversation['id'])
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(source['id'],))
    client.put(f'/api/v1/projects/{pid}/memory/policy',json={'recent_messages':4})
    task=assistant.send_message(pid,conversation['id'],{'text':'继续','file_ids':[],'use_knowledge':False})
    captured={}
    def fake(task,system,user,manifest,images=None):
        tasks._validate_model_source_inputs(pid,manifest)
        captured.update(manifest)
        return {'text':db.dumps({'text':'已按实际载入消息回复。','citations':[]}),'run_id':'offline-history'}
    monkeypatch.setattr(tasks,'model_call',fake)
    result=tasks.dispatch(task)
    assert result['message']['data']['text']=='已按实际载入消息回复。'
    assert old['id'] in captured['history_scope']['omitted_message_ids']
    assert old['id'] not in {row['id'] for row in captured['conversation_history']['messages']}
    included=captured['conversation_history']['messages'][0]
    current=records.get(pid,included['id'],'message')
    records.save(pid,'message',{**current['data'],'text':'消息已编辑。'},current['id'],conversation['id'],current['revision'])
    with pytest.raises(HTTPException) as error:
        tasks._validate_model_source_inputs(pid,captured)
    assert error.value.status_code==409


def test_old_author_decisions_remain_retrievable_beyond_first_memory_page(client):
    pid=project(client);first=remember(client,pid)
    db.execute("UPDATE studio_records SET updated_at='2000-01-01' WHERE id=?",(first['id'],))
    with db.transaction():
        for index in range(501):
            data={'memory_kind':'working_hypothesis','subject':'假设'+str(index),'text':'待验证。',
                  'memory_state':'active','classification':'unverified_working_memory','source_refs':[],'section_versions':{},'evidence':False}
            db.execute('INSERT INTO studio_records(id,project_id,kind,owner_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                       (db.uid(),pid,'note','',db.dumps(data),db.now(),db.now()))
    page=client.get(f'/api/v1/projects/{pid}/memory?offset=500&limit=100')
    assert page.status_code==200 and page.json()['total']==502
    assert first['id'] in {row['id'] for row in page.json()['entries']}
    snap=memory.snapshot(pid)
    assert len(snap['entries'])==502
    value=memory.context(pid,snap,'研究范围')
    assert first['id'] in {row['id'] for row in value['items']}
    assert value['bytes']<=memory.DEFAULT['max_input_bytes'] and value['deferred']


def test_child_tasks_reuse_frozen_memory_without_reading_new_inventory(client,monkeypatch):
    from app.task_transitions import ownership
    from app import skill_runtime,writing_flow
    pid=project(client);remember(client,pid)
    parent=tasks.enqueue(pid,'write',{})
    db.execute("UPDATE tasks SET status='running',attempt=1 WHERE id=?",(parent['id'],))
    parent=db.one('SELECT * FROM tasks WHERE id=?',(parent['id'],));before=frozen(parent)
    def changed(*args):pytest.fail('子任务不能重新发现当前项目记忆、技能或写作策略')
    monkeypatch.setattr(memory,'snapshot',changed)
    monkeypatch.setattr(skill_runtime,'snapshot',changed)
    monkeypatch.setattr(writing_flow,'settings',changed)
    with ownership(parent):child=tasks.enqueue(pid,'studio',{'action':'frozen_child'})
    assert frozen(child)==before


def test_nonwriting_task_captures_memory_once(client,monkeypatch):
    pid=project(client);calls=[];original=memory.snapshot
    def counted(pid):calls.append(pid);return original(pid)
    monkeypatch.setattr(memory,'snapshot',counted)
    tasks.enqueue(pid,'studio',{'action':'fixture'})
    assert calls==[pid]


def test_context_mode_comparison_pairs_same_sources_and_preserves_other_controls(client):
    from app.ai_runtime import evaluation
    from test_ai_runtime_regressions import paired_tasks
    pid=project(client);ids=paired_tasks(pid)
    for index,identity in enumerate(ids):
        task=db.one('SELECT * FROM tasks WHERE id=?',(identity,));body=db.loads(task['payload']);result=db.loads(task['result'])
        mode='full_manuscript' if index==0 else 'hierarchical_v1'
        body['ai_runtime']['writing_flow_policy']['context_mode']=mode
        result['manifest']['writing_state']={'related_revisions':{'s':'rev'},'paper_context':{'scope':mode,'provided_characters':100 if index else 1000}}
        db.execute('UPDATE tasks SET payload=?,result=? WHERE id=?',(db.dumps(body),db.dumps(result),identity))
        db.execute('UPDATE generation_runs SET usage=? WHERE task_id=?',(db.dumps({'input_tokens':100 if index else 1000,'output_tokens':50}),identity))
    report=evaluation.compare(pid,{'task_ids':ids,'comparison_axis':'context_mode'})
    assert len(report['paired_input_groups'])==1 and report['paired_input_groups'][0]['ready_for_human_comparison']
    assert report['new_model_requests']==0 and report['semantic_improvement_verified'] is False
    assert [row['context_observations']['input_tokens'] for row in report['tasks']]==[1000,100]
    assert all(row['context_observations']['human_omission_count'] is None for row in report['tasks'])
    assert len(evaluation.compare(pid,{'task_ids':ids})['paired_input_groups'])==2
    second=db.one('SELECT * FROM tasks WHERE id=?',(ids[1],));body=db.loads(second['payload'])
    body['ai_runtime']['writing_flow_policy']['unit_target_words']+=100
    db.execute('UPDATE tasks SET payload=? WHERE id=?',(db.dumps(body),second['id']))
    assert len(evaluation.compare(pid,{'task_ids':ids,'comparison_axis':'context_mode'})['paired_input_groups'])==2
