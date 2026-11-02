"""One canonical source map for materials, rules and literature workspaces."""
from collections import Counter,defaultdict
from fastapi import APIRouter
from . import db,security

router=APIRouter()

@router.get('/projects/{pid}/source-workspace')
@db.read_session()
def overview(pid:str):
    security.require_project(pid)
    originals=db.rows('SELECT id,project_id,name,role,extension,status,active,egress,pages,size,sha256,created_at FROM files WHERE project_id=? ORDER BY name,id',(pid,))
    uses=defaultdict(set)
    primary={'research':'research','rules':'writing_guidance','example':'example'}
    for row in db.rows('SELECT u.file_id,u.usage FROM file_usage_additions u JOIN files f ON f.id=u.file_id WHERE f.project_id=?',(pid,)):uses[row['file_id']].add(row['usage'])
    kinds={r['file_id']:r['kind'] for r in db.rows('SELECT sp.file_id,sp.kind FROM file_source_profiles sp JOIN files f ON f.id=sp.file_id WHERE f.project_id=?',(pid,))}
    books=defaultdict(list)
    for row in db.rows('SELECT id,file_id,metadata,verification FROM bibliography WHERE project_id=? ORDER BY created_at,id',(pid,)):
        meta=db.loads(row['metadata'],{});books[row['file_id']].append({'id':row['id'],'title':meta.get('title',''),'verification':row['verification']})
    from .rule_generations import visible
    rule_counts=defaultdict(Counter)
    rules=visible(pid,db.rows('SELECT r.id,r.state,r.modality,s.file_id FROM rules r LEFT JOIN sources s ON s.id=r.source_id WHERE r.project_id=?',(pid,)))
    for row in rules:
        count=rule_counts[row['file_id']];count['total']+=1
        if row['state']=='candidate':count['pending']+=1
        if row['state'] in ('confirmed','machine_verified') and row['modality'] in ('mandatory','recommended'):count['eligible']+=1
    parses={r['file_id']:r['parse_id'] for r in db.rows('SELECT h.* FROM knowledge_heads h JOIN files f ON f.id=h.file_id WHERE f.project_id=?',(pid,))}
    unit_counts={r['file_id']:r['n'] for r in db.rows("SELECT p.file_id,COUNT(*) n FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id JOIN knowledge_heads h ON h.parse_id=p.id WHERE p.project_id=? AND u.review_state!='excluded' GROUP BY p.file_id",(pid,))}
    readings=Counter(r['file_id'] for r in db.rows('SELECT file_id FROM document_readings WHERE project_id=?',(pid,)))
    wiki=defaultdict(list)
    for row in db.rows("SELECT p.id,p.slug,p.namespace,p.current_version_id,v.state FROM wiki_pages p LEFT JOIN wiki_versions v ON v.id=p.current_version_id WHERE p.project_id=? AND p.slug LIKE 'source-%'",(pid,)):
        fid=row['slug'][7:39]
        if fid:wiki[fid].append({k:row[k] for k in ('id','namespace','state','current_version_id')})
    items=[]
    for original in originals:
        fid=original['id'];role=original['role']
        if role in primary:uses[fid].add(primary[role])
        kind=kinds.get(fid) or ('course_guidance' if role=='rules' else 'research_publication' if role=='research' or books[fid] else 'writing_sample' if role=='example' else 'unclassified')
        research='research' in uses[fid] and kind=='research_publication'
        guidance=bool(uses[fid]&{'writing_guidance','grading','example'})
        items.append({**original,'usages':sorted(uses[fid]),'source_kind':kind,'research_eligible':research,'guidance_eligible':guidance,
            'references':books[fid],'rules':dict(rule_counts[fid]),'parse_id':parses.get(fid),'knowledge_units':unit_counts.get(fid,0),
            'readings':readings[fid],'wiki':wiki[fid]})
    active=[r for r in items if r['active']]
    from .summary_billing import budget
    from .expense_evidence import policy
    billing=budget(pid);strict=policy(pid)['require_pricing_basis'] and policy(pid)['block_on_unresolved']
    paid_blocked=bool(strict and billing['unknown_calls'] or billing['available']<=0)
    billing.update(paid_blocked=paid_blocked,reason=f"仍有{billing['unknown_calls']}笔调用的实际费用待核实，请先补齐费用依据" if strict and billing['unknown_calls'] else '项目可用预算不足' if billing['available']<=0 else '',profile_check_required=True)
    return {'project_id':pid,'files':items,'billing':billing,'counts':{'active_files':len(active),'research_files':sum(r['research_eligible'] for r in active),
        'rule_sources':sum(r['role']=='rules' for r in active),'references':sum(len(v) for v in books.values()),
        'unlinked_references':len(books[None]),'current_rules':len(rules),'eligible_rules':sum(c['eligible'] for c in rule_counts.values()),
        'pending_rules':sum(c['pending'] for c in rule_counts.values()),'parsed_files':sum(bool(r['parse_id']) for r in active),
        'wiki_pages':db.one('SELECT COUNT(*) n FROM wiki_pages WHERE project_id=?',(pid,))['n']},
        'manual_rule_count':rule_counts[None]['total'],'generated_at':db.now(),'source_identity':'canonical_file_id',
        'note':'资料、规则和文献共用同一原件；用途、书目核对、支持性和规则发布分别记录。'}
