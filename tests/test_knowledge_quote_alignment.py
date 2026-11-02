import pytest

from app import knowledge_compiler as compiler
from test_workbench import client,project,file


def candidate(quote,**extra):
    return {'observation_id':'observed','statement':'作者报告了条件与结果','quote':quote,'conditions':[],**extra}


def test_restores_original_crlf_spaces_and_offsets():
    original='本文只在单用\r\n户条件下测试，样本为 12。'
    quote='本文只在单用户条件下测试，样本为12。'
    result=compiler.align_quote(original,quote)
    assert result==original and result in original


@pytest.mark.parametrize('quote',['样本为13。','样本为12…通过验证。','样本为12……通过验证。','样本为12,','验证有效。',None,{},''])
def test_cannot_repair_words_numbers_punctuation_ellipsis_or_paraphrases(quote):
    assert compiler.align_quote('样本为12。未验证并发性能。通过验证。',quote) is None


def test_drops_only_unlocatable_candidates_with_auditable_reasons():
    observation={'id':'observed','text':'方法仅适用于单用\r\n户。未验证并发性能。'}
    source=[candidate('方法仅适用于单用户。'),candidate('方法适用于所有用户。'),candidate('未验证并发性能。',observation_id='another')]
    accepted,report=compiler.validate_candidates(observation,source)
    assert len(accepted)==1 and accepted[0]['quote']=='方法仅适用于单用\r\n户。'
    assert report['whitespace_aligned_indices']==[0]
    assert report['received']==3 and report['accepted']==1 and len(report['rejected'])==2
    assert not report['source_words_or_punctuation_changed'] and not report['human_approval_added']
    assert source[0]['quote']=='方法仅适用于单用户。'


def test_entity_quotes_follow_same_exact_source_constraint():
    observation={'id':'observed','text':'基于分布式身\n份标识，未证明普适性。'}
    claim=candidate('基于分布式身份标识，未证明普适性。',entities=[
        {'name':'分布式身份标识','quote':'分布式身份标识'},
        {'name':'已证明普适性','quote':'已证明普适性'}])
    accepted,report=compiler.validate_candidates(observation,[claim])
    assert accepted[0]['entities']==[{'name':'分布式身份标识','quote':'分布式身\n份标识'}]


def test_all_invalid_and_empty_outputs_are_distinguishable():
    observation={'id':'observed','text':'原文无可支持的有效结果。'}
    assert compiler.validate_candidates(observation,[])[1]['received']==0
    accepted,report=compiler.validate_candidates(observation,[candidate('不存在的引句')])
    assert not accepted and report['received']==1 and report['rejected']


def test_fullwidth_ascii_locating_preserves_every_original_character():
    original='Ａｂｓｔｒａｃｔ：Ｔｈｅ ｓａｍｐｌｅ ｉｓ １２．\r\n未验证并发场景。'
    quote='Abstract:The sample is 12.'
    result=compiler.align_quote(original,quote)
    assert result=='Ａｂｓｔｒａｃｔ：Ｔｈｅ ｓａｍｐｌｅ ｉｓ １２．' and result in original
    accepted,report=compiler.validate_candidates({'id':'observed','text':original},[candidate(quote)])
    assert accepted[0]['quote']==result
    assert report['fullwidth_ascii_aligned_indices']==[0] and report['original_character_width_preserved']
    assert compiler.align_quote(original,'Abstract:The sample is 13.') is None


@pytest.mark.parametrize('source,quote',[('ﬁ','fi'),('²','2'),('Ⅰ','I'),('μ','u'),('研究—结论','研究-结论'),('未验证','已验证')])
def test_width_support_cannot_expand_to_semantic_or_compatibility_repairs(source,quote):
    assert compiler.align_quote(source,quote) is None


def test_repeated_entity_case_variant_preserves_prior_claim_and_manual_edits_still_invalidate(client):
    from app import db,knowledge_units,knowledge_graph as graph,knowledge_lineage as lineage
    pid=project(client);original=file(client,pid,'immutability and Immutability are two spellings used in this source.')
    knowledge_units.build(pid,original['id']);unit=db.one('select id from knowledge_units limit 1')
    observation=compiler.observe_unit(pid,unit['id'])
    document=graph.entity(pid,{'namespace':'literature','kind':'document','key':'file:'+original['id'],
        'name':original['name'],'sources':[lineage.ref(pid,'observation',observation['id'])]})
    def proposed(name,statement):
        return {'statement':statement,'quote':observation['text'],'conditions':[],
                'entities':[{'name':name,'kind':'concept','quote':name}],'object_entity_name':name}
    first=compiler.adopt_candidates(pid,observation,[proposed('immutability','The source mentions immutability.')],document,'llm')
    entity=graph.get_entity(pid,first['entity_ids'][0]);head=entity['current_version_id']
    second=compiler.adopt_candidates(pid,observation,[proposed('Immutability','The source also mentions Immutability.')],document,'llm')
    assert first['entity_ids']==second['entity_ids']
    assert graph.get_entity(pid,entity['id'])['current_version_id']==head
    for cid in first['assertion_ids']+second['assertion_ids']:assert graph.get_assertion(pid,cid)['current']
    graph.entity(pid,{'namespace':'literature','kind':'concept','key':entity['entity_key'],'name':entity['name'],
                     'description':'An explicit new definition.','method':'human_resolution','sources':entity['sources']})
    assert not graph.get_assertion(pid,first['assertion_ids'][0])['current']
    assert not db.rows('select * from generation_runs')
