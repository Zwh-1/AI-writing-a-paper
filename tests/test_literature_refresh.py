import pytest
from test_workbench import client,project,file
from app import db,provider,security,tasks,material_summary as summary,literature_refresh as refresh


@pytest.fixture
def library(client,monkeypatch):
    pid=project(client);original=file(client,pid,'研究问题是验证凭证。研究方法采用案例分析。适用条件仅为单用户。结果不含并发验证。')
    security.save_key('summary-test-key',summary.PROFILE)
    db.set_setting('material_summary_provider',{**summary.DEFAULT,'model':'deepseek-test','input_price':0,'output_price':0})
    return client,pid,original


def fake_model(monkeypatch,after=None):
    calls=[]
    def call(task,system,user,manifest,**kwargs):
        body=db.loads(user);calls.append(body)
        if after:after()
        if 'original_pages' in body:
            page=body['original_pages'][0];quote=page['text'][:60];number=page['page']
        else:
            point=body['partial_readings'][0]['key_points'][0];quote=point['quote'];number=point['pages'][0]
        point={'text':'研究方法为案例分析，适用范围仍需对照原件核对。','pages':[number],'quote':quote}
        return {'text':db.dumps({'overview':'文献研究凭证验证方法，并保留单用户适用条件。','key_points':[point],'topics':[{'title':'方法与适用条件','points':[point]}]})}
    monkeypatch.setattr(tasks,'model_call',call);return calls


def execute(pid,**extra):
    task=tasks.enqueue(pid,'literature_refresh',{'generation_id':db.uid(),'root_task_id':None,'file_ids':[f['id'] for f in refresh.files_for(pid)],'budget_limit':3,**extra})
    return task,refresh.process(task,db.loads(task['payload']))


def test_fresh_literature_content_replaces_unchanged_cached_summary(library,monkeypatch):
    c,pid,original=library;fid=original['id'];source_hash=summary.source(fid)[2]
    old={'overview':'旧RAG阶段总结','key_points':[{'text':'旧要点','pages':[1]}],'topics':[{'title':'旧主题','points':[{'text':'旧要点','pages':[1]}]}]}
    db.execute('INSERT INTO material_summaries VALUES(?,?,?,?,?,?,?)',(fid,source_hash,summary.configuration_hash(summary.config()),db.dumps(old),'old-model',None,'2025-01-01'))
    assert summary.current_summary(db.one('SELECT * FROM material_summaries'),source_hash)
    calls=fake_model(monkeypatch);task,result=execute(pid)
    assert result['complete'] and result['updated']==1 and calls
    row=db.one('SELECT * FROM material_summaries');data=db.loads(row['data'])
    assert row['created_at']!='2025-01-01' and data['overview']!=old['overview']
    assert data['reading_profile']['method']==refresh.METHOD and data['reading_profile']['all_available_text_read']
    assert summary.status(fid)['stale'] is False
    assert db.loads(db.one('SELECT payload FROM tasks WHERE id=?',(task['id'],))['payload'])['generation_id']==db.loads(task['payload'])['generation_id']
    assert refresh.status(pid)['updated']==1
    gen=db.loads(task['payload'])['generation_id']
    assert db.loads(db.setting(refresh.state_key(gen,'previous:'+fid))['data'])==old


def test_same_generation_reuses_its_new_readings(library,monkeypatch):
    c,pid,original=library;calls=fake_model(monkeypatch);task,result=execute(pid);before=len(calls)
    assert refresh.process(task,db.loads(task['payload']))['complete'] and len(calls)==before
    _,second=execute(pid)
    assert second['complete'] and len(calls)>before


def test_resume_checks_current_source_instead_of_only_generation_id(library,monkeypatch):
    c,pid,original=library;fake_model(monkeypatch);task,result=execute(pid)
    db.execute('UPDATE pages SET notes=? WHERE file_id=?',('新增原文核对备注',original['id']))
    assert summary.status(original['id'])['stale']
    result=refresh.process(task,db.loads(task['payload']))
    assert result['complete']
    row=db.one('SELECT * FROM material_summaries WHERE file_id=?',(original['id'],))
    assert row['input_hash']==summary.source(original['id'])[2] and not summary.status(original['id'])['stale']


