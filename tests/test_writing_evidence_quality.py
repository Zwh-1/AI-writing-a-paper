"""Precise citation contracts, faithful result levels and transactional mapping."""
from copy import deepcopy

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import db,documents,security
from app import writing_evidence_quality as quality
from app.schemas import CandidateParagraph
from app.writing_agent.models import DraftParagraph
from test_workbench import client,project,file,section  # noqa: F401


@pytest.fixture
def material(client):
    pid=project(client)
    original=file(client,pid,'研究指出模块分层便于维护，但没有测量性能。')
    source=db.one('SELECT * FROM sources WHERE file_id=?',(original['id'],))
    reference=client.post(f'/api/v1/projects/{pid}/references',json={'file_id':original['id'],'metadata':{'title':'分层研究','year':'2024'}}).json()
    context={'sources':[dict(source)],'references':[reference],'project_evidence':[], 'knowledge_units':[], 'document_readings':[],'facts':[]}
    return {'pid':pid,'fid':original['id'],'sid':source['id'],'ref':reference['id'],'context':context,'section':section(client,pid)}


def precise(material,text='😀研究指出模块分层便于维护。本文另讨论适用范围。'):
    claim='研究指出模块分层便于维护。'
    return {'text':text,'claim_spans':[{'local_id':'c1','text':claim,'source_ids':[material['sid']]}],
            'citation_intents':[{'claim_id':'c1','reference_id':material['ref'],'quote':'模块分层便于维护'}]}


def test_old_contracts_remain_compatible():
    assert CandidateParagraph.model_validate({'text':'旧正文。'}).claim_spans==[]
    assert DraftParagraph.model_validate({'local_id':'p1','text':'旧正文。'}).citation_intents==[]
    with pytest.raises(ValidationError):
        CandidateParagraph.model_validate({'text':'x','claim_spans':[{'local_id':'c','text':'x','start_cp':-1}]})


def test_native_citation_is_inserted_at_exact_claim_end_with_unicode(material):
    raw=precise(material)
    result=quality.compile_paragraph(material['pid'],raw,material['context'])
    assert result['handled'] and result['coverage_complete'] and not result['findings']
    content=result['node']['content']
    assert [node['type'] for node in content]==['text','citation','text']
    assert content[0]['text']=='😀研究指出模块分层便于维护。'
    assert content[-1]['text']=='本文另讨论适用范围。'
    assert result['claim_spans'][0]['start_cp']==1
    assert content[1]['attrs']['claimMapping']['support']=='not_checked'
    assert result['citation_proposals'][0]['source_ids']==[material['sid']]


@pytest.mark.parametrize('change',[{'start_cp':0,'end_cp':4},{'text_hash':'0'*64},{'text':'不在正文中的原句。'}])
def test_invalid_range_or_hash_cannot_create_citation(material,change):
    raw=precise(material);raw['claim_spans'][0].update(change)
    result=quality.compile_paragraph(material['pid'],raw,material['context'])
    assert not result['coverage_complete'] and not result['citation_proposals']
    assert any(f['kind']=='claim_mapping_invalid' for f in result['findings'])


def test_repeated_claim_requires_explicit_range(material):
    claim='研究指出模块分层便于维护。'
    raw=precise(material,claim+claim)
    assert not quality.compile_paragraph(material['pid'],raw,material['context'])['citation_proposals']
    raw['claim_spans'][0].update(start_cp=len(claim),end_cp=2*len(claim))
    assert quality.compile_paragraph(material['pid'],raw,material['context'])['citation_proposals']


def test_unknown_unread_or_revoked_source_is_rejected(material,client):
    raw=precise(material);raw['claim_spans'][0]['source_ids']=['invented']
    assert not quality.compile_paragraph(material['pid'],raw,material['context'])['citation_proposals']
    raw=precise(material)
    unread=deepcopy(material['context']);unread['sources'][0].pop('text')
    assert not quality.compile_paragraph(material['pid'],raw,unread)['citation_proposals']
    assert client.patch('/api/v1/files/'+material['fid'],json={'egress':'local_only'}).status_code==200
    assert not quality.compile_paragraph(material['pid'],raw,material['context'])['citation_proposals']


