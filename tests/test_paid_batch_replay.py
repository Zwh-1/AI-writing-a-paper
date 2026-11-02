import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from test_workbench import client, file
from test_paid_testing import setup_allocation
from app import db, provider, paid_testing, knowledge_units, knowledge_batch_compiler

runner=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/knowledge_paid_testing.py'))


def stopped_batch(client,monkeypatch,tmp_path):
    pid,cfg,grant,_=setup_allocation(client,monkeypatch,limit=.3,history=False)
    original=file(client,pid,'方法只验证单用户环境。\n\n并发环境尚未验证。')
    knowledge_units.build(pid,original['id'])
    sent=[]
    def unknown(request):
        sent.append(json.loads(json.loads(request.content)['messages'][-1]['content']))
        raise httpx.ReadTimeout('synthetic response interrupted',request=request)
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(unknown)))
    args=SimpleNamespace(allocation=grant['id'],phase='semantic',file=[original['id']],max_items=None,
        artifact=None,query=None,output=tmp_path/'semantic',batch_extraction=True,parallel_requests=1)
    report=runner['run_phase'](pid,args)
    assert report['status']=='outcome_unknown' and len(sent)==1
    run=db.one('SELECT * FROM generation_runs WHERE task_id=?',(report['task_id'],))
    authorization=paid_testing.authorize_resume(pid,grant['id'],[run['id']],'允许重试这笔新未知请求并继续编译。')
    args=SimpleNamespace(allocation=grant['id'],phase='replay',unknown_run=run['id'],file=[],max_items=None,
        artifact=None,query=None,output=tmp_path/'replay',resume_authorization=authorization['id'])
    return pid,grant,run,sent[0],args


def test_batch_replay_uses_exact_original_scope_once_and_retains_hold(client,monkeypatch,tmp_path):
    pid,grant,run,original,args=stopped_batch(client,monkeypatch,tmp_path)
    seen=[]
    def response(request):
        value=json.loads(json.loads(request.content)['messages'][-1]['content']);seen.append(value)
        data={'observations':[{'observation_id':o['id'],'no_claim_reason':'','claims':[
            {'statement':'此段保留原文条件','source_segment':0,'conditions':[]}]} for o in value['observations']]}
        return httpx.Response(200,json={'id':'synthetic-authorized-replay','choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50}})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(response)))
    result=runner['run_phase'](pid,args)
    assert result['status']=='succeeded' and seen==[original]
    assert db.one('SELECT * FROM generation_runs WHERE id=?',(run['id'],))==run
    replay=db.one('SELECT manifest FROM generation_runs WHERE task_id=?',(result['task_id'],))
    assert db.loads(replay['manifest'])['knowledge_authorized_replay_of_run_id']==run['id']
    repeated=runner['run_phase'](pid,args)
    assert repeated['status']=='budget_blocked' and len(seen)==1
    assert paid_testing.get(pid,grant['id'])['limit']==.3
    assert paid_testing.charge(pid,grant['id'])>0


def test_changed_batch_protocol_is_rejected_before_creating_replay_task(client,monkeypatch,tmp_path):
    pid,grant,run,original,args=stopped_batch(client,monkeypatch,tmp_path)
    before=db.rows('SELECT id FROM tasks')
    actual=knowledge_batch_compiler.payload_for
    monkeypatch.setattr(knowledge_batch_compiler,'payload_for',lambda observations:{**actual(observations),'changed':True})
    with pytest.raises(ValueError,match='输入或协议已变化'):runner['run_phase'](pid,args)
    assert db.rows('SELECT id FROM tasks')==before


def test_unknown_batch_replay_stops_further_charges(client,monkeypatch,tmp_path):
    pid,grant,run,original,args=stopped_batch(client,monkeypatch,tmp_path)
    seen=[]
    def unknown(request):
        seen.append(request);raise httpx.ReadTimeout('synthetic second interruption',request=request)
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(unknown)))
    result=runner['run_phase'](pid,args)
    assert result['status']=='outcome_unknown' and len(seen)==1
    again=runner['run_phase'](pid,args)
    assert again['status']=='budget_blocked' and len(seen)==1
