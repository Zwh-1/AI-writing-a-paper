"""Adopt inspected image excerpts with explicit coverage and reversible provenance.

An excerpt review can classify a teaching screenshot without claiming every
character in its UI or example prose has been transcribed. Only the inspected
literal passages replace noisy OCR in current retrieval.
"""
import hashlib
from . import db,security,rule_governance,source_payload,ai_autopilot as auto
from .rule_candidate_repair import editable

VERSION='visual-rule-excerpts-v1'
PURPOSES={'example','counterexample','conditional','knowledge','heading','noise'}
SCOPES={'complete_image_text','selected_complete_passages'}


def asset_cropped(sha):
    return bool(db.one("SELECT s.id FROM sources s,json_each(s.locator,'$.source_issues') issue WHERE json_extract(s.locator,'$.evidence_asset_sha256')=? AND issue.value='cropped_original' LIMIT 1",(sha,)))


@db.atomic
def synchronize_reference_sources(pid):
    """Move unedited reference rows off superseded OCR, preserving their history."""
    from .rule_generations import visible
    from .rule_pipeline import exact_quote
    mapping=db.setting('document_observation_supersessions',{});changed=[];skipped=[]
    for before in visible(pid,db.rows("SELECT * FROM rules WHERE project_id=? AND state='reference_only'",(pid,))):
        if before['source_id'] not in mapping:continue
        if not editable(before,('reference_only',)):skipped.append(before['id']);continue
        target=before['source_id'];seen=set()
        while target in mapping and target not in seen:
            seen.add(target);target=mapping[target]['replacement_source_id']
        if target in seen:raise ValueError('来源替代链存在循环')
        original=auto.rule_source(before);source=auto.rule_source({'source_id':target,'project_id':pid})
        if not source or not source['active'] or source['file_id']!=original['file_id'] or source['page_id']!=original['page_id']:skipped.append(before['id']);continue
        loc=db.loads(source['locator'],{})
        if source['method'] not in ('assistant_visual_excerpt','visual_transcription'):skipped.append(before['id']);continue
        literal=exact_quote(source['text'],before['statement'])
        if not literal:
            # A source revision is not a replacement for every distinct rule
            # that happened to originate in that source.
            skipped.append(before['id']);continue
        text=literal
        prior=auto.receipt(pid,'rule',before['id']);purpose=prior.get('purpose','conditional')
        if not literal:
            peer=db.one("SELECT id FROM rules WHERE project_id=? AND source_id=? AND state='reference_only' AND id!=? ORDER BY created_at DESC LIMIT 1",(pid,target,before['id']))
            peer_receipt=auto.receipt(pid,'rule',peer['id']) if peer else {}
            purpose=peer_receipt.get('purpose','conditional')
        if purpose not in PURPOSES:purpose='conditional'
        reason='关联来源已有原图复验修订；'+('原句逐字映射到修订来源，保留条款文本' if literal else '旧句无法逐字映射，采用已核对摘录并保留原图复验范围，旧文本留存历史')
        db.execute('UPDATE rules SET source_id=?,statement=?,modality=?,decision_note=? WHERE id=?',(target,text,purpose if purpose in ('example','counterexample') else 'pending',reason,before['id']))
        after=db.one('SELECT * FROM rules WHERE id=?',(before['id'],))
        outcome='example' if purpose in ('example','counterexample') else 'conditional_reference' if purpose=='conditional' else 'knowledge'
        rule_governance.record(pid,before,after,source,outcome,[reason,'原文依据：'+text,'来源修订：'+source['id']])
        auto.save_receipt(pid,'rule',before['id'],{'fingerprint':auto.rule_fingerprint(after,source),'completed':True,'state':'completed','ai_covered':True,
            'purpose':purpose,'reason':reason,'quote':text,'confidence':None,'previous_model_review':prior,'before':before,'after':after,
            'human_approval':False,'decision_method':'inspected_source_revision_sync','image_review':{'asset_sha256':loc.get('evidence_asset_sha256'),
                'coverage':loc.get('transcription_scope','complete_image_text'),'scope_limited':loc.get('transcription_scope')=='selected_complete_passages','version':VERSION}})
        db.audit(pid,'visual_reference_source_synchronized',{'before':before,'after':after,'method':'inspected_source_revision_sync','human_approval':False})
        changed.append(before['id'])
    return {'synchronized_rule_ids':changed,'skipped_rule_ids':skipped}


