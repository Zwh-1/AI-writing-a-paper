import pytest
from test_workbench import client,project,file
from app import db,ai_autopilot as auto,rule_rebuild as rebuild,rule_candidate_repair as repair,document_structure as structure
from app.document_observations import observations


def seed(client,text,purpose='counterexample',score=.97,origin='pptx_slide',model_judgment=None):
    pid=project(client);f=file(client,pid,text,role='rules');s=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],));job='cached-generation';uid='U1'
    loc={'page':1,'generation_id':job,'unit_id':uid}
    db.execute('UPDATE sources SET locator=? WHERE id=?',(db.dumps(loc),s['id']))
    rid=db.uid();db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(rid,pid,s['id'],text,'pending',db.now()))
    item={'unit_id':uid,'quote':text,'purpose':purpose,'confidence':score,'scope':'global','conditions':[],'reason':'完整原页中给出明确的用途上下文'}
    if model_judgment:item['model_judgment']=model_judgment
    unit={'id':uid,'text':text,'origin':origin,'locator':{'page':1},'hash':'test','parse_id':None}
    db.set_setting(rebuild.key(job,'batch:test'),{'complete':True,'file_id':f['id'],'target_unit_ids':[uid],'items':[item],'units':[unit]})
    return pid,f,rid,job


def test_numbered_error_and_icon_context_replays_without_any_provider_call(client):
    text='❌ 错误二：大量堆砌技术细节\n后果：论文严重偏离综述体裁要求，评审老师会直接判为结构错误。'
    pid,f,rid,job=seed(client,text)
    result=repair.replay(pid,job)
    rule=db.one('SELECT * FROM rules WHERE id=?',(rid,));rec=auto.receipt(pid,'rule',rid)
    assert result['fixed_count']==1 and rule['state']=='reference_only' and rule['modality']=='counterexample'
    assert rec['confidence']==.97 and rec['human_approval'] is False
    decision=db.one('SELECT * FROM rule_decisions WHERE rule_id=? ORDER BY id DESC',(rid,))
    assert db.loads(decision['before_row'])['state']=='candidate'
    assert db.loads(decision['after_row'])==rule
    assert not db.rows('SELECT * FROM generation_runs')


@pytest.mark.parametrize('text,score', [('论文必须说明研究方法。',.99),('❌ 错误二：论文必须说明研究方法。',.7)])
def test_missing_context_or_low_confidence_stays_pending(client,text,score):
    pid,f,rid,job=seed(client,text,score=score)
    result=repair.replay(pid,job)
    assert result['fixed_count']==0 and db.one('SELECT state FROM rules WHERE id=?',(rid,))['state']=='candidate'
    assert not auto.receipt(pid,'rule',rid)['completed']


def test_short_standalone_heading_does_not_fail_literal_validation():
    item={'unit_id':'U1','quote':'引言','purpose':'heading','confidence':.95,'conditions':[],'reason':'原页仅有栏目标题'}
    values,_=rebuild.validate({'covered_unit_ids':['U1'],'items':[item]},{'target_unit_ids':['U1'],'units':[{'id':'U1','text':'引言','origin':'pptx_slide'}]})
    assert values[0]['purpose']=='heading' and values[0]['confidence']==.95


def test_complete_unit_pointer_reads_literal_source_and_rejects_cross_unit_or_conflicting_quote():
    text='示例：本系统用于版权存证。';batch={'target_unit_ids':['U1'],'units':[{'id':'U1','text':text,'origin':'pptx_slide'}]}
    item={'unit_id':'U1','quote_unit_id':'U1','purpose':'example','confidence':.95,'conditions':[],'reason':'原页为教学示范'}
    values,_=rebuild.validate({'covered_unit_ids':['U1'],'items':[item]},batch)
    assert values[0]['quote']==text and values[0]['purpose']=='example'
    quoted={**item,'quote':text,'quote_unit_id':None}
    assert rebuild.validate({'covered_unit_ids':['U1'],'items':[quoted]},batch)[0][0]['quote']==text
    with pytest.raises(ValueError):rebuild.validate({'covered_unit_ids':['U1'],'items':[{**item,'quote_unit_id':'U2'}]},batch)
    with pytest.raises(ValueError):rebuild.validate({'covered_unit_ids':['U1'],'items':[{**item,'quote':'编造的项目成果'}]},batch)