def test_quote_and_source_version_must_match_actual_read(material):
    raw=precise(material);raw['citation_intents'][0]['quote']='性能提高99%'
    assert not quality.compile_paragraph(material['pid'],raw,material['context'])['citation_proposals']
    raw=precise(material);raw['citation_intents'][0]['source_version']='different-version'
    assert not quality.compile_paragraph(material['pid'],raw,material['context'])['citation_proposals']


def test_paragraph_sources_do_not_automatically_support_multiple_claims(material):
    raw=precise(material);raw['source_ids']=[material['sid']]
    raw['claim_spans'][0].pop('source_ids')
    raw['claim_spans'].append({'local_id':'c2','text':'本文另讨论适用范围。'})
    result=quality.compile_paragraph(material['pid'],raw,material['context'])
    assert not result['citation_proposals']
    raw['citation_intents'][0]['source_ids']=[material['sid']]
    result=quality.compile_paragraph(material['pid'],raw,material['context'])
    assert result['citation_proposals'] and not result['claim_spans'][1]['source_records']


@pytest.mark.parametrize('item,level,can_measure',[
    ({'kind':'data_result','payload':{'status':'planned','verification':'verified'}},'planned',False),
    ({'kind':'test_definition','payload':{}},'static',False),
    ({'kind':'data_result','payload':{'status':'measured','verification':'verified'}},'unverified',False),
    ({'kind':'data_result','payload':{'artifact_sha256':'raw-hash','metadata':{'actual_execution':True,'run_id':'run1'}}},'executed',True),
    ({'kind':'data_result','payload':{'status':'measured','artifact_sha256':'raw-hash','metadata':{'verification_origin':'imported'}}},'imported_measurement',True),
    ({'kind':'author_statement','payload':{'status':'measured'}},'declared',False),
])
def test_result_kind_does_not_attest_execution(item,level,can_measure):
    result=quality.assess_result_evidence(item)
    assert result['level']==level and result['can_assert_measurement'] is can_measure


def test_legacy_claims_are_inventory_not_semantic_approval(material):
    result=quality.check_candidate(material['pid'],[{'text':'测试结果表明响应降低30%。'}],material['context'])
    assert result['claim_checks'][0]['support']=='unsupported'
    assert result['findings'][0]['repair_query']
    linked={'text':'研究指出模块分层便于维护。','source_ids':[material['sid']]}
    result=quality.check_candidate(material['pid'],[linked],material['context'])
    assert result['claim_checks'][0]['support']=='unchecked'
    assert result['semantic_support']=='not_checked'


def test_planned_fact_does_not_become_measured_even_after_negative_sentence(material):
    context=deepcopy(material['context'])
    context['facts']=[{'id':'planned','status':'planned','statement':'计划测量响应','verification':'unknown'}]
    result=quality.check_candidate(material['pid'],[{'text':'尚未完成部署。实测响应为10毫秒。','fact_ids':['planned']}],context)
    assert any(f['kind']=='result_evidence_level' for f in result['findings'])


def test_mapping_survives_normalize_save_and_creates_unverified_claim_links(material):
    result=quality.compile_paragraph(material['pid'],precise(material),material['context'])
    doc={'type':'doc','content':[result['node']]}
    saved=documents.save(security.require_section(material['section']),doc,None,kind='accepted',citation_proposals=result['citation_proposals'])
    restored=db.loads(saved['document'])
    assert restored['content'][0]['attrs']['claimEvidence'][0]['local_id']=='c1'
    cite=next(n for n in documents.nodes(restored) if n['type']=='citation')
    assert cite['attrs']['claimMapping']['text']=='研究指出模块分层便于维护。'
    archive=db.setting('writing_claim_mapping:'+saved['id'],{})
    assert archive['claims'][0]['state']=='mapped_not_verified'
    claim=db.one('SELECT * FROM claim_versions WHERE id=?',(archive['claims'][0]['claim_version_id'],))
    assert db.loads(claim['proposition_json'])['support']=='unknown'
    assert db.one('SELECT * FROM claim_evidence_links WHERE claim_version_id=?',(claim['id'],))
    assert db.one('SELECT * FROM sentence_claim_links WHERE claim_version_id=?',(claim['id'],))
    assert db.one('SELECT * FROM citations WHERE revision_id=?',(saved['id'],))['support']=='unknown'