def asset_ids(source,seen=None):
    seen=set() if seen is None else seen
    if not source or source['id'] in seen:return set()
    seen.add(source['id']);loc=db.loads(source.get('locator'),{});assets=set()
    for key in ('asset_sha256','evidence_asset_sha256'):
        if loc.get(key):assets.add(loc[key])
    parents=list(loc.get('parent_source_ids',[]))
    if loc.get('parent_source_id'):parents.append(loc['parent_source_id'])
    if loc.get('unit_id'):
        unit=db.one('SELECT locator FROM knowledge_units WHERE id=?',(loc['unit_id'],))
        uloc=db.loads(unit['locator'],{}) if unit else {}
        if uloc.get('source_id'):parents.append(uloc['source_id'])
        parents.extend(uloc.get('source_ids',[]))
    for identity in parents:
        parent=db.one('SELECT * FROM sources WHERE id=? AND file_id=?',(identity,source['file_id']))
        assets.update(asset_ids(parent,seen))
    return assets


def validate_review(review):
    if not isinstance(review,dict) or review.get('purpose') not in PURPOSES or review.get('coverage') not in SCOPES:raise ValueError('图片复验用途或核对范围无效')
    text=review.get('text');reason=review.get('reason');sha=review.get('asset_sha256')
    if not isinstance(text,str) or not 1<=len(text.strip())<=12000 or not isinstance(reason,str) or len(reason.strip())<12:raise ValueError('缺少已核对原句或具体依据')
    if review.get('compared_original') is not True or review.get('human_approval') is not False:raise ValueError('须明确原图比较及非人工确认')
    issues=review.get('source_issues',[])
    if not isinstance(issues,list) or any(issue not in ('cropped_original',) for issue in issues):raise ValueError('原件问题类型无效')
    if (issues or asset_cropped(sha)) and review['coverage']=='complete_image_text':raise ValueError('原图存在裁断，不能声明完整图文转录')
    visual_sha=(review.get('rendered_evidence') or {}).get('image_sha256') or sha
    path=security.safe_object(visual_sha)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=visual_sha:raise ValueError('原图指纹不一致')
    return text.strip(),reason.strip(),sha


