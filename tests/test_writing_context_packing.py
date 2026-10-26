from copy import deepcopy
import pytest
from fastapi import HTTPException
from test_writing_agent import env,fake_model,finish
from app import db,parsers,provider
from app.writing_agent import evidence,contracts,coordinator,judgment,context,profiles,verification
from app.writing_agent.common import state
from app.writing_agent.packing import fit,estimate


def test_source_projection_preserves_text_and_local_ocr_provenance():
    raw={'text':'仅发行方且启用时允许；尚未执行测试。','locator':{'kind':'windows_ocr','page':18,'char_start':0,'recognition':[{'text':'重复原页文字','words':[{'box':{'x':12,'y':34}}]}]},'file_id':'file','method':'ocr'}
    original=deepcopy(raw)
    projected=evidence.for_model({'id':'E1','kind':'material_observation','origin_type':'source','payload':raw,'payload_hash':'a'*64,'conditions':['保留否定条件']})
    assert projected['payload']['text']==raw['text']
    assert projected['payload']['locator']=={'kind':'windows_ocr','page':18,'char_start':0}
    assert projected['original_payload_hash']=='a'*64
    assert projected['conditions']==['保留否定条件'] and raw==original


def sample_payload():
    rules=[{'id':'mandatory','statement':'不得删掉任何否定条件，不得把源码当成测试通过。'}]
    rules.extend({'id':f'advice-{i}','statement':('文献综述应比较研究不足与技术差异。' if i%2 else '封面装订与时间安排。')*20} for i in range(50))
    return {'section':{'id':'chapter','title':'第二章 相关技术与研究现状'},'contract':{'goal':'比较相关技术与研究现状'},'rules':rules,'evidence':[],'references':[],'project_inputs':{'rule_ids':[r['id'] for r in rules]}},{'stage':'judgment','evidence_versions':{},'rule_modalities':{r['id']:'mandatory' if r['id']=='mandatory' else 'recommended' for r in rules}}


def test_advisory_retrieval_retains_all_mandatory_rules_and_complete_statements():
    payload,manifest=sample_payload();original=deepcopy(payload)
    cfg={'context_limit':7000,'max_output_tokens':1000}
    fit(payload,manifest,'写作',cfg)
    assert payload['rules'][0]==original['rules'][0]
    by_id={r['id']:r for r in original['rules']}
    assert all(rule==by_id[rule['id']] for rule in payload['rules'])
    deferred=manifest['rule_selection']['deferred_recommended_ids']
    assert deferred and all(manifest['rule_modalities'][identity]=='recommended' for identity in deferred)
    assert estimate(payload,manifest,'写作',cfg)+1000<=7000
    assert any('研究不足' in r['statement'] for r in payload['rules'][1:])
    assert manifest['required_rule_ids']==['mandatory']


def test_untyped_rules_and_large_mandatory_rules_are_never_removed():
    payload={'rules':[{'id':'required','statement':'必要条件必须保留。'*3000}],'evidence':[]}
    manifest={'stage':'judgment','evidence_versions':{},'rule_modalities':{'required':'mandatory'}}
    original=deepcopy(payload['rules']);fit(payload,manifest,'写作',{'context_limit':5000,'max_output_tokens':1000})
    assert payload['rules']==original
    with pytest.raises(HTTPException) as error:context.save_context('no-step',payload,manifest,'写作',{'context_limit':5000,'max_output_tokens':1000})
    assert error.value.detail['code']=='CONTEXT_BUDGET_EXCEEDED'
    assert payload['rules']==original


def test_catalogue_sources_are_not_clipped_or_treated_as_supplied_evidence():
    payload,manifest=sample_payload();payload['rules']=payload['rules'][:1]
    source_rows=[{'id':f'source-{i}','kind':'literature','conditions':['不得推断运行结果'],'payload':{'text':('完整来源文字，保留来源条件。'*130),'locator':{'page':i+1}}} for i in range(4)]
    payload['evidence']=deepcopy(source_rows);manifest['evidence_versions']={e['id']:'v1' for e in source_rows}
    manifest['source_evidence']={e['id']:{'source_id':'raw-'+e['id'],'title':'相关技术文献'} for e in source_rows}
    cfg={'context_limit':11000,'max_output_tokens':2000};fit(payload,manifest,'写作',cfg)
    assert payload['available_sources'] and payload['evidence']
    originals={e['id']:e for e in source_rows}
    for item in payload['evidence']:assert item==originals[item['id']]
    assert set(manifest['provided_evidence_ids'])=={e['id'] for e in payload['evidence']}
    unread=payload['available_sources'][0]['evidence_id']
    with pytest.raises(HTTPException) as error:judgment.validate({'task_goal':'核对来源','claims':[{'local_id':'j1','statement':'不能凭目录支持结论','evidence_ids':[unread]}]},manifest)
    assert error.value.detail['code']=='INVALID_EVIDENCE'


