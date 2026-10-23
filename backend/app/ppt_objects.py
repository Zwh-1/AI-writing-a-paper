"""Source-bound charts/SmartArt/embedded-sheet extraction. Never activates embedded objects."""
import hashlib,io,json,statistics
from xml.etree import ElementTree as E
from . import db,security
from .slide_layout import slide_package,NS
from .parsers import relationships,tokens
C='http://schemas.openxmlformats.org/drawingml/2006/chart';D='http://schemas.openxmlformats.org/drawingml/2006/diagram';A=NS['a'];R=NS['r']

def objects_on_page(file,page):
    z,part,_,_=slide_package(file,page);out=[]
    try:
        rels=relationships(z,part);slide=E.fromstring(z.read(part));used={value for node in slide.iter() for key,value in node.attrib.items() if key.startswith('{'+R+'}')}
        for rid,(target,kind) in rels.items():
            if target not in z.namelist() or rid not in used:continue
            if kind.endswith('/chart'):
                root=E.fromstring(z.read(target));series=[]
                for s in root.findall('.//{'+C+'}ser'):
                    item={'title':' '.join(n.text or '' for n in s.findall('.//{'+C+'}tx//{'+C+'}v'))}
                    for field in ('cat','val','xVal','yVal','bubbleSize'):
                        values=[]
                        for pt in s.findall('.//{'+C+'}'+field+'//{'+C+'}pt'):
                            v=pt.find('{'+C+'}v');values.append({'index':pt.get('idx'),'value':v.text if v is not None else None})
                        if values:item[field]=values
                    series.append(item)
                out.append({'kind':'chart_cache','page':page,'source_part':target,'relationship_id':rid,'series':series,'review_state':'pending','note':'图表缓存值，未冒充新计算或实测结果'})
            elif kind.endswith('/diagramData'):
                root=E.fromstring(z.read(target));points=[{'id':p.get('modelId'),'type':p.get('type'),'text':' '.join(n.text or '' for n in p.findall('.//{'+A+'}t'))} for p in root.findall('.//{'+D+'}pt')];edges=[{'from':c.get('srcId'),'to':c.get('destId'),'type':c.get('type')} for c in root.findall('.//{'+D+'}cxn')]
                out.append({'kind':'smart_art','page':page,'source_part':target,'points':points,'connections':edges,'review_state':'pending'})
            elif kind.endswith('/package') or kind.endswith('/oleObject'):
                raw=z.read(target);entry={'kind':'embedded_object','page':page,'source_part':target,'sha256':hashlib.sha256(raw).hexdigest(),'activated':False,'review_state':'pending'}
                if target.lower().endswith('.xlsx') and len(raw)<20*1024*1024:
                    import openpyxl
                    book=openpyxl.load_workbook(io.BytesIO(raw),read_only=True,data_only=False,keep_links=False);sheets=[]
                    try:
                        for sheet in book.worksheets[:10]:
                            cells=[]
                            for row in sheet.iter_rows(min_row=1,max_row=min(sheet.max_row,300),max_col=min(sheet.max_column,30)):
                                cells.append([{'cell':c.coordinate,'value':str(c.value) if c.value is not None else None,'formula':c.data_type=='f'} for c in row])
                            sheets.append({'name':sheet.title,'cells':cells,'truncated':sheet.max_row>300 or sheet.max_column>30})
                    finally:book.close()
                    entry['sheets']=sheets
                elif target.lower().endswith('.docx') and len(raw)<20*1024*1024:
                    import zipfile
                    with zipfile.ZipFile(io.BytesIO(raw)) as embedded:
                        if sum(info.file_size for info in embedded.infolist())>80*1024*1024:raise ValueError('嵌入Word解压体积超过80MB')
                    from .parsers import parse_pages
                    entry['document_blocks']=list(parse_pages(raw,'.docx'))
                    entry['read_only']=True
                else:entry['note']='保留原件标识；该嵌入格式不执行、不推测内容'
                out.append(entry)
        return out
    finally:z.close()