def test_native_example_can_recover_page_use_but_never_recover_unmatched_norm():
    text='正文-相关技术介绍\n\n示例①：区块链技术介绍\n\n写作示范\n\n本系统用于版权存证。'
    batch={'target_unit_ids':['U1'],'units':[{'id':'U1','text':text,'origin':'pptx_slide'}]}
    item={'unit_id':'U1','quote':'改写而非原句','purpose':'example','confidence':.95,'conditions':[],'reason':'原页为教学写作示范'}
    values,_=rebuild.validate({'covered_unit_ids':['U1'],'items':[item]},batch)
    assert values[0]['purpose']=='example' and values[0]['quote']==text
    assert values[0]['model_judgment']['quote']=='改写而非原句'
    values,_=rebuild.validate({'covered_unit_ids':['U1'],'items':[{**item,'purpose':'requirement'}]},batch)
    assert values[0]['purpose']=='uncertain' and values[0]['quote_issue']=='not_literal'
    ocr={**batch,'units':[{**batch['units'][0],'origin':'windows_ocr'}]}
    values,_=rebuild.validate({'covered_unit_ids':['U1'],'items':[item]},ocr)
    assert values[0]['purpose']=='uncertain'


def test_cached_heading_is_recovered_and_human_edits_are_preserved(client):
    raw={'quote':'引言','purpose':'heading','confidence':.95,'conditions':[],'reason':'原页仅有栏目标题','scope':'introduction'}
    pid,f,rid,job=seed(client,'引言',purpose='uncertain',score=0,model_judgment=raw)
    assert repair.replay(pid,job)['fixed_count']==1
    assert auto.receipt(pid,'rule',rid)['purpose']=='heading'
    pid,f,rid,job=seed(client,'引言',purpose='uncertain',score=0,model_judgment=raw)
    db.audit(pid,'edit_rule',{'id':rid,'changes':{'statement':'引言'}})
    assert repair.replay(pid,job)['fixed_count']==0


def test_grade_repair_preserves_complete_row_and_does_not_invent_confidence(client,monkeypatch):
    pid,f,rid,job=seed(client,'论文应当使用专业术语。',purpose='example',score=.3,origin='pdfium_text_order')
    db.execute('INSERT INTO file_source_profiles VALUES(?,?,?,?)',(f['id'],'grading_criteria','评分表',db.now()))
    text='学术能力\n综合等级：B级\n分数范围：70-85分\n评定标准：论文应当使用专业术语。'
    row={'id':'T1R2','grade':'B','score':'70-85分','pages':[1,2],'text':text,'cells':[]}
    monkeypatch.setattr('app.pdf_rule_contexts.extract',lambda f:[row])
    result=repair.replay(pid,job);rule=db.one('SELECT * FROM rules WHERE id=?',(rid,));rec=auto.receipt(pid,'rule',rid)
    assert result['fixed_count']==1 and rule['statement']==text and rule['state']=='reference_only'
    assert rec['purpose']=='conditional' and rec['confidence'] is None and rec['original_model_judgment']['confidence']==.3
    assert rec['decision_method']=='tagged_source_structure'
    assert auto.rule_source(rule)['method']=='document_rule_context'
    assert repair.replay(pid,job)['fixed_count']==0


def source(sid,start,text,asset='image'):
    return {'id':sid,'text':text,'method':'windows_ocr','locator':db.dumps({'asset_sha256':asset,'char_start':start,'ocr_version':2})}


def test_storage_slices_rejoin_before_semantic_candidates_are_built():
    text='论文必须保留数字、否定和条件。'*100
    values=observations([source('a',0,text[:1000]),source('b',1000,text[1000:])])
    assert len(values)==1 and values[0]['text']==text
    assert values[0]['locator']['source_ids']==['a','b']
    page={'number':1,'text':text,'origin':'windows_ocr','unit_kind':'image_observation','observation_locator':values[0]['locator']}
    candidates=list(structure.candidates(page,'paragraphs'))
    assert len(candidates)==1 and candidates[0]['text']==text
    assert 'transcript_needs_original_comparison' in candidates[0]['issues']


def test_distinct_images_missing_slices_and_conflicts_are_never_silently_merged():
    assert len(observations([source('a',0,'甲图'),source('b',0,'乙图','other')]))==2
    missing=observations([source('a',0,'首段'),source('b',1000,'末段')])
    assert len(missing)==2 and all('observation_has_missing_slices' in o['issues'] for o in missing)
    conflicting=observations([source('a',0,'旧转录'),source('b',0,'新转录')])
    assert len(conflicting)==2 and all('conflicting_observation_slices' in o['issues'] for o in conflicting)


def test_complete_new_observation_can_represent_older_storage_slices():
    old=[source('a',0,'参考文献不'),source('b',5,'少于十篇。')]
    whole=source('c',0,'参考文献不少于十篇。');loc=db.loads(whole['locator']);loc['storage_scope']='complete_image_observation';whole['locator']=db.dumps(loc)
    combined=observations(old+[whole])
    assert len(combined)==1 and combined[0]['text']==whole['text'] and combined[0]['source_ids'][0]=='c'


