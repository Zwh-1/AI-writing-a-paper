"""Freeze, review and atomically adopt mutually dependent candidate sections."""
from copy import deepcopy
from .. import db,documents,provider
from .common import blob,unblob,digest,fail
from . import adoption,profiles,verification,provider_adapter,prompts
from .models import Verification


def inputs(pid,items):
    if not isinstance(items,list) or not 2<=len(items)<=10:fail('INVALID_PATCH','联合复核需要2至10个不同章节候选')
    checked=[adoption.inspect(pid,item['candidate_id'],{**item['body'],'accept_policy':'draft'}) for item in items]
    if len({e[2]['id'] for e in checked})!=len(items):fail('INVALID_PATCH','联合复核不能重复修改同一章节')
    sections=[]
    for item,entry in zip(items,checked):
        c,session,s,doc,manifest,analysis=entry
        current=documents.current(s);old=db.loads(current['document'],{}) if current else None
        merged=adoption.merge_document(old,doc,item['body'])
        if digest(merged)!=digest(doc):fail('PARTIAL_REVIEW_REQUIRED','先将局部修改形成完整合并候选并完成逐句核验，再联合复核',409)
        if not analysis:fail('VERIFICATION_UNRESOLVED','联合候选必须先有逐句分析',409)
        from . import evidence
        sections.append({'candidate_id':c['id'],'section_id':s['id'],'base_revision_id':s['current_revision_id'],'candidate_hash':c['text_hash'],'analysis_id':analysis['id'],'manifest':manifest,'before_document':old,'candidate_document':doc,'judgment':unblob(c['decision_blob'])['judgment'],'evidence':[evidence.for_model(evidence.get(pid,eid,True)) for eid in manifest.get('evidence_versions',{})]})
    return {'items':deepcopy(items),'sections':sections}


def get(pid,identity):
    r=db.one('SELECT * FROM writing_joint_reviews WHERE id=? AND project_id=?',(identity,pid))
    if not r:fail('NOT_FOUND','联合复核不存在',404)
    return {**r,'input':unblob(r['input_blob']),'result':unblob(r['result_blob']) if r['result_blob'] else None}


@db.atomic
def create(pid,body):
    profile=profiles.get(pid,body['profile_id']);frozen=inputs(pid,body.get('items'))
    frozen['profile_id']=profile['id'];identity=db.uid()
    from .. import tasks
    task=tasks.enqueue(pid,'agent_joint_review',{'joint_review_id':identity})
    db.execute('INSERT INTO writing_joint_reviews VALUES(?,?,?,?,?,?,?,?)',(identity,pid,blob(frozen),digest(frozen),None,'queued',task['id'],db.now()))
    return get(pid,identity)


def current(pid,review):
    frozen=review['input'];now=inputs(pid,frozen['items']);now['profile_id']=frozen['profile_id']
    if digest(now)!=review['input_hash']:fail('INPUT_VERSION_CHANGED','联合候选、逐句分析或基础正文已变化',409)


def process(task,body):
    r=get(task['project_id'],body['joint_review_id']);current(task['project_id'],r)
    profile=profiles.get(task['project_id'],r['input']['profile_id']);sections=r['input']['sections']
    manifest={'project_id':task['project_id'],'joint_review_id':r['id'],'joint_input_hash':r['input_hash'],'stage':'joint_review'}
    payload={'scope':'必须对照全部联合候选及其原有前提，不可独立确认后拼接','sections':sections}
    # Provider adapter enforces the full wire budget. No summaries replace passages.
    system=prompts.COHERENCE+' 本次为联合候选复核。对照before_document和全部candidate_document，检查互相引用的前提、条件、定义和结论是否同时成立。返回findings、coverage_complete、summary。不足以覆盖时明确返回false。'
    try:
        result=Verification.model_validate(provider.parse_json(provider_adapter.call(task,profile,system,payload,manifest)['text'])).model_dump()
    except Exception:
        db.execute("UPDATE writing_joint_reviews SET status='failed' WHERE id=?",(r['id'],));raise
    with db.transaction():
        current(task['project_id'],r)
        db.execute("UPDATE writing_joint_reviews SET status='needs_review',result_blob=? WHERE id=?",(blob({**result,'method':'model_assisted'}),r['id']))
    return get(task['project_id'],r['id'])


@db.atomic
def human_review(pid,identity,body):
    r=get(pid,identity);current(pid,r);note=str(body.get('note','')).strip()
    if body.get('reviewed_input_hash')!=r['input_hash'] or body.get('all_candidates_reviewed') is not True or len(note)<20:fail('REVIEW_REQUIRED','需通读全部联合候选、核对版本并给出至少20字依据')
    if body.get('decision') not in ('approved','needs_revision'):fail('INVALID_REVIEW','联合复核判断无效')
    if not r['result'] or not r['result'].get('coverage_complete'):fail('COVERAGE_INCOMPLETE','联合原文尚未完整覆盖',409)
    result={**r['result'],'human_review':{'decision':body['decision'],'note':note,'input_hash':r['input_hash'],'at':db.now(),'method':'human_reviewed'}}
    db.execute('UPDATE writing_joint_reviews SET status=?,result_blob=? WHERE id=?',('human_reviewed' if body['decision']=='approved' else 'needs_revision',blob(result),identity))
    return get(pid,identity)


def require_approved(pid,identity,items):
    r=get(pid,identity);current(pid,r)
    if r['input']['items']!=items or r['status']!='human_reviewed' or r['result'].get('human_review',{}).get('decision')!='approved':fail('JOINT_REVIEW_REQUIRED','联合候选尚未完成当前版本的明确人工复核',409)
    return r


def rebind_committed(pid,results,checked):
    """Publish copied analyses against the jointly reviewed resulting versions."""
    versions={entry[2]['id']:result['revision_ids'][0] for result,entry in zip(results,checked)}
    for result in results:
        aid=result.get('analysis_id')
        if not aid:continue
        for run in verification.runs(pid,aid):
            manifest=deepcopy(run['input_manifest'])
            manifest['related_revisions']={sid:versions.get(sid,rid) for sid,rid in manifest.get('related_revisions',{}).items()}
            db.execute('UPDATE writing_verification_runs SET input_manifest_blob=? WHERE id=?',(blob(manifest),run['id']))
    for result,entry in zip(results,checked):
        rid=result['revision_ids'][0]
        for sid,old in entry[4].get('related_revisions',{}).items():
            if sid not in versions or sid==entry[2]['id']:continue
            db.execute("UPDATE object_dependencies SET upstream_id=?,upstream_version=? WHERE project_id=? AND upstream_type='revision' AND upstream_id=? AND downstream_type='revision' AND downstream_id=?",(versions[sid],versions[sid],pid,old,rid))
        # Only the causes created by this very joint commit are superseded.
        for sid,new in versions.items():
            db.execute("UPDATE writing_stale_reasons SET status='resolved',resolved_at=?,resolution_ref=? WHERE project_id=? AND section_id=? AND cause_type='revision' AND cause_id=? AND cause_version=?",(db.now(),rid,pid,entry[2]['id'],sid,new))
        from .impact import project_stale
        project_stale(entry[2]['id'])
