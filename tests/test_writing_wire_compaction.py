"""Exact wire aliases reduce copies without changing local evidence contracts."""
from copy import deepcopy
import json

import pytest

from test_workbench import client,project
from app import db,provider,tasks,writing_flow
from app.writing_agent.common import digest
from app.writing_request import (compact_model_context,expand_model_context,
                                pack,WIRE_FIELD)


def context_fixture():
    first='只有在授权条件满足时才可使用该方法，不能据此证明本项目已运行。'*100
    second='未测量的区间不构成实验结果，必须保留否定及适用条件。'*80
    joined=first+'\n\n[独立读取范围；未读取的间隔不作为证据]\n\n'+second
    return {'rules':'必须保留所有限定与否定。'*80,
        'paper_draft_sections':[{'section_id':'prior','text':joined}],
        'candidate_paragraphs':[{'text':first}],
        'claims':[{'claim_id':'claim','text':first}],
        'knowledge_units':[{'unit_id':'u','file_id':'f','text':joined,
            'scope':'disjoint_read_observations',
            'read_observations':[{'unit_id':'u','char_start':100,'char_end':100+len(first),'text':first},
                                 {'unit_id':'u','char_start':9000,'char_end':9000+len(second),'text':second}]}],
        'document_readings':[{'reading_id':'reading','version':'v','intent':'research',
            'semantic_support':'unknown','passages':[{'unit_id':'u','text':first,'char_start':100,'char_end':100+len(first)}]}],
        'knowledge_context':{'wiki':[{'id':'derived','text':'这是唯一的综合内容，不能凭文本别名认定为已验证来源。'*35,
            'semantic_support':'not_checked'}],
            'graph':{'assertions':[{'id':'assertion','proposition':{'statement':first},
                'attribution':'outside_research','support_state':'unknown',
                'evidence':[{'scope_json':db.dumps({'observation_id':'observation','quote':first})}]}]}}}


def test_wire_view_is_fully_reversible_and_canonical_input_types_stay_intact():
    original=context_fixture();before=deepcopy(original)
    wire=compact_model_context(original)
    assert original==before
    assert expand_model_context(wire)==before
    assert len(db.dumps(wire).encode())<len(db.dumps(original).encode())*.65
    for key in ('rules','paper_draft_sections','candidate_paragraphs','claims'):
        assert wire[key]==original[key]
    assert isinstance(original['knowledge_context']['graph']['assertions'][0]['proposition']['statement'],str)
    assert isinstance(original['knowledge_units'][0]['read_observations'][0]['text'],str)
    assert original['knowledge_units'][0]['text']==wire['knowledge_units'][0]['text']
    assert wire[WIRE_FIELD]['text_references'] and wire[WIRE_FIELD]['json_strings']


def test_disjoint_ranges_are_not_joined_or_replaced_by_an_unread_interval():
    context=context_fixture();observations=context['knowledge_units'][0]['read_observations']
    invented=observations[0]['text']+observations[1]['text']
    context['knowledge_context']['not_a_provided_interval']=invented
    wire=compact_model_context(context)
    # Neither contiguous source string contains this text across the read gap.
    assert wire['knowledge_context']['not_a_provided_interval']==invented
    assert expand_model_context(wire)['knowledge_units'][0]['read_observations']==observations
    restored=expand_model_context(wire)
    assert restored['document_readings'][0]['semantic_support']=='unknown'
    assert restored['knowledge_context']['graph']['assertions'][0]['support_state']=='unknown'
    assert restored['knowledge_context']['graph']['assertions'][0]['attribution']=='outside_research'


def test_unique_navigation_and_noncanonical_json_string_remain_complete():
    context=context_fixture();scope=' { "quote": '+json.dumps('唯一的条件与否定。'*100,ensure_ascii=False)+' } '
    context['knowledge_context']['unique_scope_json']=scope
    original=deepcopy(context)
    wire=compact_model_context(context)
    assert wire['knowledge_context']['unique_scope_json']==scope
    assert wire['knowledge_context']['wiki']==context['knowledge_context']['wiki']
    assert expand_model_context(wire)==original


def test_encoded_view_is_idempotent_and_does_not_overwrite_caller_reserved_data():
    context=context_fixture();wire=compact_model_context(context)
    assert compact_model_context(wire)==wire
    context[WIRE_FIELD]={'caller':'do not overwrite'}
    assert compact_model_context(context)==context


def test_modified_wire_reference_cannot_restore_different_text():
    wire=compact_model_context(context_fixture())
    target=wire['document_readings'][0]['passages'][0]['text']['_writing_text_ref']
    target['value_end_cp']-=1
    with pytest.raises(ValueError,match='hash does not match'):
        expand_model_context(wire)


def test_reference_contract_never_increases_a_small_request():
    text='仅保留同一范围原文，不新增支持结论。'*6
    context={'knowledge_units':[{'unit_id':'u','file_id':'f','text':text}],
             'knowledge_context':{'copy':text}}
    wire=compact_model_context(context)
    assert len(db.dumps(wire).encode())<=len(db.dumps(context).encode())
    assert expand_model_context(wire)==context


