from .. import db
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
