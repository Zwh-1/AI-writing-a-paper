import re
from . import db,documents

def run(pid):
    findings=[];project=db.one('SELECT * FROM projects WHERE id=?',(pid,))
    def add(message,result='fail',severity='warning',sid=None,rid=None,rule=None,method='deterministic',evidence=''):
        findings.append({'id':db.uid(),'project_id':pid,'section_id':sid,'revision_id':rid,'rule_id':rule,'severity':severity,'result':result,'method':method,'message':message,'evidence':evidence,'status':'open','created_at':db.now()})
    if not project['rule_package_id']: add('尚未发布论文规则包','fail','blocker')
    sections=db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position",(pid,));refs=db.rows('SELECT * FROM bibliography WHERE project_id=?',(pid,));text_all=[]
    for s in sections:
        rev=documents.current(s)
        if not rev: add('章节尚无正文',sid=s['id']);continue
        text=rev['text'];text_all.append(text)
        if s['stale']: add('需要复查：'+s['stale_reason'],sid=s['id'],rid=rev['id'])
        if re.search(r'待补|TODO|TBD|待核实|待提供',text,re.I): add('正文仍有待补或待核实内容',sid=s['id'],rid=rev['id'])
        for n in documents.nodes(db.loads(rev['document'],{})):
            if n['type']=='citation':
                ref=n.get('attrs',{}).get('citationId');r=next((x for x in refs if x['id']==ref),None)
                if not r: add('存在悬空引用','fail','blocker',s['id'],rev['id'])
        for cite in db.rows('SELECT * FROM citations WHERE revision_id=?',(rev['id'],)):
            if not db.loads(cite['source_ids'],[]): add('引用缺少具体原文依据','unknown','warning',s['id'],rev['id'],method='human')
            elif cite['support'] not in ('supported','human_confirmed'): add('引用支持性尚待核对','unknown','warning',s['id'],rev['id'],method='human')
        if re.search(r'\[\d+(?:[-,，]\d+)*\]',text) and not db.one('SELECT id FROM citations WHERE revision_id=?',(rev['id'],)):add('存在纯文本引用编号，尚未关联到文献与原文',sid=s['id'],rid=rev['id'])
        count=documents.counts(text)['total']
        if count<s['target_words']*.45: add(f'篇幅偏少：当前约{count}，目标{s["target_words"]}',sid=s['id'],rid=rev['id'])
    if not sections: add('尚未建立论文提纲','fail','blocker')
    package=db.one('SELECT * FROM rule_packages WHERE id=?',(project['rule_package_id'],)) if project['rule_package_id'] else None
    used_ids={x['reference_id'] for x in db.rows('SELECT DISTINCT c.reference_id FROM citations c JOIN sections s ON s.current_revision_id=c.revision_id WHERE s.project_id=?',(pid,))}
    unique_used=set()
    for ref in refs:
        if ref['id'] not in used_ids:continue
        meta=db.loads(ref['metadata'],{});doi=str(meta.get('doi','')).lower().removeprefix('https://doi.org/').strip();title=re.sub(r'\W+','',str(meta.get('title','')).lower())
        unique_used.add(('doi',doi) if doi else ('title',title,str(meta.get('year',''))) if title else ('id',ref['id']))
    cited_count=len(unique_used)
    from .rule_engine import evaluate,condition_matches
    for rule in db.loads(package['rules'],[]) if package else []:
        if rule['modality'] not in ('mandatory','recommended'):continue
        targets=[s for s in sections if condition_matches(rule.get('conditions',{}),{'paper_type':project['paper_type'],'section_kind':s['kind'],'operation':'generate','language':'en' if s['kind']=='abstract_en' else 'zh'})]
        for item in evaluate(rule,pid,targets,refs):add(item['message'],item['result'],item['severity'],item['section_id'],item['revision_id'],item['rule_id'],item['method'],item['evidence'])
    for r in refs:
        m=db.loads(r['metadata'],{})
        if not m.get('title') or not m.get('authors') or not m.get('year'): add('文献书目信息不完整：'+m.get('title','未命名'))
        if r['verification'] not in ('human_confirmed','metadata_matched'): add('文献元数据尚待核实：'+m.get('title','未命名'),'unknown','info',method='human')
    facts=db.rows('SELECT * FROM facts WHERE project_id=?',(pid,))
    for f in facts:
        if f['status'] in ('implemented','measured') and not db.loads(f['source_ids'],[]) and not f['artifact_id']: add('实际成果缺少材料关联：'+f['label'],'unknown','warning',method='human')
    for t in db.rows('SELECT * FROM mentor_tasks WHERE project_id=? AND status NOT IN (\'rechecked\',\'dismissed\')',(pid,)): add('导师意见尚未复查：'+t['comment'][:100],sid=t['section_id'])
    from .figure_standards import evaluate as evaluate_figures
    for item in evaluate_figures(pid)['items']:
        rev=next((s['current_revision_id'] for s in sections if s['id']==item['section_id']),None)
        add(item['message'],item['result'],item['severity'],item['section_id'],rev,method=item['method'],evidence=db.dumps({'node_id':item['node_id'],'figure_check':item['code']}))
    with db.transaction() as c:
        previous=[dict(r) for r in c.execute('SELECT * FROM findings WHERE project_id=?',(pid,)).fetchall()]
        if previous: c.execute('INSERT INTO project_revisions VALUES(?,?,?,?,?)',(db.uid(),pid,'review_history',db.dumps(previous),db.now()))
        linked={r['id'] for r in c.execute("SELECT DISTINCT f.id FROM findings f WHERE f.project_id=? AND (EXISTS(SELECT 1 FROM research_links l WHERE l.project_id=f.project_id AND l.target_type='finding' AND l.target_id=f.id) OR EXISTS(SELECT 1 FROM dependencies d WHERE d.project_id=f.project_id AND d.upstream_type='knowledge:finding' AND d.upstream_id=f.id))",(pid,))}
        for row in previous:
            if row['id'] in linked:c.execute("UPDATE findings SET status='superseded' WHERE id=?",(row['id'],))
            else:c.execute('DELETE FROM findings WHERE id=?',(row['id'],))
        for f in findings: c.execute('INSERT INTO findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',tuple(f.values()))
    from .knowledge_lineage import changed
    for identity in linked:changed(pid,'finding',identity,'检查已重新执行，旧检查意见及其复用内容需复查')
    return {'findings':findings,'summary':{'fail':sum(f['result']=='fail' for f in findings),'unknown':sum(f['result']=='unknown' for f in findings),'pass':sum(f['result']=='pass' for f in findings)}}
