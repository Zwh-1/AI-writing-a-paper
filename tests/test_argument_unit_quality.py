"""Real checkpoint/ledger tests for unit scope, length and copied full chapters."""
from copy import deepcopy

import httpx
import pytest

from test_workbench import client  # noqa: F401
from test_writing_flow_integration import writing_project, response  # noqa: F401
from test_writing_flow_safety import _unit_setup
from app import db, provider, tasks, writing_flow
from app.writing_agent.common import blob, unblob

_REAL_GENERATE=provider.generate


def run(task,section,context,manifest):
    return writing_flow.generate(task,section,'严格依据原文返回JSON',context,manifest,tasks.candidate_from_text)


def checkpoint(task):
    return db.one("SELECT * FROM writing_steps WHERE task_id=? AND stage='argument_units' ORDER BY rowid DESC LIMIT 1",(task['id'],))


def test_unit_scope_changes_output_targets_and_keeps_global_requirements(writing_project,monkeypatch):
    task,section,context,manifest=_unit_setup(writing_project)
    context['section']=deepcopy(section)
    context['instruction']='全篇不能编造实测结果；第四章约3000字。'
    context['writing_brief']={'target_words':3000,'points':[{'id':'p'+str(i)} for i in range(6)],
        'paragraph_plan':{'count':12,'total_words':3000,'paragraphs':[
            {'index':i,'purpose':'任务'+str(i),'target_words':250} for i in range(12)]}}
    context['argument_plan']={'paragraph_cards':[{'id':'a'+str(i),'point_ids':['p'+str(i)],
                                                'question':'任务'+str(i)} for i in range(6)]}
    original=deepcopy(context);seen=[]
    def transport(system,user,images=None):
        payload=db.loads(user);seen.append(payload)
        assert payload['section']['target_words']==1000
        assert payload['section']['paragraph_count']==4
        assert payload['writing_brief']['paragraph_plan']['count']==4
        assert sum(p['target_words'] for p in payload['writing_brief']['paragraph_plan']['paragraphs'])==1000
        assert len(payload['argument_plan']['paragraph_cards'])==2
        assert len(payload['writing_brief']['points'])==2
        assert payload['chapter_background']['instruction']==original['instruction']
        assert payload['chapter_background']['writing_brief']==original['writing_brief']
        assert payload['sources']==original['sources']
        return response({'paragraphs':[{'text':'当前单元'+str(payload['argument_unit']['index'])}]})
    monkeypatch.setattr(provider,'generate',transport)
    run(task,section,context,manifest)
    assert len(seen)==3 and context==original
    assert [len(p['argument_unit']['previous_actual_paragraphs']) for p in seen]==[0,1,2]


@pytest.mark.parametrize('words',[5741,3460])
def test_whole_chapter_sized_unit_is_rewritten_not_clipped(writing_project,monkeypatch,words):
    task,section,context,manifest=_unit_setup(writing_project);calls=[]
    def transport(system,user,images=None):
        unit=db.loads(user)['argument_unit']['index'];calls.append(unit)
        text='甲'*words if len(calls)==1 else '单元'+str(unit)+'的新论证。'
        return response({'paragraphs':[{'text':text}]})
    monkeypatch.setattr(provider,'generate',transport)
    result=db.loads(run(task,section,context,manifest)['text'])
    assert calls==[1,1,2,3]
    assert result['paragraphs'][0]['text']=='单元1的新论证。'
    saved=unblob(checkpoint(task)['output_blob'])
    assert '超过' in saved['unit_validation_attempts']['0'][0]['error']
    runs=db.rows('SELECT manifest FROM generation_runs WHERE task_id=? ORDER BY rowid',(task['id'],))
    from app import security
    original_blob=db.loads(runs[0]['manifest'])['output_blob']
    assert ('甲'*words) in security.safe_object(original_blob).read_text('utf-8')


def test_quality_rewrite_can_succeed_on_the_fifth_shared_opportunity(writing_project,monkeypatch):
    task,section,context,manifest=_unit_setup(writing_project);counts={}
    def transport(system,user,images=None):
        unit=db.loads(user)['argument_unit']['index'];counts[unit]=counts.get(unit,0)+1
        text='甲'*1600 if unit==1 and counts[unit]<5 else '新的单元'+str(unit)
        return response({'paragraphs':[{'text':text}]})
    monkeypatch.setattr(provider,'generate',transport)
    run(task,section,context,manifest)
    assert counts=={1:5,2:1,3:1}
    saved=unblob(checkpoint(task)['output_blob'])
    assert [a['valid'] for a in saved['unit_validation_attempts']['0']]==[False]*4+[True]
    assert len(db.rows('SELECT id FROM generation_runs WHERE task_id=?',(task['id'],)))==7


