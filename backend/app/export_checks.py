"""Read-consistent export snapshot and concrete DOCX property checks."""
from . import db
from .security import require_project

def freeze(pid):
    with db.connect() as c:
        c.execute('BEGIN')
        project=c.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
        if not project:raise ValueError('项目不存在')
        project=dict(project);sections=[dict(x) for x in c.execute("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position",(pid,))];revisions={}
        for s in sections:
            if s['current_revision_id']:
                row=c.execute('SELECT * FROM revisions WHERE id=?',(s['current_revision_id'],)).fetchone()
                if not row:raise ValueError('章节指向不存在的正文版本')
                revisions[s['id']]=dict(row)
        refs={x['id']:dict(x) for x in c.execute('SELECT * FROM bibliography WHERE project_id=?',(pid,))}
        cites=[dict(x) for x in c.execute('SELECT c.* FROM citations c JOIN sections s ON s.current_revision_id=c.revision_id WHERE s.project_id=?',(pid,))]
        artifacts={x['id']:dict(x) for x in c.execute('SELECT * FROM artifacts WHERE project_id=?',(pid,))}
        package=c.execute('SELECT * FROM rule_packages WHERE id=?',(project['rule_package_id'],)).fetchone() if project['rule_package_id'] else None
        settings={x['key']:db.loads(x['value']) for x in c.execute('SELECT * FROM settings WHERE key IN (?,?,?)',('format:'+pid,'template:'+pid,'figure_policy:'+pid))}
        findings=[dict(x) for x in c.execute('SELECT * FROM findings WHERE project_id=?',(pid,))];facts=[dict(x) for x in c.execute('SELECT * FROM facts WHERE project_id=?',(pid,))];files=[dict(x) for x in c.execute('SELECT id,sha256,name,active,egress,role FROM files WHERE project_id=?',(pid,))]
        knowledge_links=[dict(x) for x in c.execute('SELECT l.* FROM citation_knowledge_links l JOIN citations ci ON ci.id=l.citation_id JOIN sections s ON s.current_revision_id=ci.revision_id WHERE s.project_id=?',(pid,))]
        knowledge_units={x['id']:dict(x) for x in c.execute('SELECT u.*,p.file_id,p.file_hash,p.engine,p.strategy FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE p.project_id=?',(pid,))}
        knowledge_checks=[dict(x) for x in c.execute('SELECT * FROM knowledge_claim_checks WHERE project_id=?',(pid,))]
        reading_links=[dict(x) for x in c.execute('SELECT l.* FROM citation_reading_links l JOIN citations ci ON ci.id=l.citation_id JOIN sections s ON s.current_revision_id=ci.revision_id WHERE s.project_id=?',(pid,))]
        reading_records={x['id']:dict(x) for x in c.execute('SELECT * FROM document_readings WHERE project_id=?',(pid,))}
        for identity,record in reading_records.items():
            record['passages']=[dict(x) for x in c.execute('SELECT * FROM document_reading_passages WHERE reading_id=? ORDER BY created_at,id',(identity,))]
            record['findings']=[dict(x) for x in c.execute('SELECT * FROM document_reading_findings WHERE reading_id=? ORDER BY created_at,rowid',(identity,))]
        material_uses=[dict(x) for x in c.execute('SELECT u.* FROM file_usage_additions u JOIN files f ON f.id=u.file_id WHERE f.project_id=?',(pid,))]
        material_profiles=[dict(x) for x in c.execute('SELECT sp.* FROM file_source_profiles sp JOIN files f ON f.id=sp.file_id WHERE f.project_id=?',(pid,))]
        reading_reviews=[dict(x) for x in c.execute("SELECT * FROM audit WHERE project_id=? AND action='human_knowledge_citation_review' ORDER BY id",(pid,))]
        from .documents import nodes
        evidence_ids={eid for revision in revisions.values() for node in nodes(db.loads(revision['document'],{})) for eid in node.get('attrs',{}).get('evidenceIds',[])}
        project_evidence={identity:dict(c.execute('SELECT * FROM evidence_items WHERE id=? AND project_id=?',(identity,pid)).fetchone()) for identity in evidence_ids if c.execute('SELECT id FROM evidence_items WHERE id=? AND project_id=?',(identity,pid)).fetchone()}
        graph_wiki=freeze_knowledge(c,pid,revisions)
        c.commit()
    return {'project':project,'sections':sections,'revisions':revisions,'references':refs,'citations':cites,'artifacts':artifacts,'package':dict(package) if package else None,'settings':settings,'findings':findings,'facts':facts,'file_versions':files,'knowledge_units':knowledge_units,'knowledge_links':knowledge_links,'knowledge_claim_checks':knowledge_checks,'document_readings':reading_records,'citation_reading_links':reading_links,'material_usages':material_uses,'material_profiles':material_profiles,'reading_citation_reviews':reading_reviews,'project_evidence':project_evidence,'graph_wiki':graph_wiki,'created_at':db.now()}

