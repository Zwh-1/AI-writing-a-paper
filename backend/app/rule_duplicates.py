"""Deterministic duplicate review. Quantities and executable context remain significant."""
import hashlib,json,re,unicodedata
from fastapi import HTTPException
from . import db


def normalize_statement(text):
    text=unicodedata.normalize('NFKC',str(text))
    text=re.sub(r'[‐‑‒–—−]', '-',text).replace('。','.')
    text=' '.join(text.split())
    # Preserve word boundaries, quantities and logical punctuation. Only layout differs.
    text=re.sub(r'(?<=[\u3400-\u9fff])\s+|\s+(?=[\u3400-\u9fff])','',text)
    text=re.sub(r'\s*([,;:()\[\]/%<>=-])\s*',r'\1',text)
    return text.rstrip('.;')


def execution_signature(rule):
    def parsed(value,default=None):return db.loads(value,default) if isinstance(value,str) else value if value is not None else default
    return json.dumps({'scope':rule.get('scope','global'),'checker':rule.get('checker','manual'),'value':parsed(rule.get('value')),'category':rule.get('category','content'),'operations':sorted(parsed(rule.get('operations'),[])),'conditions':parsed(rule.get('conditions'),{})},ensure_ascii=False,sort_keys=True,separators=(',',':'))


def duplicate_key(rule):
    context=rule.get('comparison_context') or {}
    purpose=rule.get('modality') if rule.get('modality') in ('example','counterexample') else 'norm_or_candidate'
    semantic=context.get('semantic_key') or purpose
    if context.get('mergeable') is False or (rule.get('source_observation') or {}).get('scope_limited'):
        semantic+=':unresolved:'+str(rule.get('id',rule.get('source_id','')))
    return normalize_statement(rule['statement']),execution_signature(rule)+':'+semantic


def preferred(items):
    return min(items,key=lambda r:(r['state']!='confirmed',r['state']!='machine_verified',r['modality']!='mandatory',r['modality']!='recommended',r['created_at'],r['id']))


def detect(items):
    groups={}
    for rule in items:
        if rule['state']=='rejected':continue
        if (rule.get('comparison_context') or {}).get('mergeable') is False or (rule.get('source_observation') or {}).get('scope_limited'):continue
        from .rule_text_integrity import inspect_rule
        if (rule.get('text_integrity') or inspect_rule(rule)).get('needs_attention'):continue
        key=duplicate_key(rule)
        groups.setdefault(key,[]).append(rule)
    result=[]
    for key,members in groups.items():
        if len(members)<2:continue
        canonical=preferred(members)
        result.append({'id':hashlib.sha256((key[0]+key[1]).encode()).hexdigest()[:20],'kind':'exact' if len({r['statement'] for r in members})==1 else 'normalized','canonical_id':canonical['id'],'rules':members,'extra_count':len(members)-1,'review_difference':len({(r['state'],r['modality']) for r in members})>1})
    return sorted(result,key=lambda g:(-g['extra_count'],g['id']))


def text_overlaps(items):
    """Shared excerpts are useful navigation, not permission to discard records."""
    grouped={}
    for rule in items:
        if rule['state']=='rejected':continue
        key=normalize_statement(rule['statement']),execution_signature(rule)
        grouped.setdefault(key,[]).append(rule)
    result=[]
    for key,members in grouped.items():
        if len(members)<2 or len({duplicate_key(r) for r in members})==1:continue
        reasons=sorted({reason for r in members for reason in (r.get('comparison_context') or {}).get('reasons',[])})
        if not reasons:reasons=['用途、评分等级或完整来源语境不同，文字相同尚不足以判定内容重复']
        result.append({'id':hashlib.sha256(db.dumps(key).encode()).hexdigest()[:20],'kind':'text_or_source_overlap','mergeable':False,
            'reasons':reasons,'rules':members,'count':len(members)})
    return sorted(result,key=lambda g:(-g['count'],g['id']))


def collapse(items):
    groups={}
    for rule in items:groups.setdefault(duplicate_key(rule),[]).append(rule)
    result=[]
    for members in groups.values():
        rule=dict(preferred(members));rule['duplicate_count']=len(members);rule['duplicate_rule_ids']=[r['id'] for r in members]
        result.append(rule)
    return result


def history(pid):
    audits=db.rows("SELECT * FROM audit WHERE project_id=? AND action IN ('merge_duplicate_rules','restore_duplicate_rules') ORDER BY id",(pid,))
    restored={db.loads(r['detail'],{}).get('merge_id') for r in audits if r['action']=='restore_duplicate_rules'}
    return [{**db.loads(r['detail'],{}),'id':r['id'],'created_at':r['created_at'],'restored':r['id'] in restored} for r in reversed(audits) if r['action']=='merge_duplicate_rules']


