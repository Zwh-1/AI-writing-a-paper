"""Local source proof, review receipt and adoption preview for duplicate records."""
import hashlib
from . import db,security,source_payload,ai_autopilot as auto

VERSION='duplicate-source-review-adoption-v2'
PREFIX='duplicate_review:'


def digest(value):return hashlib.sha256(db.dumps(value).encode()).hexdigest()
def review_key(pid,identity):return PREFIX+pid+':'+identity


def validate_ids(canonical,duplicates):
    from fastapi import HTTPException
    if not isinstance(canonical,str) or not canonical or not isinstance(duplicates,list) or not duplicates or len(duplicates)>2000 or any(not isinstance(i,str) for i in duplicates):raise HTTPException(422,'请选择保留记录和需要比较的其他记录')
    if canonical in duplicates or len(set(duplicates))!=len(duplicates):raise HTTPException(422,'比较记录不能重复或包含保留记录')


def source_snapshot(rule,file_cache):
    from .rule_pipeline import exact_quote
    from .rule_duplicates import normalize_statement
    source=auto.rule_source(rule);issues=[]
    if not source:return {'rule_id':rule['id'],'source_id':rule.get('source_id'),'issues':['来源记录不存在'],'fingerprint':digest(['missing',rule.get('source_id')])}
    if not rule.get('source_id'):
        return {'rule_id':rule['id'],'source_id':None,'file_id':None,'source_kind':'manual','method':'manual','text':rule['statement'],
            'locator':{},'issues':[],'fingerprint':digest(source),'page':None,'full_scope':True,'literal_binding':'manual_statement','human_approval':False,
            'occurrence_key':'manual:'+rule['id']}
    file=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(source['file_id'],rule['project_id']))
    if not file or not file['active']:issues.append('来源文件已停用或不属于当前项目')
    if file and file['status']!='ready':issues.append('来源文件解析未就绪')
    if file:
        if file['sha256'] not in file_cache:
            path=security.safe_object(file['sha256'])
            try:
                with path.open('rb') as stream:file_cache[file['sha256']]=hashlib.file_digest(stream,'sha256').hexdigest()==file['sha256']
            except OSError:file_cache[file['sha256']]=False
        if not file_cache[file['sha256']]:issues.append('原文件缺失或内容指纹不一致')
    loc=db.loads(source.get('locator'),{});unit=db.one('SELECT * FROM knowledge_units WHERE id=?',(loc.get('unit_id'),))
    parent=None
    if unit:
        uloc=db.loads(unit['locator'],{});parent=db.one('SELECT * FROM sources WHERE id=?',(uloc.get('source_id'),))
    origin=(unit or {}).get('origin') or source['method'];parent_loc=db.loads((parent or {}).get('locator'),{})
    coverage=loc.get('transcription_scope') or parent_loc.get('transcription_scope')
    full_scope=origin in ('pptx_slide','pdfium_text_order','ooxml_structure','native_extraction','native','native_supplement','embedded_native','tagged_table','pdf_tagged_table') or loc.get('kind')=='tagged_grade_context' or coverage=='complete_image_text'
    if not full_scope:issues.append('底层转录缺少完整校验范围')
    if coverage=='selected_complete_passages':issues.append('只核对了局部摘录')
    superseded=db.setting('document_observation_supersessions',{}).get(source['id'])
    if superseded:issues.append('当前主来源已被修订，需先核对并更新逐条对应关系')
    page=db.one('SELECT id,page_number,title,review_state,images FROM pages WHERE id=?',(source.get('page_id'),))
    if page and page['review_state']=='not_applicable':issues.append('原页已排除')
    literal_binding='literal' if exact_quote(source['text'],rule['statement']) else 'standalone_label' if rule['statement'].strip()==source['text'].strip() else 'complete_source_layout_equivalent' if normalize_statement(rule['statement'])==normalize_statement(source['text']) else 'unmatched'
    if literal_binding=='unmatched':issues.append('当前条款不能对应到主来源完整原句')
    head=db.one('SELECT parse_id FROM knowledge_heads WHERE file_id=?',(source['file_id'],))
    safe_loc={k:v for k,v in loc.items() if k in ('kind','page','pages','unit_id','parse_id','unit_hash','asset_sha256','evidence_asset_sha256','transcription_scope','read_scope','grade','score','table_row','grade_rows')}
    material={'id':source['id'],'text':source['text'],'method':source['method'],'locator_hash':digest(loc),
        'file':{k:(file or {}).get(k) for k in ('id','sha256','role','egress','active','status')},'page':page,'head':head,
        'underlying_unit':{k:(unit or {}).get(k) for k in ('id','content_hash','parse_id','origin')},'parent_hash':source_payload.source_hash(parent) if parent else None}
    uloc=db.loads((unit or {}).get('locator'),{});physical=parent_loc or uloc or loc
    if loc.get('table_row'):
        occurrence=digest([source['file_id'],'table_row',loc['table_row'],loc.get('grade')])
    else:
        occurrence=digest([source['file_id'],source.get('page_number'),physical.get('asset_sha256') or physical.get('evidence_asset_sha256'),
            physical.get('char_start'),physical.get('char_end'),physical.get('paragraph'),physical.get('unit_index'),physical.get('bbox')])
    return {'rule_id':rule['id'],'source_id':source['id'],'file_id':source['file_id'],'file_name':source['source_file'],
        'file_hash':(file or {}).get('sha256'),'source_kind':origin,'method':source['method'],'text':source['text'],'page':source.get('page_number'),
        'locator':safe_loc,'full_scope':full_scope,'literal_binding':literal_binding,'issues':issues,'fingerprint':digest(material),'human_approval':False,
        'current_parse_id':(head or {}).get('parse_id'),'image_assets':[v.get('sha256') for v in db.loads((page or {}).get('images'),[])],
        'occurrence_key':occurrence}


