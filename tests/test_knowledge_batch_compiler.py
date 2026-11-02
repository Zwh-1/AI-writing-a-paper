import pytest
from fastapi import HTTPException

from test_workbench import client,project,file
from app import db,provider,tasks,knowledge_units,knowledge_compiler as compiler,knowledge_batch_compiler as batch


def library(client):
    pid=project(client);original=file(client,pid,'方法仅在单用户环境验证。\n\n没有验证并发场景。\n\n参考文献仅为导航。')
    knowledge_units.build(pid,original['id']);task=tasks.enqueue(pid,'graph_wiki_build',{'provider_profile':'primary','batch_extraction':True})
    return pid,original,task


def fake_batches(monkeypatch,seen,missing=False,wrong_quote=False):
    from app import knowledge_model_bridge
    def json_call(task,operation,payload,refs):
        seen.append(operation)
        if operation=='knowledge_extract_quote_correction':
            o=payload['observation']
            return {'observation_id':o['id'],'no_claim_reason':'','claims':[{'statement':'待核论断','quote':'不存在的文字' if wrong_quote else o['text'],'conditions':[]}]},'synthetic-correction'
        if operation=='knowledge_extract':
            o=payload['observation'];return {'claims':[{'observation_id':o['id'],'statement':'作者报告限定条件','quote':o['text'],'conditions':[]}]},'synthetic-single'
        result=[]
        for i,o in enumerate(payload['observations']):
            result.append({'observation_id':o['id'],'no_claim_reason':'没有可提取研究论断' if i%2 else '',
                'claims':[] if i%2 else [{'statement':'作者报告限定条件','quote':'不存在的文字' if wrong_quote else o['text'],'conditions':[]}]})
        return {'observations':result[:-1] if missing else result},'synthetic-batch'
    monkeypatch.setattr(knowledge_model_bridge,'json_call',json_call)


def test_batch_reads_every_range_and_persists_zero_claim_reasons(client,monkeypatch):
    pid,original,task=library(client);seen=[];fake_batches(monkeypatch,seen)
    result=batch.compile_file(task,original['id'],namespace='literature')
    assert result['all_current_ranges_model_processed'] and result['expected_ranges']==len(result['observation_ids'])
    assert result['page_ids'] and result['gaps'] and not result['semantic_quality_verified']
    assert len(seen)==1
    again=batch.compile_file(task,original['id'],namespace='literature')
    assert len(seen)==1 and again['gaps']==result['gaps']
    assert not db.rows('SELECT * FROM generation_runs') and not db.rows('SELECT * FROM knowledge_review_events')


def test_existing_single_range_checkpoints_are_reused(client,monkeypatch):
    pid,original,task=library(client);seen=[];fake_batches(monkeypatch,seen)
    original_result=compiler.compile_file(task,original['id'],'llm',namespace='literature')
    count=len(seen)
    result=batch.compile_file(task,original['id'],namespace='literature')
    assert len(seen)==count and result['assertion_ids']==original_result['assertion_ids']


def test_missing_observation_requires_a_separate_completed_model_judgment(client,monkeypatch):
    pid,original,task=library(client);seen=[];fake_batches(monkeypatch,seen,missing=True)
    result=batch.compile_file(task,original['id'],namespace='literature')
    assert result['all_current_ranges_model_processed']
    assert seen.count('knowledge_extract_quote_correction')==1


def test_invented_quote_is_never_adopted(client,monkeypatch):
    pid,original,task=library(client);seen=[];fake_batches(monkeypatch,seen,wrong_quote=True)
    with pytest.raises(HTTPException) as error:batch.compile_file(task,original['id'],namespace='literature')
    assert error.value.detail['code']=='INVALID_QUOTE'
    assert not db.rows('SELECT * FROM kg_assertions')


def test_groups_fit_original_config_context_without_truncation():
    observations=[{'id':str(i),'text':'原文字符。'*500,'namespace':'literature','metadata_json':'{}'} for i in range(5)]
    cfg={**provider.DEFAULT,'context_limit':32000,'max_output_tokens':8192}
    groups=list(batch.groups(observations,cfg))
    assert sum(len(g) for g in groups)==5 and len(groups)>1
    assert [o['text'] for g in groups for o in g]==[o['text'] for o in observations]


