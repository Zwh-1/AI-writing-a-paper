"""Evidence-scoped human precedence and explicit, append-only adjudication."""
from fastapi import HTTPException
from . import db,knowledge_lineage as lineage

STATES={'supported','partial','contradicted','insufficient','not_checked'}

def fingerprint(checks):
    return lineage.digest([{k:c[k] for k in ('id','source_version','support','method')} for c in checks])

def state(pid,cid,checks):
    valid=[];expired=[]
    for check in checks:
        typ,identity=('observation',check['observation_id']) if check['observation_id'] else ('evidence',check['evidence_id'])
        try:current=lineage.version_of(pid,typ,identity)==check['source_version']
        except HTTPException:current=False
        (valid if current else expired).append(check)
    human=[c for c in valid if c['method']=='human'];active=human or valid
    stamp=fingerprint(valid)
    decision=db.one('SELECT * FROM kg_support_decisions WHERE project_id=? AND claim_version_id=? ORDER BY rowid DESC LIMIT 1',(pid,cid))
    chosen=decision if decision and decision['assessment_fingerprint']==stamp else None
    # Different judgments on the same original version need explicit resolution.
    grouped={}
    for check in active:grouped.setdefault((check['observation_id'],check['evidence_id'],check['source_version']),set()).add(check['support'])
    conflict=any(len(values-{'not_checked'})>1 for values in grouped.values())
    supports={c['support'] for c in active}-{'not_checked'}
    if chosen:support=chosen['support']
    elif conflict or 'supported' in supports and 'contradicted' in supports:support='not_checked';conflict=True
    elif 'contradicted' in supports:support='contradicted'
    elif 'partial' in supports:support='partial'
    elif 'insufficient' in supports:support='insufficient'
    elif 'supported' in supports:support='supported'
    else:support='not_checked'
    return {'support_state':support,'human_reviewed':bool(human),'requires_adjudication':conflict and not chosen,
            'decision':chosen,'decision_basis':'explicit_human_adjudication' if chosen else 'human_assessments' if human else 'model_candidates',
            'assessment_fingerprint':stamp,'expired_assessment_ids':[c['id'] for c in expired],
            'active_assessment_ids':[c['id'] for c in active],'decision_outdated':bool(decision and not chosen)}

@db.atomic
def adjudicate(pid,cid,body):
    lineage.version_of(pid,'assertion',cid)
    checks=db.rows('SELECT * FROM kg_support_assessments WHERE project_id=? AND claim_version_id=? ORDER BY created_at,id',(pid,cid))
    current=state(pid,cid,checks)
    if body.get('expected_assessment_fingerprint')!=current['assessment_fingerprint']:lineage.fail('ASSESSMENTS_CHANGED','判断记录已变化，请重新比较')
    ids=body.get('assessment_ids',[]);reviewer=body.get('reviewer');note=body.get('note');support=body.get('support')
    if not isinstance(ids,list) or not ids or set(ids)-{c['id'] for c in checks} or not set(ids)<=set(current['active_assessment_ids']):lineage.fail('INVALID_ADJUDICATION','请选择当前有效的判断记录',422)
    if support not in STATES or not isinstance(reviewer,str) or not reviewer.strip() or not isinstance(note,str) or not note.strip() or body.get('compared_original') is not True:lineage.fail('INVALID_ADJUDICATION','裁决需要原文对照、核对人、状态及理由',422)
    identity=db.uid();db.execute('INSERT INTO kg_support_decisions VALUES(?,?,?,?,?,?,?,?,?)',(identity,pid,cid,db.dumps(ids),current['assessment_fingerprint'],support,reviewer.strip(),note.strip(),db.now()))
    lineage.changed(pid,'assertion',cid,'论断采用判断已更新，相关知识和正文需要复查')
    db.audit(pid,'knowledge_support_adjudicated',{'decision_id':identity,'claim_id':cid,'assessment_ids':ids,'reviewer':reviewer})
    return db.one('SELECT * FROM kg_support_decisions WHERE id=?',(identity,))
