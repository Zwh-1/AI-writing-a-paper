from copy import deepcopy
import pytest
from fastapi import HTTPException
from test_workbench import client,project,manual_package,save,file
from test_writing_agent import env,fake_model,finish,start
from app import db,documents,provider,security,tasks
from app.writing_continuity import collect,fit_optional
from app.writing_agent import context,impact,coordinator


def chapter(client,pid,title,text=None,kind='body'):
    response=client.post(f'/api/v1/projects/{pid}/sections',json={'title':title,'kind':kind,'goal':title+'的论证目标'})
    assert response.status_code==200,response.text
    sid=response.json()['id']
    if text is not None:assert save(client,sid,text).status_code==200
    return sid


def test_chapter_context_includes_complete_earlier_previous_and_later_chapters(client):
    pid=project(client)
    definition=chapter(client,pid,'第一章 术语定义','开始界定研究范围。\n中间细节。\n本文统一称为任务证据。')
    previous=chapter(client,pid,'第二章 需求','开头不是本节接口。\n范围仅限静态分析。\n下面讨论授权条件。')
    current=chapter(client,pid,'第三章 设计')
    following=chapter(client,pid,'第四章 实现','本章接续设计。\n实现仍需核对来源。\n本章尾段不作为相邻开头。')
    planned=chapter(client,pid,'第五章 测试')
    archived=chapter(client,pid,'已归档','不能进入正文上下文。')
    assert client.post(f'/api/v1/sections/{archived}/archive',json={'base_revision_id':security.require_section(archived)['current_revision_id']}).status_code==200
    other=project(client,'另一个项目');chapter(client,other,'不属于本论文','其他论文的私有文字。')
    payload,manifest,_=collect(pid,current)
    rows={row['section_id']:row for row in payload['related_sections']}
    assert rows[previous]['text']=='开头不是本节接口。\n范围仅限静态分析。\n下面讨论授权条件。'
    assert rows[following]['text']=='本章接续设计。\n实现仍需核对来源。\n本章尾段不作为相邻开头。'
    assert rows[definition]['text']=='开始界定研究范围。\n中间细节。\n本文统一称为任务证据。'
    assert not rows[definition]['partial'] and rows[definition]['scope']=='full_manuscript_section'
    assert planned in {row['id'] for row in payload['outline']}
    assert archived not in {row['id'] for row in payload['outline']}
    assert '其他论文的私有文字' not in db.dumps(payload)
    assert set(manifest['related_revisions'])==set(rows)


def test_selected_rewrite_receives_read_only_surroundings_and_preserves_them(client,monkeypatch):
    pid=project(client);manual_package(client,pid)
    previous=chapter(client,pid,'需求','统一术语为任务证据。')
    sid=chapter(client,pid,'设计','前文已界定范围。\n改写此段。\n下文保持限定。')
    revision=documents.current(security.require_section(sid));target=db.loads(revision['document'])['content'][1]['attrs']['nodeId']
    db.set_setting('provider',{**provider.config(),'model':'fixture'})
    captured={}
    def generate(system,user,images=None):
        captured.update(payload=db.loads(user),system=system)
        return {'text':db.dumps({'paragraphs':[{'text':'承接前文并保留范围的新段落。'}]}),'usage':{},'cost':0,'remote_id':'fixture','request_hash':'fixture'}
    monkeypatch.setattr(provider,'generate',generate)
    task=tasks.enqueue(pid,'write',{'section_id':sid,'base_revision_id':revision['id'],'operation':'revise','target_node_ids':[target]})
    assert tasks.run_one();result=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert result['status']=='succeeded',result['error']
    payload=captured['payload'];surrounding=payload['selection_context']
    assert payload['existing_text']=='改写此段。'
    assert [row['text'] for row in surrounding['before']]==['前文已界定范围。']
    assert [row['text'] for row in surrounding['after']]==['下文保持限定。']
    assert surrounding['read_only']
    candidate=db.loads(result['result'])
    assert documents.plain(candidate['document'])=='前文已界定范围。\n承接前文并保留范围的新段落。\n下文保持限定。'
    manifest=candidate['manifest']['writing_state']
    assert previous in manifest['related_revisions'] and not impact.check_manifest(pid,manifest)
    old=security.require_section(previous)['current_revision_id'];assert save(client,previous,'前文范围已经修改。',old).status_code==200
    assert '关联正文:'+previous in impact.check_manifest(pid,manifest)
    adoption=client.post(f'/api/v1/sections/{sid}/accept',json={'task_id':task['id'],'base_revision_id':revision['id']})
    assert adoption.status_code==409 and '关联正文:'+previous in adoption.text