def test_evidence_used_by_a_claim_is_pinned_in_full():
    source={'id':'critical','kind':'literature','conditions':['关键例外'],'payload':{'text':'明确的否定条件必须保留。'*1500,'locator':{'page':1}}}
    payload={'rules':[],'section':{'title':'相关研究'},'contract':{},'evidence':[source],'judgment':{'claims':[{'evidence_ids':['critical']}]}}
    manifest={'stage':'semantic','evidence_versions':{'critical':'v1'},'source_evidence':{'critical':{'source_id':'raw','title':'关键文献'}}}
    fit(payload,manifest,'核验',{'context_limit':8000,'max_output_tokens':1000})
    assert payload['evidence']==[source] and 'available_sources' not in payload
    with pytest.raises(HTTPException):context.save_context('no-step',payload,manifest,'核验',{'context_limit':8000,'max_output_tokens':1000})


def test_large_ocr_and_many_advisories_complete_all_stages_in_a_small_context(env,monkeypatch):
    calls=fake_model(monkeypatch)
    db.set_setting('provider',{**provider.config(),'context_limit':16000,'max_output_tokens':2048})
    source_ids=[]
    for i in range(3):
        f,_=parsers.register_file(env['pid'],f'文献-{i}.txt',((f'第{i+1}份资料。'+'完整资料：发行方且启用时允许，非运行结果。'*140).encode()),'research')
        parsers.parse_file(f['id']);source=db.one('SELECT * FROM sources WHERE file_id=? ORDER BY rowid LIMIT 1',(f['id'],))
        db.execute('UPDATE sources SET locator=? WHERE id=?',(db.dumps({'kind':'ocr','page':i+1,'recognition':[{'text':'本机保留的原页识别数据。'*8000}]}),source['id']))
        source_ids.append(evidence.create(env['pid'],{'kind':'literature','origin_type':'source','origin_id':source['id']})['id'])
    package=db.one('SELECT * FROM rule_packages WHERE project_id=?',(env['pid'],));rules=db.loads(package['rules'],[]);mandatory=rules[0]['id']
    rules.extend({**rules[0],'id':db.uid(),'modality':'recommended','statement':('解释授权条件，保留否定、角色、来源和范围。'*35)} for _ in range(70))
    db.execute('UPDATE rule_packages SET rules=? WHERE id=?',(db.dumps(rules),package['id']))
    contracts.save_contract(env['pid'],env['sid'],{'goal':'解释发行方与启用的合取条件','evidence_ids':[env['eid'],*source_ids]},state(env['pid'])['revision'])
    result=coordinator.start(env['pid'],{'section_id':env['sid'],'expected_project_state_revision':state(env['pid'])['revision']})
    completed=finish(env,result)
    assert completed['status']=='needs_review',completed
    assert all(step['state']=='completed' for step in completed['steps'])
    assert calls and all('本机保留的原页识别数据' not in db.dumps(payload) for _,payload in calls)
    for system,payload in calls:
        if 'rules' in payload:assert rules[0]['statement'] in {r['statement'] for r in payload['rules']}
    for row in db.rows('SELECT manifest FROM generation_runs WHERE project_id=?',(env['pid'],)):
        manifest=db.loads(row['manifest'],{})
        assert mandatory in manifest.get('required_rule_ids',manifest.get('rule_ids',[]))
    contexts=db.rows('SELECT budget_json FROM writing_contexts')
    assert contexts and all(db.loads(row['budget_json'])['input_estimate']+db.loads(row['budget_json'])['reserved_generated_total']<=db.loads(row['budget_json'])['context_limit'] for row in contexts)


