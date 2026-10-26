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
        c.commit()
    return {'project':project,'sections':sections,'revisions':revisions,'references':refs,'citations':cites,'artifacts':artifacts,'package':dict(package) if package else None,'settings':settings,'findings':findings,'facts':facts,'file_versions':files,'knowledge_units':knowledge_units,'knowledge_links':knowledge_links,'knowledge_claim_checks':knowledge_checks,'document_readings':reading_records,'citation_reading_links':reading_links,'material_usages':material_uses,'material_profiles':material_profiles,'reading_citation_reviews':reading_reviews,'created_at':db.now()}

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
    rfonts=normal.element.get_or_add_rPr().find(qn('w:rFonts'));add('body.east_asian_font',rfonts.get(qn('w:eastAsia')) if rfonts is not None else None,fmt['font']);add('body.line_spacing',normal.paragraph_format.line_spacing,fmt['line_spacing'],.01)
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