def test_source_changed_between_compile_and_adoption_rolls_back(material):
    result=quality.compile_paragraph(material['pid'],precise(material),material['context'])
    db.execute('UPDATE sources SET text=? WHERE id=?',('更新后的原文。',material['sid']))
    with pytest.raises(HTTPException) as exc:
        documents.save(security.require_section(material['section']),{'type':'doc','content':[result['node']]},None,kind='accepted',citation_proposals=result['citation_proposals'])
    assert exc.value.status_code==409
    assert not db.rows('SELECT * FROM revisions')


def test_manual_text_edit_marks_old_mapping_stale_instead_of_inheriting_support(material):
    result=quality.compile_paragraph(material['pid'],precise(material),material['context'])
    doc={'type':'doc','content':[result['node']]}
    doc['content'][0]['content'][0]['text']='😀研究声称性能显著提高。'
    saved=documents.save(security.require_section(material['section']),doc,None)
    archive=db.setting('writing_claim_mapping:'+saved['id'],{})
    assert archive['claims'][0]['state']=='stale_text_mapping'
    assert not db.rows('SELECT * FROM claim_versions')


def test_unit_only_precise_citation_freezes_actual_read_range_and_claim_evidence(material):
    from app import knowledge_units as units
    parsed=units.build(material['pid'],material['fid'])
    uid=parsed['units'][0]['id']
    read=units.read(material['pid'],uid,before=0,after=0)
    context=deepcopy(material['context']);context['knowledge_units']=[read]
    raw=precise(material);raw['claim_spans'][0]={'local_id':'c1','text':'研究指出模块分层便于维护。','knowledge_unit_ids':[uid]}
    result=quality.compile_paragraph(material['pid'],raw,context,{'knowledge_unit_versions':{uid:read['unit_hash']}})
    assert result['citation_proposals'] and not result['findings']
    saved=documents.save(security.require_section(material['section']),{'type':'doc','content':[result['node']]},None,
                         kind='accepted',citation_proposals=result['citation_proposals'])
    link=db.one('SELECT * FROM citation_knowledge_links')
    assert db.loads(link['scope'])['claim_mapping']['local_id']=='c1'
    archive=db.setting('writing_claim_mapping:'+saved['id'],{})
    evidence_link=db.one('SELECT e.* FROM claim_evidence_links l JOIN evidence_items e ON e.id=l.evidence_id WHERE l.claim_version_id=?',
                         (archive['claims'][0]['claim_version_id'],))
    assert evidence_link['origin_type']=='knowledge_unit'
    assert db.loads(evidence_link['locator_json'])['before']==0


def test_reading_only_precise_citation_uses_frozen_reading_version(material):
    from app import knowledge_units as units,document_reading as reader
    units.build(material['pid'],material['fid'])
    reading=reader.create(material['pid'],{'file_id':material['fid'],'section_id':material['section'],'question':'如何描述分层维护？'})
    reader.read_range(material['pid'],reading['id'],{'ordinal_start':1,'ordinal_end':1})
    current=reader.context(material['pid'],reading['id'])
    context=deepcopy(material['context']);context['document_readings']=[current]
    raw=precise(material);raw['claim_spans'][0]={'local_id':'c1','text':'研究指出模块分层便于维护。','document_reading_ids':[reading['id']]}
    result=quality.compile_paragraph(material['pid'],raw,context,{'document_reading_versions':{reading['id']:current['version']}})
    assert result['citation_proposals'] and not result['findings']
    documents.save(security.require_section(material['section']),{'type':'doc','content':[result['node']]},None,
                   kind='accepted',citation_proposals=result['citation_proposals'])
    assert db.one('SELECT * FROM citation_reading_links')['reading_version']==current['version']


