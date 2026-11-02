"""Undo bulk excerpt substitutions while retaining inspected corrections as a parallel view."""
from . import db,ai_autopilot as auto,rule_governance,source_payload
from .rule_candidate_repair import editable
from .rule_pipeline import exact_quote


@db.atomic
def restore_distinct_candidates(pid):
    from .rule_generations import visible
    events=db.rows("SELECT detail FROM audit WHERE project_id=? AND action IN ('visual_rule_excerpt_adopted','visual_reference_source_synchronized') ORDER BY id",(pid,))
    first={};last={}
    for event in events:
        value=db.loads(event['detail'],{});before=value.get('before',{});after=value.get('after',{})
        if before.get('id') and after.get('id')==before['id']:first.setdefault(before['id'],before);last[before['id']]=after
    repaired=[];reopened=[];skipped=[]
    for current in visible(pid,db.rows("SELECT * FROM rules WHERE project_id=? AND state='reference_only'",(pid,))):
        rid=current['id'];original=first.get(rid)
        if not original or original['statement']==current['statement']:continue
        if current!=last[rid] or not editable(current,('reference_only',)):skipped.append(rid);continue
        inspected=auto.rule_source(current);meta=db.loads(inspected.get('locator'),{})
        if inspected['method']!='assistant_visual_excerpt':continue
        literal=exact_quote(inspected['text'],original['statement'])
        note='恢复独立提取对象；校对摘录与完整图像观察并列保存，不能用同一摘录覆盖不同条款。'
        if literal:
            db.execute('UPDATE rules SET statement=?,decision_note=? WHERE id=?',(literal,note+'原候选已逐字映射到校对文字。',rid))
        else:
            if not db.one('SELECT id FROM sources WHERE id=? AND file_id=?',(original['source_id'],inspected['file_id'])):skipped.append(rid);continue
            db.execute("UPDATE rules SET statement=?,source_id=?,state='candidate',modality=?,decision_note=? WHERE id=?",(original['statement'],original['source_id'],original['modality'],note+'原候选尚未由完整原图逐项支持，已恢复待核验。',rid))
        after=db.one('SELECT * FROM rules WHERE id=?',(rid,));source=auto.rule_source(after);prior=auto.receipt(pid,'rule',rid)
        outcome='needs_resolution' if not literal else 'example' if after['modality'] in ('example','counterexample') else 'conditional_reference'
        rule_governance.record(pid,current,after,source,outcome,[note,after['decision_note'],'并列校对来源：'+inspected['id']])
        auto.save_receipt(pid,'rule',rid,{'fingerprint':auto.rule_fingerprint(after,source),'completed':bool(literal),'ai_covered':True,
            'state':'completed' if literal else 'needs_full_source','purpose':prior.get('purpose') if literal else 'uncertain',
            'classification_purpose':prior.get('purpose'),'reason':after['decision_note'],'quote':literal,'confidence':None,
            'verified_excerpt_source_id':inspected['id'],'previous_model_review':prior,'before':current,'after':after,
            'original_candidate':original,'content_review_state':'literal_supported' if literal else 'needs_original_comparison',
            'decision_method':'restore_distinct_extraction_subject','human_approval':False})
        db.audit(pid,'restore_distinct_candidate_identity',{'before':current,'after':after,'original_candidate':original,
            'verified_excerpt_source_id':inspected['id'],'source_hash':source_payload.source_hash(source),'human_approval':False})
        (repaired if literal else reopened).append(rid)
    return {'literal_subjects_restored':repaired,'reopened_for_full_review':reopened,'skipped_changed_records':skipped}