def test_agent_draft_and_review_receive_route_and_terminology(env,monkeypatch):
    client=env['client'];pid=env['pid']
    prior=chapter(client,pid,'研究范围','仅描述授权条件，不声称测试通过。')
    db.execute('UPDATE sections SET position=-1 WHERE id=?',(prior,))
    db.execute('INSERT INTO terms(id,project_id,zh,en) VALUES(?,?,?,?)',(db.uid(),pid,'授权条件','authorization condition'))
    calls=fake_model(monkeypatch);session=finish(env,start(env))
    assert session['status']=='needs_review',session
    draft=next(payload for system,payload in calls if '返回 {"paragraphs"' in system)
    review=next(payload for system,payload in calls if '通读当前段落' in system)
    for payload in (draft,review):
        assert any(row['title']=='研究范围' for row in payload['outline'])
        assert any('不声称测试通过' in row['text'] for row in payload['related_sections'])
        assert payload['terminology']==[{'zh':'授权条件','en':'authorization condition'}]


def test_agent_selected_rewrite_has_explicit_scope_and_read_only_surroundings(env,monkeypatch):
    assert save(env['client'],env['sid'],'范围已在前文说明。\n需要改写的段落。\n下文继续讨论限制。').status_code==200
    section=security.require_section(env['sid']);revision=documents.current(section)
    target=db.loads(revision['document'])['content'][1]['attrs']['nodeId']
    from app.writing_agent.common import state,unblob
    calls=fake_model(monkeypatch)
    session=coordinator.create(env['pid'],{'section_id':env['sid'],'operation':'generate_unit','base_revision_id':revision['id'],'expected_project_state_revision':state(env['pid'])['revision'],'contract_version_id':env['contract']['id'],'profile_id':env['profile']['id'],'target_node_ids':[target]})
    completed=finish(env,session);assert completed['status']=='needs_review',completed
    draft=next(payload for system,payload in calls if '返回 {"paragraphs"' in system)
    assert draft['scope']=='selected_paragraphs'
    assert documents.plain(draft['existing_document'])=='需要改写的段落。'
    assert [row['text'] for row in draft['selection_context']['before']]==['范围已在前文说明。']
    assert [row['text'] for row in draft['selection_context']['after']]==['下文继续讨论限制。']
    candidate=unblob(db.one('SELECT document_blob FROM writing_candidates WHERE id=?',(completed['candidates'][-1]['id'],))['document_blob'])
    assert candidate['content'][0]==db.loads(revision['document'])['content'][0]
    assert candidate['content'][-1]==db.loads(revision['document'])['content'][-1]


def test_outline_identifiers_preserve_hierarchy_and_dependency_links_for_models():
    from app.writing_agent.identifiers import encode,decode
    payload={'section':{'id':'child'},'outline':[{'id':'parent','parent_id':None,'dependencies':[],'current_revision_id':'old-revision'},{'id':'child','parent_id':'parent','dependencies':['parent'],'current_revision_id':None}],'contract':{'depends_on_sections':['parent']},'related_sections':[{'section_id':'parent','revision_id':'old-revision','text':'parent这个词在原文中不应改写。'}]}
    encoded,aliases=encode(payload,{'stage':'draft'})
    parent,child=encoded['outline']
    assert child['parent_id']==parent['id'] and child['dependencies']==[parent['id']]
    assert encoded['contract']['depends_on_sections']==[parent['id']]
    assert encoded['related_sections'][0]['section_id']==parent['id']
    assert encoded['related_sections'][0]['revision_id']==parent['current_revision_id']
    assert encoded['related_sections'][0]['text']==payload['related_sections'][0]['text']
    assert decode(encoded,aliases)==payload


