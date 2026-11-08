from .. import db
from .common import owned,state,unblob,digest,fail

GLOBAL={'project','core','rule_package','brief'}

def reasons(pid,sid=None):
    return db.rows("SELECT * FROM writing_stale_reasons WHERE project_id=?"+(' AND section_id=?' if sid else '')+' ORDER BY created_at,id',(pid,sid) if sid else (pid,))

def project_stale(sid):
    pending=db.rows("SELECT message FROM writing_stale_reasons WHERE section_id=? AND status='open' ORDER BY created_at,id",(sid,))
    db.execute('UPDATE sections SET stale=?,stale_reason=? WHERE id=?',(int(bool(pending)),'；'.join(dict.fromkeys(r['message'] for r in pending)),sid))

def retain_existing(pid,sid):
    s=db.one('SELECT stale,stale_reason FROM sections WHERE id=? AND project_id=?',(sid,pid))
    if s and s['stale'] and not db.one("SELECT id FROM writing_stale_reasons WHERE section_id=? AND status='open'",(sid,)):
        add_reason(pid,sid,'legacy','unclassified',digest(s['stale_reason']),s['stale_reason'] or '旧正文仍需复查')

def add_reason(pid,sid,kind,identity,version,message):
    identity=str(identity);version=str(version)
    key=digest([pid,sid,kind,identity,version])
    db.execute("INSERT OR IGNORE INTO writing_stale_reasons VALUES(?,?,?,?,?,?,?,'open',?,NULL,NULL)",(key,pid,sid,kind,identity,version,message,db.now()))
    project_stale(sid)

def references(manifest):
    result=set()
    for field,kind in (('evidence_versions','evidence'),('fact_versions','fact'),('fact_revisions','fact'),('reference_versions','reference')):
        result.update((kind,str(key)) for key in manifest.get(field,{}))
    result.update(('source',str(x)) for x in manifest.get('source_ids',[]))
    result.update(('file',str(x)) for x in manifest.get('file_ids',[]))
    result.update(('knowledge_unit',str(x)) for x in manifest.get('knowledge_unit_versions',{}))
    result.update(('document_reading',str(x)) for x in manifest.get('document_reading_versions',{}))
    result.update(('section',str(x)) for x in manifest.get('related_revisions',{}))
    result.update(('revision',str(x)) for x in manifest.get('related_revisions',{}).values())
    for field,kind in (('core_version_id','core'),('contract_version_id','contract'),('target_snapshot_id','snapshot'),('rule_package_id','rule_package')):
        if manifest.get(field):result.add((kind,str(manifest[field])))
    result.update(('contract',str(v)) for v in manifest.get('contract_versions',{}).values() if v)
    return result

def descendants(pid,roots):
    reached=set(roots);pending=list(roots)
    while pending:
        kind,identity=pending.pop()
        for row in db.rows('SELECT downstream_type,downstream_id FROM object_dependencies WHERE project_id=? AND upstream_type=? AND upstream_id=?',(pid,kind,identity)):
            key=(row['downstream_type'],row['downstream_id'])
            if key not in reached:reached.add(key);pending.append(key)
    return reached

def change_roots(pid,kind,identity):
    roots={(kind,str(identity))}
    if kind=='contract':
        c=db.one('SELECT section_id,parent_id FROM section_contract_versions WHERE id=? AND project_id=?',(identity,pid))
        if c:
            roots.add(('section_contract',c['section_id']))
            if c['parent_id']:roots.add(('contract',c['parent_id']))
    if kind=='repository':
        roots.update(('snapshot',r['id']) for r in db.rows('SELECT id FROM repository_snapshots WHERE binding_id=? AND project_id=?',(identity,pid)))
    if kind=='file':roots.update(('source',r['id']) for r in db.rows('SELECT id FROM sources WHERE file_id=?',(identity,)))
    if kind=='file':roots.update(('knowledge_unit',r['id']) for r in db.rows('SELECT u.id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE p.file_id=?',(identity,)))
    if kind=='file':roots.update(('document_reading',r['id']) for r in db.rows('SELECT id FROM document_readings WHERE file_id=?',(identity,)))
    for e in db.rows('SELECT id,origin_type,origin_id FROM evidence_items WHERE project_id=?',(pid,)):
        parent=('snapshot' if e['origin_type']=='code' else e['origin_type'],e['origin_id'])
        if parent in roots:roots.add(('evidence',e['id']))
    return roots