def examine(pid,canonical_id,duplicate_ids,items=None):
    from fastapi import HTTPException
    from . import rule_duplicates
    from .duplicate_lineage import flattened
    validate_ids(canonical_id,duplicate_ids);security.require_project(pid)
    if items is None:
        from .rule_workspace import entries
        items=entries(pid)
    lookup={r['id']:r for r in items};ids=[canonical_id,*sorted(duplicate_ids)]
    if any(i not in lookup for i in ids):raise HTTPException(422,'比较记录不属于当前有效规则代次')
    selected=[lookup[i] for i in ids];file_cache={};sources=[];checks=[];snapshots=[];seen_origins=set()
    equal_text=len({rule_duplicates.normalize_statement(r['statement']) for r in selected})==1
    equal_execution=len({rule_duplicates.execution_signature(r) for r in selected})==1
    equal_context=len({(r.get('comparison_context') or {}).get('semantic_key') for r in selected})==1
    checks.extend([
        {'key':'matching_content','passed':equal_text,'message':'原句文字一致，保留数字、单位和否定' if equal_text else '原句内容存在差异'},
        {'key':'matching_execution','passed':equal_execution,'message':'适用范围、操作和执行条件一致' if equal_execution else '适用范围或执行条件存在差异'},
        {'key':'matching_context','passed':equal_context,'message':'用途、来源政策、评分等级和完整语境一致' if equal_context else '用途、来源政策、评分等级或完整语境不同'}])
    priority=not any(r['state']=='confirmed' for r in selected[1:]) or selected[0]['state']=='confirmed'
    checks.append({'key':'retain_existing_confirmation','passed':priority,'message':'保留已有人工确认的记录' if priority else '选定主记录会丢失既有人工确认状态'})
    for rule in selected:
        raw=db.one('SELECT * FROM rules WHERE id=?',(rule['id'],));source=source_snapshot(raw,file_cache);sources.append(source)
        rec=auto.receipt(pid,'rule',rule['id']);context=rule.get('comparison_context') or {}
        blocked=list(context.get('reasons',[]))+source['issues']
        if rule['state']=='rejected':blocked.append('记录已归并或排除')
        if rule.get('text_integrity',{}).get('needs_attention'):blocked.append('条款边界或条件仍不完整')
        if rule['state']=='machine_verified':
            from .rule_governance import machine_valid
            if not machine_valid(raw):blocked.append('机器审核依据已过期')
        if rule['modality'] in ('mandatory','recommended') and rule.get('source_id') and rule.get('source_role')!='rules':blocked.append('规范条款关联的来源用途不适合启用')
        checks.append({'key':'member:'+rule['id'],'rule_id':rule['id'],'passed':not blocked,'message':'当前来源、范围与审核依据有效' if not blocked else '；'.join(dict.fromkeys(blocked)),
            'existing_review':{'state':rec.get('state'),'completed':rec.get('completed'),'current':context.get('review_current'),'method':rec.get('decision_method'),'human_approval':False}})
        prior={k:rec.get(k) for k in ('fingerprint','state','completed','purpose','quote','confidence','required_confidence','decision_method','content_review_state')}
        origins=[{'id':rule['id'],'source_id':rule.get('source_id'),'depth':0,'via_rule_ids':[rule['id']]}]+flattened(pid,rule['id'],items=items)
        lineage=[]
        for origin in origins:
            if origin['id'] not in lookup:continue
            entry=lookup[origin['id']];value=source_snapshot(db.one('SELECT * FROM rules WHERE id=?',(entry['id'],)),file_cache)
            lineage.append({'rule_id':entry['id'],'source_id':entry.get('source_id'),'source_fingerprint':value['fingerprint'],'via_rule_ids':origin.get('via_rule_ids'),'depth':origin.get('depth',0),
                'origin_changed':origin.get('origin_changed',False),'original_source_id':origin.get('original_source_id'),'original_statement':origin.get('original_statement')})
            if entry['id']!=rule['id']:
                origin_issues=value['issues']+(['历史归并记录的来源或条款已被后续改动'] if origin.get('origin_changed') else [])
                checks.append({'key':'origin:'+rule['id']+':'+entry['id'],'rule_id':entry['id'],'passed':not origin_issues,
                    'message':'历史归并来源仍完整可追溯' if not origin_issues else '历史归并来源需核对：'+'；'.join(origin_issues)})
            if entry['id'] not in seen_origins:
                seen_origins.add(entry['id'])
                if entry['id']!=rule['id']:sources.append({**value,'lineage_parent_rule_id':rule['id'],'via_rule_ids':origin.get('via_rule_ids'),'historical_merged_origin':True,
                    'origin_changed':origin.get('origin_changed',False),'historical_review_id':origin.get('historical_review_id')})
        snapshots.append({'rule':raw,'conditions':rule.get('conditions',{}),'comparison':context,'source_fingerprint':source['fingerprint'],'review':prior,'lineage':lineage})
    ready=all(c['passed'] for c in checks);canonical=selected[0]
    preview={'canonical_id':canonical_id,'canonical_state':canonical['state'],'canonical_modality':canonical['modality'],
        'canonical_purpose':(canonical.get('comparison_context') or {}).get('purpose'),
        'canonical_statement':canonical['statement'],'canonical_state_after':canonical['state'],'canonical_modality_after':canonical['modality'],
        'duplicate_ids':sorted(duplicate_ids),'duplicate_state_after':'rejected','retained_rule_occurrences':len(seen_origins),
        'distinct_source_occurrences':len({s.get('occurrence_key') or s['fingerprint'] for s in sources}),
        'distinct_source_files':len({s['file_id'] for s in sources if s.get('file_id')}),
        'effective_requirement_added':False,'requires_package_publish':canonical['state'] in ('confirmed','machine_verified') and canonical['modality'] in ('mandatory','recommended'),
        'scope_note':'仅归并重复提取记录；主记录状态、用途和条件保持原值，全部来源及恢复记录保留'}
    material={'version':VERSION,'project_id':pid,'canonical_id':canonical_id,'duplicate_ids':sorted(duplicate_ids),'members':snapshots,
        'source_policy':db.setting('rule_source_policy:'+pid,{})}
    return {'version':VERSION,'project_id':pid,'canonical_id':canonical_id,'duplicate_ids':sorted(duplicate_ids),'fingerprint':digest(material),
        'ready':ready,'sources':sources,'checks':checks,'adoption_preview':preview,'snapshots':snapshots,'review_kind':'local_structural_review','human_approval':False}


