import hashlib
import pytest
from test_workbench import client,project,file
from test_visual_rule_repair import seed
from app import db,ai_autopilot as auto,visual_rule_repair as repair,document_structure,document_coverage,knowledge_units,rule_duplicates,rule_workspace,candidate_identity,rule_rebuild


def legacy_substitution(client):
    pid,first,source,review=seed(client);originals=[first]
    other_id=db.uid();db.execute('INSERT INTO rules(id,project_id,source_id,statement,created_at) VALUES(?,?,?,?,?)',(other_id,pid,source['id'],'参考文献至少20篇。',db.now()))
    originals.append(db.one('SELECT * FROM rules WHERE id=?',(other_id,)))
    image=review['asset_sha256'];new_id=db.uid();text='共同的目录标题'
    loc={'kind':'visual_rule_excerpt','transcription_scope':'selected_complete_passages','evidence_asset_sha256':image,'page':1}
    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(new_id,source['file_id'],source['page_id'],db.dumps(loc),text,text,'rule_candidate','assistant_visual_excerpt',0))
    db.set_setting('document_observation_supersessions',{source['id']:{'replacement_source_id':new_id,'coverage':'selected_complete_passages','asset_sha256':image}})
    for original in originals:
        db.execute("UPDATE rules SET statement=?,source_id=?,state='reference_only' WHERE id=?",(text,new_id,original['id']))
        after=db.one('SELECT * FROM rules WHERE id=?',(original['id'],));db.audit(pid,'visual_rule_excerpt_adopted',{'before':original,'after':after})
        auto.save_receipt(pid,'rule',original['id'],{'purpose':'knowledge','completed':True,'before':original,'after':after})
    return pid,originals,source,new_id


def test_partial_review_keeps_every_saved_ocr_character_in_current_structure(client):
    pid,rule,source,review=seed(client);text='正文的完整观察。'*400+'原图尾部例外：仅适用于第二章，不适用于摘要。'
    db.execute('UPDATE sources SET text=?,normalized=? WHERE id=?',(text,text,source['id']))
    source=auto.rule_source(rule);review.pop('candidate_replacements')
    result=repair.apply(pid,review,[{'rule':rule,'source':source}])
    assert result['pending_rule_ids']==[rule['id']]
    assert db.one('SELECT statement FROM rules WHERE id=?',(rule['id'],))['statement']==rule['statement']
    f=db.one('SELECT * FROM files WHERE id=?',(source['file_id'],));parts=document_structure.extract(f)
    raw=next(p for p in parts if p['origin']=='unverified_image_transcript');assert raw['text']==text
    assert next(p for p in parts if p['origin']=='assistant_visual_excerpt')['text']==review['text']
    unit=list(document_structure.candidates(raw,'paragraphs'))[0]
    assert unit['text']==text and unit['locator']['cannot_support_claims']
    assert unit['locator']['read_scope']=='full_saved_ocr_observation'


def test_asset_reader_returns_full_observation_and_checked_excerpt_separately(client):
    pid,rule,source,review=seed(client);text='完整条件。'*150+'尾部否定：不得将案例当成当前项目成果。'
    db.execute('UPDATE sources SET text=? WHERE id=?',(text,source['id']));source=auto.rule_source(rule)
    repair.apply(pid,review,[{'rule':rule,'source':source}])
    result=document_coverage.read_asset(pid,source['file_id'],review['asset_sha256'],1)
    assert result['raw_observations'][0]['text']==text and result['raw_observations'][0]['cannot_support_claims']
    assert result['verified_passages'][0]['text']==review['text'] and not result['entire_image_compared']
    narrow=document_coverage.read_asset(pid,source['file_id'],review['asset_sha256'],1,max_chars=200)
    assert narrow['status']=='needs_narrower_range' and narrow['raw_observations']==[]
    knowledge_units.build(pid,source['file_id'],strategy='page',allow_material=True)
    found=document_coverage.locate(pid,source['file_id'],'尾部否定')
    assert found['items'] and all(item['cannot_support_claims'] for item in found['items'])
    assert any(item['matching_view']=='unverified_image_transcript' for item in found['items'])


def test_full_ocr_read_cannot_become_verified_norm_in_rebuild():
    text='参考文献不少于20篇，近五年文献不少于80%。';unit={'id':'U','origin':'unverified_image_transcript','text':text}
    items,_=rule_rebuild.validate({'covered_unit_ids':['U'],'items':[{'unit_id':'U','quote':text,'purpose':'requirement','scope':'global','conditions':[],'reason':'模型从OCR读到这条要求','confidence':.99}]},
        {'target_unit_ids':['U'],'units':[unit]})
    assert items[0]['purpose']=='uncertain'


def test_bulk_repair_cannot_rewrite_distinct_candidates_to_one_excerpt(client):
    pid,rule,source,review=seed(client);review.pop('candidate_replacements');second=db.uid()
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,created_at) VALUES(?,?,?,?,?)',(second,pid,source['id'],'摘要应说明研究方法。',db.now()))
    other=db.one('SELECT * FROM rules WHERE id=?',(second,))
    result=repair.apply(pid,review,[{'rule':rule,'source':source},{'rule':other,'source':auto.rule_source(other)}])
    assert result['fixed_rule_ids']==[] and set(result['pending_rule_ids'])=={rule['id'],second}
    assert len({r['statement'] for r in db.rows('SELECT statement FROM rules WHERE project_id=?',(pid,))})==2


