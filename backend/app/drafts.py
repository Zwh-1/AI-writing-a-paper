from pathlib import Path
from fastapi import HTTPException
from . import db,documents
from .security import safe_object,put_blob

def import_draft(file):
    if file['role']!='draft':raise HTTPException(422,'请先将文件用途设为“已有草稿”；示例论文不会自动成为你的正文')
    if file['status']!='ready':raise HTTPException(422,'请等待文件解析完成')
    pid=file['project_id'];groups=[];artifacts=[];warnings=['原稿中的纯文本引用编号需要在文献管理中重新关联；导入不代表研究事实已核实。']
    sources=db.rows('SELECT * FROM sources WHERE file_id=?',(file['id'],))
    def provenance(text):
        key=text.strip()[:70]
        return [s['id'] for s in sources if key and key in s['text']][:2]
    def paragraph(text,marks=None):
        n={'type':'paragraph','attrs':{'nodeId':db.uid(),'sourceIds':provenance(text)},'content':[{'type':'text','text':text}] if text else []}
        if marks and n['content']:n['content'][0]['marks']=marks
        return n
    current={'title':Path(file['name']).stem,'content':[]};groups.append(current)
    path=safe_object(file['sha256'])
    if file['extension']=='.docx':
        from docx import Document
        from docx.text.paragraph import Paragraph
        from docx.table import Table
        from docx.oxml.ns import qn
        doc=Document(str(path));picture_count=0;formula_count=0
        for child in doc.element.body:
            if child.tag==qn('w:p'):
                p=Paragraph(child,doc._body);text=p.text
                name=p.style.name if p.style else ''
                import re
                heading=re.match(r'(?:Heading|标题)\s*([1-6])',name,re.I)
                if heading and heading.group(1)=='1' and text.strip():
                    if not current['content'] and len(groups)==1:current['title']=text
                    else:current={'title':text,'content':[]};groups.append(current)
                    continue
                if text:
                    node=paragraph(text)
                    if heading:node['type']='heading';node['attrs']['level']=min(3,int(heading.group(1)))
                    elif ''.join(r.text for r in p.runs)==text:
                        node['content']=[]
                        for run in p.runs:
                            if not run.text:continue
                            item={'type':'text','text':run.text};marks=[]
                            if run.bold:marks.append({'type':'bold'})
                            if run.italic:marks.append({'type':'italic'})
                            if marks:item['marks']=marks
                            node['content'].append(item)
                    current['content'].append(node)
                for blip in child.iter(qn('a:blip')):
                    rid=blip.get(qn('r:embed'));part=doc.part.related_parts.get(rid)
                    if not part:continue
                    picture_count+=1;aid=db.uid();sha=put_blob(part.blob);name=f'原稿图 {picture_count}';artifacts.append((aid,pid,name,'figure',sha,db.dumps({'source_file_id':file['id'],'egress':file['egress'],'origin':'draft_import'}),db.now()));current['content'].append({'type':'figure','attrs':{'nodeId':db.uid(),'artifactId':aid,'caption':name}})
                for formula in child.iter(qn('m:oMath')):
                    expression=''.join(t.text or '' for t in formula.iter(qn('m:t')))
                    if expression:formula_count+=1;current['content'].append({'type':'formula','attrs':{'nodeId':db.uid(),'expression':expression}})
            elif child.tag==qn('w:tbl'):
                table=Table(child,doc._body);rows=[]
                for row in table.rows:rows.append({'type':'tableRow','content':[{'type':'tableCell','content':[paragraph(cell.text)]} for cell in row.cells]})
                if rows:current['content'].append({'type':'table','attrs':{'nodeId':db.uid()},'content':rows})
        if formula_count:warnings.append('公式已提取为可编辑数学文本；分式、矩阵与复杂符号版式需要人工复核。')
        if picture_count:warnings.append('图片按原稿位置导入，图题和编号请在编辑器核对。')
    else:
        pages=db.rows('SELECT * FROM pages WHERE file_id=? ORDER BY page_number',(file['id'],))
        text='\n'.join(p['text'] for p in pages)
        for line in text.splitlines():
            if file['extension']=='.md' and line.startswith('# '):
                if not current['content'] and len(groups)==1:current['title']=line[2:].strip()
                else:current={'title':line[2:].strip(),'content':[]};groups.append(current)
            elif line.strip():current['content'].append(paragraph(line))
        if file['extension']=='.pdf':warnings.append('PDF按已提取文本建立草稿，原PDF的复杂排版和图片未自动重建。')
    groups=[g for g in groups if g['content']]
    if not groups:raise HTTPException(422,'没有找到可以导入的正文')
    ids=[]
    with db.transaction() as c:
        duplicate=c.execute("SELECT id FROM project_revisions WHERE project_id=? AND kind='draft_import' AND json_extract(content,'$.file_id')=?",(pid,file['id'])).fetchone()
        if duplicate:raise HTTPException(409,'此文件版本已导入正文，请直接修改已有章节或导入新版本')
        position=c.execute('SELECT COALESCE(MAX(position),0) FROM sections WHERE project_id=?',(pid,)).fetchone()[0]
        for artifact in artifacts:c.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',artifact)
        for i,group in enumerate(groups,1):
            sid=db.uid();rid=db.uid();doc=documents.normalize({'type':'doc','content':group['content']});ids.append(sid)
            c.execute('INSERT INTO sections(id,project_id,title,kind,position,current_revision_id,state) VALUES(?,?,?,?,?,?,?)',(sid,pid,group['title'],'body',position+i,rid,'draft'))
            c.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)',(rid,sid,None,db.dumps(doc),documents.plain(doc),'draft_import',None,db.now()))
            used={source_id for n in documents.nodes(doc) for source_id in n.get('attrs',{}).get('sourceIds',[])}
            for source_id in used:c.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,'source',source_id,source_id,sid,rid))
        c.execute('UPDATE projects SET outline_revision=outline_revision+1,updated_at=? WHERE id=?',(db.now(),pid))
        c.execute('INSERT INTO project_revisions VALUES(?,?,?,?,?)',(db.uid(),pid,'draft_import',db.dumps({'file_id':file['id'],'section_ids':ids,'warnings':warnings}),db.now()))
    return {'section_ids':ids,'imported_sections':len(ids),'imported_images':len(artifacts),'warnings':warnings}
