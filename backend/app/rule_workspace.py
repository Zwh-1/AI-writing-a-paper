"""Rule review workspace and publication diagnostics shared by UI and publish gate."""
from . import db
from fastapi import HTTPException

MODALITIES={'mandatory','recommended','pending','example','counterexample'}
STATES={'candidate','confirmed','machine_verified','reference_only','rejected'}
CATEGORIES={'content','format','reference','structure'}

def validate_rule(body):
    for key,values,label in (('state',STATES,'状态'),('modality',MODALITIES,'性质'),('category',CATEGORIES,'类别')):
        if key in body and body[key] not in values:raise HTTPException(422,'规则'+label+'无效')
    if 'statement' in body and (not isinstance(body['statement'],str) or not body['statement'].strip() or len(body['statement'])>12000):raise HTTPException(422,'规则内容应为1至12000字的文字')
    if 'scope' in body and (not isinstance(body['scope'],str) or not body['scope'].strip() or len(body['scope'])>200):raise HTTPException(422,'适用范围不能为空或超过200字')

@db.read_session()
def entries(pid):
    items=db.rows('''SELECT r.*,s.text AS origin_text,s.locator AS origin_locator,s.method AS origin_method,coalesce(u.origin,s.method) AS context_origin,p.text AS origin_page_text,p.images AS context_images,f.id AS source_file_id,f.name AS source_file,f.sha256 AS source_file_hash,f.egress AS source_egress,f.status AS source_status,f.active AS source_active,f.role AS source_role,sp.kind AS source_kind,p.id AS source_page_id,p.page_number,p.review_state AS source_review_state,rc.expression AS condition_expression
        FROM rules r LEFT JOIN sources s ON s.id=r.source_id LEFT JOIN knowledge_units u ON u.id=json_extract(s.locator,'$.unit_id') LEFT JOIN files f ON f.id=s.file_id LEFT JOIN file_source_profiles sp ON sp.file_id=f.id LEFT JOIN pages p ON p.id=s.page_id LEFT JOIN rule_conditions rc ON rc.rule_id=r.id WHERE r.project_id=? ORDER BY r.created_at,r.id''',(pid,))
    from .rule_generations import visible
    items=visible(pid,items)
    from .rule_comparison import enrich
    enrich(pid,items)
    from .rule_governance import current_decisions
    decisions=current_decisions(pid)
    from .rule_text_integrity import inspect_rule,summary
    for r in items:
        locator=db.loads(r.pop('origin_locator'),{})
        if locator.get('kind')=='visual_rule_excerpt':
            limited=locator.get('transcription_scope')!='complete_image_text'
            r['source_observation']={'coverage':locator.get('transcription_scope'),'scope_limited':limited,'asset_sha256':locator.get('evidence_asset_sha256'),
                'label':'已核对所列图像摘录，非整图转录' if limited else '已对照原图转录全部文字','note':locator.get('review_note_not_source_text'),'human_approval':False}
        original={'text':r.pop('origin_text') or '', 'method':r.pop('origin_method'),'page_text':r.pop('origin_page_text') or '', 'source_file':r.get('source_file') or ''}
        r['text_integrity']=summary(inspect_rule(r,original)) if r['source_id'] else {'needs_attention':False,'status':'manual'}
        r['conditions']=db.loads(r.pop('condition_expression'),{})
        r['machine_review']=decisions.get(r['id'])
        if r['state']=='candidate':
            reasons=(r['machine_review'] or {}).get('reasons',[])
            reason=reasons[0] if reasons else r['decision_note'].split('待核对：')[-1] if '待核对：' in r['decision_note'] else r['decision_note'] or '尚无可靠的用途判定'
            kind='source_quality' if r['context_origin']=='windows_ocr' or any(word in reason for word in ('OCR','乱码','图片转录','识别残缺')) else 'context' if r['text_integrity']['needs_attention'] or any(word in reason for word in ('完整性','无法逐字定位','上下文','跨页')) else 'classification'
            r['candidate_issue']={'kind':kind,'label':{'source_quality':'原图转录待核对','context':'原文上下文待补齐','classification':'用途判定待复核'}[kind],'reason':reason}
    return items

