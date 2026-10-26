from .. import db,documents,parsers,security
from . import evidence
from .common import state,update_state,owned,section,digest,fail

def require_remote_content(pid,content):
    from . import evidence
    ids=set(content.get('evidence_ids',[]))
    for cid in content.get('claim_versions',[]):
        owned('claim',cid,pid);ids.update(r['evidence_id'] for r in db.rows('SELECT evidence_id FROM claim_evidence_links WHERE claim_version_id=?',(cid,)))
    for eid in ids:
        if not evidence.allowed_remote(pid,eid):fail('SOURCE_ACCESS_REVOKED','核心或章节任务的派生来源当前禁止外发',403)

def validate_graph(pid,content):
    edges=content.get('argument_edges',[]);nodes=content.get('claim_versions',[])
    for cid in nodes:owned('claim',cid,pid)
    for eid in content.get('evidence_ids',[]):owned('evidence',eid,pid)
    graph={}
    for edge in edges:
        a,b=edge.get('from'),edge.get('to')
        if a not in nodes or b not in nodes:fail('INVALID_ARGUMENT','论证边引用了未登记论断')
        if edge.get('relation') in ('supports','requires','derives'):
            if a==b:fail('ARGUMENT_CYCLE','不能用论断自身证明自身')
            graph.setdefault(a,[]).append(b)
    done=set();active=set()
    def visit(k):
        if k in active:fail('ARGUMENT_CYCLE','论证依赖存在循环')
        if k in done:return
        active.add(k)
        for n in graph.get(k,[]):visit(n)
        active.remove(k);done.add(k)
    for k in graph:visit(k)

def save_core(pid,content,parent=None):
    state(pid)
    if not isinstance(content,dict) or not str(content.get('research_question','')).strip() or len(db.dumps(content))>80000:fail('INVALID_CORE','请填写明确的研究问题')
    if parent:owned('core',parent,pid)
    for fid in content.get('fact_ids',[]):
        if not db.one('SELECT id FROM facts WHERE id=? AND project_id=?',(fid,pid)):fail('INVALID_DEPENDENCY','论文核心引用了不属于本项目的研究事实')
    for aid in content.get('artifact_ids',[]):
        if not db.one('SELECT id FROM artifacts WHERE id=? AND project_id=?',(aid,pid)):fail('INVALID_DEPENDENCY','论文核心引用了不属于本项目的研究产物')
    validate_graph(pid,content);identity=db.uid()
    db.execute('INSERT INTO thesis_core_versions VALUES(?,?,?,?,?,?)',(identity,pid,parent,db.dumps(content),digest(content),db.now()))
    return owned('core',identity,pid)

@db.atomic
def publish_core(pid,cid,expected):
    core=owned('core',cid,pid);validate_graph(pid,db.loads(core['content_json'],{}))
    current=update_state(pid,expected,core_version_id=cid)
    from .impact import changed
    changed(pid,'core',cid,'论文核心版本已发布')
    return current

@db.atomic
def save_contract(pid,sid,content,expected,publish=True):
    section(sid,pid);s=state(pid)
    if s['revision']!=expected:fail('INPUT_VERSION_CHANGED','项目状态已变更',409)
    if not isinstance(content,dict) or not str(content.get('goal','')).strip() or len(db.dumps(content))>40000:fail('INVALID_CONTRACT','请填写本节要回答的问题')
    for eid in content.get('evidence_ids',[]):owned('evidence',eid,pid)
    for fid in content.get('fact_ids',[]):
        if not db.one('SELECT id FROM facts WHERE id=? AND project_id=?',(fid,pid)):fail('INVALID_DEPENDENCY','章节任务引用了不属于本项目的研究事实')
    for aid in content.get('artifact_ids',[]):
        if not db.one('SELECT id FROM artifacts WHERE id=? AND project_id=?',(aid,pid)):fail('INVALID_DEPENDENCY','章节任务引用了不属于本项目的研究产物')
    for cid in content.get('claim_versions',[]):owned('claim',cid,pid)
    for pre in content.get('depends_on_sections',[]):
        section(pre,pid)
        if pre==sid:fail('ARGUMENT_CYCLE','章节不能依赖自身')
    identity=db.uid();parent=s['contracts'].get(sid)
    db.execute('INSERT INTO section_contract_versions VALUES(?,?,?,?,?,?,?)',(identity,pid,sid,parent,db.dumps(content),digest(content),db.now()))
    if publish:
        heads={**s['contracts'],sid:identity};graph={}
        for chapter,version in heads.items():graph[chapter]=db.loads(owned('contract',version,pid)['content_json'],{}).get('depends_on_sections',[])
        active=set();done=set()
        def visit(k):
            if k in active:fail('ARGUMENT_CYCLE','章节前置关系存在循环')
            if k in done:return
            active.add(k)
            for dep in graph.get(k,[]):visit(dep)
            active.remove(k);done.add(k)
        for chapter in graph:visit(chapter)
        update_state(pid,expected,contract_heads_json=db.dumps(heads))
        from .impact import changed
        changed(pid,'contract',identity,'章节任务已更新')
    return owned('contract',identity,pid)

