import re
from fastapi import HTTPException
from . import db
def text_document(text):
    return {'type':'doc','content':[{'type':'paragraph','attrs':{'nodeId':db.uid()},'content':[{'type':'text','text':line}]} if line else {'type':'paragraph','attrs':{'nodeId':db.uid()}} for line in text.split('\n')]}
def nodes(doc):
    yield doc
    for c in doc.get('content',[]): yield from nodes(c)
def plain(doc):
    if isinstance(doc,str): doc=db.loads(doc,{})
    if doc.get('type')=='text': return doc.get('text','')
    if doc.get('type')=='citation': return '[cite:'+doc.get('attrs',{}).get('citationId','')+']'
    if doc.get('type')=='footnote': return '(脚注：'+doc.get('attrs',{}).get('noteText','')+')'
    if doc.get('type')=='figure': return '[图：'+doc.get('attrs',{}).get('caption','')+']'
    return ('\n' if doc.get('type') in ('doc','table','tableRow','blockquote','bulletList','orderedList') else '').join(plain(c) for c in doc.get('content',[]))
def normalize(doc,depth=0):
    allowed={'doc','paragraph','heading','text','citation','blockquote','bulletList','orderedList','listItem','hardBreak','table','tableRow','tableHeader','tableCell','horizontalRule','figure','formula','footnote','crossReference'}
    if depth>30 or doc.get('type') not in allowed: raise HTTPException(422,'正文结构不支持或嵌套过深')
    result={'type':doc['type']}
    if doc['type']=='text':
        result['text']=str(doc.get('text',''))[:200000]
        marks=[m for m in doc.get('marks',[]) if m.get('type') in ('bold','italic','strike','code','underline','subscript','superscript')]
        if marks: result['marks']=marks
    else:
        attrs={k:v for k,v in doc.get('attrs',{}).items() if k in ('nodeId','level','citationId','artifactId','caption','expression','targetId','colspan','rowspan','colwidth','start','sourceIds','factIds','omml','noteText','latex')};
        if not attrs.get('nodeId'): attrs['nodeId']=db.uid()
        for key in ('sourceIds','factIds'):
            if key in attrs:
                if not isinstance(attrs[key],list) or len(attrs[key])>60 or any(not isinstance(x,str) for x in attrs[key]):raise HTTPException(422,'段落依据关联格式无效')
                if not attrs[key]:attrs.pop(key)
        if doc['type']=='formula' and attrs.get('omml'):
            from .ooxml_preserve import omml
            omml(attrs['omml'])
        if doc['type']=='footnote' and (not isinstance(attrs.get('noteText',''),str) or len(attrs.get('noteText',''))>10000):raise HTTPException(422,'脚注内容过长')
        result['attrs']=attrs
    if 'content' in doc: result['content']=[normalize(c,depth+1) for c in doc['content']]
    return result