def test_five_oversize_responses_stop_and_preserve_valid_prefix(writing_project,monkeypatch):
    task,section,context,manifest=_unit_setup(writing_project);counts={}
    def transport(system,user,images=None):
        unit=db.loads(user)['argument_unit']['index'];counts[unit]=counts.get(unit,0)+1
        return response({'paragraphs':[{'text':'已完成首单元。' if unit==1 else '甲'*1600}]})
    monkeypatch.setattr(provider,'generate',transport)
    with pytest.raises(ValueError,match='超过'):
        run(task,section,context,manifest)
    assert counts=={1:1,2:5}
    step=checkpoint(task);saved=unblob(step['output_blob'])
    assert step['state']=='needs_review' and len(saved['units'])==1
    assert saved['units'][0]['candidate']['paragraphs'][0]['text']=='已完成首单元。'
    assert len(saved['unit_validation_attempts']['1'])==5


def test_substantive_long_paragraph_copy_is_rewritten_but_short_terms_are_allowed(writing_project,monkeypatch):
    task,section,context,manifest=_unit_setup(writing_project);counts={}
    long='接口边界根据授权条件划分并将来源版本与签发主体绑定，撤销状态必须在验证时读取，静态代码只能说明实现结构，不能替代真实运行记录。'*2
    def transport(system,user,images=None):
        unit=db.loads(user)['argument_unit']['index'];counts[unit]=counts.get(unit,0)+1
        if unit==1 or unit==2 and counts[unit]==1:
            text=long if unit==1 else long.replace('，',' ').replace('。','！')
            return response({'paragraphs':[{'text':text},{'text':'术语保持一致。'}]})
        return response({'paragraphs':[{'text':'术语保持一致。'},{'text':'当前单元展开不同问题'+str(unit)}]})
    monkeypatch.setattr(provider,'generate',transport)
    run(task,section,context,manifest)
    assert counts=={1:1,2:2,3:1}
    saved=unblob(checkpoint(task)['output_blob'])
    assert '重复已有长段' in saved['unit_validation_attempts']['1'][0]['error']


def test_invalid_saved_unit_is_archived_and_replaced_without_replaying_valid_prefix(writing_project,monkeypatch):
    task,section,context,manifest=_unit_setup(writing_project);calls=[]
    def transport(system,user,images=None):
        unit=db.loads(user)['argument_unit']['index'];calls.append(unit)
        return response({'paragraphs':[{'text':'有效单元'+str(unit)}]})
    monkeypatch.setattr(provider,'generate',transport)
    run(task,section,context,manifest)
    step=checkpoint(task);saved=unblob(step['output_blob']);old=deepcopy(saved['units'][1:])
    saved['units'][1]['candidate']['paragraphs'][0]['text']='甲'*3460
    invalid=deepcopy(saved['units'][1:])
    db.execute('UPDATE writing_steps SET output_blob=? WHERE id=?',(blob(saved),step['id']))
    calls.clear()
    manifest.pop('argument_units',None)  # the next real preparation builds a fresh manifest
    run(task,section,context,manifest)
    # Completed valid responses may be reused from the immutable call ledger;
    # neither the valid prefix nor the bad stored prose is sent/accepted again.
    assert calls==[]
    repaired=unblob(checkpoint(task)['output_blob'])
    assert len(repaired['units'])==3
    assert repaired['units'][1]['candidate']==old[0]['candidate']
    assert repaired['rejected_units'][0]['units']==invalid
    assert repaired['rejected_units'][0]['retained_prefix_count']==1


def test_literal_citation_markers_share_the_same_five_repair_budget(writing_project,monkeypatch):
    task,section,context,manifest=_unit_setup(writing_project);counts={}
    def transport(system,user,images=None):
        unit=db.loads(user)['argument_unit']['index'];counts[unit]=counts.get(unit,0)+1
        text='必须根据原文讨论[cite:real-looking-id]。' if unit==1 and counts[unit]<5 else '结构化单元'+str(unit)
        return response({'paragraphs':[{'text':text}]})
    monkeypatch.setattr(provider,'generate',transport)
    run(task,section,context,manifest)
    assert counts=={1:5,2:1,3:1}
    saved=unblob(checkpoint(task)['output_blob'])
    assert '字面引用标记' in saved['unit_validation_attempts']['0'][0]['error']


def test_connection_failures_and_unit_quality_share_five_real_http_opportunities(writing_project,monkeypatch):
    task,section,context,manifest=_unit_setup(writing_project);calls=[]
    def handle(request):
        import json
        payload=json.loads(json.loads(request.content)['messages'][-1]['content'])
        unit=payload['argument_unit']['index'];calls.append(unit)
        if unit==1 and len(calls) in (1,3):
            raise httpx.ConnectError('synthetic preconnect failure',request=request)
        text='甲'*1600 if unit==1 and len(calls)<5 else '本单元新的论证'+str(unit)
        return httpx.Response(200,json={'id':'synthetic-unit','choices':[
            {'message':{'content':db.dumps({'paragraphs':[{'text':text}]})},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':3,'completion_tokens':4}})
    monkeypatch.setattr(provider,'generate',_REAL_GENERATE)
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    run(task,section,context,manifest)
    assert calls==[1]*5+[2,3]
    states=[r['status'] for r in db.rows('SELECT status FROM generation_runs WHERE task_id=? ORDER BY rowid',(task['id'],))]
    assert states==['failed_before_send','completed','failed_before_send','completed','completed','completed','completed']
