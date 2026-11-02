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
    if doc.get('type')=='crossReference':return '[figref:'+doc.get('attrs',{}).get('targetSectionId','')+':'+doc.get('attrs',{}).get('targetId','')+']'
    if doc.get('type')=='hardBreak':return '\n'
    return ('\n' if doc.get('type') in ('doc','table','tableRow','tableCell','tableHeader','blockquote','bulletList','orderedList') else '').join(plain(c) for c in doc.get('content',[]))
def normalize(doc,depth=0):
    allowed={'doc','paragraph','heading','text','citation','blockquote','bulletList','orderedList','listItem','hardBreak','table','tableRow','tableHeader','tableCell','horizontalRule','figure','formula','footnote','crossReference'}
    if depth>30 or doc.get('type') not in allowed: raise HTTPException(422,'正文结构不支持或嵌套过深')
    result={'type':doc['type']}
    if doc['type']=='text':
        result['text']=str(doc.get('text',''))[:200000]
        marks=[m for m in doc.get('marks',[]) if m.get('type') in ('bold','italic','strike','code','underline','subscript','superscript')]
        if marks: result['marks']=marks
    else:
        attrs={k:v for k,v in doc.get('attrs',{}).items() if k in ('nodeId','level','citationId','artifactId','caption','expression','targetId','targetSectionId','colspan','rowspan','colwidth','start','sourceIds','factIds','omml','noteText','latex','tableStyle','note','diagramType','figurePurpose','explanationNodeId','describesFigureId','relatedSectionIds')};
        if doc['type']=='table':
            if attrs.get('tableStyle') not in (None,'grid','three-line'):raise HTTPException(422,'表格样式无效')
            for key in ('caption','note'):
                if key in attrs and (not isinstance(attrs[key],str) or len(attrs[key])>1500):raise HTTPException(422,'表题或表注过长')
        if doc['type']=='figure' and 'caption' in attrs and (not isinstance(attrs['caption'],str) or len(attrs['caption'])>1500):raise HTTPException(422,'图题格式无效')
        if not attrs.get('nodeId'): attrs['nodeId']=db.uid()
        if 'knowledgeUnitIds' in doc.get('attrs',{}):attrs['knowledgeUnitIds']=doc['attrs']['knowledgeUnitIds']
        if 'documentReadingIds' in doc.get('attrs',{}):attrs['documentReadingIds']=doc['attrs']['documentReadingIds']
        for extra_key in ('evidenceIds','knowledgeClaimIds','wikiVersionIds','wikiBlockIds'):
            if extra_key in doc.get('attrs',{}):attrs[extra_key]=doc['attrs'][extra_key]
        for key in ('sourceIds','factIds','evidenceIds','knowledgeUnitIds','relatedSectionIds','documentReadingIds','knowledgeClaimIds','wikiVersionIds','wikiBlockIds'):
            if key in attrs:
                if not isinstance(attrs[key],list) or len(attrs[key])>60 or any(not isinstance(x,str) for x in attrs[key]):raise HTTPException(422,'段落依据关联格式无效')
                if not attrs[key]:attrs.pop(key)
        for key in ('targetId','targetSectionId','diagramType','figurePurpose','explanationNodeId','describesFigureId'):
            if key in attrs and (not isinstance(attrs[key],str) or len(attrs[key])>100):raise HTTPException(422,'图文关联标识格式无效')
        if doc['type']=='formula' and attrs.get('omml'):
            from .ooxml_preserve import omml
            omml(attrs['omml'])
        if doc['type']=='footnote' and (not isinstance(attrs.get('noteText',''),str) or len(attrs.get('noteText',''))>10000):raise HTTPException(422,'脚注内容过长')
        result['attrs']=attrs
    if 'content' in doc: result['content']=[normalize(c,depth+1) for c in doc['content']]
    return result
def current(s): return db.one('SELECT * FROM revisions WHERE id=?',(s['current_revision_id'],)) if s['current_revision_id'] else None

