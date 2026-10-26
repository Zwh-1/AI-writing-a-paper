from copy import deepcopy
import pytest
from test_workbench import client,project,section,save,manual_package
from app import db,documents,provider,rules,tasks
from app.polishing import blocks,validate,VERSION


def model(monkeypatch,transform=lambda row:row['text']):
    calls=[]
    def generate(system,user,images=None):
        context=db.loads(user);calls.append((system,context))
        answer={'paragraphs':[{'source_node_id':row['source_node_id'],'text':transform(row)} for row in context['polish_blocks']],'issues':[]}
        return {'text':db.dumps(answer),'usage':{},'cost':0,'remote_id':'polish-fixture','request_hash':'polish-fixture'}
    monkeypatch.setattr(provider,'generate',generate)
    db.set_setting('provider',{**provider.config(),'model':'fixture','context_limit':100000,'max_output_tokens':2048})
    return calls


def execute(pid,sid,revision,target_ids=()):
    job=tasks.enqueue(pid,'write',{'section_id':sid,'operation':'polish','base_revision_id':revision,'target_node_ids':list(target_ids)})
    assert tasks.run_one()
    result=db.one('SELECT * FROM tasks WHERE id=?',(job['id'],))
    assert result['status']=='succeeded',result['error']
    return job,db.loads(result['result'])


def test_polish_rules_are_independent_and_old_packages_are_immutable(client,monkeypatch):
    pid=project(client)
    edit=client.post(f'/api/v1/projects/{pid}/rules',json={'statement':'修改专用条款，要求重构论证。','operations':['revise'],'conditions':{'field':'operation','op':'eq','value':'revise'}}).json()
    polish=client.post(f'/api/v1/projects/{pid}/rules',json={'statement':'润写专用条款，保持原有结论与术语。','operations':['polish'],'conditions':{'field':'operation','op':'eq','value':'polish'}})
    assert polish.status_code==200,polish.text
    assert db.loads(polish.json()['operations'])==['polish']
    package=rules.publish(pid);original=db.one('SELECT * FROM rule_packages WHERE id=?',(package['id'],))
    sid=section(client,pid);base=save(client,sid,'这个系统的响应为42毫秒，尚未部署。').json()
    calls=model(monkeypatch,lambda row:row['text'].replace('这个系统','该系统'))
    _,result=execute(pid,sid,base['id'])
    assert result['document'] and result['polish_validation']['passed']
    system,context=calls[0]
    assert context['operation']=='polish' and context['polish_policy']['id']==VERSION
    assert {row['id'] for row in context['rules']}=={polish.json()['id']}
    assert edit['id'] not in result['manifest']['loaded_rule_ids']
    assert '独立学术润写模块' in system and '返回完整候选小节' not in system
    assert documents.plain(result['document'])=='该系统的响应为42毫秒，尚未部署。'
    assert db.one('SELECT * FROM rule_packages WHERE id=?',(package['id'],))==original
    assert db.one('SELECT current_revision_id FROM sections WHERE id=?',(sid,))['current_revision_id']==base['id']


def test_old_revision_only_package_uses_only_independent_polish_policy(client,monkeypatch):
    pid=project(client)
    client.post(f'/api/v1/projects/{pid}/rules',json={'statement':'只用于定点修改的要求。','operations':['revise']})
    package=rules.publish(pid);snapshot=db.one('SELECT rules FROM rule_packages WHERE id=?',(package['id'],))['rules']
    sid=section(client,pid);base=save(client,sid,'原有的研究方法保持一致。').json();calls=model(monkeypatch)
    _,result=execute(pid,sid,base['id'])
    assert calls[0][1]['rules']==[] and result['manifest']['required_rule_ids']==[]
    assert result['manifest']['polish_policy']['id']==VERSION
    assert result['polish_validation']['semantic_review']=='requires_human_review'
    assert db.one('SELECT rules FROM rule_packages WHERE id=?',(package['id'],))['rules']==snapshot


def test_polish_operation_can_be_configured_and_published_separately(client):
    pid=project(client);manual_package(client,pid);rule=db.one('SELECT id FROM rules WHERE project_id=?',(pid,))
    response=client.put(f'/api/v1/rules/{rule["id"]}/execution',json={'operations':['polish']})
    assert response.status_code==200,response.text
    package=rules.publish(pid);published=db.one('SELECT * FROM rule_packages WHERE id=?',(package['id'],))
    sid=section(client,pid);chapter=db.one('SELECT * FROM sections WHERE id=?',(sid,))
    assert [row['id'] for row in rules.applicable(published,chapter,'polish')]==[rule['id']]
    assert rules.applicable(published,chapter,'revise')==[]
    assert rules.applicable(published,chapter,'expand')==[]