def test_changes_to_outline_or_glossary_invalidate_continuity(env):
    profile=env['profile'];payload,manifest=context.assemble(env['pid'],env['sid'],env['contract']['id'],profile)
    assert not impact.check_manifest(env['pid'],manifest)
    db.execute('UPDATE sections SET goal=? WHERE id=?',('变更后的研究范围',env['sid']))
    assert '全篇提纲' in impact.check_manifest(env['pid'],manifest)
    db.execute('INSERT INTO terms(id,project_id,zh,en) VALUES(?,?,?,?)',(db.uid(),env['pid'],'授权条件','authorization condition'))
    assert '全文术语' in impact.check_manifest(env['pid'],manifest)


def test_private_derived_text_blocks_full_context_instead_of_silently_omitting_a_chapter(client):
    pid=project(client);origin=file(client,pid,'不得对外发送的研究范围。')
    source=db.one('SELECT id FROM sources WHERE file_id=?',(origin['id'],))['id']
    previous=chapter(client,pid,'前文')
    doc=documents.text_document('不得对外发送的研究范围。');doc['content'][0]['attrs']['sourceIds']=[source]
    revision=documents.save(security.require_section(previous),doc,None)
    sid=chapter(client,pid,'当前章节')
    _,allowed,_=collect(pid,sid)
    assert previous in allowed['related_revisions']
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(origin['id'],))
    with pytest.raises(HTTPException) as error:collect(pid,sid)
    assert error.value.status_code==403 and error.value.detail['code']=='MANUSCRIPT_CONTEXT_UNAVAILABLE'
    preview=client.get(f'/api/v1/sections/{sid}/writing-context').json()
    assert not preview['can_load'] and not preview['complete']
    assert '不得对外发送的研究范围' not in db.dumps(preview)
    with pytest.raises(HTTPException) as error:collect(pid,sid,required_sections=[previous])
    assert error.value.status_code==403
    # Permission revocation is also checked after context was assembled.
    from app.writing_agent.common import state
    head=state(pid)
    check={**allowed,'remote':True,'section_id':sid,'core_version_id':head['core_version_id'],'target_snapshot_id':head['target_snapshot_id'],'rule_package_id':None,'contract_version_id':None}
    assert '关联正文外发权限:'+previous in impact.check_manifest(pid,check)


@pytest.mark.parametrize('operation',['generate','revise','polish','expand','compress'])
def test_every_writing_operation_sends_all_saved_chapter_text_to_provider(client,monkeypatch,operation):
    pid=project(client);manual_package(client,pid)
    originals={}
    for index in range(8):
        text=f'第{index}章开头。\n'+('完整正文保留所有条件与否定。'*100)+f'\n第{index}章中部唯一标记_{index}，不得截掉。\n第{index}章结尾。'
        sid=chapter(client,pid,f'第{index}章',text);originals[sid]=text
    current=list(originals)[3];revision=documents.current(security.require_section(current))
    target=db.loads(revision['document'])['content'][2]['attrs']['nodeId']
    db.set_setting('provider',{**provider.config(),'model':'fixture','context_limit':120000,'max_output_tokens':2048})
    captured={}
    def generate(system,user,images=None):
        captured.update(payload=db.loads(user),system=system)
        if operation=='polish':return {'text':db.dumps({'paragraphs':[{'source_node_id':row['source_node_id'],'text':row['text']} for row in captured['payload']['polish_blocks']]}),'usage':{},'cost':0,'remote_id':'fixture','request_hash':'fixture'}
        return {'text':db.dumps({'paragraphs':[{'text':'本次操作生成的候选。'}]}),'usage':{},'cost':0,'remote_id':'fixture','request_hash':'fixture'}
    monkeypatch.setattr(provider,'generate',generate)
    body={'section_id':current,'base_revision_id':revision['id'],'operation':operation,'instruction':'按全文语境处理当前内容。','target_node_ids':[] if operation=='generate' else [target]}
    task=tasks.enqueue(pid,'write',body);assert tasks.run_one()
    result=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],));assert result['status']=='succeeded',result['error']
    payload=captured['payload'];actual={row['section_id']:row['text'] for row in payload['related_sections']}
    actual[current]=payload['current_section_context']['text']
    assert actual==originals
    assert payload['paper_context']['saved_sections']==8 and payload['paper_context']['complete']
    assert payload['operation']==operation
    if operation=='polish':assert payload['polish_policy']['id']=='academic_polish.v1' and '独立学术润写模块' in captured['system']
    else:assert payload['operation_goal'] in captured['system']
    manifest=db.loads(result['result'])['manifest']
    assert manifest['paper_context']['scope']=='full_saved_manuscript'
    assert set(manifest['related_revisions'])==set(originals)-{current}
    if operation!='generate':
        assert (payload['polish_blocks'][0]['text'] if operation=='polish' else payload['existing_text'])=='第3章中部唯一标记_3，不得截掉。'
        candidate=db.loads(result['result'])['document']
        assert documents.plain(candidate).startswith('第3章开头。\n')
        assert documents.plain(candidate).endswith('第3章结尾。')