@db.atomic
def prepare(pid,canonical_id,duplicate_ids,items=None):
    value=examine(pid,canonical_id,duplicate_ids,items)
    identity=digest([value['fingerprint'],value['ready']])[:32]
    head_key='duplicate_review_head:'+pid+':'+value['fingerprint']
    current_id=db.setting(head_key,identity);stored=db.setting(review_key(pid,current_id),{})
    if stored and stored.get('fingerprint')==value['fingerprint'] and stored.get('ready')==value['ready'] and stored.get('status') in ('ready','blocked'):return stored
    if db.setting(review_key(pid,identity),{}):identity=db.uid()
    value.update(id=identity,status='ready' if value['ready'] else 'blocked',created_at=db.now())
    db.set_setting(review_key(pid,identity),value)
    db.set_setting(head_key,identity)
    db.audit(pid,'duplicate_structural_review',{'review_id':identity,'fingerprint':value['fingerprint'],'ready':value['ready'],
        'canonical_id':canonical_id,'duplicate_ids':sorted(duplicate_ids),'checks':value['checks'],'human_approval':False})
    return value


def require_current(pid,review_id,fingerprint,canonical_id,duplicate_ids,items=None):
    from fastapi import HTTPException
    prior=db.setting(review_key(pid,review_id),{})
    if not prior:raise HTTPException(404,'重复资料复核记录不存在')
    if prior.get('status')!='ready':raise HTTPException(409,'复核记录已采用或撤回，请重新复核')
    if prior.get('canonical_id')!=canonical_id or set(prior.get('duplicate_ids',[]))!=set(duplicate_ids) or prior.get('fingerprint')!=fingerprint:raise HTTPException(409,'采用对象与复核记录不一致，请重新复核')
    current=examine(pid,canonical_id,duplicate_ids,items)
    if current['fingerprint']!=prior['fingerprint']:raise HTTPException(409,'来源、原文、审核状态、条件或归并来源已变化，请重新复核')
    if not prior.get('ready') or not current['ready']:raise HTTPException(409,'复核尚未通过，不能采用归并结果')
    return prior


def mark_applied(pid,review,result,after):
    value={**review,'status':'applied','applied_at':db.now(),'result':result,'after_rows':after}
    db.set_setting(review_key(pid,review['id']),value)
    return value


@db.atomic
def adopt(pid,review_id,fingerprint):
    from fastapi import HTTPException
    from .rule_duplicates import merge
    prior=db.setting(review_key(pid,review_id),{})
    if not prior:raise HTTPException(404,'重复资料复核记录不存在')
    if prior.get('fingerprint')!=fingerprint:raise HTTPException(409,'复核指纹不一致')
    if prior.get('status')=='applied':
        if all(db.one('SELECT * FROM rules WHERE id=?',(r['id'],))==r for r in prior.get('after_rows',[])):return {**prior['result'],'already_applied':True}
        raise HTTPException(409,'采用后记录已变化，不能重复执行旧复核结果')
    if prior.get('status')!='ready':raise HTTPException(409,'该复核记录当前不可采用，请重新复核')
    return merge(pid,prior['canonical_id'],prior['duplicate_ids'],review_id=review_id,expected_fingerprint=fingerprint)
