"""Short chapters reuse the same strict explicit local-draft gate."""
import pytest
from fastapi import HTTPException

from test_workbench import client  # noqa: F401
from test_writing_flow_integration import writing_project  # noqa: F401
from test_local_argument_unit_recovery import recovery,record,install,generate  # noqa: F401
from app import db,tasks,writing_flow
from app.writing_agent.common import unblob


def short(env):
    env['section']={**env['section'],'target_words':1000}
    item=record(env,index=0,prefix=[])
    item['total']=1
    return item


def no_old_route(monkeypatch):
    def fail(*args,**kwargs):pytest.fail('Explicit local short-chapter recovery used an old or paid writer path')
    for name in ('completed_candidate_response','completed_invalid_draft','_cached_call'):
        monkeypatch.setattr(writing_flow,name,fail)


def test_matching_short_chapter_record_is_local_and_remains_unverified(recovery,monkeypatch):
    env=recovery;item=short(env);install(env,{'0':item});no_old_route(monkeypatch)
    before=db.rows('SELECT id,status FROM generation_runs WHERE task_id=? ORDER BY rowid',(env['task']['id'],))
    result=generate(env)
    assert tasks.candidate_from_text(result['text'])[0]['paragraphs'][0]['text']==env['candidate']['paragraphs'][0]['text']
    assert result['run_id'] is None and result['local_recovery_blob']==item['candidate_blob']
    step=db.one("SELECT * FROM writing_steps WHERE task_id=? AND stage='local_chapter_recovery'",(env['task']['id'],))
    saved=unblob(step['output_blob'])
    assert step['state']=='completed' and saved['run_id'] is None
    assert saved['external_api_response'] is False and saved['human_review']==saved['semantic_review']=='pending'
    assert env['manifest']['local_argument_unit_recoveries']['status']=='unverified'
    assert db.rows('SELECT id,status FROM generation_runs WHERE task_id=? ORDER BY rowid',(env['task']['id'],))==before


def test_invalid_short_chapter_record_never_falls_through_to_an_api(recovery,monkeypatch):
    env=recovery;item=short(env);item['expected_prefix_hash']='does-not-match-empty-prefix'
    install(env,{'0':item});no_old_route(monkeypatch)
    with pytest.raises(HTTPException) as error:generate(env)
    assert error.value.status_code==409
    assert error.value.detail['code']=='LOCAL_ARGUMENT_UNIT_RECOVERY_INVALID'
    assert not db.one("SELECT id FROM writing_steps WHERE task_id=? AND stage='local_chapter_recovery'",(env['task']['id'],))


def test_short_chapter_without_record_keeps_the_existing_remote_path(recovery,monkeypatch):
    env=recovery;short(env);calls=[]
    expected={'text':db.dumps(env['candidate']),'run_id':'synthetic-existing-remote'}
    monkeypatch.setattr(writing_flow,'completed_candidate_response',lambda *args:None)
    monkeypatch.setattr(writing_flow,'completed_invalid_draft',lambda *args:None)
    def remote(*args,**kwargs):calls.append(kwargs['stage']);return expected
    monkeypatch.setattr(writing_flow,'_cached_call',remote)
    assert generate(env) is expected
    assert calls==['draft']
    assert 'local_argument_unit_recoveries' not in env['manifest']


def test_short_chapter_without_record_still_prefers_its_completed_old_candidate(recovery,monkeypatch):
    env=recovery;short(env)
    expected={'text':'old complete candidate','run_id':'synthetic-old-run'}
    monkeypatch.setattr(writing_flow,'completed_candidate_response',lambda *args:expected)
    def fail(*args,**kwargs):pytest.fail('Existing candidate cache was bypassed')
    monkeypatch.setattr(writing_flow,'completed_invalid_draft',fail)
    monkeypatch.setattr(writing_flow,'_cached_call',fail)
    assert generate(env) is expected


def test_translation_does_not_enter_the_local_chapter_generation_route(recovery,monkeypatch):
    env=recovery;item=short(env);install(env,{'0':item})
    env['context']['operation']='translate'
    env['section']['kind']='abstract_en'
    expected={'text':'unchanged translation route','run_id':'synthetic-translation'}
    monkeypatch.setattr(writing_flow,'completed_candidate_response',lambda *args:expected)
    def fail(*args,**kwargs):pytest.fail('Translation entered a local generation recovery')
    monkeypatch.setattr(writing_flow,'_local_argument_unit_recovery',fail)
    assert generate(env) is expected