def freeze_knowledge(connection,pid,revisions):
    from .documents import nodes
    queue=[]
    for revision in revisions.values():
        for node in nodes(db.loads(revision['document'],{})):
            attrs=node.get('attrs',{})
            queue.extend(('assertion',cid) for cid in attrs.get('knowledgeClaimIds',[]))
            queue.extend(('wiki_version',vid) for vid in attrs.get('wikiVersionIds',[]))
            queue.extend(('wiki_block',bid) for bid in attrs.get('wikiBlockIds',[]))
        if revision.get('task_id'):
            for run in connection.execute('SELECT manifest FROM generation_runs WHERE task_id=? AND project_id=?',(revision['task_id'],pid)):
                manifest=db.loads(run['manifest'],{})
                queue.extend((r['type'],r['id']) for r in manifest.get('knowledge_refs',[]))
    tables={'assertion':('kg_assertions','claim_version_id'),'wiki_version':('wiki_versions','id'),'wiki_block':('wiki_blocks','id'),'entity_version':('kg_entity_versions','id'),'observation':('knowledge_observations','id'),'artifact':('knowledge_artifacts','id')}
    objects=[];dependencies=[];seen=set()
    while queue:
        kind,identity=queue.pop(0)
        if (kind,identity) in seen:continue
        seen.add((kind,identity))
        if len(seen)>10000:raise ValueError('知识引用追溯超过导出范围，请缩小导出范围')
        if kind not in tables:continue
        table,key=tables[kind]
        row=connection.execute('SELECT b.*,v.project_id,v.page_id FROM wiki_blocks b JOIN wiki_versions v ON v.id=b.version_id WHERE b.id=? AND v.project_id=?',(identity,pid)).fetchone() if kind=='wiki_block' else connection.execute(f'SELECT * FROM {table} WHERE {key}=? AND project_id=?',(identity,pid)).fetchone()
        if not row:raise ValueError('正文知识引用缺少所属项目的历史对象')
        item=dict(row)
        if kind=='assertion':
            claim=connection.execute('SELECT proposition_json FROM claim_versions WHERE id=?',(identity,)).fetchone();item['proposition']=db.loads(claim['proposition_json'],{})
            item['assessments']=[dict(r) for r in connection.execute('SELECT * FROM kg_support_assessments WHERE claim_version_id=?',(identity,))]
        if kind=='wiki_version':
            item['blocks']=[dict(r) for r in connection.execute('SELECT * FROM wiki_blocks WHERE version_id=? ORDER BY position',(identity,))]
        objects.append({'type':kind,'id':identity,'snapshot':item})
        if kind=='wiki_block':
            item['sources']=[dict(r) for r in connection.execute('SELECT * FROM wiki_links WHERE block_id=?',(identity,))]
            queue.extend((r['target_type'],r['target_id']) for r in item['sources'])
        for dependency in connection.execute('SELECT * FROM knowledge_dependencies WHERE project_id=? AND downstream_type=? AND downstream_id=?',(pid,kind,identity)):
            d=dict(dependency);dependencies.append(d);queue.append((d['upstream_type'],d['upstream_id']))
    return {'format':'knowledge-writing-trace.v1','objects':objects,'dependencies':dependencies,'complete':True,'note':'与导出正文同一只读事务的知识历史快照，不自动证明支持性'}