@db.atomic
def merge(pid,canonical_id,duplicate_ids,review_id=None,expected_fingerprint=None):
    from .rule_workspace import entries
    if bool(review_id)!=bool(expected_fingerprint):raise HTTPException(422,'复核编号和版本指纹必须一同提供')
    if not isinstance(canonical_id,str) or not isinstance(duplicate_ids,list) or not duplicate_ids or len(duplicate_ids)>2000 or any(not isinstance(r,str) for r in duplicate_ids):raise HTTPException(422,'请选择保留条款和需要归并的重复条款')
    ids=set(duplicate_ids)
    if canonical_id in ids:raise HTTPException(422,'保留条款不能同时归并')
    items=entries(pid);lookup={r['id']:r for r in items};canonical=lookup.get(canonical_id)
    if not canonical or not ids.issubset(lookup):raise HTTPException(422,'所选条款不属于当前项目')
    selected=[canonical]+[lookup[rid] for rid in ids]
    if any((r.get('comparison_context') or {}).get('mergeable') is False or (r.get('source_observation') or {}).get('scope_limited') for r in selected):raise HTTPException(409,'相同摘录或修订来源尚不足以证明内容重复；请先核对完整来源、用途和原提取范围')
    if any(r.get('text_integrity',{}).get('needs_attention') for r in selected):raise HTTPException(409,'条款存在截断或未完整的原文条件，暂不按相同片段归并')
    if any(r['state']=='rejected' for r in selected) or any(duplicate_key(r)!=duplicate_key(canonical) for r in selected):raise HTTPException(409,'条款内容或执行范围已变化，请重新检查重复情况')
    if canonical['state']!='confirmed' and any(r['state']=='confirmed' for r in selected):raise HTTPException(422,'请保留已经确认的条款，避免丢失人工核对结果')
    from . import duplicate_workflow
    if review_id:
        proof=duplicate_workflow.require_current(pid,review_id,expected_fingerprint,canonical_id,duplicate_ids,items)
    else:
        proof=duplicate_workflow.prepare(pid,canonical_id,duplicate_ids,items)
        if not proof['ready']:raise HTTPException(409,{'message':'来源或审核依据未通过，暂不能采用归并结果','checks':[c for c in proof['checks'] if not c['passed']]})
    before=[db.one('SELECT * FROM rules WHERE id=?',(rid,)) for rid in sorted(ids)]
    note='重复条款已归并，保留：'+canonical['statement'][:120]
    with db.transaction() as c:
        for old in before:c.execute("UPDATE rules SET state='rejected',decision_note=? WHERE id=?",((old['decision_note']+'\n'+note).strip(),old['id']))
        after=[dict(c.execute('SELECT * FROM rules WHERE id=?',(r['id'],)).fetchone()) for r in before]
        detail={'canonical_id':canonical_id,'canonical_statement':canonical['statement'],'before':before,'after':after,
            'review_id':proof['id'],'review_fingerprint':proof['fingerprint'],'review_kind':proof['review_kind'],
            'source_manifest':proof['sources'],'adoption_preview':proof['adoption_preview'],'human_approval':False}
        merge_id=c.execute('INSERT INTO audit(project_id,action,detail,created_at) VALUES(?,?,?,?)',(pid,'merge_duplicate_rules',db.dumps(detail),db.now())).lastrowid
    result={'merge_id':merge_id,'canonical_id':canonical_id,'merged':len(ids),'retained_sources':len({r['source_id'] for r in proof['sources'] if r.get('source_id')}),
        'retained_occurrences':proof['adoption_preview']['retained_rule_occurrences'],'review_id':proof['id'],
        'retained_source_positions':proof['adoption_preview']['distinct_source_occurrences'],
        'canonical_state':canonical['state'],'canonical_modality':canonical['modality'],'effective_requirement_added':False}
    duplicate_workflow.mark_applied(pid,proof,result,[db.one('SELECT * FROM rules WHERE id=?',(identity,)) for identity in sorted({canonical_id,*ids})])
    return result


@db.atomic
def restore(pid,merge_id):
    event=db.one("SELECT * FROM audit WHERE id=? AND project_id=? AND action='merge_duplicate_rules'",(merge_id,pid))
    if not event:raise HTTPException(404,'归并记录不存在')
    if any(r['id']==merge_id and r['restored'] for r in history(pid)):raise HTTPException(409,'此归并已经恢复')
    detail=db.loads(event['detail'],{})
    for after in detail['after']:
        if db.one('SELECT * FROM rules WHERE id=?',(after['id'],))!=after:raise HTTPException(409,'归并后的条款已被修改，请逐条核对，避免覆盖后续操作')
    with db.transaction() as c:
        for before in detail['before']:c.execute('UPDATE rules SET state=?,decision_note=? WHERE id=?',(before['state'],before['decision_note'],before['id']))
        c.execute('INSERT INTO audit(project_id,action,detail,created_at) VALUES(?,?,?,?)',(pid,'restore_duplicate_rules',db.dumps({'merge_id':merge_id}),db.now()))
        if detail.get('review_id'):
            from .duplicate_workflow import review_key
            prior=db.setting(review_key(pid,detail['review_id']),{})
            if prior:db.set_setting(review_key(pid,detail['review_id']),{**prior,'status':'restored','restored_at':db.now()})
    return {'restored':len(detail['before'])}


def source_duplicate(pid,source_id,statement):
    key=normalize_statement(statement)
    return any(normalize_statement(r['statement'])==key for r in db.rows('SELECT statement FROM rules WHERE project_id=? AND source_id=?',(pid,source_id)))