@pytest.mark.parametrize('text,locked',[('',False),('已有正文。',True)])
def test_polish_requires_editable_original_text_before_model_call(client,monkeypatch,text,locked):
    pid=project(client);manual_package(client,pid);sid=section(client,pid);base=save(client,sid,text).json()
    if locked:
        identity=db.loads(base['document'])['content'][0]['attrs']['nodeId']
        assert client.put(f'/api/v1/sections/{sid}/locks',json={'node_ids':[identity]}).status_code==200
    monkeypatch.setattr(provider,'generate',lambda *args:pytest.fail('No editable original text must not trigger a model'))
    response=client.post(f'/api/v1/sections/{sid}/writing-tasks',json={'operation':'polish','base_revision_id':base['id']})
    assert response.status_code==422 and response.json()['detail']['code']=='POLISH_TEXT_REQUIRED'
    assert not db.rows("SELECT * FROM tasks WHERE kind='write'")


def test_polish_preserves_inline_citations_footnotes_formatting_and_outer_blocks():
    doc=documents.normalize({'type':'doc','content':[{'type':'heading','attrs':{'level':2},'content':[{'type':'text','text':'标题'}]},{'type':'paragraph','content':[{'type':'text','text':'这个系统使用了'},{'type':'text','text':'关键术语','marks':[{'type':'bold'}]},{'type':'citation','attrs':{'citationId':'book'}},{'type':'text','text':'，测量为42毫秒。'},{'type':'footnote','attrs':{'noteText':'条件与范围需要保留。'}}]},{'type':'table','content':[]},{'type':'paragraph','content':[{'type':'text','text':'锁定段落。'}]}]})
    original=deepcopy(doc);locked=doc['content'][-1]['attrs']['nodeId'];editable=blocks(doc,locks=[locked]);row=editable[0]
    answer={'paragraphs':[{'source_node_id':row['source_node_id'],'text':row['text'].replace('这个系统','该系统')}]}
    result,checks,_=validate(answer,doc,editable)
    assert checks['passed'] and checks['protected_spans_preserved']==3
    assert result['content'][0]==doc['content'][0] and result['content'][2:]==doc['content'][2:]
    old_atoms=[node for node in doc['content'][1]['content'] if node.get('type')!='text' or node.get('marks')]
    new_atoms=[node for node in result['content'][1]['content'] if node.get('type')!='text' or node.get('marks')]
    assert old_atoms==new_atoms and doc==original


@pytest.mark.parametrize('replacement',['响应为43毫秒，尚未部署。','响应为42秒，尚未部署。','响应为42毫秒，已经部署。',' '])
def test_polish_rejects_changed_numbers_or_completion_state(client,monkeypatch,replacement):
    pid=project(client);manual_package(client,pid);sid=section(client,pid);base=save(client,sid,'响应为42毫秒，尚未部署。').json()
    model(monkeypatch,lambda row:replacement);job,result=execute(pid,sid,base['id'])
    assert result['document'] is None and not result['polish_validation']['passed']
    assert result['validation_error'] and result['raw_text']
    response=client.post(f'/api/v1/sections/{sid}/accept',json={'task_id':job['id'],'base_revision_id':base['id']})
    assert response.status_code==422
    assert documents.current(db.one('SELECT * FROM sections WHERE id=?',(sid,)))['text']=='响应为42毫秒，尚未部署。'


def test_polish_rejects_reordered_missing_or_duplicate_paragraphs():
    doc=documents.normalize(documents.text_document('第一段。\n第二段。'));editable=blocks(doc)
    entries=[{'source_node_id':row['source_node_id'],'text':row['text']} for row in editable]
    for changed in (entries[::-1],entries[:1],[entries[0],entries[0]]):
        with pytest.raises(ValueError):validate({'paragraphs':changed},doc,editable)


def test_polish_does_not_delete_or_strengthen_a_condition():
    doc=documents.normalize(documents.text_document('如果启用校验，系统可能拒绝该请求。'));editable=blocks(doc)
    for text in ('系统可能拒绝该请求。','如果启用校验，系统拒绝该请求。'):
        with pytest.raises(ValueError):validate({'paragraphs':[{'source_node_id':editable[0]['source_node_id'],'text':text}]},doc,editable)
    doc=documents.normalize(documents.text_document('响应不超过42毫秒。'));editable=blocks(doc)
    with pytest.raises(ValueError):validate({'paragraphs':[{'source_node_id':editable[0]['source_node_id'],'text':'响应为42毫秒。'}]},doc,editable)


def test_polish_preserves_selected_scope_and_accepts_a_valid_candidate(client,monkeypatch):
    pid=project(client);manual_package(client,pid);sid=section(client,pid);base=save(client,sid,'首段保留。\n这个系统尚未部署。\n末段保留。').json()
    original=db.loads(base['document']);target=original['content'][1]['attrs']['nodeId']
    model(monkeypatch,lambda row:row['text'].replace('这个系统','该系统'))
    job,result=execute(pid,sid,base['id'],[target])
    assert result['document']['content'][0]==original['content'][0]
    assert result['document']['content'][2]==original['content'][2]
    response=client.post(f'/api/v1/sections/{sid}/accept',json={'task_id':job['id'],'base_revision_id':base['id']})
    assert response.status_code==200,response.text
    assert response.json()['text']=='首段保留。\n该系统尚未部署。\n末段保留。'