def bootstrap_project(pid):
    """Build the writing baseline from the current project records.

    This is deliberately derived from the project brief, outline, facts, artifacts,
    saved paragraph links, and the latest ready repository snapshot. It does not
    invent a research question or ask the user to copy project data into a second form.
    """
    p=security.require_project(pid);head=state(pid);brief=db.loads(p['brief'],{})
    sections=db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position",(pid,))
    facts=db.rows('SELECT id FROM facts WHERE project_id=? ORDER BY created_at',(pid,))
    artifacts=db.rows('SELECT id FROM artifacts WHERE project_id=? ORDER BY created_at',(pid,))
    research_question=str(brief.get('question') or '').strip() or f'围绕“{p["title"]}”组织项目的需求、设计、实现与验证论证。'
    scope=str(brief.get('scope') or '').strip() or '依据当前项目提纲、资料、研究事实与成果组织论文论证。'
    method=str(brief.get('method') or '').strip() or '使用当前项目已登记的研究资料、事实、产物和章节正文进行有据写作。'
    limitations=brief.get('limitations',[])
    if isinstance(limitations,str):limitations=[x.strip() for x in limitations.splitlines() if x.strip()]
    if not isinstance(limitations,list):limitations=[]
    desired_core={'research_question':research_question,'scope':scope,'method':method,'limitations':limitations,'fact_ids':[x['id'] for x in facts],'artifact_ids':[x['id'] for x in artifacts]}
    current_core=db.one('SELECT * FROM thesis_core_versions WHERE id=? AND project_id=?',(head['core_version_id'],pid)) if head['core_version_id'] else None
    current_content=db.loads(current_core['content_json'],{}) if current_core else None
    if current_content!=desired_core:
        core=save_core(pid,desired_core,head['core_version_id'])
        head=publish_core(pid,core['id'],head['revision'])
    snapshots=db.rows("SELECT id FROM repository_snapshots WHERE project_id=? AND state='ready' ORDER BY created_at DESC LIMIT 1",(pid,))
    if not head['target_snapshot_id'] and snapshots:
        head=update_state(pid,head['revision'],target_snapshot_id=snapshots[0]['id'])
    from ..reference_knowledge import sync_project
    sync_project(pid)
    created=[]
    for s in sections:
        current=state(pid);existing_id=current['contracts'].get(s['id']);existing_row=db.one('SELECT content_json FROM section_contract_versions WHERE id=? AND project_id=?',(existing_id,pid)) if existing_id else None;existing_content=db.loads(existing_row['content_json'],{}) if existing_row else {}
        fact_ids=set();artifact_ids=set();source_ids=set()
        rev=documents.current(s)
        if rev:
            for node in documents.nodes(db.loads(rev['document'],{})):
                attrs=node.get('attrs',{});fact_ids.update(attrs.get('factIds',[]));artifact_id=attrs.get('artifactId')
                if artifact_id:artifact_ids.add(artifact_id)
            source_ids.update(x['upstream_id'] for x in db.rows("SELECT upstream_id FROM dependencies WHERE revision_id=? AND upstream_type='source'",(rev['id'],)))
        query=s['title']+' '+s['goal']
        from ..knowledge_service import search as knowledge_search
        hits=knowledge_search(pid,query,limit=6,remote=True)['items'] if query.strip() else []
        unit_ids=set(r['unit_id'] for r in db.rows('SELECT unit_id FROM knowledge_selections WHERE section_id=?',(s['id'],)))
        for hit in hits:
            if hit['id'].startswith('unit:'):unit_ids.add(hit['knowledge_unit_id'])
            else:source_ids.add(hit['id'])
        evidence_ids=[evidence.sync_source(pid,sid,'material_observation')['id'] for sid in sorted(source_ids)]
        evidence_ids.extend(evidence.sync_unit(pid,uid)['id'] for uid in sorted(unit_ids))
        merged={**existing_content,'goal':existing_content.get('goal') or s['goal'] or f'围绕“{s["title"]}”完成有依据的章节论证。','evidence_ids':sorted(set([*existing_content.get('evidence_ids',[]),*evidence_ids])),'fact_ids':sorted(set([*existing_content.get('fact_ids',[]),*fact_ids])),'artifact_ids':sorted(set([*existing_content.get('artifact_ids',[]),*artifact_ids])),'depends_on_sections':sorted(set([*existing_content.get('depends_on_sections',[]),*db.loads(s['dependencies'],[])]))}
        if merged!=existing_content:
            contract=save_contract(pid,s['id'],merged,current['revision']);created.append(contract['id'])
    return {'core_version_id':state(pid)['core_version_id'],'target_snapshot_id':state(pid)['target_snapshot_id'],'created_contracts':created,'section_count':len(sections)}
