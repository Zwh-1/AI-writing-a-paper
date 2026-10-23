from .. import db,documents
from .common import state,owned,blob,unblob,digest,fail
from . import sentences,verification,provider_adapter,prompts,profiles

def coverage(pid):
    head=state(pid);rows=[]
    for s in db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position",(pid,)):
        analysis=db.one('SELECT * FROM document_analyses WHERE revision_id=? ORDER BY rowid DESC LIMIT 1',(s['current_revision_id'],)) if s['current_revision_id'] else None
        runs=verification.runs(pid,analysis['id']) if analysis else []
        rows.append({'section_id':s['id'],'title':s['title'],'revision_id':s['current_revision_id'],'stale':bool(s['stale']),'analysis_id':analysis['id'] if analysis else None,'analysis_state':analysis['state'] if analysis else 'not_analyzed','coverage':sentences.get(pid,analysis['id'])['coverage'] if analysis else None,'review_runs':len(runs)})
    return {'project_state_revision':head['revision'],'core_version_id':head['core_version_id'],'target_snapshot_id':head['target_snapshot_id'],'sections':rows,'scope_complete':bool(rows and all(x['analysis_id'] and not x['stale'] for x in rows)),'guarantee':'coverage_is_not_a_truth_probability'}

def review(task,body):
    from .paper_pipeline import review as bounded_review
    return bounded_review(task,body)


def export_report(pid,strict=False):
    report=coverage(pid);problems=[]
    for s in report['sections']:
        if not s['revision_id']:problems.append({'section_id':s['section_id'],'message':'章节无正文'})
        elif s['stale'] or s['analysis_state']!='human_reviewed':problems.append({'section_id':s['section_id'],'message':'正文缺少当前人工核对或仍需复查'})
        if s['coverage'] and s['coverage']['unclassified']:problems.append({'section_id':s['section_id'],'message':'存在未分类句子'})
    analysis_rows=db.rows('SELECT a.* FROM document_analyses a JOIN sections s ON s.current_revision_id=a.revision_id WHERE s.project_id=?',(pid,));mapping=[]
    from .impact import check_manifest
    for a in analysis_rows:
        mapping.append(sentences.get(pid,a['id']))
        for run in verification.runs(pid,a['id']):
            if run['status']=='stale' or check_manifest(pid,run['input_manifest']):problems.append({'analysis_id':a['id'],'message':'句子、扩展前提或其他核验依据已变化','stage':run['stage']})
    reviews=[{**r,'result':unblob(r['result_blob'])} for r in db.rows("SELECT * FROM writing_verification_runs WHERE project_id=? AND stage='paper_review' ORDER BY created_at DESC LIMIT 5",(pid,))]
    if not reviews:problems.append({'message':'全文论证复核尚未运行'})
    else:
        current={s['section_id']:s['revision_id'] for s in report['sections'] if s['revision_id']}
        latest_manifest=unblob(reviews[0]['input_manifest_blob'])
        if reviews[0]['status']=='stale' or reviews[0]['result'].get('reviewed_sections')!=current or not reviews[0]['result'].get('coverage_complete') or check_manifest(pid,latest_manifest):problems.append({'message':'全文论证复核未覆盖当前全部版本与依据'})
        if any(f.get('severity')=='blocker' for f in reviews[0]['result'].get('findings',[])):problems.append({'message':'全文复核有未处理阻断项'})
        if reviews[0]['method']!='human_reviewed':
            profile_id=latest_manifest.get('profile_id');qualified=False
            if profile_id:
                profile=profiles.get(pid,profile_id);qualified=profile['configuration_current'] and profile['qualification']=='qualified_for_policy' and profile['role']=='verify'
            if not qualified:problems.append({'message':'全文仍需明确人工通读，或当前已校准的核验角色配置'})
    result={'coverage':report,'sentence_mapping':mapping,'paper_reviews':reviews,'unresolved':problems,'mode':'strict' if strict else 'draft'}
    if strict and problems:fail('STRICT_EXPORT_BLOCKED','严格交付仍存在未决项',409,unresolved=problems)
    return result

@db.atomic
def human_paper_review(pid,body):
    from . import evidence
    from .impact import check_manifest
    current=coverage(pid);versions={s['section_id']:s['revision_id'] for s in current['sections'] if s['revision_id']}
    note=str(body.get('note','')).strip()
    if body.get('all_sections_reviewed') is not True or len(note)<20:fail('REVIEW_REQUIRED','需要明确声明已通读全部当前章节，并给出至少20字核对依据')
    if not versions or any(not s['revision_id'] for s in current['sections']):fail('COVERAGE_INCOMPLETE','仍有未写章节，不能登记全文通读完成')
    if body.get('reviewed_revisions')!=versions:fail('INPUT_VERSION_CHANGED','正文版本已变化，请重新核对范围',409)
    head=state(pid);project=db.one('SELECT * FROM projects WHERE id=?',(pid,));ids={d['upstream_id'] for rid in versions.values() for d in db.rows("SELECT upstream_id FROM object_dependencies WHERE project_id=? AND downstream_type='revision' AND downstream_id=? AND upstream_type='evidence'",(pid,rid))}
    ee=[evidence.get(pid,eid) for eid in ids]
    manifest={'project_id':pid,'core_version_id':head['core_version_id'],'target_snapshot_id':head['target_snapshot_id'],'rule_package_id':project['rule_package_id'],'related_revisions':versions,'contract_versions':{sid:head['contracts'].get(sid) for sid in versions},'evidence_versions':{e['id']:e['origin_version'] for e in ee},'permission_stamps':{eid:evidence.permission_stamp(pid,eid) for eid in ids},'remote':False}
    if check_manifest(pid,manifest):fail('INPUT_VERSION_CHANGED','全文核对来源已变化',409)
    result={'findings':[],'coverage_complete':True,'summary':note,'reviewed_sections':versions,'coverage_report':current,'unresolved_scopes':[],'method':'human_reviewed','declaration':'用户声明已通读当前全部章节；并非程序证明全文真实','supersedes_runs':[x['id'] for x in db.rows("SELECT id FROM writing_verification_runs WHERE project_id=? AND stage='paper_review'",(pid,))]}
    identity=verification.save_run(pid,None,'paper_review',manifest,result,'human_reviewed');return {'verification_run_id':identity,**result}