def affected(manifest,kind,roots,reached):
    if kind in GLOBAL:return True
    if ('section_contract',str(manifest.get('section_id'))) in roots:return True
    return bool(references(manifest)&reached)

@db.atomic
def changed(pid,kind,identity,reason,version=None):
    """Propagate concrete version edges; retain conservative fallback for untracked legacy prose."""
    roots=change_roots(pid,kind,identity);reached=descendants(pid,roots)
    version=version or digest([state(pid)['revision'],kind,identity,reason,db.now()])
    impacted=set()
    for session in db.rows("SELECT * FROM writing_sessions WHERE project_id=? AND status NOT IN ('cancelled','failed')",(pid,)):
        if affected(db.loads(session['base_versions_json'],{}),kind,roots,reached):
            if session['status']!='completed':db.execute("UPDATE writing_sessions SET status='stale',updated_at=? WHERE id=?",(db.now(),session['id']))
    for run in db.rows("SELECT * FROM writing_verification_runs WHERE project_id=? AND status!='stale'",(pid,)):
        if affected(unblob(run['input_manifest_blob']),kind,roots,reached):db.execute("UPDATE writing_verification_runs SET status='stale' WHERE id=?",(run['id'],))
    for section in db.rows('SELECT * FROM sections WHERE project_id=? AND current_revision_id IS NOT NULL',(pid,)):
        rid=section['current_revision_id'];tracked=db.one("SELECT 1 FROM object_dependencies WHERE project_id=? AND downstream_type='revision' AND downstream_id=? LIMIT 1",(pid,rid))
        legacy_dependency=any((d['upstream_type'],d['upstream_id']) in reached for d in db.rows('SELECT upstream_type,upstream_id FROM dependencies WHERE revision_id=?',(rid,)))
        contract_affected=('section_contract',section['id']) in roots
        if kind in GLOBAL or ('revision',rid) in reached or section['id'] in impacted or legacy_dependency or contract_affected or not tracked:
            retain_existing(pid,section['id']);add_reason(pid,section['id'],kind,identity,version,reason);impacted.add(section['id'])
    # Existing declared chapter dependencies and summaries are also consumers.
    pending=True
    while pending:
        pending=False
        for s in db.rows('SELECT * FROM sections WHERE project_id=? AND current_revision_id IS NOT NULL',(pid,)):
            if s['id'] in impacted:continue
            if set(db.loads(s['dependencies'],[]))&impacted or (impacted and s['kind'] in ('abstract','abstract_en','conclusion')):
                retain_existing(pid,s['id']);add_reason(pid,s['id'],kind,identity,version,reason);impacted.add(s['id']);pending=True
    db.audit(pid,'writing_dependency_changed',{'type':kind,'id':identity,'reason':reason,'strategy':'version_edges_with_legacy_fallback','sections':sorted(impacted)})
    return {'section_ids':sorted(impacted),'dependency_objects':len(reached)}

def resolve_covered(pid,sid,manifest,analysis_id):
    """Clear only causes covered by this current, explicitly approved analysis."""
    if check_manifest(pid,manifest):return
    refs=references(manifest)
    for row in reasons(pid,sid):
        if row['status']!='open':continue
        kind=row['cause_type'];key=(kind,row['cause_id'])
        covered=kind in GLOBAL or key in refs
        if kind=='contract':covered=bool(manifest.get('contract_version_id'))
        if kind=='revision':covered=('section',row['cause_id']) in refs
        if covered:db.execute("UPDATE writing_stale_reasons SET status='resolved',resolved_at=?,resolution_ref=? WHERE id=?",(db.now(),analysis_id,row['id']))
    project_stale(sid)

@db.atomic
def resolve_manual(pid,sid,body):
    from .common import section
    s=section(sid,pid);ids=body.get('reason_ids',[]);note=str(body.get('note','')).strip()
    if body.get('reviewed_revision_id')!=s['current_revision_id'] or len(note)<12 or not isinstance(ids,list) or not ids:fail('REVIEW_REQUIRED','请选择原因，核对当前正文并给出至少12字处理依据',409)
    for identity in ids:
        r=db.one('SELECT * FROM writing_stale_reasons WHERE id=? AND project_id=? AND section_id=?',(identity,pid,sid))
        if not r:fail('NOT_FOUND','复查原因不属于本节',404)
        db.execute("UPDATE writing_stale_reasons SET status='resolved',resolved_at=?,resolution_ref=? WHERE id=?",(db.now(),s['current_revision_id'],identity))
    db.audit(pid,'writing_stale_resolved',{'section_id':sid,'reason_ids':ids,'revision_id':s['current_revision_id'],'note':note,'method':'human_reviewed'})
    project_stale(sid);return reasons(pid,sid)

