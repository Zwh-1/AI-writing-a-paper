"""Compare rule meaning and complete source scope before treating text as a duplicate."""
import hashlib,re,math
from . import db


def digest(value):return hashlib.sha256(db.dumps(value).encode()).hexdigest()


def enrich(pid,items):
    prefix=f'ai_autopilot:{pid}:rule:'
    receipts={r['key'][len(prefix):]:r for r in db.rows("SELECT key,json_extract(value,'$.purpose') AS purpose,json_extract(value,'$.verified_excerpt_source_id') AS excerpt_id,json_extract(value,'$.state') AS review_state,json_extract(value,'$.completed') AS completed,json_extract(value,'$.fingerprint') AS fingerprint,json_extract(value,'$.confidence') AS confidence,json_extract(value,'$.required_confidence') AS required_confidence FROM settings WHERE key LIKE ? AND json_valid(value)",(prefix+'%',))}
    verified={s['id']:s for s in db.rows("SELECT s.* FROM sources s JOIN files f ON f.id=s.file_id WHERE f.project_id=? AND s.method='assistant_visual_excerpt'",(pid,))}
    originals={}
    for row in db.rows("SELECT json_extract(detail,'$.before.id') AS rid,json_extract(detail,'$.before.statement') AS statement FROM audit WHERE project_id=? AND action IN ('visual_rule_excerpt_adopted','visual_reference_source_synchronized') ORDER BY id",(pid,)):
        if row['rid'] and row['statement']:originals.setdefault(row['rid'],row['statement'])
    policy=db.setting('rule_source_policy:'+pid,{})
    from .ai_autopilot import rule_signature
    superseded=db.setting('document_observation_supersessions',{})
    for rule in items:
        loc=db.loads(rule.get('origin_locator'),{});rec=receipts.get(rule['id'],{});purpose=rec.get('purpose')
        excerpt=verified.get(receipts.get(rule['id'],{}).get('excerpt_id'))
        if excerpt:
            emeta=db.loads(excerpt['locator'],{})
            rule['supporting_excerpt']={'source_id':excerpt['id'],'text':excerpt['text'],'asset_sha256':emeta.get('evidence_asset_sha256'),
                'coverage':emeta.get('transcription_scope'),'scope_limited':emeta.get('transcription_scope')=='selected_complete_passages'}
        if rule.get('modality') in ('example','counterexample'):purpose=rule['modality']
        if purpose in ('requirement','recommendation') or not purpose:purpose='norm_or_candidate'
        full=(rule.get('origin_text') or '').strip();meta=loc.get('observation_locator',{})
        coverage=loc.get('transcription_scope') or meta.get('transcription_scope')
        rewritten=rule['id'] in originals and originals[rule['id']].strip()!=rule['statement'].strip()
        stored_grade=loc.get('grade')
        grade=sorted(set(stored_grade if isinstance(stored_grade,list) else [stored_grade] if isinstance(stored_grade,str) and stored_grade in ('A','B','C','D') else re.findall(r'([ABCD])\s*级',full)))
        reasons=[]
        source={'id':rule.get('source_id'),'file_id':rule.get('source_file_id'),'text':rule.get('origin_text') or '',
            'method':rule.get('origin_method'),'file_sha256':rule.get('source_file_hash'),'role':rule.get('source_role'),
            'active':rule.get('source_active'),'egress':rule.get('source_egress')}
        if not rule.get('source_id'):
            source={'id':'manual:'+rule['id'],'file_id':None,'text':rule['statement'],'method':'manual','active':1,'egress':'selected_content'}
        condition={'expression':rule['condition_expression']} if rule.get('condition_expression') is not None else None
        review_current=bool(rec.get('fingerprint') and rec['fingerprint']==rule_signature(rule,source,condition,policy))
        if rule.get('source_id') and (not rule.get('source_active') or rule.get('source_status')!='ready' or rule.get('source_review_state')=='not_applicable'):reasons.append('来源已停用、未就绪或原页已排除')
        if rule.get('source_id') in superseded:reasons.append('当前记录仍指向已被修订的历史来源')
        if rec.get('review_state') in ('needs_full_source','blocked','uncertain','unknown','conflict') or rec.get('completed')==0:reasons.append('当前内容审核尚未完成，不能作为已审核重复采用')
        if rec.get('fingerprint') and not review_current and rule.get('state')!='confirmed':reasons.append('审核记录与当前条款、来源或条件不一致')
        if rec.get('completed') and not rec.get('fingerprint') and rule.get('state')!='confirmed':reasons.append('审核记录缺少当前条款与来源的对应指纹')
        score=rec.get('confidence');minimum=rec.get('required_confidence')
        if type(score) in (int,float) and type(minimum) in (int,float) and (not math.isfinite(score) or score<minimum):reasons.append('既有审核置信度未达到该条记录的要求')
        if coverage=='selected_complete_passages':reasons.append('只核对了图像摘录，缺少整图内容比较')
        if rule.get('origin_method') in ('windows_ocr','unverified_image_transcript') or rule.get('context_origin') in ('windows_ocr','unverified_image_transcript'):reasons.append('OCR观察尚未完成原图比较')
        if rewritten:reasons.append('来源修订改写了原候选，显示文字相同不证明原提取对象相同')
        reference=purpose!='norm_or_candidate'
        # Reference uses keep their whole-page/grade teaching context. Norms
        # retain existing cross-source duplicate behavior within one policy.
        images=[v.get('sha256') for v in db.loads(rule.get('context_images'),[]) if v.get('sha256')]
        context={'purpose':purpose,'authority':policy.get(rule.get('source_file_id'),'course') if rule.get('source_file_id') else 'manual',
            'source_kind':rule.get('source_kind') or ('manual' if not rule.get('source_id') else {'research':'research_publication','example':'writing_sample','rules':'course_guidance'}.get(rule.get('source_role'),'unclassified')),'grade':grade or None,
            'reference_context':digest([loc.get('native_page_context',''),full,images,
                rule.get('source_page_id') if purpose in ('heading','noise') else None]) if reference and rule.get('source_id') else None}
        rule['comparison_context']={'mergeable':not reasons,'reasons':reasons,'semantic_key':digest(context),'purpose':purpose,
            'source_id':rule.get('source_id'),'original_candidate_hash':digest(originals[rule['id']]) if rewritten else None,
            'review_current':review_current,'review_state':rec.get('review_state'),'review_completed':bool(rec.get('completed')),
            'relationship':'source_revision_or_excerpt_overlap' if reasons else 'complete_text_and_compatible_context'}
    return items