def test_permission_revoked_after_compile_cannot_be_adopted(material,client):
    result=quality.compile_paragraph(material['pid'],precise(material),material['context'])
    client.patch('/api/v1/files/'+material['fid'],json={'egress':'local_only'})
    with pytest.raises(HTTPException):
        documents.save(security.require_section(material['section']),{'type':'doc','content':[result['node']]},None,
                       kind='accepted',citation_proposals=result['citation_proposals'])
    assert not db.rows('SELECT * FROM revisions')


def test_agent_drafting_uses_precise_compiler_when_context_is_provided(material):
    from app.writing_agent import drafting
    paragraph={**precise(material),'local_id':'p1'}
    doc,result=drafting.document({'paragraphs':[paragraph]},references=material['context']['references'],
                                evidence_ids=[],pid=material['pid'],context=material['context'],manifest={})
    assert [n['type'] for n in doc['content'][0]['content']]==['text','citation','text']
    assert result['citation_proposals'] and not result['evidence_quality_findings']


def test_precise_agent_draft_never_silently_falls_back_without_actual_context(material):
    from app.writing_agent import drafting
    with pytest.raises(HTTPException) as exc:
        drafting.document({'paragraphs':[{**precise(material),'local_id':'p1'}]},references=material['context']['references'])
    assert exc.value.detail['code']=='PRECISE_CONTEXT_REQUIRED'


def test_precise_reference_alias_is_decoded_but_local_claim_identity_is_unchanged():
    from app.writing_agent.identifiers import decode
    value={'citation_intents':[{'claim_id':'B1','reference_id':'B1','evidence_ids':['E1']}],'claim_spans':[{'local_id':'B1','text':'B1 is literal prose.'}]}
    restored=decode(value,{'B1':'real-reference','E1':'real-evidence'})
    assert restored['citation_intents'][0]['reference_id']=='real-reference'
    assert restored['citation_intents'][0]['claim_id']=='B1'
    assert restored['claim_spans'][0]['local_id']=='B1'


def test_planned_fact_linked_only_inside_precise_claim_cannot_attest_measurement(material,client):
    response=client.post(f"/api/v1/projects/{material['pid']}/facts",json={'label':'计划试验','statement':'计划测量请求延迟','status':'planned'})
    assert response.status_code==200,response.text
    context=deepcopy(material['context']);context['facts']=[response.json()]
    raw={'text':'实测请求延迟为10毫秒。','claim_spans':[{'local_id':'c1','text':'实测请求延迟为10毫秒。','fact_ids':[response.json()['id']]}]}
    result=quality.check_candidate(material['pid'],[raw],context)
    assert any(f['kind']=='result_evidence_level' for f in result['findings'])


@pytest.mark.parametrize('change',[{'source_records':'not-a-list'},{'evidence_ids':[{'id':'wrong'}]},
                                   {'local_id':None},{'source_records':[{'type':'source','id':'unknown'}]}])
def test_manual_malformed_claim_metadata_returns_422_instead_of_server_error(material,change):
    result=quality.compile_paragraph(material['pid'],precise(material),material['context'])
    doc={'type':'doc','content':[result['node']]}
    doc['content'][0]['attrs']['claimEvidence'][0].update(change)
    with pytest.raises(HTTPException) as exc:
        documents.save(security.require_section(material['section']),doc,None)
    assert exc.value.status_code==422
    assert not db.rows('SELECT * FROM revisions')


def test_absent_result_payload_remains_unverified_instead_of_crashing():
    assert quality.assess_result_evidence({'kind':'data_result','payload':None})['level']=='unverified'