def excerpt_source(pid,source,review):
    text,reason,sha=validate_review(review)
    page=db.one('SELECT p.*,f.project_id FROM pages p JOIN files f ON f.id=p.file_id WHERE p.id=? AND p.file_id=? AND f.active=1',(source['page_id'],source['file_id']))
    if not page or page['project_id']!=pid:raise ValueError('图片不属于当前原页')
    if sha not in {v.get('sha256') for v in db.loads(page['images'],[])}:
        proof=review.get('rendered_evidence') or {}
        expected=hashlib.sha256(f"slide:{source['file_sha256']}:{page['page_number']}:office".encode()).hexdigest()
        if sha!=expected or proof.get('file_sha256')!=source['file_sha256'] or proof.get('slide_number')!=page['page_number']:raise ValueError('图片不属于当前原页，整页渲染与原件不对应')
        from .office_renderer import page_image
        original=db.one('SELECT * FROM files WHERE id=?',(source['file_id'],))
        rendered=page_image(original,page['page_number'])
        if hashlib.sha256(rendered.read_bytes()).hexdigest()!=proof.get('image_sha256'):raise ValueError('核对图片不等于当前原件的整页渲染')
    loc={'kind':'visual_rule_excerpt','page':page['page_number'],'evidence_asset_sha256':sha,'review_version':VERSION,
        'verification_method':'assistant_visual_original_comparison','human_approval':False,'read_scope':review['coverage'],
        'transcription_scope':review['coverage'],'source_instruction_authority':'project_material_not_system_instruction',
        'semantic_support':'claim_specific_check_required','native_page_context':review.get('native_page_context',''),
        'review_note_not_source_text':reason,'untranscribed_image_content_retained':review['coverage']!='complete_image_text'}
    if review.get('source_issues'):loc['source_issues']=review['source_issues']
    if review.get('rendered_evidence'):loc['rendered_evidence']=review['rendered_evidence']
    fingerprint=hashlib.sha256(db.dumps([source['file_id'],page['id'],loc,text]).encode()).hexdigest();loc['visual_review_key']=fingerprint
    old=db.one("SELECT * FROM sources WHERE file_id=? AND method='assistant_visual_excerpt' AND json_extract(locator,'$.visual_review_key')=?",(source['file_id'],fingerprint))
    if old:return old
    sid=db.uid();db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,source['file_id'],page['id'],db.dumps(loc),text,text,'rule_candidate','assistant_visual_excerpt',0))
    from .parsers import tokens
    db.execute('INSERT INTO source_fts VALUES(?,?)',(sid,tokens(text)))
    return db.one('SELECT * FROM sources WHERE id=?',(sid,))