def check_docx(path,fmt,section_index=None):
    from docx import Document
    from docx.oxml.ns import qn
    doc=Document(path);result=[]
    def add(key,actual,expected,tolerance=None):
        equal=abs(float(actual)-float(expected))<=tolerance if tolerance is not None and actual is not None else actual==expected
        result.append({'key':key,'actual':actual,'expected':expected,'result':'pass' if equal else 'fail','method':'docx_property'})
    for i,sec in enumerate(doc.sections):
        if section_index is not None and i!=section_index:continue
        add(f'section_{i+1}.width_mm',sec.page_width.mm,210,.1);add(f'section_{i+1}.height_mm',sec.page_height.mm,297,.1)
        for side in ('top','bottom','left','right'):add(f'section_{i+1}.margin_{side}_mm',getattr(sec,side+'_margin').mm,fmt['margin_'+side],.1)
    normal=doc.styles['Normal'];add('body.font_size_pt',normal.font.size.pt if normal.font.size else None,fmt['font_size'],.01);add('body.latin_font',normal.font.name,fmt['latin_font'])
    rfonts=normal.element.get_or_add_rPr().find(qn('w:rFonts'));add('body.east_asian_font',rfonts.get(qn('w:eastAsia')) if rfonts is not None else None,fmt['font'])
    if fmt.get('line_spacing_points'):add('body.line_spacing_pt',getattr(normal.paragraph_format.line_spacing,'pt',None),fmt['line_spacing_points'],.01)
    else:add('body.line_spacing',normal.paragraph_format.line_spacing,fmt['line_spacing'],.01)
    ind=normal.element.get_or_add_pPr().find(qn('w:ind'));actual=float(ind.get(qn('w:firstLineChars')))/100 if ind is not None and ind.get(qn('w:firstLineChars')) else None;add('body.first_line_chars',actual,fmt['first_line_chars'],.01)
    return {'checks':result,'pass':sum(x['result']=='pass' for x in result),'fail':sum(x['result']=='fail' for x in result),'visual_verification':'unknown','page_count_verification':'unknown'}

def markdown_document(doc,figure_rows=None,section_id=None):
    from .figure_standards import resolve_reference
    figure_rows=figure_rows or []
    def inline(node):
        kind=node['type'];attrs=node.get('attrs',{})
        if kind=='text':
            text=node.get('text','');marks={x['type'] for x in node.get('marks',[])}
            if 'bold' in marks:text='**'+text+'**'
            if 'italic' in marks:text='*'+text+'*'
            return text
        if kind=='citation':return '[cite:'+attrs.get('citationId','')+']'
        if kind=='hardBreak':return '  \n'
        if kind=='crossReference':
            target=resolve_reference(figure_rows,attrs,section_id)
            return target['label'] if target else '【图表引用失效】'
        return ''.join(inline(x) for x in node.get('content',[]))
    def block(n,depth=0):
        kind=n['type'];attrs=n.get('attrs',{})
        if kind=='heading':return '#'*min(int(attrs.get('level',2))+1,6)+' '+inline(n)
        if kind=='paragraph':return inline(n)
        if kind=='formula':return '$$\n'+attrs.get('expression','')+'\n$$'
        if kind=='figure':
            row=next((r for r in figure_rows if r['section_id']==section_id and r['node_id']==attrs.get('nodeId')),None)
            title=((row['label']+' ') if row else '')+attrs.get('caption','图表')
            return '!['+title+'](图-'+attrs.get('artifactId','')+'.png)'+('\n\n'+title if row else '')
        if kind=='blockquote':return '\n'.join('> '+line for line in inline(n).splitlines())
        if kind=='table':
            rows=[[' '.join(inline(c).splitlines()).replace('|','\\|') for c in row.get('content',[])] for row in n.get('content',[])]
            if not rows:return ''
            cols=max(map(len,rows));rows=[row+['']*(cols-len(row)) for row in rows];out=['| '+' | '.join(rows[0])+' |','| '+' | '.join(['---']*cols)+' |'];out+=['| '+' | '.join(row)+' |' for row in rows[1:]]
            row=next((r for r in figure_rows if r['section_id']==section_id and r['node_id']==attrs.get('nodeId')),None)
            if attrs.get('caption') or row:
                out=[((row['label']+' ') if row else '')+(attrs.get('caption') or '【表题待补】'),'']+out
            if attrs.get('note'):out+=['','注：'+attrs['note']]
            return '\n'.join(out)
        if kind in ('bulletList','orderedList'):
            return '\n'.join(('  '*depth+(str(i+1)+'. ' if kind=='orderedList' else '- ')+block(x,depth+1).strip()) for i,x in enumerate(n.get('content',[])))
        return '\n\n'.join(block(x,depth) for x in n.get('content',[]))
    return block(doc)