@db.read_session()
def diagnostics(pid,items=None):
    from . import rules
    items=entries(pid) if items is None else items
    files=db.rows("SELECT f.id,f.name,f.status,f.error,f.egress,COUNT(p.id) AS pages,SUM(CASE WHEN p.review_state='reviewed' THEN 1 ELSE 0 END) AS reviewed,SUM(CASE WHEN p.review_state='not_applicable' THEN 1 ELSE 0 END) AS excluded FROM files f LEFT JOIN pages p ON p.file_id=f.id WHERE f.project_id=? AND f.role='rules' AND f.active=1 GROUP BY f.id ORDER BY f.created_at,f.id",(pid,))
    pending_pages=db.rows("SELECT p.id,p.file_id,p.page_number,p.title,f.name,f.extension,f.role FROM pages p JOIN files f ON f.id=p.file_id WHERE f.project_id=? AND f.role='rules' AND f.active=1 AND p.review_state NOT IN ('reviewed','not_applicable') ORDER BY f.created_at,p.page_number",(pid,))
    from .rule_governance import page_covered
    pending_pages=[p for p in pending_pages if not page_covered(db.one('SELECT * FROM pages WHERE id=?',(p['id'],)))]
    from .rule_governance import machine_validity
    publishable=[r for r in items if r['state'] in ('confirmed','machine_verified') and r['modality'] in ('mandatory','recommended')]
    machine_rules=[r for r in publishable if r['state']=='machine_verified']
    validity=machine_validity(pid,machine_rules)
    stale_machine=[r for r in machine_rules if not validity[r['id']]]
    invalid=[r for r in publishable if r['source_id'] and (not r['source_active'] or r['source_role']!='rules' or r['source_review_state']=='not_applicable')]
    blockers=[]
    unresolved=[r for r in items if r['state']=='candidate' and r.get('machine_review') and r['machine_review']['outcome']=='conflict']
    if unresolved:blockers.append({'code':'RULE_AUTOMATIC_CONFLICTS','message':f'{len(unresolved)}条强制要求存在已隔离冲突，请按冲突组选择适用来源','rule_ids':[r['id'] for r in unresolved]})
    if stale_machine:blockers.append({'code':'RULE_MACHINE_REVIEW_STALE','message':f'{len(stale_machine)}条机器审核依据已变更，请运行自动审核更新','rule_ids':[r['id'] for r in stale_machine]})
    unfinished=[f for f in files if f['status']!='ready']
    if unfinished:blockers.append({'code':'RULE_PARSE_INCOMPLETE','message':f'还有{len(unfinished)}份规则材料未完成解析','files':unfinished})
    if pending_pages:blockers.append({'code':'RULE_COVERAGE_INCOMPLETE','message':f'还有{len(pending_pages)}页规则材料未核对','pages':pending_pages})
    if not publishable:blockers.append({'code':'RULE_CONFIRMATION_REQUIRED','message':'自动审核通过或人工确认适用要求后即可发布'})
    if invalid:blockers.append({'code':'RULE_SOURCE_INACTIVE','message':f'{len(invalid)}条已确认规则来自已移出范围或已排除的材料页，请复核或排除这些条款','rule_ids':[r['id'] for r in invalid]})
    from .rule_duplicates import detect
    confirmed_duplicates=detect(publishable)
    if confirmed_duplicates:blockers.append({'code':'RULE_DUPLICATES','message':f'已确认规则中有{len(confirmed_duplicates)}组重复，请先归并再处理冲突','groups':len(confirmed_duplicates)})
    hard=rules.conflicts(pid,mandatory_only=True)
    if hard:blockers.append({'code':'RULE_CONFLICT','message':f'已确认强制规则仍有{len(hard)}组冲突，请先解决','conflicts':hard})
    package=db.one('SELECT * FROM rule_packages WHERE id=(SELECT rule_package_id FROM projects WHERE id=?)',(pid,))
    def signature(r):return {k:db.loads(r.get(k),[]) if k=='operations' else db.loads(r.get(k)) if k=='value' else r.get(k) for k in ('id','statement','state','category','modality','scope','checker','rule_key','value','operations','conditions','source_id','decision_note')}
    manifest=db.rows("SELECT id,sha256,egress FROM files WHERE project_id=? AND role='rules' AND active=1 ORDER BY id",(pid,))
    dirty=bool(package) and (sorted([signature(r) for r in publishable],key=lambda r:r['id'])!=sorted([signature(r) for r in db.loads(package['rules'],[])],key=lambda r:r['id']) or sorted(db.loads(package['source_manifest'],[]),key=lambda r:r['id'])!=manifest)
    return {'files':files,'pending_pages':pending_pages,'blockers':blockers,'can_publish':not blockers,'dirty':dirty,'publishable_count':len(publishable),'counts':{state:sum(r['state']==state for r in items) for state in STATES},'package_rule_count':len(db.loads(package['rules'],[])) if package else 0}

@db.read_session()
def workspace(pid):
    from . import rules,rule_engine,rule_duplicates
    items=entries(pid);duplicates=rule_duplicates.detect(items);merges=rule_duplicates.history(pid)
    from .duplicate_lineage import flattened
    lookup={r['id']:r for r in items}
    for merge in merges:
        if merge['restored'] or merge['canonical_id'] not in lookup:continue
        canonical=lookup[merge['canonical_id']]
        canonical['merged_sources']=flattened(pid,canonical['id'],items,merges)
    pending=[r['candidate_issue'] for r in items if r.get('candidate_issue')]
    from .document_coverage import project_report
    return {'rules':items,'document_coverage':project_report(pid),'text_overlaps':rule_duplicates.text_overlaps(items),
        'pending_diagnostics':{'total':len(pending),'by_kind':{kind:sum(r['kind']==kind for r in pending) for kind in ('source_quality','context','classification')}},
        'duplicates':duplicates,'duplicate_extra_count':sum(g['extra_count'] for g in duplicates),
        'duplicate_merges':[{'id':m['id'],'canonical_id':m['canonical_id'],'canonical_statement':m['canonical_statement'],'count':len(m['before']),
            'restored':m['restored'],'created_at':m['created_at'],'review_id':m.get('review_id'),
            'review_kind':m.get('review_kind'),'retained_occurrences':(m.get('adoption_preview') or {}).get('retained_rule_occurrences'),
            'retained_source_positions':(m.get('adoption_preview') or {}).get('distinct_source_occurrences')} for m in merges[:20]],
        'conflicts':rules.conflicts(pid),'package':db.one('SELECT * FROM rule_packages WHERE id=(SELECT rule_package_id FROM projects WHERE id=?)',(pid,)),
        'readiness':diagnostics(pid,items),'checkers':sorted(rule_engine.CHECKERS),'format_keys':sorted(rule_engine.FORMAT_KEYS)}