@db.atomic
def apply(pid,review,snapshots):
    """snapshots are frozen rule/source pairs selected before image comparison."""
    text,reason,sha=validate_review(review)
    if asset_cropped(sha):review={**review,'source_issues':['cropped_original']}
    checked=[];previous_coverage={}
    for snapshot in snapshots:
        before=snapshot['rule'];expected=snapshot['source'];current=db.one('SELECT * FROM rules WHERE id=? AND project_id=?',(before['id'],pid))
        if current!=before or not editable(current):raise ValueError('图片复验期间候选被修改，未覆盖后续内容')
        source=auto.rule_source(current)
        if not source or not source['active'] or source_payload.source_hash(source)!=source_payload.source_hash(expected) or source['file_sha256']!=expected['file_sha256']:raise ValueError('原件或原文已变化')
        if sha not in asset_ids(source):raise ValueError('复验图片不是候选的原始图片')
        checked.append((before,source))
        page=db.one('SELECT * FROM pages WHERE id=?',(source['page_id'],))
        if page and page['id'] not in previous_coverage and rule_governance.page_covered(page):previous_coverage[page['id']]=db.one('SELECT * FROM rule_page_checks WHERE page_id=?',(page['id'],))
    from .rule_pipeline import exact_quote
    mapping=db.setting('document_observation_supersessions',{});fixed=[];pending=[];replacements={}
    for before,source in checked:
        key=(source['file_id'],source['page_id'])
        if key not in replacements:replacements[key]=excerpt_source(pid,source,review)
        replacement=replacements[key]
        literal=exact_quote(text,before['statement'])
        selected=(review.get('candidate_replacements') or {}).get(before['id'])
        if selected:
            if selected.get('previous_statement_hash')!=hashlib.sha256(before['statement'].encode()).hexdigest():raise ValueError('逐条修订引用的原候选指纹不一致')
            literal=exact_quote(text,selected.get('quote'))
            if not literal:raise ValueError('逐条修订引句不在已核对文字中')
        cropped='cropped_original' in review.get('source_issues',[])
        resolved=literal is not None and not cropped
        if cropped:literal=None
        note=before['decision_note']+'；原图复验：'+reason+'；核对范围：'+('图中全部文字' if review['coverage']=='complete_image_text' else '仅所列摘录，非整图转录')
        if resolved:
            db.execute("UPDATE rules SET statement=?,source_id=?,state='reference_only',modality=?,decision_note=? WHERE id=?",(literal,replacement['id'],review['purpose'] if review['purpose'] in ('example','counterexample') else 'pending',note,before['id']))
        else:
            db.execute('UPDATE rules SET decision_note=? WHERE id=?',(note+('；原图自身裁断，保留未完成原候选，缺失文字不得推测' if cropped else '；原候选尚未逐项定位，保留独立候选待完整核验'),before['id']))
        after=db.one('SELECT * FROM rules WHERE id=?',(before['id'],));now_source=auto.rule_source(after)
        outcome=('example' if review['purpose'] in ('example','counterexample') else 'conditional_reference' if review['purpose']=='conditional' else 'knowledge') if resolved else 'needs_resolution'
        rule_governance.record(pid,before,after,now_source,outcome,['助手对照原图复验（非人工确认）：'+reason,'核对范围：'+review['coverage'],'原文摘录：'+text])
        previous=auto.receipt(pid,'rule',before['id'])
        auto.save_receipt(pid,'rule',before['id'],{'fingerprint':auto.rule_fingerprint(after,now_source),'state':'completed' if resolved else 'needs_full_source','completed':resolved,'ai_covered':True,
            'purpose':review['purpose'] if resolved else 'uncertain','classification_purpose':review['purpose'],
            'content_review_state':'literal_supported' if resolved else 'source_cropped' if cropped else 'needs_original_comparison',
            'confidence':None,'quote':literal,'reason':reason if resolved or cropped else reason+'；原候选未由完整原图逐项支持；校对摘录作为并列来源保留，不替换不同提取对象',
            'verified_excerpt_source_id':replacement['id'],'decision_method':'assistant_visual_original_comparison',
            'image_review':{'asset_sha256':sha,'coverage':review['coverage'],'scope_limited':review['coverage']!='complete_image_text','version':VERSION},
            'previous_model_review':previous,'before':before,'after':after,'human_approval':False,'model':'codex_assistant'})
        db.audit(pid,'visual_rule_excerpt_adopted',{'version':VERSION,'before':before,'after':after,'review':review,'human_approval':False})
        (fixed if resolved else pending).append(before['id'])
    # Raw OCR and derived snippets remain readable in history, but are no
    # longer eligible evidence once a corrected excerpt replaces them.
    for (fid,page_id),replacement in replacements.items():
        for old in db.rows('SELECT * FROM sources WHERE file_id=? AND page_id=?',(fid,page_id)):
            if old['id']==replacement['id'] or old['method'] in ('native','native_supplement','embedded_native'):continue
            # A new partial inspection cannot erase a different previously
            # inspected passage from the same image. Keep both readable.
            if old['method']=='assistant_visual_excerpt' and review['coverage']!='complete_image_text' and not exact_quote(text,old['text']):continue
            if asset_ids(old)=={sha}:mapping[old['id']]={'replacement_source_id':replacement['id'],'asset_sha256':sha,'version':VERSION,'reason':reason,'coverage':review['coverage']}
    db.set_setting('document_observation_supersessions',mapping)
    for page_id,prior in previous_coverage.items():
        page=db.one('SELECT * FROM pages WHERE id=?',(page_id,))
        db.execute('INSERT OR REPLACE INTO rule_page_checks VALUES(?,?,?,?,?)',(page_id,rule_governance.page_hash(page),'prior_coverage_plus_inspected_visual_excerpts','machine_covered',db.now()))
        db.audit(pid,'rule_page_coverage_extended',{'page_id':page_id,'prior_check':prior,'added_reviewed_sources':[s['id'] for (fid,pid2),s in replacements.items() if pid2==page_id],
            'scope':'prior text already machine-covered; only new excerpts read and compared, not human semantic approval','version':VERSION})
    return {'fixed_rule_ids':fixed,'pending_rule_ids':pending,'replacement_source_ids':[s['id'] for s in replacements.values()],'coverage':review['coverage']}