def test_grade_columns_are_compared_as_separate_cells(client,monkeypatch):
    pid,f,rid,job=seed(client,'C级 （建议修改） 60-69分 论文应当使用专业术语。',purpose='example',origin='pdfium_text_order')
    db.execute('INSERT INTO file_source_profiles VALUES(?,?,?,?)',(f['id'],'grading_criteria','评分表',db.now()))
    text='综合等级：C级\n分数范围：60-69分\n评定标准：论文应当使用专业术语。'
    monkeypatch.setattr('app.pdf_rule_contexts.extract',lambda f:[{'id':'T1R3','grade':'C','score':'60-69分','pages':[1],'text':text,'cells':[]}])
    assert repair.replay(pid,job)['fixed_count']==1
    assert db.one('SELECT statement FROM rules WHERE id=?',(rid,))['statement']==text


def test_visual_number_correction_is_versioned_and_does_not_erase_original(client):
    import io,hashlib
    from PIL import Image
    from app import security,source_payload
    pid,f,rid,job=seed(client,'参考文献数量不少于二十篇。',purpose='uncertain',score=.3,origin='visual_transcription')
    old=auto.rule_source(db.one('SELECT * FROM rules WHERE id=?',(rid,)))
    buf=io.BytesIO();Image.new('RGB',(100,100),'white').save(buf,format='PNG');sha=security.put_blob(buf.getvalue())
    db.execute('UPDATE pages SET images=? WHERE id=?',(db.dumps([{'sha256':sha}]),old['page_id']))
    db.execute("UPDATE sources SET method='visual_transcription',locator=? WHERE id=?",(db.dumps({'evidence_asset_sha256':sha}),old['id']))
    rule=db.one('SELECT * FROM rules WHERE id=?',(rid,));old=auto.rule_source(rule)
    text='参考文献数量不少于十篇。';reason='原图数量为十篇，旧转录误写二十篇；保留课程条件。'
    revised=repair.register_visual_correction(pid,f['id'],old['page_id'],sha,text,reason)
    review={'rule_id':rid,'source_id':old['id'],'source_hash':source_payload.source_hash(old),'file_sha256':old['file_sha256'],
        'statement_hash':hashlib.sha256(rule['statement'].encode()).hexdigest(),'quote':text,'replacement_source_id':revised['id'],
        'purpose':'conditional','reason':reason,'method':'assistant_visual_original_comparison','visual_asset_sha256':sha,'compared_original':True}
    after=repair.apply_source_review(pid,review)
    assert after['statement']==text and after['state']=='reference_only'
    assert db.one('SELECT text FROM sources WHERE id=?',(old['id'],))['text']=='参考文献数量不少于二十篇。'
    assert db.setting('document_observation_supersessions')[old['id']]['replacement_source_id']==revised['id']
    assert auto.receipt(pid,'rule',rid)['confidence'] is None
    assert [o['text'] for o in observations(db.rows('SELECT * FROM sources WHERE page_id=?',(old['page_id'],)),db.setting('document_observation_supersessions')) if o['origin']=='visual_transcription']==[text]
    decision=db.one('SELECT * FROM rule_decisions WHERE rule_id=? ORDER BY id DESC',(rid,))
    assert db.loads(decision['before_row'])['statement']=='参考文献数量不少于二十篇。'
    assert db.loads(decision['after_row'])==after
    from app import rule_governance
    rule_governance.restore(pid,decision['id'])
    restored=db.one('SELECT * FROM rules WHERE id=?',(rid,))
    assert restored['source_id']==old['id'] and restored['statement']==rule['statement']
    from app import parsers
    assert old['id'] not in {s['id'] for s in parsers.keyword_search(pid,'参考文献',kind='all',limit=100)}
    assert revised['id'] in {s['id'] for s in parsers.keyword_search(pid,'参考文献',kind='all',limit=100)}


def test_workspace_separates_ocr_context_and_purpose_issues(client):
    from app import rule_workspace
    pid,f,rid,job=seed(client,'原图转录仍有乱码',purpose='uncertain',score=0)
    db.execute("UPDATE sources SET method='windows_ocr' WHERE id=(SELECT source_id FROM rules WHERE id=?)",(rid,))
    value=rule_workspace.workspace(pid)
    assert value['pending_diagnostics']['by_kind']['source_quality']==1
    assert value['rules'][0]['candidate_issue']['label']=='原图转录待核对'