def test_unknown_prices_enable_exact_billing_intervals_without_changing_project_limit(env):
    db.set_setting('provider',{**provider.config(),'input_price':None,'output_price':None})
    selected=profiles.current_default(env['pid'])
    assert selected['configuration']['reconcile_billing'] is True
    assert db.one('SELECT budget FROM projects WHERE id=?',(env['pid'],))['budget']==20


def test_redundant_book_label_does_not_change_claim_or_grant_new_evidence():
    raw={'task_goal':'核对文献','claims':[{'local_id':'j1','statement':'保留全部条件和否定。','evidence_ids':['source-evidence','book'],'conditions':['仅限该研究'],'not_established':['不代表本系统成果']} ]}
    manifest={'evidence_versions':{'source-evidence':'v1'},'provided_evidence_ids':['source-evidence'],'source_evidence':{'source-evidence':{'source_id':'raw-source'}}}
    normalized,corrections=judgment.normalize_redundant_books(raw,manifest,[{'id':'book','source_ids':['raw-source']}])
    assert normalized['claims'][0]=={**raw['claims'][0],'evidence_ids':['source-evidence']}
    assert raw['claims'][0]['evidence_ids']==['source-evidence','book']
    assert corrections[0]['prose_changed'] is False and corrections[0]['semantic_support']=='not_granted'
    assert judgment.validate(normalized,manifest)['claims'][0]['evidence_ids']==['source-evidence']


@pytest.mark.parametrize('book_sources,provided',[(['different-source'],['source-evidence']),(['raw-source'],[])])
def test_book_without_explicit_same_book_source_still_fails(book_sources,provided):
    raw={'task_goal':'核对文献','claims':[{'local_id':'j1','statement':'不能用书目代替原文','evidence_ids':['source-evidence','book']}]}
    manifest={'evidence_versions':{'source-evidence':'v1'},'provided_evidence_ids':provided,'source_evidence':{'source-evidence':{'source_id':'raw-source'}}}
    normalized,corrections=judgment.normalize_redundant_books(raw,manifest,[{'id':'book','source_ids':book_sources}])
    assert not corrections
    with pytest.raises(HTTPException):judgment.validate(normalized,manifest)


def test_only_known_empty_sentence_evidence_metadata_is_removed():
    from pydantic import ValidationError
    data={'sentences':[{'sentence_id':'s1','role':'transition','claims':[],'evidence_ids':[]}],'coverage_complete':True}
    normalized,corrections=verification.normalize_empty_sentence_metadata(data)
    assert 'evidence_ids' not in normalized['sentences'][0] and data['sentences'][0]['evidence_ids']==[]
    assert verification.Annotation.model_validate(normalized).sentences[0].claims==[]
    assert corrections[0]['semantic_support']=='not_granted'
    for extra in ({'evidence_ids':['unmapped']},{'unexpected_note':[]}):
        raw={'sentences':[{'sentence_id':'s1',**extra}]}
        fixed,_=verification.normalize_empty_sentence_metadata(raw)
        with pytest.raises(ValidationError):verification.Annotation.model_validate(fixed)


def test_role_explanation_is_preserved_without_changing_claims_or_support():
    note='该句兼具比较归纳性质，仍需对其中事实性论断逐项核对。'
    data={'sentences':[{'sentence_id':'s1','role':'inference','claims':[],'role_note':note}]}
    normalized,corrections=verification.normalize_empty_sentence_metadata(data)
    parsed=verification.Annotation.model_validate(normalized)
    assert parsed.sentences[0].role=='inference' and parsed.sentences[0].claims==[]
    finding=parsed.sentences[0].findings[0]
    assert finding.message==note and finding.severity=='info' and finding.evidence_ids==[]
    assert corrections[0]['semantic_support']=='not_granted' and data['sentences'][0]['role_note']==note


def test_sentence_level_support_cannot_upgrade_unmapped_claims():
    data={'sentences':[{'sentence_id':'s1','role':'fact','sentence_id_note':None,'support':'supported','claims':[{'statement':'未核对命题','evidence_ids':[],'support':'unsupported'}]}]}
    normalized,_=verification.normalize_empty_sentence_metadata(data);parsed=verification.Annotation.model_validate(normalized)
    assert parsed.sentences[0].claims[0].support=='unsupported'
    assert parsed.sentences[0].claims[0].evidence_ids==[]
    assert parsed.sentences[0].findings[0].kind=='sentence_support_note'
    data['sentences'][0]['support']='contradicted';normalized,_=verification.normalize_empty_sentence_metadata(data)
    assert verification.Annotation.model_validate(normalized).sentences[0].findings[0].severity=='blocker'