def test_permission_change_keeps_previous_content(library,monkeypatch):
    c,pid,original=library;fid=original['id']
    db.execute('INSERT INTO material_summaries VALUES(?,?,?,?,?,?,?)',(fid,summary.source(fid)[2],'old','{}','old',None,'2025-01-01'))
    old=db.one('SELECT * FROM material_summaries')
    fake_model(monkeypatch,lambda:db.execute("UPDATE files SET egress='local_only' WHERE id=?",(fid,)))
    _,result=execute(pid)
    assert not result['complete'] and result['unresolved']
    assert db.one('SELECT * FROM material_summaries')==old


def test_explicit_ranges_keep_every_original_character():
    page={'page':1,'title':'方法','text':'完整原文与限定条件。'*1000,'char_start':0,'char_end':11000,'scope':'complete_available_original_page_text'}
    page['char_end']=len(page['text'])
    groups=refresh.chunks_for('研究文献',[page],{'context_limit':4096,'max_output_tokens':512})
    ranges=[p for group in groups for p in group]
    assert ''.join(p['text'] for p in ranges)==page['text']
    assert ranges[0]['char_start']==0 and ranges[-1]['char_end']==len(page['text'])
    assert all(a['char_end']==b['char_start'] for a,b in zip(ranges,ranges[1:]))


def test_quote_location_is_not_forged_when_model_misquotes():
    point={'text':'模型候选','pages':[1],'quote':'不存在的千人实验'}
    data={'overview':'概括','key_points':[point],'topics':[{'title':'适用条件','points':[point]}]}
    checked=refresh.checked(data,{1},{1:'原文仅限单用户，未作并发验证。'})
    assert checked['key_points'][0]['evidence_status']=='needs_review'
    assert checked['key_points'][0]['quote']=='' and checked['quote_checks']['located']==0


def test_secondary_research_use_includes_sbtcert_style_original(client):
    from app import material_policy
    pid=project(client);original=file(client,pid,'学术凭证研究。',role='example')
    material_policy.set_usages(pid,original['id'],{'usages':['example','research'],'source_kind':'research_publication','note':'同时用于研究与示例'})
    assert [f['id'] for f in refresh.files_for(pid)]==[original['id']]


def test_missing_citation_is_quarantined_instead_of_inventing_a_page():
    good={'text':'仅限单用户。','pages':[1],'quote':'仅限单用户'}
    unsupported={'text':'千人实验通过。','pages':[],'quote':''}
    result=refresh.checked({'overview':'研究概括','key_points':[good,unsupported],'topics':[{'title':'范围','points':[good]}]},{1},{1:'原文仅限单用户。'})
    assert len(result['key_points'])==1 and result['reading_gaps'][0]['text']=='千人实验通过。'


def test_printed_page_number_can_be_resolved_only_from_a_real_quote():
    point={'text':'适用范围受限。','pages':[100],'quote':'仅限单用户'}
    result=refresh.checked({'overview':'研究概括','key_points':[point],'topics':[{'title':'范围','points':[point]}]},{2},{2:'条件：仅限单用户。'})
    assert result['key_points'][0]['pages']==[2] and result['key_points'][0]['evidence_status']=='quote_located'


def test_known_invalid_json_recovers_once_without_using_old_summary(library,monkeypatch):
    c,pid,original=library;calls=[]
    def fake(task,system,user,manifest,**kwargs):
        body=db.loads(user);calls.append(body)
        if 'validation_feedback' not in body:return {'text':'{"overview":"不完整"}{"extra":0}'}
        page=body['original_pages'][0];point={'text':'保留适用条件。','pages':[page['page']],'quote':page['text'][:40]}
        return {'text':db.dumps({'overview':'按原文核对范围。','key_points':[point],'topics':[{'title':'范围','points':[point]}]})}
    monkeypatch.setattr(tasks,'model_call',fake)
    _,result=execute(pid)
    assert result['complete'] and len(calls)==2 and calls[-1]['validation_feedback']