def test_previous_shared_excerpt_is_overlap_and_api_cannot_merge_it(client):
    pid,originals,source,new_id=legacy_substitution(client)
    data=rule_workspace.workspace(pid)
    assert data['duplicates']==[] and len(data['text_overlaps'])==1
    response=client.post(f'/api/v1/projects/{pid}/rules/duplicates/merge',json={'canonical_id':originals[0]['id'],'duplicate_ids':[originals[1]['id']]})
    assert response.status_code==409


def test_restoration_recovers_distinct_subjects_and_reopens_unsupported_content(client):
    pid,originals,source,new_id=legacy_substitution(client);result=candidate_identity.restore_distinct_candidates(pid)
    assert set(result['reopened_for_full_review'])=={r['id'] for r in originals}
    for original in originals:
        current=db.one('SELECT * FROM rules WHERE id=?',(original['id'],));assert current['statement']==original['statement'] and current['source_id']==original['source_id'] and current['state']=='candidate'
        assert auto.receipt(pid,'rule',current['id'])['verified_excerpt_source_id']==new_id
    assert candidate_identity.restore_distinct_candidates(pid)['reopened_for_full_review']==[]


def test_restoration_never_overwrites_following_user_edits(client):
    pid,originals,source,new_id=legacy_substitution(client)
    db.execute('UPDATE rules SET decision_note=? WHERE id=?',('用户后续核对',originals[0]['id']))
    result=candidate_identity.restore_distinct_candidates(pid)
    assert originals[0]['id'] in result['skipped_changed_records']
    assert db.one('SELECT decision_note FROM rules WHERE id=?',(originals[0]['id'],))['decision_note']=='用户后续核对'


def test_same_words_with_example_and_counterexample_purposes_are_separate(client):
    pid=project(client)
    for modality in ('example','counterexample'):
        db.execute("INSERT INTO rules(id,project_id,statement,state,modality,created_at) VALUES(?,?,?,?,?,?)",(db.uid(),pid,'论文必须说明研究方法。','reference_only',modality,db.now()))
    data=rule_workspace.workspace(pid)
    assert data['duplicates']==[] and len(data['text_overlaps'])==1


def test_equal_criterion_text_in_different_grades_is_not_a_duplicate(client):
    pid=project(client)
    for grade in ('A','B'):
        f=file(client,pid,grade+'级：论文应说明研究方法。',role='rules');s=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
        db.execute('UPDATE sources SET locator=? WHERE id=?',(db.dumps({'grade':grade,'page':1}),s['id']))
        rid=db.uid();db.execute("INSERT INTO rules(id,project_id,source_id,statement,state,created_at) VALUES(?,?,?,?,?,?)",(rid,pid,s['id'],'论文应说明研究方法。','reference_only',db.now()))
        auto.save_receipt(pid,'rule',rid,{'purpose':'conditional'})
    data=rule_workspace.workspace(pid);assert data['duplicates']==[]


def test_same_image_has_independent_occurrences_under_two_document_contexts(client):
    pid,rule,source,review=seed(client);other=file(client,pid,'反例场景',role='rules')
    p=db.one('SELECT id FROM pages WHERE file_id=?',(other['id'],));db.execute('UPDATE pages SET images=? WHERE id=?',(db.dumps([{'sha256':review['asset_sha256']}]),p['id']))
    coverage=document_coverage.project_report(pid,include_assets=True)
    assert coverage['asset_occurrences']==2 and len(coverage['documents'])==2
    assert coverage['unique_image_assets']==1 and coverage['reused_image_assets']==1
    assert len(coverage['asset_reuse'][0]['occurrences'])==2
    assert {r['file_id'] for d in coverage['documents'] for r in d['assets']}=={source['file_id'],other['id']}


def test_parallel_image_views_have_separate_context_streams(client):
    pid,rule,source,review=seed(client);repair.apply(pid,review,[{'rule':rule,'source':source}])
    f=db.one('SELECT * FROM files WHERE id=?',(source['file_id'],));parts=document_structure.extract(f)
    raw=next(p for p in parts if p['origin']=='unverified_image_transcript');verified=next(p for p in parts if p['origin']=='assistant_visual_excerpt')
    r=list(document_structure.candidates(raw,'page'))[0];v=list(document_structure.candidates(verified,'page'))[0]
    assert r['locator']['context_stream']!=v['locator']['context_stream']
    parsed=knowledge_units.build(pid,f['id'],strategy='page',allow_material=True)
    row=db.one("SELECT id FROM knowledge_units WHERE parse_id=? AND origin='unverified_image_transcript'",(parsed['id'],))
    read=knowledge_units.read(pid,row['id'],intent='guidance')
    assert read['observation_scope']['cannot_support_claims'] and all(p['origin']=='unverified_image_transcript' for p in read['passages'])
