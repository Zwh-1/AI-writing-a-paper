import hashlib,io
import pytest
from PIL import Image
from test_workbench import client,project,file
from app import db,security,ai_autopilot as auto,visual_rule_repair as repair,rule_governance,document_structure


def seed(client):
    pid=project(client);f=file(client,pid,'原生标题',role='rules');s=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    buf=io.BytesIO();Image.new('RGB',(100,100),'white').save(buf,format='PNG');sha=security.put_blob(buf.getvalue())
    db.execute('UPDATE pages SET images=? WHERE id=?',(db.dumps([{'sha256':sha}]),s['page_id']))
    db.execute("UPDATE sources SET method='windows_ocr',text=?,normalized=?,locator=? WHERE id=?",('数量之50','数量之50',db.dumps({'asset_sha256':sha,'char_start':0}),s['id']))
    rid=db.uid();db.execute('INSERT INTO rules(id,project_id,source_id,statement,created_at) VALUES(?,?,?,?,?)',(rid,pid,s['id'],'数量之50',db.now()))
    rule=db.one('SELECT * FROM rules WHERE id=?',(rid,));source=auto.rule_source(rule)
    auto.save_receipt(pid,'rule',rid,{'confidence':.3,'completed':False,'state':'blocked','reason':'图文不清楚'})
    review={'asset_sha256':sha,'text':'近五年文献≥50%','reason':'对照原图中的数量与范围，旧转录把大于等于号识别成汉字。','purpose':'conditional',
        'coverage':'selected_complete_passages','compared_original':True,'human_approval':False}
    review['candidate_replacements']={rid:{'previous_statement_hash':hashlib.sha256(rule['statement'].encode()).hexdigest(),'quote':review['text']}}
    return pid,rule,source,review


def test_selected_excerpt_does_not_claim_full_image_or_inflate_model_score(client):
    pid,rule,source,review=seed(client)
    result=repair.apply(pid,review,[{'rule':rule,'source':source}]);after=db.one('SELECT * FROM rules WHERE id=?',(rule['id'],));now=auto.rule_source(after)
    assert result['fixed_rule_ids']==[rule['id']] and after['state']=='reference_only'
    assert after['statement']=='近五年文献≥50%' and now['text']==after['statement']
    meta=db.loads(now['locator']);assert meta['transcription_scope']=='selected_complete_passages' and meta['untranscribed_image_content_retained']
    rec=auto.receipt(pid,'rule',rule['id']);assert rec['confidence'] is None and rec['previous_model_review']['confidence']==.3
    assert rec['human_approval'] is False and now['verified']==0
    assert source['id'] in db.setting('document_observation_supersessions')
    assert db.one('SELECT text FROM sources WHERE id=?',(source['id'],))['text']=='数量之50'
    decision=db.one('SELECT * FROM rule_decisions WHERE rule_id=? ORDER BY id DESC',(rule['id'],))
    rule_governance.restore(pid,decision['id'])
    assert db.one('SELECT * FROM rules WHERE id=?',(rule['id'],))==rule


@pytest.mark.parametrize('change',[{'purpose':'requirement'},{'coverage':'whole_document'},{'compared_original':False},{'human_approval':True}])
def test_excerpt_review_cannot_grant_global_norm_or_fake_human_approval(client,change):
    pid,rule,source,review=seed(client)
    with pytest.raises(ValueError):repair.apply(pid,{**review,**change},[{'rule':rule,'source':source}])
    assert db.one('SELECT * FROM rules WHERE id=?',(rule['id'],))==rule


def test_changed_rule_or_source_rejects_whole_batch(client):
    pid,rule,source,review=seed(client)
    db.execute('UPDATE rules SET decision_note=? WHERE id=?',('用户已修改',rule['id']))
    with pytest.raises(ValueError,match='候选被修改'):repair.apply(pid,review,[{'rule':rule,'source':source}])
    assert not db.rows("SELECT * FROM sources WHERE method='assistant_visual_excerpt'")


def test_wrong_asset_does_not_become_source_for_another_page(client):
    pid,rule,source,review=seed(client)
    db.execute('UPDATE pages SET images=? WHERE id=?',('[]',source['page_id']))
    with pytest.raises(ValueError,match='图片不属于'):repair.apply(pid,review,[{'rule':rule,'source':source}])
    assert db.one('SELECT * FROM rules WHERE id=?',(rule['id'],))==rule


def test_user_edited_candidates_are_never_overwritten(client):
    pid,rule,source,review=seed(client)
    db.audit(pid,'edit_rule',{'id':rule['id'],'changes':{'statement':rule['statement']}})
    with pytest.raises(ValueError):repair.apply(pid,review,[{'rule':rule,'source':source}])


def test_excerpt_scope_is_preserved_in_structure_and_native_is_not_superseded(client):
    pid,rule,source,review=seed(client);f=db.one('SELECT * FROM files WHERE id=?',(source['file_id'],))
    repair.apply(pid,review,[{'rule':rule,'source':source}])
    pages=document_structure.extract(f)
    excerpt=next(p for p in pages if p['origin']=='assistant_visual_excerpt')
    unit=list(document_structure.candidates(excerpt,'paragraphs'))[0]
    assert 'selected_visual_excerpts_not_complete_image' in unit['issues']
    assert 'transcript_needs_original_comparison' not in unit['issues']
    assert unit['locator']['transcription_scope']=='selected_complete_passages'
    assert unit['locator']['entire_original_image_transcribed'] is False
    assert '原生标题' in next(p for p in pages if p['origin']=='native_extraction')['text']