def test_full_context_preview_matches_saved_revisions_and_rejects_budget_overflow(client,monkeypatch):
    pid=project(client);manual_package(client,pid)
    old=chapter(client,pid,'第一章','开头\n'+('完整保留，不能截断。'*1000)+'\n结束')
    current=chapter(client,pid,'第二章')
    db.set_setting('provider',{**provider.config(),'context_limit':100000,'max_output_tokens':2048})
    preview=client.get(f'/api/v1/sections/{current}/writing-context').json()
    assert preview['can_load'] and preview['saved_sections']==1 and preview['total_sections']==2
    entry=next(row for row in preview['sections'] if row['section_id']==old)
    assert entry['revision_id']==security.require_section(old)['current_revision_id']
    assert entry['characters']==len(documents.current(security.require_section(old))['text'])
    db.set_setting('provider',{**provider.config(),'context_limit':4000})
    limited=client.get(f'/api/v1/sections/{current}/writing-context').json()
    assert not limited['can_load'] and limited['code']=='CONTEXT_BUDGET_EXCEEDED'
    monkeypatch.setattr(provider,'generate',lambda *args:pytest.fail('Oversized full context must not call a model'))
    rejected=client.post(f'/api/v1/sections/{current}/writing-tasks',json={'operation':'generate','base_revision_id':None})
    assert rejected.status_code==422 and rejected.json()['detail']['code']=='CONTEXT_BUDGET_EXCEEDED'
    assert not db.rows("SELECT * FROM tasks WHERE kind='write'")


def test_writing_a_previously_empty_chapter_invalidates_full_context(env):
    other=chapter(env['client'],env['pid'],'原来未写的后章')
    _,manifest=context.assemble(env['pid'],env['sid'],env['contract']['id'],env['profile'])
    assert manifest['empty_section_revisions'][other] is None
    assert save(env['client'],other,'现在已经补写的具体结果。').status_code==200
    assert '新增正文:'+other in impact.check_manifest(env['pid'],manifest)


def test_budget_removes_distant_passages_before_boundaries_and_keeps_dependencies():
    required={'section_id':'required','revision_id':'r1','scope':'full_required_dependency','text':'必要前提，不能删去否定条件。'}
    boundary={'section_id':'previous','revision_id':'r2','scope':'complete_boundary_paragraphs','text':'前文结尾的完整段落。'}
    distant={'section_id':'earlier','revision_id':'r3','scope':'cross_section_passages','text':'更早的完整原文。'}
    payload={'related_sections':deepcopy([required,boundary,distant]),'rules':[{'statement':'保留全部强制规则。'}]}
    manifest={'related_revisions':{row['section_id']:row['revision_id'] for row in payload['related_sections']}}
    fit_optional(payload,manifest,lambda:len(payload['related_sections'])<=2)
    assert payload['related_sections']==[required,boundary]
    assert manifest['related_revisions']=={'required':'r1','previous':'r2'}
    assert payload['omitted_optional_boundary_sections'][0]['section_id']=='earlier'
    assert payload['rules']==[{'statement':'保留全部强制规则。'}]


def test_budget_retains_version_guard_for_expanded_original_premises():
    row={'section_id':'previous','revision_id':'r1','scope':'complete_boundary_paragraphs','text':'必要原文。'}
    payload={'related_sections':[row],'expanded_original_passages':[{'section_id':'previous','revision_id':'r1','paragraphs':[{'text':'扩展后已读取的必要前提。'}]}]}
    manifest={'related_revisions':{'previous':'r1'}}
    fit_optional(payload,manifest,lambda:False)
    assert manifest['related_revisions']=={'previous':'r1'} and payload['related_sections']==[row]
