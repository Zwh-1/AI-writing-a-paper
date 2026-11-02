from pathlib import Path
from types import SimpleNamespace
import json
import runpy

import httpx
import pytest

from test_workbench import client,file
from test_paid_testing import setup_allocation
from app import db,provider,knowledge_units,knowledge_acceptance

verifier=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/verify_full_knowledge_compilation.py'))
runner=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/knowledge_paid_testing.py'))


def compiled(client,monkeypatch,tmp_path,extra_source=False):
    pid,cfg,grant,_=setup_allocation(client,monkeypatch,limit=.3,history=False)
    original=file(client,pid,'方法仅在单用户环境验证。\n\n没有验证并发性能。');knowledge_units.build(pid,original['id'])
    if extra_source:
        other=file(client,pid,'另一篇文献有独立条件。');knowledge_units.build(pid,other['id'])
    def response(request):
        prompt=json.loads(json.loads(request.content)['messages'][-1]['content'])
        value={'observations':[{'observation_id':o['id'],'no_claim_reason':'','claims':[{'statement':'原文限定条件','quote':o['text'],'conditions':[]}]}
            for o in prompt['observations']]}
        return httpx.Response(200,json={'id':'synthetic-full-coverage','choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50}})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(response)))
    result=runner['run_phase'](pid,SimpleNamespace(allocation=grant['id'],phase='semantic',file=[original['id']],max_items=None,
        artifact=None,query=None,output=tmp_path/'verification',batch_extraction=True,parallel_requests=1))
    assert result['status']=='succeeded'
    return pid,original,grant,result['task_id']


def test_full_coverage_certificate_checks_each_response_and_source(client,monkeypatch,tmp_path):
    pid,original,grant,tid=compiled(client,monkeypatch,tmp_path)
    checked=verifier['verify'](pid,tid,grant['id'])
    assert checked['compilation_complete'] and checked['required_ranges']==checked['model_processed_ranges']
    assert checked['completed_model_responses_referenced']==1 and checked['planned_scope_covers_current_research_files']
    assert not checked['human_semantic_quality_accepted']
    verifier['record_verified'](checked)
    status=knowledge_acceptance.semantic_compilation_status(pid)
    assert status['complete'] and not status['human_quality_accepted']
    assert client.get(f'/api/v1/projects/{pid}/knowledge/status').json()['semantic_compilation']['complete']
    assert not knowledge_acceptance.status(pid)['all_complete']
    db.execute('UPDATE sources SET text=text||? WHERE file_id=?',('原文变更',original['id']))
    assert not knowledge_acceptance.semantic_compilation_status(pid)['complete']


def test_selected_subset_cannot_claim_full_project_compilation(client,monkeypatch,tmp_path):
    pid,original,grant,tid=compiled(client,monkeypatch,tmp_path,extra_source=True)
    checked=verifier['verify'](pid,tid,grant['id'])
    assert not checked['planned_scope_covers_current_research_files'] and not checked['compilation_complete']
    with pytest.raises(ValueError):verifier['record_verified'](checked)
    assert not db.rows("SELECT id FROM audit WHERE action='knowledge_full_semantic_compilation_verified'")


def test_completed_checkpoint_without_completed_response_is_not_full_proof(client,monkeypatch,tmp_path):
    pid,original,grant,tid=compiled(client,monkeypatch,tmp_path)
    db.execute("UPDATE generation_runs SET status='completed_invalid' WHERE task_id=?",(tid,))
    checked=verifier['verify'](pid,tid,grant['id'])
    assert not checked['compilation_complete'] and checked['model_processed_ranges']==0
    assert any('model_response_not_complete' in reason for row in checked['file_checks'] for reason in row['errors'])