@db.read_session()
def check_manifest(pid,manifest):
    from ..knowledge_units import cache_inputs
    # Every check is a new preflight. Share only input hashes across its reads;
    # later stages must observe source edits and fresh permission checks.
    with cache_inputs(fresh=True):
        return _check_manifest(pid,manifest)


def _check_manifest(pid,manifest):
    if manifest.get('project_memory'):
        from ..project_memory import validate_context
        from fastapi import HTTPException
        try:validate_context(pid,manifest['project_memory'])
        except HTTPException:return ['项目记忆或其来源版本、权限']
    if manifest.get('evidence_bundle'):
        from ..writing_retrieval import validate as validate_bundle
        try:validate_bundle(pid,manifest['evidence_bundle'],remote=bool(manifest.get('remote',True)))
        except __import__('fastapi').HTTPException:return ['写作素材包的来源版本、用途或权限']
    if manifest.get('live_input_versions') is not None:
        from ..writing_live import validate
        try:validate(pid,manifest,manifest.get('writing_task_id'))
        except __import__('fastapi').HTTPException:return ['项目、资料、图表或格式版本']
    from ..knowledge_units import get as get_unit
    from fastapi import HTTPException
    unit_conflicts=[]
    from ..section_materials import validate as validate_materials
    try:validate_materials(pid,manifest.get('section_materials'),remote=bool(manifest.get('remote')))
    except HTTPException:unit_conflicts.append('章节写作材料集合已变化')
    if manifest.get('resource_refs'):
        from ..resource_refs import validate as validate_resources
        try:validate_resources(pid,manifest['resource_refs'],remote=bool(manifest.get('remote')))
        except HTTPException:unit_conflicts.append('互通材料的来源版本或权限')
    if manifest.get('knowledge_refs'):
        from ..knowledge_lineage import validate_refs
        try:validate_refs(pid,manifest['knowledge_refs'],remote=bool(manifest.get('remote')))
        except HTTPException:unit_conflicts.append('图谱或Wiki依赖版本与权限')
    from ..document_reading import context as reading_context
    for identity,version in manifest.get('document_reading_versions',{}).items():
        try:
            reading=reading_context(pid,identity,remote=bool(manifest.get('remote')))
            if not reading['current'] or reading['version']!=version:unit_conflicts.append('文献阅读:'+identity)
        except HTTPException:unit_conflicts.append('文献阅读:'+identity)
    if 'knowledge_selection' in manifest and sorted(manifest['knowledge_selection'])!=sorted(r['unit_id'] for r in db.rows('SELECT unit_id FROM knowledge_selections WHERE section_id=?',(manifest.get('section_id'),))):unit_conflicts.append('章节证据选择')
    for uid,version in manifest.get('knowledge_unit_versions',{}).items():
        try:
            item=get_unit(pid,uid,remote=bool(manifest.get('remote')))
            if not item['current'] or item['content_hash']!=version or item['review_state']=='excluded':unit_conflicts.append('知识单元:'+uid)
        except HTTPException:unit_conflicts.append('知识单元:'+uid)
    for scope in manifest.get('knowledge_read_scopes',{}).values():
        for uid in scope.get('passage_ids',[]):
            try:
                item=get_unit(pid,uid,remote=bool(manifest.get('remote')))
                if not item['current'] or item['review_state']=='excluded':unit_conflicts.append('相邻原文:'+uid)
            except HTTPException:unit_conflicts.append('相邻原文:'+uid)
    from . import evidence
    from .. import security
    current=state(pid);p=security.require_project(pid);conflicts=[]
    if manifest.get('project_brief_hash') is not None:
        from .common import digest
        if digest(db.loads(p['brief'],{}))!=manifest['project_brief_hash']:conflicts.append('研究简报')
    if current['core_version_id']!=manifest.get('core_version_id'):conflicts.append('论文核心')
    if current['target_snapshot_id']!=manifest.get('target_snapshot_id'):conflicts.append('研究快照')
    if p['rule_package_id']!=manifest.get('rule_package_id'):conflicts.append('论文规则')
    sid=manifest.get('section_id')
    if manifest.get('continuity_outline_hash') is not None:
        from ..writing_continuity import outline,outline_hash
        if outline_hash(outline(pid))!=manifest['continuity_outline_hash']:conflicts.append('全篇提纲')
    if manifest.get('terminology_hash') is not None:
        from .common import digest
        if digest(db.rows('SELECT zh,en FROM terms WHERE project_id=?',(pid,)))!=manifest['terminology_hash']:conflicts.append('全文术语')
    for chapter,revision in manifest.get('empty_section_revisions',{}).items():
        row=db.one('SELECT current_revision_id FROM sections WHERE id=? AND project_id=?',(chapter,pid))
        if not row or row['current_revision_id']!=revision:conflicts.append('新增正文:'+chapter)
    if current['contracts'].get(sid)!=manifest.get('contract_version_id'):conflicts.append('章节任务')
    for chapter,version in manifest.get('contract_versions',{}).items():
        if current['contracts'].get(chapter)!=version:conflicts.append('章节任务:'+chapter)
    for eid,version in manifest.get('evidence_versions',{}).items():
        e=evidence.get(pid,eid)
        if e['origin_version']!=version or not e['version_current'] or e['validity'] not in ('current','historical'):conflicts.append('证据:'+eid)
        if manifest.get('remote') and not evidence.allowed_remote(pid,eid):conflicts.append('证据外发权限:'+eid)
        if eid in manifest.get('permission_stamps',{}) and evidence.permission_stamp(pid,eid)!=manifest['permission_stamps'][eid]:conflicts.append('证据权限版本:'+eid)
    for fid,version in manifest.get('fact_versions',{}).items():
        f=db.one('SELECT * FROM facts WHERE id=? AND project_id=?',(fid,pid))
        if not f or f['revision']!=version:conflicts.append('研究事实:'+fid)
    for rid,version in manifest.get('reference_versions',{}).items():
        r=db.one('SELECT revision FROM bibliography WHERE id=? AND project_id=?',(rid,pid))
        if not r or r['revision']!=version:conflicts.append('文献版本:'+rid)
    for chapter,revision in manifest.get('related_revisions',{}).items():
        section=db.one('SELECT current_revision_id FROM sections WHERE id=? AND project_id=?',(chapter,pid))
        if not section or section['current_revision_id']!=revision:conflicts.append('关联正文:'+chapter)
        elif manifest.get('remote'):
            from .provenance import revision_permissions
            from fastapi import HTTPException
            try:revision_permissions(pid,revision)
            except HTTPException as error:
                if error.status_code!=403:raise
                conflicts.append('关联正文外发权限:'+chapter)
    return conflicts+unit_conflicts