def test_parallel_batches_inherit_the_same_frozen_provider_context(client,monkeypatch):
    import threading,time
    from app import knowledge_model_bridge
    pid,original,task=library(client)
    task['payload']=db.dumps({'provider_profile':'primary','batch_extraction':True,'knowledge_parallel_requests':2})
    db.execute('UPDATE tasks SET payload=? WHERE id=?',(task['payload'],task['id']))
    monkeypatch.setattr(batch,'groups',lambda observations,cfg:iter([[o] for o in observations]))
    seen=[];active=0;maximum=0;lock=threading.Lock();barrier=threading.Barrier(2)
    def json_call(task,operation,payload,refs):
        nonlocal active,maximum
        with lock:
            active+=1;maximum=max(maximum,active);seen.append(provider.config()['model']);index=len(seen)
        if index<=2:barrier.wait(timeout=20)
        o=payload['observations'][0]
        with lock:active-=1
        return {'observations':[{'observation_id':o['id'],'no_claim_reason':'','claims':[{'statement':'限定条件','quote':o['text'],'conditions':[]}]}]},'synthetic-parallel'
    monkeypatch.setattr(knowledge_model_bridge,'json_call',json_call)
    with provider.request_scope({**provider.DEFAULT,'model':'same-frozen-model'},'synthetic-key'):
        result=batch.compile_file(task,original['id'],namespace='literature')
    assert maximum==2 and set(seen)=={'same-frozen-model'}
    assert result['all_current_ranges_model_processed'] and len(seen)==result['expected_ranges']


def test_correction_can_explicitly_leave_an_unreadable_fragment_without_fake_claim(client,monkeypatch):
    from app import knowledge_model_bridge
    pid,original,task=library(client);unit=db.one('SELECT id FROM knowledge_units ORDER BY ordinal LIMIT 1')
    observation=compiler.observe_unit(pid,unit['id'],remote=True)
    def correction(task,operation,payload,refs):
        assert operation=='knowledge_extract_quote_correction' and payload['observation']['text']==observation['text']
        return {'observation_id':observation['id'],'claims':[],'no_claim_reason':'片段不支持此前的跨页推断，需回读原件'},'synthetic-correction'
    monkeypatch.setattr(knowledge_model_bridge,'json_call',correction)
    result=batch.validate_row(task,observation,{'claims':[{'statement':'错误推断','quote':'当前原文不存在','conditions':[]}]},'synthetic-original')
    assert not result['candidates'] and result['no_claim_reason']
    assert result['run_ids']==['synthetic-original','synthetic-correction'] and not result['human_approval_added']


def test_completed_paid_batch_recovers_after_checkpoint_loss_without_another_request(client,monkeypatch,tmp_path):
    import httpx,json,runpy
    from pathlib import Path
    from types import SimpleNamespace
    from test_paid_testing import setup_allocation
    pid,cfg,grant,_=setup_allocation(client,monkeypatch,limit=.3,history=False)
    original=file(client,pid,'方法仅验证单用户环境。\n\n并发场景尚未验证。');knowledge_units.build(pid,original['id'])
    seen=[]
    def handle(request):
        seen.append(request.headers['X-Client-Request-Id']);body=json.loads(request.content)
        payload=json.loads(body['messages'][-1]['content'])
        data={'observations':[{'observation_id':o['id'],'no_claim_reason':'','claims':[{'statement':'该段保留适用条件','quote':o['text'],'conditions':[]}]}
            for o in payload['observations']]}
        return httpx.Response(200,json={'id':'saved-batch','choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50}})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    runner=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/knowledge_paid_testing.py'))
    args=SimpleNamespace(allocation=grant['id'],phase='semantic',file=[original['id']],max_items=None,artifact=None,query=None,
        output=tmp_path/'batch-recovery',batch_extraction=True,parallel_requests=1)
    first=runner['run_phase'](pid,args);assert first['status']=='succeeded' and len(seen)==1
    db.execute("UPDATE knowledge_build_items SET status='failed' WHERE project_id=?",(pid,))
    second=runner['run_phase'](pid,args)
    assert second['status']=='succeeded' and len(seen)==1
    assert db.one('SELECT COUNT(*) n FROM generation_runs')['n']==1


def test_selected_quote_window_is_exact_original_and_keeps_full_coverage(client,monkeypatch):
    pid,original,task=library(client);unit=db.one('SELECT id FROM knowledge_units ORDER BY ordinal LIMIT 1')
    observation=compiler.observe_unit(pid,unit['id'],remote=True)
    row={'claims':[{'statement':'原文限定为单用户环境','source_segment':0,'conditions':[]}],
         'entities':[{'name':'单用户','kind':'environment'}]}
    result=batch.validate_row(task,observation,row,'synthetic-window',allow_correction=False)
    assert result['candidates'][0]['quote'] in observation['text']
    assert result['candidates'][0]['quote_binding']['method']=='model_selected_exact_original_window'
    text='原文文字。'*1500;windows=batch.quote_windows(text)
    covered=set(i for w in windows for i in range(w['start'],w['end']))
    assert len(covered)==len(text) and windows[0]['start']==0 and windows[-1]['end']==len(text)


@pytest.mark.parametrize('index',[True,-1,9999,'0'])
def test_invalid_quote_window_cannot_be_treated_as_verbatim_source(client,index):
    pid,original,task=library(client);unit=db.one('SELECT id FROM knowledge_units ORDER BY ordinal LIMIT 1')
    observation=compiler.observe_unit(pid,unit['id'],remote=True)
    with pytest.raises(HTTPException):batch.validate_row(task,observation,{'claims':[{'statement':'待核','source_segment':index,'conditions':[]}]},'synthetic-window',allow_correction=False)
