import re,io,csv,zipfile,posixpath
from pathlib import Path
from xml.etree import ElementTree as ET
from . import db
from .config import MAX_FILE,MAX_UNPACKED
from .security import put_blob,safe_object
NS={'a':'http://schemas.openxmlformats.org/drawingml/2006/main','p':'http://schemas.openxmlformats.org/presentationml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
SUPPORTED={'.pptx','.pdf','.docx','.doc','.txt','.md','.csv','.xlsx','.bib','.ris','.json'}
def tokens(text):
    text=text.lower()
    try:
        import jieba
        return ' '.join(w for w in jieba.cut(text) if w.strip())
    except ImportError:
        return ' '.join(re.findall(r'[a-z0-9_]+',text)+[s[i:i+2] for s in re.findall(r'[\u4e00-\u9fff]+',text) for i in range(max(1,len(s)-1))])
def relationships(z,path):
    rp=posixpath.join(posixpath.dirname(path),'_rels',posixpath.basename(path)+'.rels')
    if rp not in z.namelist(): return {}
    return {r.get('Id'):(posixpath.normpath(posixpath.join(posixpath.dirname(path),r.get('Target',''))) if not r.get('Target','').startswith('/') else r.get('Target').lstrip('/'),r.get('Type','')) for r in ET.fromstring(z.read(rp)) if r.get('TargetMode')!='External'}
def pptx_pages(data):
    """Follow presentation order, keep table cells and include inherited image assets."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names=set(z.namelist())
        if sum(i.file_size for i in z.infolist())>MAX_UNPACKED: raise ValueError('文档解压体积超过上限')
        root=ET.fromstring(z.read('ppt/presentation.xml'));rels=relationships(z,'ppt/presentation.xml')
        order=[rels[n.get('{'+NS['r']+'}id')][0] for n in root.findall('./p:sldIdLst/p:sldId',NS)]
        for number,path in enumerate(order,1):
            page=ET.fromstring(z.read(path));pr=relationships(z,path);parts=[];images=[];notes=[]
            # XML document order also preserves grouped shapes; tables retain row/cell boundaries.
            for node in page.iter():
                if node.tag=='{'+NS['p']+'}sp':
                    lines=[''.join(t.text or '' for t in p.findall('.//a:t',NS)).strip() for p in (node.findall('./p:txBody/a:p',NS) or node.findall('.//a:p',NS))]
                    if any(lines):parts.append('\n'.join(x for x in lines if x))
                elif node.tag=='{'+NS['a']+'}tbl':
                    rows=[]
                    for row in node.findall('./a:tr',NS):
                        cells=[' / '.join(''.join(t.text or '' for t in p.findall('.//a:t',NS)) for p in cell.findall('./a:txBody/a:p',NS)) for cell in row.findall('./a:tc',NS)]
                        rows.append(' | '.join(cells))
                    if rows:parts.append('\n'.join(rows))
            seen=set()
            def collect_images(part,depth=0):
                if part in seen or part not in names or depth>2:return
                seen.add(part);tree=ET.fromstring(z.read(part));rr=relationships(z,part)
                for blip in tree.findall('.//a:blip',NS):
                    rid=blip.get('{'+NS['r']+'}embed');link=rr.get(rid)
                    if not link or link[0] not in names:continue
                    target=link[0];sha=put_blob(z.read(target))
                    if any(x['sha256']==sha for x in images):continue
                    images.append({'sha256':sha,'extension':Path(target).suffix.lower(),'name':Path(target).name,'source_part':part,'asset_part':target,'inherited':part!=path})
                for target,typ in rr.values():
                    if typ.endswith(('/slideLayout','/slideMaster')):collect_images(target,depth+1)
            collect_images(path)
            for target,typ in pr.values():
                if typ.endswith('/notesSlide') and target in names:
                    n=ET.fromstring(z.read(target))
                    for shape in n.findall('.//p:sp',NS):
                        ph=shape.find('.//p:ph',NS)
                        if ph is not None and ph.get('type') in ('sldNum','dt','hdr','ftr','sldImg'): continue
                        notes.extend(''.join(t.text or '' for t in p.findall('.//a:t',NS)) for p in shape.findall('.//a:p',NS))
            yield {'number':number,'title':parts[0].split('\n')[0][:150] if parts else f'第 {number} 页','text':'\n\n'.join(parts),'images':images,'notes':'\n'.join(notes),'visual':bool(images)}

def parse_pages(data,ext):
    if ext=='.doc':
        from .word_conversion import convert_doc
        yield from parse_pages(convert_doc(data),'.docx')
        return
    if ext in ('.docx','.xlsx'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(i.file_size for i in archive.infolist())>MAX_UNPACKED:raise ValueError('文档解压体积超过上限')
    if ext=='.pptx': yield from pptx_pages(data)
    elif ext=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(''): raise ValueError('PDF需要打开密码，请解锁后导入；权限限制不等同于需要密码')
        def has_images(resources,seen=None):
            seen=set() if seen is None else seen
            try:
                resources=resources.get_object() if hasattr(resources,'get_object') else resources;objects=resources.get('/XObject',{}).get_object() if hasattr(resources.get('/XObject',{}),'get_object') else resources.get('/XObject',{})
                for ref in objects.values():
                    marker=(getattr(ref,'idnum',None),getattr(ref,'generation',None))
                    if marker!=(None,None) and marker in seen:continue
                    seen.add(marker);obj=ref.get_object()
                    if obj.get('/Subtype')=='/Image':return True
                    if obj.get('/Subtype')=='/Form' and has_images(obj.get('/Resources',{}),seen):return True
            except (AttributeError,TypeError,ValueError):return True
            return False
        for n,p in enumerate(reader.pages,1):
            native_error=None
            try:text=p.extract_text() or ''
            except Exception as error:text='';native_error='原生文字提取失败：'+str(error)[:250]
            visual=has_images(p.get('/Resources',{})) or len(text.strip())<40 or text.count('\ufffd')>max(3,len(text)//50)
            yield {'number':n,'title':f'第 {n} 页','text':text,'images':[],'notes':'','visual':visual,'native_error':native_error}
    elif ext=='.docx':
        from docx import Document
        from docx.text.paragraph import Paragraph
        from docx.table import Table
        d=Document(io.BytesIO(data));parts=[];images=[]
        for element in d.element.body:
            if element.tag.endswith('}p'):
                text=Paragraph(element,d).text
                if text.strip():parts.append(text)
            elif element.tag.endswith('}tbl'):
                table=Table(element,d)
                parts.append('\n'.join(' | '.join(c.text for c in row.cells) for row in table.rows))
        for rel in d.part.rels.values():
            if not rel.is_external and rel.reltype.endswith('/image'):
                image=rel.target_part;images.append({'sha256':put_blob(image.blob),'extension':Path(str(image.partname)).suffix.lower(),'name':Path(str(image.partname)).name,'source_part':'word/document.xml'})
        yield {'number':1,'title':'文档段落与表格（非固定页码）','text':'\n\n'.join(parts),'images':images,'notes':'','visual':bool(images)}
    elif ext=='.xlsx':
        import openpyxl
        book=openpyxl.load_workbook(io.BytesIO(data),read_only=True,data_only=True)
        for n,s in enumerate(book.worksheets,1): yield {'number':n,'title':s.title,'text':'\n'.join(' | '.join('' if v is None else str(v) for v in r) for r in s.iter_rows(values_only=True)),'images':[],'notes':'','visual':False}
        book.close()
    else:
        text=None
        encodings=('utf-16','utf-8-sig','gb18030') if data.startswith((b'\xff\xfe',b'\xfe\xff')) or data[:200].count(b'\x00')>20 else ('utf-8-sig','gb18030','utf-16')
        for enc in encodings:
            try: text=data.decode(enc);break
            except UnicodeError: pass
        if text is None: raise ValueError('无法识别文本编码')
        if ext=='.csv': text='\n'.join(f'{i+1}: '+' | '.join(r) for i,r in enumerate(csv.reader(io.StringIO(text))))
        for n,start in enumerate(range(0,max(len(text),1),12000),1): yield {'number':n,'title':f'文本片段 {n}','text':text[start:start+12000],'images':[],'notes':'','visual':False}
def register_file(pid,name,data,role='research',egress='selected_content'):
    ext=Path(name).suffix.lower()
    if ext not in SUPPORTED: raise ValueError(f'不支持 {ext}，请先转换')
    if not data or len(data)>MAX_FILE: raise ValueError('文件为空或超过120MB')
    if role not in ('rules','example','research','draft','data','supervisor') or egress not in ('local_only','selected_content','allowed'): raise ValueError('资料用途或权限无效')
    sha=put_blob(data);old=db.one('SELECT * FROM files WHERE project_id=? AND sha256=? AND role=?',(pid,sha,role))
    if old:
        from .reference_knowledge import register_document
        register_document(old['id'])
        return old,False
    fid=db.uid();db.execute('INSERT INTO files(id,project_id,name,sha256,extension,size,role,egress,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(fid,pid,Path(name).name,sha,ext,len(data),role,egress,db.now()))
    from .reference_knowledge import register_document
    register_document(fid)
    return db.one('SELECT * FROM files WHERE id=?',(fid,)),True
def parse_file(fid,progress=lambda *a:None,repair=False,native_only=False):
    """Add missing extraction without deleting stable page/source identities."""
    from . import material_quality
    f=db.one('SELECT * FROM files WHERE id=?',(fid,))
    if not f:raise ValueError('文件不存在')
    if f['status']=='ready' and not repair and not material_quality.summary(f)['repair_needed']:
        return {'file_id':fid,'pages':f['pages'],'cached':True,'quality':material_quality.summary(f)}
    db.execute('UPDATE files SET status=?,error=NULL WHERE id=?',('parsing',fid))
    record=db.setting(material_quality.key(fid),{})
    record.pop('object_error',None)
    if record.get('sha256')!=f['sha256']:record={'sha256':f['sha256'],'page_states':{}}
    try:
        progress('提取原生文字、表格和备注',3)
        parsed=list(parse_pages(safe_object(f['sha256']).read_bytes(),f['extension']))
        if not parsed:raise ValueError('未提取到任何页面，文件内容需要检查')
        with db.transaction() as c:
            for p in parsed:
                old=c.execute('SELECT * FROM pages WHERE file_id=? AND page_number=?',(fid,p['number'])).fetchone()
                page_id=old['id'] if old else db.uid()
                raw=p['text']+('\n[备注]\n'+p['notes'] if p['notes'] else '')
                kind='example' if f['role']=='example' else 'rule_candidate' if f['role']=='rules' else 'commentary' if f['role'] in ('draft','supervisor') else 'evidence'
                if old:
                    # Append recovered native content; existing citations, OCR and manual transcripts survive.
                    old_native='\n'.join(r['text'] for r in c.execute("SELECT text FROM sources WHERE page_id=? AND method IN ('native','native_supplement')",(page_id,)).fetchall())
                    known=re.sub(r'\s+','',old['text']+'\n'+old['notes']+'\n'+old_native)
                    additional=[block for block in raw.split('\n\n') if block.strip() and re.sub(r'\s+','',block) not in known]
                    new_raw='\n\n'.join(additional)
                    displayed=old['text']+('\n[补充原生内容]\n'+new_raw if new_raw else '')
                    state='unreviewed' if new_raw and old['review_state']=='reviewed' else old['review_state']
                    c.execute('UPDATE pages SET title=?,text=?,images=?,notes=?,has_visual=?,review_state=? WHERE id=?',(p['title'],displayed,db.dumps(p['images']),p['notes'],int(p['visual']),state,page_id))
                    source_method='native_supplement'
                else:
                    c.execute('INSERT INTO pages(id,file_id,page_number,title,text,images,notes,has_visual) VALUES(?,?,?,?,?,?,?,?)',(page_id,fid,p['number'],p['title'],p['text'],db.dumps(p['images']),p['notes'],int(p['visual'])))
                    new_raw=raw;source_method='native'
                for start in range(0,len(new_raw),800):
                    fragment=new_raw[start:start+1000]
                    if not fragment.strip():continue
                    sid=db.uid();c.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,fid,page_id,db.dumps({'kind':f['extension'][1:],'page':p['number'],'char_start':start,'parser_version':material_quality.PARSER_VERSION}),fragment,fragment.strip(),kind,source_method,0))
                    c.execute('INSERT INTO source_fts VALUES(?,?)',(sid,tokens(fragment)))
                previous=record['page_states'].get(page_id,{})
                previous.update({'native_chars':len(raw),'asset_count':len(p['images']),'ocr_state':previous.get('ocr_state','pending') if p['visual'] else 'not_needed','errors':[]})
                record['page_states'][page_id]=previous
            c.execute('UPDATE files SET pages=? WHERE id=?',(len(parsed),fid))
        record['native_version']=material_quality.PARSER_VERSION
        material_quality.save(fid,record)
        structure_errors={}
        # Complex objects are opt-in by presence and are read without activation.
        if f['extension']=='.pptx':
            from .ppt_objects import extract
            with zipfile.ZipFile(safe_object(f['sha256'])) as z:
                complex_objects=any(name.startswith(('ppt/charts/','ppt/diagrams/','ppt/embeddings/')) for name in z.namelist())
            if complex_objects:
                progress('提取图表、SmartArt和嵌入表格',12)
                try:
                    extracted=extract(f)
                    for obj in extracted['objects']:
                        if obj['kind']=='embedded_object' and 'sheets' not in obj and 'document_blocks' not in obj:
                            structure_errors.setdefault(obj['page'],[]).append('嵌入对象仅登记原件，尚未解析其内部内容：'+obj['source_part'])
                except InterruptedError:raise
                except Exception as error:record['object_error']=str(error)[:300]
        from .local_ocr import page_ocr,capabilities
        visual=db.rows('SELECT * FROM pages WHERE file_id=? AND has_visual=1 ORDER BY page_number',(fid,))
        available=capabilities()['available'] if visual and not native_only else True
        for i,page in enumerate([] if native_only else visual):
            progress(f'本地图片识别 {i+1}/{len(visual)} · 第{page["page_number"]}页',15+int(80*i/max(1,len(visual))))
            if page['review_state']=='not_applicable':
                record['page_states'][page['id']]['ocr_state']='excluded'
            elif page['review_state']=='reviewed':
                record['page_states'][page['id']]['ocr_state']='reviewed'
            elif not available:
                record['page_states'][page['id']].update({'ocr_state':'unavailable','errors':['本机OCR引擎或所需语言不可用，请安装语言包后重试或人工转录']})
            else:
                try:
                    result=page_ocr(page['id'])
                    record['page_states'][page['id']].update(result['diagnostic'])
                except InterruptedError:raise
                except Exception as error:
                    record['page_states'][page['id']].update({'ocr_state':'failed','errors':[str(error)[:400]]})
            material_quality.save(fid,record)
        for p in parsed:
            page=db.one('SELECT id FROM pages WHERE file_id=? AND page_number=?',(fid,p['number']))
            if page:
                state=record['page_states'][page['id']]
                state.setdefault('warnings',[])
                if p.get('native_error'):state['warnings'].append(p['native_error'])
                state.setdefault('errors',[]).extend(structure_errors.get(p['number'],[]))
        if record.get('object_error') and record['page_states']:

            next(iter(record['page_states'].values())).setdefault('errors',[]).append(record['object_error'])
        db.execute('UPDATE files SET status=?,error=NULL WHERE id=?',('ready',fid))
        from .reference_knowledge import sync_document
        sync_document(fid)
        material_quality.save(fid,record)
        quality=material_quality.summary(db.one('SELECT * FROM files WHERE id=?',(fid,)))
        progress('解析完成，图片候选仍需人工核对' if quality['state']=='extracted' else '解析结束，仍有缺项需要处理',98)
        return {'file_id':fid,'pages':len(parsed),'quality':quality,'external_requests':False}
    except InterruptedError:
        db.execute('UPDATE files SET status=? WHERE id=?',('ready' if db.one('SELECT id FROM pages WHERE file_id=?',(fid,)) else 'queued',fid))
        material_quality.save(fid,record)
        raise
    except Exception as e:
        db.execute('UPDATE files SET status=?,error=? WHERE id=?',('failed',str(e)[:1000],fid));raise

def keyword_search(pid,query,kind='research',limit=12,remote=False,knowledge=False,file_ids=None):
    from .document_observations import current_source_sql
    for term in db.rows('SELECT zh,en FROM terms WHERE project_id=?',(pid,)):
        if term['zh'].lower() in query.lower() or term['en'].lower() in query.lower(): query+=' '+term['zh']+' '+term['en']
    terms=list(dict.fromkeys(tokens(query).split()))[:35]
    if not terms: return []
    match=' OR '.join('"'+t.replace('"','')+'"' for t in terms);limit=min(max(int(limit),1),100)
    role="s.kind='evidence'" if kind=='research' and not knowledge else ("s.kind IN ('example','counterexample')" if kind=='example' else '1=1')
    permission="AND f.egress!='local_only' AND NOT EXISTS (WITH RECURSIVE origin(id) AS (SELECT parent_file_id FROM file_derivations WHERE child_file_id=f.id UNION SELECT d.parent_file_id FROM file_derivations d JOIN origin o ON d.child_file_id=o.id) SELECT 1 FROM origin o JOIN files p ON p.id=o.id WHERE p.egress='local_only' OR p.active=0 OR p.project_id!=f.project_id)" if remote else ''
    scope=__import__('app.reference_knowledge',fromlist=['scope_sql']).scope_sql() if knowledge else ''
    if file_ids and (not isinstance(file_ids,list) or len(file_ids)>100 or any(not isinstance(fid,str) for fid in file_ids)):raise ValueError('原件范围无效')
    file_scope=' AND f.id IN ('+','.join('?' for _ in file_ids)+')' if file_ids else ''
    sql=f"SELECT s.*,f.name,f.role,f.egress,f.extension,p.page_number,p.title AS page_title,p.review_state,bm25(source_fts) AS rank FROM source_fts JOIN sources s ON s.id=source_fts.source_id JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE source_fts MATCH ? AND f.project_id=? AND f.active=1 AND {current_source_sql()} AND {role} {permission} {scope} {file_scope} ORDER BY rank,s.id LIMIT ?"
    return db.rows(sql,(match,pid,*(file_ids or []),limit))


def search(pid,query,kind='research',limit=12,remote=False):
    if kind=='research':
        from .knowledge_service import search as knowledge_search
        return knowledge_search(pid,query,limit,remote,include_units=False)['items']
    from . import retrieval
    if retrieval.config(pid)['enabled']:
        return retrieval.hybrid(pid,query,kind,limit,remote)['items']
    return keyword_search(pid,query,kind,limit,remote)
