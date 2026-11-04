"""Offline byte stability, losslessness and legacy compatibility of cache_v2."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

from app import ai_cache_policy, ai_request_cache, db, prompt_layout, writing_flow
from app.writing_request import compact_model_context, expand_model_context


@pytest.fixture(autouse=True)
def isolated_block_policy(monkeypatch):
    # These tests assemble data only: even the cache policy must not open the
    # user's database merely because no TestClient fixture was needed.
    monkeypatch.setattr(ai_cache_policy, 'settings', lambda: dict(ai_cache_policy.DEFAULTS))
    ai_request_cache.clear_blocks()
    yield
    ai_request_cache.clear_blocks()


def task(layout='cache_v2'):
    return {'id':'synthetic-task','project_id':'synthetic-project',
            'payload':db.dumps({'prompt_layout_version':layout})}


def sample():
    original='本观察仅适用于单用户模拟环境；未测量并发吞吐率，不证明生产部署。'*90
    return {'section':{'id':'chapter-b','title':'讨论'},'instruction':'核对本章条件。',
            'rules':'必须保留限定、否定和实际来源。',
            'terminology':[{'zh':'乙','en':'B'},{'zh':'甲','en':'A'}],
            'sources':[{'id':'s2','file_id':'f2','text':'第二份完整原文，不构成实测结果。'},
                       {'id':'s1','file_id':'f1','text':original}],
            'knowledge_units':[{'unit_id':'u2','file_id':'f2','text':'独立原文。'},
                {'unit_id':'u1','file_id':'f1','text':original,'scope':'question_bound_reading_range',
                 'char_start':300,'char_end':300+len(original),
                 'read_observations':[{'reading_id':'r1','char_start':300,
                     'char_end':300+len(original),'text':original}],
                 'material_use':{'can_prove_project_execution':False}}],
            'project_evidence':[{'id':'e1','payload':{'text':original},
                'conditions':['仅为已读原文','不是本项目运行结果']}],
            'references':[{'id':'b2','file_id':'f2'},{'id':'b1','file_id':'f1'}],
            'document_readings':[{'reading_id':'r1','version':'v1','semantic_support':'unknown',
                'passages':[{'unit_id':'u1','text':original,'char_start':300,'char_end':300+len(original)}]}],
            'candidate_paragraphs':[{'text':'先给条件。'},{'text':'再给局限。'}],
            'claim_spans':[{'local_id':'c2'},{'local_id':'c1'}]}


def permute(value, rng, key=None):
    if isinstance(value, dict):
        items=list(value.items());rng.shuffle(items)
        return {name:permute(item,rng,name) for name,item in items}
    if isinstance(value, list):
        result=[permute(item,rng) for item in value]
        if key in prompt_layout._COLLECTIONS:rng.shuffle(result)
        return result
    return value


def test_one_hundred_dictionary_and_material_order_variants_have_identical_wire():
    baseline=sample()
    expected_system,expected,meta=prompt_layout.prepare(task(),'fixed system',baseline,'draft')
    expected_bytes=db.dumps(expected).encode()
    for seed in range(100):
        value=permute(baseline,random.Random(seed));before=deepcopy(value)
        system,wire,actual=prompt_layout.prepare(task(),'fixed system',value,'draft')
        assert system==expected_system and db.dumps(wire).encode()==expected_bytes
        assert actual['stable_block_hash']==meta['stable_block_hash']
        assert value==before
    assert actual['stable_block_cache_hit'] is True


def test_one_hundred_unit_and_repair_parameters_follow_the_same_material_prefix():
    systems=[];prefixes=[];stable_hashes=[]
    for variant in range(100):
        context=sample();context['section']['id']='chapter-'+str(variant)
        context['instruction']='不同任务 '+str(variant)
        system,request=writing_flow._unit_prompt(task(),'fixed system',context,
            variant%3,3,900+variant,variant%5,'不同格式错误 '+str(variant))
        system,wire,meta=prompt_layout.prepare(task(),system,request,'argument_unit',variant)
        stable={key:wire[key] for key in prompt_layout.STABLE_FIELDS if key in wire}
        prefix=db.dumps(stable)[:-1]
        assert db.dumps(wire).startswith(prefix+',')
        assert list(wire).index('section')>list(wire).index('project_evidence')
        assert list(wire)[-1] in (prompt_layout.PARAMETERS,'_writing_wire_compaction')
        systems.append(system);prefixes.append(prefix);stable_hashes.append(meta['stable_block_hash'])
    assert len(set(systems))==len(set(prefixes))==len(set(stable_hashes))==1


def test_cross_process_hash_seed_does_not_change_model_bytes():
    root=Path(__file__).resolve().parents[1]
    script='''import hashlib
from app import ai_cache_policy,db,prompt_layout
ai_cache_policy.settings=lambda:dict(ai_cache_policy.DEFAULTS)
context={'sources':[{'id':identity,'text':'条件与否定'+identity} for identity in {'z','a','m'}],
         'section':{'title':'完整题名','id':'section'},'instruction':'只核对给定原文'}
system,wire,_=prompt_layout.prepare({'project_id':'synthetic','payload':{'prompt_layout_version':'cache_v2'}},'system',context,'draft')
print(hashlib.sha256((system+'\\n'+db.dumps(wire)).encode()).hexdigest())
'''
    hashes=[]
    for seed in ('1','19','87'):
        env={**os.environ,'PYTHONHASHSEED':seed,'PYTHONIOENCODING':'utf-8',
             'PYTHONPATH':os.pathsep.join(str(root/path) for path in
                ('backend','.runtime/python-deps','.runtime/advanced-deps'))}
        hashes.append(subprocess.check_output([sys.executable,'-X','utf8','-c',script],
                                             cwd=root,env=env,text=True).strip())
    assert len(set(hashes))==1


def test_exact_compaction_references_survive_canonical_sorting_and_input_is_untouched():
    context=sample();before=deepcopy(context)
    _,wire,_=prompt_layout.prepare(task(),'system',context,'draft')
    restored=expand_model_context(wire)
    assert context==before
    assert restored==prompt_layout._ordered(context)
    assert restored['knowledge_units'][0]['text']==context['knowledge_units'][1]['text']
    assert restored['document_readings'][0]['semantic_support']=='unknown'
    assert restored['candidate_paragraphs']==context['candidate_paragraphs']
    assert restored['claim_spans']==context['claim_spans']
    assert restored['knowledge_units'][0]['material_use']['can_prove_project_execution'] is False


def test_already_encoded_context_is_expanded_before_reordering_indices():
    context=sample();encoded=compact_model_context(context)
    _,wire,_=prompt_layout.prepare(task(),'system',encoded,'draft')
    assert expand_model_context(wire)==prompt_layout._ordered(context)


@pytest.mark.parametrize('layout',[None,'legacy','unsupported'])
def test_legacy_keeps_system_and_compacted_wire_byte_exact(layout):
    context=sample();expected=compact_model_context(context)
    value={'payload':{}} if layout is None else task(layout)
    system,wire,meta=prompt_layout.prepare(value,'legacy dynamic 单元2/3',context,'argument_unit',1)
    assert system=='legacy dynamic 单元2/3'
    assert db.dumps(wire).encode()==db.dumps(expected).encode()
    assert meta=={'prompt_layout_version':'legacy'}


def test_legacy_unit_and_semantic_repair_text_stay_exact():
    value=task('legacy');context=sample()
    system,request=writing_flow._unit_prompt(value,'system',context,1,3,1000,2,'原始错误')
    expected=('system 本次唯一输出目标为argument_unit第2/3单元，目标900至1100字，'
        '正文不得超过1350字。只返回该单元JSON，不返回完整章节；'
        'text正文不得出现[cite:...]或[figref:...]等内部标记；引用使用实际给定ID的claim_spans和citation_intents，图表使用visuals。'
        '章节和全篇计划仅为背景，全部规则与证据限制仍适用。'
        '已有正文仅供衔接，禁止复制已有长段；正文必须推进分给当前单元的问题、依据和分析。'
        ' 第3/5次单元修复。上次响应未通过校验：原始错误。请重新写当前单元，约1000字，'
        '去除对已有长段的复述；只返回本单元合法JSON，保留事实、来源及待补边界。')
    assert system.encode()==expected.encode() and request is context
    system,request=writing_flow._semantic_repair_prompt(value,'system',context,1,'错误')
    assert system==('system 第2/5次核验格式尝试。上一次完整响应未通过核验结构检查：错误。'
        '请仅按相同claims和actual_sources重新核验，完整覆盖expected_claim_ids；'
        '只返回claim_reviews、point_reviews、findings、summary组成的JSON，'
        '不要输出或重写论文正文，也不要把缺乏证据的判断改成supported。')
    assert request is context


def test_v2_semantic_repairs_keep_system_stable_and_diagnostics_at_the_end():
    context={**sample(),'claims':[{'claim_id':'c1','text':'限定观察。'}],
             'expected_claim_ids':['c1']}
    baseline_system,baseline,baseline_meta=prompt_layout.prepare(task(),'verify',context,'semantic_review')
    for attempt in range(1,5):
        system,request=writing_flow._semantic_repair_prompt(task(),'verify',context,attempt,'新错误 '+str(attempt))
        system,wire,meta=prompt_layout.prepare(task(),system,request,'semantic_format_repair',attempt)
        assert system==baseline_system and meta['stable_block_hash']==baseline_meta['stable_block_hash']
        assert wire['claims']==baseline['claims']
        assert wire['expected_claim_ids']==baseline['expected_claim_ids']
        assert wire[prompt_layout.PARAMETERS]['format_attempt']==attempt+1


def test_transport_native_messages_items_tools_are_opaque_and_keep_order():
    frame={'messages':[{'role':'assistant','tool_calls':[{'id':'second'},{'id':'first'}]},
                       {'role':'tool','tool_call_id':'second','content':'原文'}],
           'tools':[{'type':'function','function':{'name':'z'}},{'type':'function','function':{'name':'a'}}],
           'items':[{'type':'message','id':'z'},{'type':'message','id':'a'}]}
    _,wire,_=prompt_layout.prepare(task(),'system',frame,'agent')
    for key in frame:assert db.dumps(wire[key])==db.dumps(frame[key])


def test_stable_metadata_contains_sizes_and_hashes_without_private_text_or_ids():
    context=sample();_,wire,meta=prompt_layout.prepare(task(),'system',context,'draft')
    stable={key:wire[key] for key in prompt_layout.STABLE_FIELDS if key in wire}
    raw=db.dumps(stable).encode()
    assert meta['stable_block_hash']==hashlib.sha256(raw).hexdigest()
    assert meta['stable_block_bytes']==len(raw)
    assert 'synthetic-task' not in db.dumps(meta)
    assert context['knowledge_units'][1]['text'] not in db.dumps(meta)


def test_only_known_fact_row_audit_dates_are_local_and_semantic_fields_are_preserved():
    context={'facts':[{'id':'fact','project_id':'project','revision':7,
        'created_at':'2026-01-01','updated_at':'2026-01-02',
        'statement':'实验观察日期为2026-01-01，未证实长期效果。',
        'verification':'unverified','status':'observed',
        'metadata':{'updated_at':'研究数据集更新日期，属于研究信息'}}],
        'sources':[{'id':'source','updated_at':'原文所述研究记录，不是事实表审计列'}]}
    before=deepcopy(context)
    _,first,meta=prompt_layout.prepare(task(),'system',context,'draft')
    context['facts'][0]['updated_at']='2026-09-01'
    _,second,other=prompt_layout.prepare(task(),'system',context,'draft')
    expected={key:value for key,value in before['facts'][0].items() if key not in ('created_at','updated_at')}
    assert first['facts']==second['facts']==[expected]
    assert meta['stable_block_hash']==other['stable_block_hash']
    assert first['sources']==before['sources']
    assert context['facts'][0]['created_at']==before['facts'][0]['created_at']
    assert before['facts'][0]['updated_at']=='2026-01-02'