def test_pack_uses_wire_size_without_dropping_mandatory_originals_or_navigation():
    text='完整方法、前提与否定。'*300
    unit={'unit_id':'unit','file_id':'file','text':text,'scope':'full_parsed_document_text','locator':{}}
    knowledge={'knowledge_units':[deepcopy(unit)],'sources':[],'selected_unit_ids':['unit'],
        'document_readings':[],'gaps':[],'knowledge_refs':[{'type':'assertion','id':'a','version':'v'}]}
    context={'rules':'规则正文。','references':[{'id':'book','file_id':'file'}],
        'sources':[],'knowledge_units':[deepcopy(unit)],'evidence_gaps':[],
        'paper_context':{'complete':True},'paper_draft_sections':[{'text':'全部已保存正文。'*200}],
        'knowledge_context':{'graph':{'assertions':[{'proposition':{'statement':text},
            'evidence':[{'scope_json':db.dumps({'quote':text,'observation_id':'o'})}]}]},
            'scope_note':'派生导航不自动证明来源支持。'},'writing_examples':[]}
    before=deepcopy(context);original_knowledge=deepcopy(knowledge)
    wire=compact_model_context(context)
    limit=len(db.dumps(wire).encode())+2200
    assert limit<len(db.dumps(context).encode())
    report=pack(context,knowledge,'写作',{'context_limit':limit,'max_output_tokens':1000})
    assert context['knowledge_context']==before['knowledge_context']
    assert context['knowledge_units'][0]['text']==text
    assert context['paper_draft_sections']==before['paper_draft_sections']
    assert knowledge==original_knowledge and not report['deferred_documents']
    assert report['input_upper_bound']+1000<=limit
    assert report['wire_compaction']['saved_bytes']>0


def test_model_serialization_uses_wire_view_but_hash_and_guard_keep_canonical_input(client,monkeypatch):
    pid=project(client)
    # This isolates lossless serialization and its canonical hash from the
    # independently tested default skill prompt and its additional input cost.
    db.set_setting('skills:'+pid,{'enabled':False})
    task=tasks.enqueue(pid,'write',{})
    db.execute("UPDATE tasks SET status='running',attempt=1 WHERE id=?",(task['id'],))
    task=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    context=context_fixture();before=deepcopy(context);manifest={'request_options':{'max_output_tokens':1000}}
    cfg={**provider.DEFAULT,'context_limit':len(db.dumps(compact_model_context(context)).encode())+3000,
         'max_output_tokens':1000};db.set_setting('provider',cfg)
    captured=[]
    def call(task,system,user,meta):
        wire=json.loads(user);assert WIRE_FIELD in wire
        assert expand_model_context(wire)==before
        captured.append(meta)
        return {'text':'{"ok":true}'}
    monkeypatch.setattr(tasks,'model_call',call)
    writing_flow._cached_call(task,'系统约束',context,manifest,stage='draft')
    expected=digest([writing_flow.VERSION,'系统约束',writing_flow.stable_input(context),None,
                     provider.config(),'draft',0,manifest['request_options']])
    assert captured[0]['writing_flow_request_hash']==expected
    assert context==before


def test_cached_stage_budgets_and_hashes_the_effective_skill_prompt(client,monkeypatch):
    from fastapi import HTTPException
    from app.skill_runtime import enrich
    pid=project(client);task=tasks.enqueue(pid,'write',{})
    db.execute("UPDATE tasks SET status='running',attempt=1 WHERE id=?",(task['id'],))
    task=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    context=context_fixture();before=deepcopy(context)
    manifest={'request_options':{'max_output_tokens':1000}}
    cfg={**provider.DEFAULT,'context_limit':len(db.dumps(compact_model_context(context)).encode())+3000,
         'max_output_tokens':1000};db.set_setting('provider',cfg)
    captured=[]
    def call(task,system,user,meta):
        captured.append((system,meta));return {'text':'{"ok":true}'}
    monkeypatch.setattr(tasks,'model_call',call)
    with pytest.raises(HTTPException) as error:
        writing_flow._cached_call(task,'系统约束',context,manifest,stage='draft')
    assert error.value.detail['code']=='WRITING_CONTEXT_BUDGET_EXCEEDED'
    assert not captured and context==before
    # A separate small input fits the same unchanged capacity; its cache key
    # must bind the actual skill instructions, not merely the base prompt.
    small={'rules':'必要条件不可删除。'}
    actual_system,_=enrich(task,'系统约束','',{'writing_stage':'draft'})
    writing_flow._cached_call(task,'系统约束',small,manifest,stage='draft')
    expected=digest([writing_flow.VERSION,actual_system,writing_flow.stable_input(small),None,
                     provider.config(),'draft',0,manifest['request_options']])
    assert len(captured)==1 and '技能 academic-expression' in captured[0][0]
    assert captured[0][1]['writing_flow_request_hash']==expected
    assert captured[0][1]['skills_usage']