def revision_changed(pid,sid,new_revision):
    with db.transaction():
        db.execute("UPDATE writing_verification_runs SET status='stale' WHERE project_id=? AND stage='paper_review'",(pid,))
        for session in db.rows("SELECT * FROM writing_sessions WHERE project_id=? AND status NOT IN ('completed','cancelled','failed')",(pid,)):
            base=db.loads(session['base_versions_json'],{})
            if sid in base.get('related_revisions',{}) and base['related_revisions'][sid]!=new_revision:
                db.execute("UPDATE writing_sessions SET status='stale' WHERE id=?",(session['id'],))
                target=db.one('SELECT current_revision_id FROM sections WHERE id=?',(session['section_id'],))
                if target and target['current_revision_id']:add_reason(pid,session['section_id'],'revision',sid,new_revision,'关联章节正文已变化')
        for edge in db.rows("SELECT downstream_id FROM object_dependencies WHERE project_id=? AND upstream_type='revision' AND upstream_id IN (SELECT id FROM revisions WHERE section_id=? AND id!=?) AND downstream_type='revision'",(pid,sid,new_revision)):
            target=db.one('SELECT id FROM sections WHERE project_id=? AND current_revision_id=?',(pid,edge['downstream_id']))
            if target and target['id']!=sid:add_reason(pid,target['id'],'revision',sid,new_revision,'所用章节前提已变化')