def visual_paragraph(node):
    return node.get('type')=='paragraph' and (bool(node.get('attrs',{}).get('describesFigureId')) or any(n.get('type')=='crossReference' for n in nodes(node)))
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
    from .image_briefs import validate_insertions
    validate_insertions(s,doc,db.loads(old['document'],{}) if old else None)
    if old and locks:
        before={n.get('attrs',{}).get('nodeId'):n for n in nodes(db.loads(old['document'],{}))};after={n.get('attrs',{}).get('nodeId'):n for n in nodes(doc)}
        if any(before.get(k)!=after.get(k) for k in locks): raise HTTPException(409,'锁定段落发生变化，请先解锁')
    paragraph_sources={};paragraph_units={};paragraph_readings={}
    for block in nodes(doc):
        if block['type'] in ('paragraph','heading'):
            for child in nodes(block):
                if child['type']=='citation':
                    paragraph_sources[child['attrs']['nodeId']]=block.get('attrs',{}).get('sourceIds',[])
                    paragraph_units[child['attrs']['nodeId']]=block.get('attrs',{}).get('knowledgeUnitIds',[])
                    paragraph_readings[child['attrs']['nodeId']]=block.get('attrs',{}).get('documentReadingIds',[])
    rid=db.uid();proposals=citation_proposals or []
    with db.transaction() as c:
        actual=c.execute('SELECT * FROM sections WHERE id=?',(s['id'],)).fetchone()
        if actual['current_revision_id']!=base: raise HTTPException(409,{'code':'REVISION_CONFLICT','current_revision_id':actual['current_revision_id'],'message':'正文已更新，请查看差异'})
        c.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)',(rid,s['id'],base,db.dumps(doc),plain(doc),kind,task_id,db.now()))
        if base:
            for dep in c.execute('SELECT * FROM dependencies WHERE revision_id=?',(base,)).fetchall():
                if dep['upstream_type'].startswith('knowledge:'):continue
                c.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,dep['upstream_type'],dep['upstream_id'],dep['upstream_version'],s['id'],rid))
        for n in nodes(doc):
            for eid in n.get('attrs',{}).get('evidenceIds',[]):
                from .writing_agent.evidence import get as get_evidence
                from .writing_agent.common import dependency
                observed=get_evidence(pid,eid)
                c.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,'evidence',eid,observed['origin_version'],s['id'],rid))
                dependency(pid,'evidence',eid,observed['origin_version'],'revision',rid,rid)
            for field,typ in (('knowledgeClaimIds','assertion'),('wikiVersionIds','wiki_version'),('wikiBlockIds','wiki_block')):
                for identity in n.get('attrs',{}).get(field,[]):
                    from .knowledge_lineage import owned
                    knowledge=owned(pid,typ,identity)
                    from .knowledge_lineage import block_hash
                    version=block_hash(pid,knowledge) if typ=='wiki_block' else knowledge['content_hash']
                    c.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,'knowledge:'+typ,identity,version,s['id'],rid))
            for identity in n.get('attrs',{}).get('documentReadingIds',[]):
                from .document_reading import context as reading_context
                reading=reading_context(pid,identity)
                c.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,'document_reading',identity,reading['version'],s['id'],rid))
            if n['type']=='crossReference' and n.get('attrs',{}).get('targetSectionId') and not c.execute('SELECT id FROM sections WHERE id=? AND project_id=?',(n['attrs']['targetSectionId'],pid)).fetchone():raise HTTPException(422,'图表交叉引用不能指向其他项目')
            for related in n.get('attrs',{}).get('relatedSectionIds',[]):
                if not c.execute("SELECT id FROM sections WHERE id=? AND project_id=? AND state!='archived'",(related,pid)).fetchone():raise HTTPException(422,'图文关联章节不属于当前项目')
            for uid in n.get('attrs',{}).get('knowledgeUnitIds',[]):
                from .knowledge_units import get as get_unit
                unit=get_unit(pid,uid)
                c.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,'knowledge_unit',uid,unit['content_hash'],s['id'],rid))
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
                c.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,'reference',refid,str(ref['revision']),s['id'],rid))
                from .writing_agent.common import dependency
                dependency(pid,'reference',refid,str(ref['revision']),'revision',rid,rid)
                prop=next((x for x in proposals if x.get('node_id')==n['attrs']['nodeId']),None) or next((x for x in proposals if x.get('reference_id')==refid and not x.get('node_id')),{})
                prev=c.execute('SELECT * FROM citations WHERE revision_id=? AND node_id=?',(base,n['attrs']['nodeId'])).fetchone() if base else None
                source_ids=prop.get('source_ids',paragraph_sources.get(n['attrs']['nodeId']) or (db.loads(prev['source_ids'],[]) if prev else []))
                source_ids=[source_id for source_id in source_ids if c.execute('SELECT id FROM sources WHERE id=? AND file_id=?',(source_id,ref['file_id'])).fetchone()]
                for source_id in source_ids:
                    if not c.execute('SELECT s.id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(source_id,pid)).fetchone(): raise HTTPException(422,'引用来源不属于当前项目')
                citation_id=db.uid()
                c.execute('INSERT INTO citations VALUES(?,?,?,?,?,?,?,?,?)',(citation_id,rid,n['attrs']['nodeId'],refid,ref['metadata'],db.dumps(source_ids),'unknown',prop.get('kind','paraphrase'),prop.get('excerpt',prev['excerpt'] if prev else '')))
                unit_ids=prop.get('knowledge_unit_ids',paragraph_units.get(n['attrs']['nodeId']) or ([x['unit_id'] for x in c.execute('SELECT unit_id FROM citation_knowledge_links WHERE citation_id=?',(prev['id'],)).fetchall()] if prev else []))
                for uid in unit_ids:
                    from .knowledge_units import get as get_unit
                    unit=get_unit(pid,uid)
                    if unit['file_id']!=ref['file_id']:raise HTTPException(422,'知识单元与引用文献不对应')
                    old_link=c.execute('SELECT scope FROM citation_knowledge_links WHERE citation_id=? AND unit_id=?',(prev['id'],uid)).fetchone() if prev else None
                    scope=db.loads(old_link['scope'],{}) if old_link else {'parse_id':unit['parse_id'],'locator':unit['locator'],'semantic_support':'not_checked'}
                    if uid in prop.get('knowledge_read_scopes',{}):scope['read_scope']=prop['knowledge_read_scopes'][uid]
                    c.execute('INSERT INTO citation_knowledge_links VALUES(?,?,?,?)',(citation_id,uid,unit['content_hash'],db.dumps(scope)))
                reading_ids=prop.get('document_reading_ids',paragraph_readings.get(n['attrs']['nodeId']) or ([x['reading_id'] for x in c.execute('SELECT reading_id FROM citation_reading_links WHERE citation_id=?',(prev['id'],)).fetchall()] if prev else []))
                for identity in reading_ids:
                    from .document_reading import context as reading_context
                    reading=reading_context(pid,identity)
                    if reading['file_id']!=ref['file_id'] or reading['intent']!='research':raise HTTPException(422,'阅读记录与研究引用的原件或用途不对应')
                    previous=c.execute('SELECT * FROM citation_reading_links WHERE citation_id=? AND reading_id=?',(prev['id'],identity)).fetchone() if prev and not prop else None
                    c.execute('INSERT INTO citation_reading_links VALUES(?,?,?,?)',(citation_id,identity,previous['reading_version'] if previous else reading['version'],previous['read_scope'] if previous else db.dumps(reading)))
            if n['type']=='figure' and not c.execute("SELECT id FROM artifacts WHERE id=? AND project_id=? AND kind='figure' AND sha256 IS NOT NULL",(n.get('attrs',{}).get('artifactId'),pid)).fetchone(): raise HTTPException(422,'图表资源不存在或不是图片')
            if n['type']=='table' and n.get('attrs',{}).get('artifactId') and not c.execute('SELECT id FROM artifacts WHERE id=? AND project_id=? AND kind=?',(n['attrs']['artifactId'],pid,'table')).fetchone():raise HTTPException(422,'表格资源不属于当前项目')
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
    if kind=='reference':
        from .writing_agent.impact import retain_existing,add_reason
        version=db.one('SELECT revision FROM bibliography WHERE id=?',(fid,))['revision']
        for row in db.rows('SELECT DISTINCT s.id FROM sections s JOIN citations c ON c.revision_id=s.current_revision_id WHERE s.project_id=? AND c.reference_id=?',(pid,fid)):
            retain_existing(pid,row['id']);add_reason(pid,row['id'],'reference',fid,str(version),'引用文献信息或原件已更新，请核对正文与来源是否仍对应')
        db.execute("UPDATE citations SET support='unknown' WHERE reference_id=? AND revision_id IN (SELECT current_revision_id FROM sections WHERE project_id=?)",(fid,pid))
    changed(pid,kind,fid,'研究事实或有关资料已变化，请复查')
def counts(text):
    zh=len(re.findall(r'[\u4e00-\u9fff]',text));en=len(re.findall(r'\b[A-Za-z]+(?:[-\'][A-Za-z]+)*\b',text));return {'chinese':zh,'english':en,'characters':len(text),'total':zh+en}