def test_unmapped_book_metadata_remains_an_explicit_unverified_gap():
    claim={'statement':'书目条目尚未人工核验。','evidence_ids':['book'],'support':'supported','conditions':['只描述登记状态']}
    data={'sentences':[{'sentence_id':'s1','role':'fact','claims':[claim]}],'coverage_complete':True}
    normalized,notes=verification.normalize_annotation_sources(data,{'evidence_versions':{},'source_evidence':{}},[{'id':'book','source_ids':['raw-source']}])
    mapped=verification.Annotation.model_validate(normalized)
    assert not mapped.coverage_complete and mapped.sentences[0].claims[0].evidence_ids==[]
    assert mapped.sentences[0].claims[0].support=='unknown'
    assert mapped.sentences[0].claims[0].statement==claim['statement'] and mapped.sentences[0].claims[0].conditions==claim['conditions']
    assert notes[0]['reference_ids']==['book'] and notes[0]['semantic_support']=='not_granted'
    assert data['sentences'][0]['claims'][0]['support']=='supported'


def test_unknown_evidence_id_is_not_reclassified_as_a_known_book():
    data={'sentences':[{'sentence_id':'s1','role':'fact','claims':[{'statement':'未知编号必须继续被拒绝','evidence_ids':['invented'],'support':'supported'}]}]}
    normalized,notes=verification.normalize_annotation_sources(data,{'evidence_versions':{}},[{'id':'book','source_ids':[]}])
    assert not notes and normalized['sentences'][0]['claims'][0]['evidence_ids']==['invented']


def test_reference_projection_keeps_conflicting_bibliographic_values():
    from app.source_payload import compact_reference_metadata
    original={'title':'研究A','authors':['甲'],'year':'2024','citation_raw':'甲.研究A[D].学校,2023.','verification':'unverified','archive_member':'private/import/file.pdf','archive_sha256':'a'*64,'catalog_number':3}
    compact=compact_reference_metadata(original)
    assert compact['year']=='2024' and '2023' in compact['citation_raw']
    assert compact['title']=='研究A' and compact['authors']==['甲'] and compact['verification']=='unverified'
    assert 'archive_member' not in compact and original['archive_member']=='private/import/file.pdf'


def test_citation_projection_preserves_text_and_codepoint_locations():
    from app.writing_agent.sentences import paragraph_for_context,paragraph_text
    node={'type':'paragraph','attrs':{'nodeId':'node','sourceIds':['raw-source']},'content':[{'type':'text','text':'甲😀。'},{'type':'citation','attrs':{'citationId':'book'}},{'type':'text','text':'乙。'}]}
    original=deepcopy(node);view=paragraph_for_context(node,1)
    assert view['text']==paragraph_text(node)=='甲😀。\ufffc乙。'
    assert view['reference_ids']==['book'] and view['citation_positions_cp']==[3]
    assert view['source_ids']==['raw-source'] and node==original


def test_extended_identifier_aliases_round_trip_without_rewriting_prose():
    from app.writing_agent.identifiers import encode,decode
    payload={'rules':[{'id':'rule-guid','statement':'source-guid必须保持原样'}],'evidence':[{'id':'evidence-guid','payload':{'source_id':'source-guid'}}],'references':[{'id':'book-guid','source_ids':['source-guid']}],'project_inputs':{'rule_ids':['rule-guid'],'material_evidence_ids':['evidence-guid']}}
    manifest={'stage':'semantic','evidence_versions':{'evidence-guid':'v1'},'source_evidence':{'evidence-guid':{'source_id':'source-guid'}},'file_ids':['file-guid']}
    encoded,aliases=encode(payload,manifest)
    assert encoded['references'][0]['source_ids']==['D1'] and encoded['rules'][0]['id']=='L1'
    assert encoded['rules'][0]['statement']==payload['rules'][0]['statement']
    assert decode(encoded,aliases)==payload