def extract(file):
    if file['extension']!='.pptx':raise ValueError('复杂对象提取目前接收PPTX')
    pages=db.rows('SELECT * FROM pages WHERE file_id=? ORDER BY page_number',(file['id'],));all_objects=[];source_ids=[]
    for page in pages:
        objects=objects_on_page(file,page['page_number']);all_objects+=objects
        for obj in objects:
            if obj['kind']=='embedded_object' and 'sheets' not in obj and 'document_blocks' not in obj:continue
            method='embedded_native' if 'document_blocks' in obj else 'object_structure'
            text='\n\n'.join(block['text'] for block in obj['document_blocks']) if 'document_blocks' in obj else db.dumps(obj)
            if 'document_blocks' in obj:
                current=db.one('SELECT text,images FROM pages WHERE id=?',(page['id'],));images=db.loads(current['images'],[])
                for block in obj['document_blocks']:
                    for image in block.get('images',[]):
                        if not any(old['sha256']==image['sha256'] for old in images):images.append({**image,'source_part':obj['source_part']+'::'+image.get('source_part','word/document.xml'),'embedded_sha256':obj['sha256']})
                if text.strip() and text not in current['text']:
                    db.execute("UPDATE pages SET text=text||?,review_state='unreviewed' WHERE id=?",('\n[嵌入DOCX原生内容，需核对]\n'+text,page['id']))
                db.execute('UPDATE pages SET images=?,has_visual=CASE WHEN ? THEN 1 ELSE has_visual END WHERE id=?',(db.dumps(images),bool(images),page['id']))
            for start in range(0,len(text),1600):
                chunk=text[start:start+1600];old=db.one("SELECT id FROM sources WHERE page_id=? AND method=? AND text=?",(page['id'],method,chunk))
                if old:source_ids.append(old['id']);continue
                sid=db.uid();kind='rule_candidate' if file['role']=='rules' else 'example' if file['role']=='example' else 'evidence'
                with db.transaction() as c:
                    c.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,file['id'],page['id'],db.dumps({'kind':obj['kind'],'page':page['page_number'],'source_part':obj['source_part'],'embedded_sha256':obj.get('sha256'),'start':start}),chunk,chunk,kind,method,0));c.execute('INSERT INTO source_fts VALUES(?,?)',(sid,tokens(chunk)));c.execute("UPDATE pages SET review_state='unreviewed' WHERE id=?",(page['id'],))
                source_ids.append(sid)
    return {'objects':all_objects,'source_ids':source_ids,'original_sha256':file['sha256'],'external_requests':0,'requires_human_review':True}

def ocr_tables(page_id):
    # Geometric candidates only; values and missing cells are never invented.
    sources=db.rows("SELECT locator FROM sources WHERE page_id=? AND method='windows_ocr'",(page_id,));tables=[]
    for source in sources:
        locator=db.loads(source['locator'],{})
        for image_index,recognition in enumerate(locator.get('recognition',[])):
            words=[w for line in recognition.get('lines',[]) for w in line.get('words',[]) if w.get('text')]
            if len(words)<8:continue
            heights=[w['box']['height'] for w in words];height=statistics.median(heights);rows=[]
            for word in sorted(words,key=lambda w:(w['box']['y'],w['box']['x'])):
                y=word['box']['y']+word['box']['height']/2
                row=next((r for r in rows if abs(r['y']-y)<height*.65),None)
                if row is None:row={'y':y,'words':[]};rows.append(row)
                row['words'].append(word)
            grouped=[]
            for row in rows:
                cells=[]
                for word in sorted(row['words'],key=lambda w:w['box']['x']):
                    box=word['box']
                    if cells and box['x']-cells[-1]['right']<height*1.4:cells[-1]['text']+=word['text'];cells[-1]['right']=box['x']+box['width']
                    else:cells.append({'text':word['text'],'x':box['x'],'right':box['x']+box['width'],'y':row['y']})
                if len(cells)>=2:grouped.append(cells)
            if len(grouped)>=3:
                width=max(map(len,grouped));consistent=[r for r in grouped if len(r)==width]
                if len(consistent)>=3:tables.append({'kind':'ocr_geometry_candidate','image_index':image_index,'rows':consistent,'requires_human_review':True,'note':'几何分列候选，需对照原图核对表格边界、合并单元格和数字'})
    return tables
