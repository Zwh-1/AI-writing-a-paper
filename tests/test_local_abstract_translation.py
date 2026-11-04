"""Explicit local English abstracts stay bound to the current Chinese candidate."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from test_workbench import client,project,manual_package,file  # noqa: F401
from app import db,documents,security,tasks,paper_writing as paper
from app.writing_agent.common import digest


@pytest.fixture
def translation(client,monkeypatch):
    pid=project(client);manual_package(client,pid)
    zh=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'摘要','kind':'abstract'}).json()
    en=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'Abstract','kind':'abstract_en'}).json()
    assert client.post(f'/api/v1/projects/{pid}/terms',json={'zh':'灵魂绑定凭证','en':'soulbound credentials'}).status_code==200
    origin=file(client,pid,'实际项目资料只说明设计范围，真实部署结果仍需核实。')
    source_doc=documents.normalize(documents.text_document(
        '本文围绕灵魂绑定凭证提出2项设计约束，尚未确认实际部署情况。【待补证据】\n关键词：灵魂绑定凭证；可验证凭证'))
    source={'section_id':zh['id'],'base_revision_id':None,'document':source_doc,
            'text':documents.plain(source_doc),'manifest':{'file_ids':[origin['id']]}}
    snapshot=paper.frozen_state(pid)
    task=tasks.enqueue(pid,'write_paper',{'snapshot':snapshot,'snapshot_hash':digest(snapshot),
        'budget_limit':10.,'generation_order':[zh['id'],en['id']]})
    db.execute("UPDATE tasks SET status='running',attempt=1,result=? WHERE id=?",
               (db.dumps({'paper_sections':[source]}),task['id']))
    task=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    candidate={'paragraphs':[{'text':'This study proposes 2 design constraints for soulbound credentials; actual deployment has not yet been confirmed. [Evidence needed]'},
                             {'text':'Keywords: soulbound credentials; verifiable credentials'}],
               'missing_materials':[],'notes':'Compared with the current Chinese candidate; human review remains pending.'}
    record={'schema':'writing.local_abstract_translation.v1','task_id':task['id'],'section_id':en['id'],
            'source_section_id':zh['id'],'source_text_hash':digest(source['text']),
            'source_document_hash':digest(source_doc),'candidate_blob':security.put_blob(db.dumps(candidate).encode()),
            'external_api_response':False,'human_review':'pending','semantic_review':'pending',
            'provenance':{'origin':'codex_recovery_translation'},
            'review_note':'逐段核对当前中文候选的目的、设计边界、2项约束、尚未部署限定及关键词，仍待最终人工审阅。'}
    def forbid(*args,**kwargs):pytest.fail('Local translation must not fall through to an API')
    monkeypatch.setattr(tasks,'model_call',forbid)
    return {'task':task,'section':en,'source':source,'record':record,'candidate':candidate,'file':origin,'pid':pid}


def install(env,record=None):
    task=env['task'];out=db.loads(db.one('SELECT result FROM tasks WHERE id=?',(task['id'],))['result'])
    out['local_abstract_translations']={env['section']['id']:env['record'] if record is None else record}
    db.execute('UPDATE tasks SET result=? WHERE id=?',(db.dumps(out),task['id']))


def translate(env):return paper.translate_candidate(env['task'],env['section'],env['source'],'保持原意')


def replace_blob(env,candidate):
    record=deepcopy(env['record']);record['candidate_blob']=security.put_blob(db.dumps(candidate).encode())
    install(env,record)


def test_matching_translation_is_local_and_tracks_the_actual_chinese_hash(translation):
    env=translation;install(env)
    result=translate(env)
    assert result['run_id'] is None
    assert result['manifest']['paper_translation']['source_text_hash']==digest(env['source']['text'])
    local=result['manifest']['local_translation']
    assert local['provenance']['origin']=='codex_recovery_translation'
    assert local['external_api_response'] is False and local['human_review']==local['semantic_review']=='pending'
    assert 'not_semantic_or_human_approval' in local['check_scope']
    assert len(result['document']['content'])==2
    assert not db.rows('SELECT id FROM generation_runs WHERE task_id=?',(env['task']['id'],))


@pytest.mark.parametrize('change',['text_hash','document_hash','task_id','section_id','source_id','human','semantic','api','origin','review_note','attempt'])
def test_wrong_binding_or_false_approval_stops_without_an_api(translation,change):
    env=translation;record=deepcopy(env['record'])
    if change=='text_hash':record['source_text_hash']='changed'
    elif change=='document_hash':record['source_document_hash']='changed'
    elif change=='task_id':record['task_id']='other-task'
    elif change=='section_id':record['section_id']='other-target'
    elif change=='source_id':record['source_section_id']='other-source'
    elif change=='human':record['human_review']='verified'
    elif change=='semantic':record['semantic_review']='supported'
    elif change=='api':record['external_api_response']=True
    elif change=='origin':record['provenance']['origin']='provider_response'
    elif change=='review_note':record['review_note']=''
    elif change=='attempt':env['task']['attempt']=0
    install(env,record)
    with pytest.raises(HTTPException) as error:translate(env)
    assert error.value.status_code==409
    assert error.value.detail['code']=='LOCAL_ABSTRACT_TRANSLATION_INVALID'


def test_passed_source_must_still_be_the_current_task_complete_candidate(translation):
    env=translation;install(env)
    env['source']=deepcopy(env['source']);env['source']['text']='替换的任意中文摘要。'
    env['source']['document']=documents.normalize(documents.text_document(env['source']['text']))
    with pytest.raises(HTTPException) as error:translate(env)
    assert '当前整篇任务保存' in error.value.detail['message']


@pytest.mark.parametrize('field',['source_ids','reference_ids','evidence_ids','claim_spans','citation_intents','fact_ids','tool_calls'])
def test_translation_cannot_add_sources_claims_or_tools_even_if_schema_ignores_them(translation,field):
    env=translation;candidate=deepcopy(env['candidate']);candidate['paragraphs'][0][field]=['extra']
    replace_blob(env,candidate)
    with pytest.raises(HTTPException) as error:translate(env)
    assert '只可含正文text' in error.value.detail['message']


@pytest.mark.parametrize('change',['number','placeholder','negative','keywords','terms','paragraph_count','empty_english'])
def test_basic_fidelity_guards_reject_clear_omissions_without_claiming_semantic_approval(translation,change):
    env=translation;candidate=deepcopy(env['candidate'])
    if change=='number':candidate['paragraphs'][0]['text']=candidate['paragraphs'][0]['text'].replace('2','3')
    elif change=='placeholder':candidate['paragraphs'][0]['text']=candidate['paragraphs'][0]['text'].replace('[Evidence needed]','')
    elif change=='negative':candidate['paragraphs'][0]['text']=candidate['paragraphs'][0]['text'].replace('has not yet been confirmed','has been confirmed')
    elif change=='keywords':candidate['paragraphs'][1]['text']=candidate['paragraphs'][1]['text'].replace('Keywords:','')
    elif change=='terms':
        for p in candidate['paragraphs']:p['text']=p['text'].replace('soulbound credentials','credentials')
    elif change=='paragraph_count':candidate['paragraphs']=candidate['paragraphs'][:1]
    elif change=='empty_english':candidate['paragraphs'][0]['text']='中文不是英文译文。'
    replace_blob(env,candidate)
    with pytest.raises(HTTPException) as error:translate(env)
    assert error.value.detail['code']=='LOCAL_ABSTRACT_TRANSLATION_INVALID'


def test_source_permission_is_freshly_checked_for_local_translation(translation):
    env=translation;install(env)
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(env['file']['id'],))
    with pytest.raises(HTTPException):translate(env)


def test_absent_record_preserves_the_original_remote_translation_path(translation,monkeypatch):
    env=translation;calls=[]
    def remote(task,system,user,manifest):
        calls.append(db.loads(user));return {'text':db.dumps(env['candidate']),'run_id':'real-existing-route'}
    monkeypatch.setattr(tasks,'model_call',remote)
    result=translate(env)
    assert len(calls)==1 and calls[0]['source_abstract']['text']==env['source']['text']
    assert 'local_translation' not in result['manifest'] and 'run_id' not in result