def current(s): return db.one('SELECT * FROM revisions WHERE id=?',(s['current_revision_id'],)) if s['current_revision_id'] else None
@db.atomic
def save(s,doc,base,kind='manual',task_id=None,citation_proposals=None,clear_stale=False):
    doc=normalize(doc);pid=s['project_id'];seen_ids=set()
    for node in nodes(doc):
        if node['type']=='text':continue
        identity=node.get('attrs',{}).get('nodeId')
        if identity in seen_ids:node['attrs']['nodeId']=db.uid()
        seen_ids.add(node['attrs']['nodeId'])
    if len(db.dumps(doc))>4_000_000: raise HTTPException(422,'正文太大，请拆分章节')
    old=current(s);locks={r['node_id'] for r in db.rows('SELECT node_id FROM locks WHERE section_id=?',(s['id'],))}
    if old and locks:
        before={n.get('attrs',{}).get('nodeId'):n for n in nodes(db.loads(old['document'],{}))};after={n.get('attrs',{}).get('nodeId'):n for n in nodes(doc)}
        if any(before.get(k)!=after.get(k) for k in locks): raise HTTPException(409,'锁定段落发生变化，请先解锁')
    paragraph_sources={}
    for block in nodes(doc):
        if block['type'] in ('paragraph','heading'):
            for child in nodes(block):
                if child['type']=='citation':paragraph_sources[child['attrs']['nodeId']]=block.get('attrs',{}).get('sourceIds',[])
    rid=db.uid();proposals=citation_proposals or []
    with db.transaction() as c:
        actual=c.execute('SELECT * FROM sections WHERE id=?',(s['id'],)).fetchone()
        if actual['current_revision_id']!=base: raise HTTPException(409,{'code':'REVISION_CONFLICT','current_revision_id':actual['current_revision_id'],'message':'正文已更新，请查看差异'})
        c.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)',(rid,s['id'],base,db.dumps(doc),plain(doc),kind,task_id,db.now()))
        if base:
            for dep in c.execute('SELECT * FROM dependencies WHERE revision_id=?',(base,)).fetchall():
                c.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,dep['upstream_type'],dep['upstream_id'],dep['upstream_version'],s['id'],rid))
        for n in nodes(doc):
            for source_id in n.get('attrs',{}).get('sourceIds',[]):
                if not c.execute('SELECT s.id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(source_id,pid)).fetchone():raise HTTPException(422,'段落证据不属于当前项目')
                c.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,'source',source_id,source_id,s['id'],rid))
            for fact_id in n.get('attrs',{}).get('factIds',[]):
                fact=c.execute('SELECT * FROM facts WHERE id=? AND project_id=?',(fact_id,pid)).fetchone()
                if not fact:raise HTTPException(422,'段落事实不属于当前项目')
                c.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,'fact',fact_id,str(fact['revision']),s['id'],rid))
            if n['type']=='citation':
                refid=n['attrs'].get('citationId');ref=c.execute('SELECT * FROM bibliography WHERE id=? AND project_id=?',(refid,pid)).fetchone()
                if not ref: raise HTTPException(422,'引用不存在或不属于当前项目')
                prop=next((x for x in proposals if x.get('node_id')==n['attrs']['nodeId']),None) or next((x for x in proposals if x.get('reference_id')==refid and not x.get('node_id')),{})
                prev=c.execute('SELECT * FROM citations WHERE revision_id=? AND node_id=?',(base,n['attrs']['nodeId'])).fetchone() if base else None
                source_ids=prop.get('source_ids',paragraph_sources.get(n['attrs']['nodeId']) or (db.loads(prev['source_ids'],[]) if prev else []))
                source_ids=[source_id for source_id in source_ids if c.execute('SELECT id FROM sources WHERE id=? AND file_id=?',(source_id,ref['file_id'])).fetchone()]
                for source_id in source_ids:
                    if not c.execute('SELECT s.id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(source_id,pid)).fetchone(): raise HTTPException(422,'引用来源不属于当前项目')
                c.execute('INSERT INTO citations VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),rid,n['attrs']['nodeId'],refid,ref['metadata'],db.dumps(source_ids),'unknown',prop.get('kind','paraphrase'),prop.get('excerpt',prev['excerpt'] if prev else '')))
            if n['type']=='figure' and not c.execute('SELECT id FROM artifacts WHERE id=? AND project_id=?',(n.get('attrs',{}).get('artifactId'),pid)).fetchone(): raise HTTPException(422,'图表资源不存在')
        c.execute("UPDATE sections SET current_revision_id=?,state='draft',stale=?,stale_reason=? WHERE id=?",(rid,0 if clear_stale else actual['stale'],'' if clear_stale else actual['stale_reason'],s['id']))
        c.execute('UPDATE projects SET updated_at=? WHERE id=?',(db.now(),pid))
    mark_downstream(pid,s['id'],'章节内容已变化')
    from .writing_agent.impact import revision_changed
    revision_changed(pid,s['id'],rid)
    return db.one('SELECT * FROM revisions WHERE id=?',(rid,))
def mark_downstream(pid,sid,reason):
    sections=db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived'",(pid,));impacted={sid};changed=True
    while changed:
        changed=False
        for s in sections:
            if s['id'] not in impacted and set(db.loads(s['dependencies'],[]))&impacted: impacted.add(s['id']);changed=True
    for s in sections:
        if s['id']!=sid and (s['id'] in impacted or s['kind'] in ('conclusion','abstract','abstract_en')):
            from .writing_agent.impact import retain_existing,add_reason
            retain_existing(pid,s['id']);add_reason(pid,s['id'],'revision',sid,str(db.one('SELECT current_revision_id FROM sections WHERE id=?',(sid,))['current_revision_id']),reason)
def fact_changed(pid,fid):
    from .writing_agent.impact import changed
    kind='brief' if fid=='brief' else 'file' if db.one('SELECT id FROM files WHERE id=? AND project_id=?',(fid,pid)) else 'reference' if db.one('SELECT id FROM bibliography WHERE id=? AND project_id=?',(fid,pid)) else 'fact'
    changed(pid,kind,fid,'研究事实或有关资料已变化，请复查')
def counts(text):
    zh=len(re.findall(r'[\u4e00-\u9fff]',text));en=len(re.findall(r'\b[A-Za-z]+(?:[-\'][A-Za-z]+)*\b',text));return {'chinese':zh,'english':en,'characters':len(text),'total':zh+en}
