"""Research originals outrank automatically discovered summaries and examples."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from app import db
from app.writing_request import pack
from test_writing_request import readings, request


def budget_for_originals(knowledge):
    context=request(deepcopy(knowledge))
    report=pack(context,deepcopy(knowledge),'写作',{'context_limit':100000,'max_output_tokens':1000})
    return report['input_upper_bound']+1000+2000


def test_automatic_summaries_and_examples_are_deferred_before_research_originals():
    knowledge=readings('文献研究方法、适用条件及局限均需完整保留。'*50)
    original=deepcopy(knowledge);context=request(knowledge)
    context['required_research_product_refs']=[]
    context['research_products']=[{'kind':'summary','text':'自动推荐摘要。'*800,
        'source_ref':{'type':'summary','id':'auto-summary','version':'v1'}}]
    context['writing_examples']=[{'file_id':'auto-example','passages':[{'text':'教学示例。'*800}]}]
    context['evidence_bundle']={'constraints':[],'primary_evidence':[],
        'derived_context':[{'source_ref':{'type':'summary','id':'auto-summary'},'title':'自动摘要'}],
        'guidance':[{'file_id':'auto-example','source_ref':{'type':'file','id':'auto-example'}}],
        'complete':True,'coverage':{'resolved_items':2},'gaps':[]}
    report=pack(context,knowledge,'写作',{'context_limit':budget_for_originals(original),'max_output_tokens':1000})
    assert context['knowledge_units'][0]['text']==original['knowledge_units'][0]['text']
    assert context['references']==[{'id':'book','file_id':'literature'}]
    assert not context['research_products'] and not context['writing_examples']
    assert not context['evidence_bundle']['derived_context'] and not context['evidence_bundle']['guidance']
    assert context['rules']=='[mandatory] 必须保留限定与否定。'
    assert {item['kind'] for item in report['deferred_documents']}=={'automatic_product_deferred','automatic_guidance_deferred'}


@pytest.mark.parametrize('explicit_marker',[True,False])
def test_author_bound_or_legacy_products_remain_mandatory(explicit_marker):
    knowledge=readings('选定原文。',selected=True);context=request(knowledge)
    product={'kind':'note','text':'作者明确绑定的完整输入。'*3000,
             'source_ref':{'type':'note','id':'bound-note','version':'v1'}}
    context['research_products']=[deepcopy(product)]
    if explicit_marker:context['required_research_product_refs']=[product['source_ref']]
    with pytest.raises(HTTPException) as error:
        pack(context,knowledge,'写作',{'context_limit':8000,'max_output_tokens':1000})
    assert error.value.detail['code']=='WRITING_CONTEXT_BUDGET_EXCEEDED'
    assert context['research_products']==[product]
    assert context['knowledge_units'][0]['text']=='选定原文。'


def test_explicit_guidance_reading_is_not_removed_to_fit_research():
    knowledge=readings('研究原文。',selected=True);context=request(knowledge)
    example={'reading_id':'explicit-reading','file_id':'chosen-guide',
             'passages':[{'text':'完整的明确指定指导阅读。'*2000}]}
    context['required_research_product_refs']=[]
    context['writing_examples']=[deepcopy(example)]
    with pytest.raises(HTTPException):
        pack(context,knowledge,'写作',{'context_limit':8000,'max_output_tokens':1000})
    assert context['writing_examples']==[example]


def test_literature_chapter_defers_automatic_code_before_actual_literature():
    knowledge=readings('本章应先核对文献原文而非项目静态实现。'*50)
    original=deepcopy(knowledge);context=request(knowledge)
    context['section']={'kind':'literature'}
    context['project_evidence']=[{'id':'auto-code','origin_type':'code','payload':{'text':'额外代码观察。'*3000}}]
    context['automatic_project_evidence_ids']=['auto-code']
    report=pack(context,knowledge,'写作',{'context_limit':budget_for_originals(original),'max_output_tokens':1000})
    assert context['knowledge_units'][0]['text']==original['knowledge_units'][0]['text']
    assert not context['project_evidence']
    assert all(item['kind']!='automatic_document_deferred' for item in report['deferred_documents'])


def test_reading_receipt_reuses_identical_text_but_retains_actual_range():
    knowledge=readings('方法、条件与局限的实际原文。'*30)
    unit=knowledge['knowledge_units'][0];context=request(knowledge)
    passage={'unit_id':unit['unit_id'],'text':unit['text'],'char_start':0,
             'char_end':len(unit['text']),'locator':{'page':2}}
    knowledge['document_readings']=[{'reading_id':'reading','file_id':unit['file_id'],
                                     'version':'frozen-version','passages':[deepcopy(passage)]}]
    context['document_readings']=deepcopy(knowledge['document_readings'])
    pack(context,knowledge,'写作',{'context_limit':30000,'max_output_tokens':1000})
    actual=context['document_readings'][0]['passages'][0]
    assert 'text' not in actual and actual['text_in_knowledge_units']==[unit['unit_id']]
    assert actual['char_start']==0 and actual['char_end']==len(unit['text'])
    assert context['knowledge_units'][0]['text']==unit['text']
    assert knowledge['document_readings'][0]['passages'][0]['text']==unit['text']