def test_same_asset_reuses_one_source_and_every_rule_has_before_after(client):
    pid,rule,source,review=seed(client);rid=db.uid()
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,created_at) VALUES(?,?,?,?,?)',(rid,pid,source['id'],'另一旧OCR候选',db.now()))
    other=db.one('SELECT * FROM rules WHERE id=?',(rid,))
    result=repair.apply(pid,review,[{'rule':rule,'source':source},{'rule':other,'source':auto.rule_source(other)}])
    assert result['fixed_rule_ids']==[rule['id']] and result['pending_rule_ids']==[rid] and len(result['replacement_source_ids'])==1
    assert db.one('SELECT statement,state FROM rules WHERE id=?',(rid,))=={'statement':'另一旧OCR候选','state':'candidate'}
    assert len(db.rows('SELECT * FROM rule_decisions'))==2


def test_current_page_reading_coverage_extends_only_to_new_inspected_text(client):
    pid,rule,source,review=seed(client);page=db.one('SELECT * FROM pages WHERE id=?',(source['page_id'],))
    db.execute('INSERT INTO rule_page_checks VALUES(?,?,?,?,?)',(page['id'],rule_governance.page_hash(page),'previous-full-reading','machine_covered',db.now()))
    repair.apply(pid,review,[{'rule':rule,'source':source}])
    assert rule_governance.page_covered(db.one('SELECT * FROM pages WHERE id=?',(page['id'],)))
    assert db.one('SELECT review_state FROM pages WHERE id=?',(page['id'],))['review_state']=='unreviewed'
    assert db.one("SELECT * FROM audit WHERE action='rule_page_coverage_extended'")


def test_stale_page_coverage_is_not_granted_by_excerpt_review(client):
    pid,rule,source,review=seed(client);page=db.one('SELECT * FROM pages WHERE id=?',(source['page_id'],))
    db.execute('INSERT INTO rule_page_checks VALUES(?,?,?,?,?)',(page['id'],'stale','previous-full-reading','machine_covered',db.now()))
    repair.apply(pid,review,[{'rule':rule,'source':source}])
    assert not rule_governance.page_covered(page)
    assert not db.one("SELECT * FROM audit WHERE action='rule_page_coverage_extended'")


def test_local_extraction_does_not_resurrect_obsolete_ocr_from_page_cache(client):
    from app import rules
    pid,rule,source,review=seed(client)
    db.execute('UPDATE pages SET text=? WHERE id=?',('参考文献必须使用乱码之50。',source['page_id']))
    repair.apply(pid,review,[{'rule':rule,'source':source}])
    rules.extract(pid)
    assert not db.one("SELECT id FROM rules WHERE statement LIKE '%乱码%'")


def test_workspace_reports_partial_coverage_even_when_purpose_is_resolved(client):
    from app import rule_workspace
    pid,rule,source,review=seed(client)
    repair.apply(pid,review,[{'rule':rule,'source':source}])
    row=next(r for r in rule_workspace.entries(pid) if r['id']==rule['id'])
    assert row['state']=='reference_only' and row['source_observation']['scope_limited']
    assert '非整图' in row['source_observation']['label']


def test_source_revision_does_not_overwrite_an_unmatched_reference(client):
    pid,rule,source,review=seed(client)
    rid=db.uid();db.execute("INSERT INTO rules(id,project_id,source_id,statement,state,created_at) VALUES(?,?,?,?,?,?)",(rid,pid,source['id'],'OCR错字的参考内容','reference_only',db.now()))
    repair.apply(pid,review,[{'rule':rule,'source':source}])
    result=repair.synchronize_reference_sources(pid)
    assert result['synchronized_rule_ids']==[] and result['skipped_rule_ids']==[rid]
    after=db.one('SELECT * FROM rules WHERE id=?',(rid,));assert after['statement']=='OCR错字的参考内容'
    assert repair.synchronize_reference_sources(pid)['synchronized_rule_ids']==[]


def test_selected_visual_scope_survives_full_excerpt_read_and_cannot_be_enabled_by_model(client):
    from app import knowledge_units
    pid,rule,source,review=seed(client)
    repair.apply(pid,review,[{'rule':rule,'source':source}]);after=db.one('SELECT * FROM rules WHERE id=?',(rule['id'],));saved=auto.rule_source(after)
    f=db.one('SELECT * FROM files WHERE id=?',(source['file_id'],));page=next(p for p in document_structure.extract(f) if p['origin']=='assistant_visual_excerpt')
    unit=list(document_structure.candidates(page,'page'))[0]
    scope=knowledge_units.observation_scope(unit['locator'])
    assert scope['literal_scope']=='listed_passages_only' and not scope['entire_original_image_transcribed']
    auto.apply_rule(pid,after,saved,{'purpose':'requirement','confidence':.99,'quote':after['statement'],'reason':'模型认为这条要求适用'},saved['text'],'test',None,'test-model')
    assert db.one('SELECT state FROM rules WHERE id=?',(rule['id'],))['state']=='reference_only'
    assert auto.receipt(pid,'rule',rule['id'])['purpose']=='conditional'
