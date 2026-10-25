"""Read tagged PDF table rows without mixing grades, columns, or page continuations."""
import hashlib,logging,re
from collections import defaultdict
from . import db,security

VERSION='tagged-pdf-rule-context-v2'


def structure(reader):
    """Preserve /Pg on each MCR; flattened MCID lists lose cross-page headers."""
    from pypdf.generic import ArrayObject, DictionaryObject, NumberObject
    pages={(p.indirect_reference.idnum,p.indirect_reference.generation):i for i,p in enumerate(reader.pages,1)}
    def page_number(ref,default):
        return pages.get((ref.idnum,ref.generation),default) if hasattr(ref,'idnum') else default
    def visit(raw,page=None):
        node=raw.get_object() if hasattr(raw,'get_object') else raw
        if isinstance(node,ArrayObject):
            return [child for value in node for child in visit(value,page)]
        if isinstance(node,NumberObject):
            return [{'type':'content','refs':[(page,int(node))]}] if page else []
        if not isinstance(node,DictionaryObject):return []
        page=page_number(node.get('/Pg'),page)
        if '/MCID' in node:
            return [{'type':'content','refs':[(page,int(node['/MCID']))]}] if page else []
        attributes=node.get('/A',[])
        if not isinstance(attributes,ArrayObject):attributes=[attributes]
        attrs={}
        for attr in attributes:
            attr=attr.get_object() if hasattr(attr,'get_object') else attr
            if isinstance(attr,DictionaryObject):
                for key,value in attr.items():
                    attrs[str(key).lstrip('/')]=[str(v) for v in value] if isinstance(value,ArrayObject) else value
        return [{'type':str(node.get('/S','Document')).lstrip('/'),'id':str(node.get('/ID','')),'attributes':attrs,'children':visit(node.get('/K',[]),page)}]
    return visit(reader.trailer['/Root'].get('/StructTreeRoot',[]))


def grade_groups(table,content):
    """Resolve spanning cells before grouping criteria under their grade cell."""
    headers=[];header_ids={};spans={};groups=[];active=None
    for index,row in enumerate(table.get('children',[])):
        if row.get('type')!='TR':continue
        cells=[cell for cell in row.get('children',[]) if cell.get('type') in ('TH','TD')]
        if not cells:continue
        if any(cell.get('type')=='TH' for cell in cells):
            headers=[content(cell)[0] for cell in cells]
            header_ids={cell.get('id'):i for i,cell in enumerate(cells) if cell.get('id')}
            continue
        grid={col:value[0] for col,value in spans.items() if value[1]>index}
        spans={col:value for col,value in spans.items() if value[1]>index}
        cursor=0
        for cell in cells:
            attrs=cell.get('attributes',{});linked=attrs.get('Headers',[])
            if isinstance(linked,str):linked=[linked]
            targets={header_ids[h] for h in linked if h in header_ids}
            if len(targets)==1:col=next(iter(targets))
            else:
                while cursor in grid:cursor+=1
                col=cursor
            if col in grid:raise ValueError('PDF表格跨行单元格重叠，不能可靠确定等级')
            grid[col]=cell;cursor=col+int(attrs.get('ColSpan',1))
            if int(attrs.get('RowSpan',1))>1:spans[col]=(cell,index+int(attrs['RowSpan']))
        grade_cell=grid.get(0)
        if not grade_cell:active=None;continue
        grade_text=content(grade_cell)[0];match=re.search(r'([ABCDＡＢＣＤ])\s*级',grade_text,re.I)
        if not match:active=None;continue
        if active is None or active['_grade_cell'] is not grade_cell:
            active={'row_index':index+1,'grade':match[1].translate(str.maketrans('ＡＢＣＤ','ABCD')).upper(),'grade_text':grade_text,'_grade_cell':grade_cell,'columns':defaultdict(list),'row_indices':[],'headers':headers}
            groups.append(active)
        active['row_indices'].append(index+1)
        for col,cell in sorted(grid.items()):
            if all(prior is not cell for prior in active['columns'][col]):active['columns'][col].append(cell)
    return groups


def extract(file):
    cached=db.setting('pdf_rule_contexts:'+file['sha256'],{})
    if cached.get('version')==VERSION:return cached['rows']
    import pdfplumber
    from pypdf import PdfReader
    from pdfplumber.utils import extract_text
    logging.getLogger('pdfminer').setLevel(logging.ERROR)
    logging.getLogger('pypdf').setLevel(logging.ERROR)
    rows=[];issues=[]
    with pdfplumber.open(security.safe_object(file['sha256'])) as document:
        characters={}
        for number,page in enumerate(document.pages,1):
            grouped=defaultdict(list)
            for char in page.chars:
                if char.get('mcid') is not None:grouped[char['mcid']].append(char)
            characters[number]=grouped
        def references(node):
            result=list(node.get('refs',[]))
            for child in node.get('children',[]):result.extend(references(child))
            return list(dict.fromkeys(result))
        def content(node):
            refs=references(node);per_page=defaultdict(list)
            for page,mcid in refs:per_page[page].extend(characters.get(page,{}).get(mcid,[]))
            parts=list(dict.fromkeys((extract_text(per_page[p],layout=False) or '').strip() for p in sorted(per_page)))
            text='\n'.join(part for part in parts if part)
            return text.strip(),[p for p in sorted(per_page) if per_page[p]],refs
        tables=[];previous=''
        def walk(nodes):
            nonlocal previous
            for node in nodes:
                if node.get('type')=='Table':tables.append((previous,node));continue
                children=node.get('children',[])
                if children:walk(children)
                else:
                    text,_,_=content(node)
                    if text and len(text)<60 and not re.fullmatch(r'\d+[/.:-].*',text):previous=text
        walk(structure(PdfReader(security.safe_object(file['sha256']))))
        for table_index,(heading,table) in enumerate(tables):
            try:groups=grade_groups(table,content)
            except ValueError as error:
                issues.append({'table':table_index+1,'reason':str(error)});continue
            for group in groups:
                cells=[];headers=group['headers']
                for col,nodes in sorted(group['columns'].items()):
                    parts=[content(node) for node in nodes]
                    cells.append({'column':col,'header':headers[col] if col<len(headers) else '', 'text':'\n'.join(part[0] for part in parts),'pages':sorted({p for part in parts for p in part[1]}),'mcid_refs':list(dict.fromkeys(ref for part in parts for ref in part[2]))})
                if not all(any(c['column']==col and c['text'] for c in cells) for col in (0,1,2)):continue
                pages=sorted({p for cell in cells for p in cell['pages']})
                text=heading+'\n'+'\n'.join(cell['header']+'：'+cell['text'] for cell in cells)
                rows.append({'id':f'T{table_index+1}R{group["row_index"]}','heading':heading,'grade':group['grade'],'grade_text':group['grade_text'],'score':next(c['text'] for c in cells if c['column']==1),'text':text,'pages':pages,'cells':cells,'row_indices':group['row_indices'],'file_id':file['id'],'file_sha256':file['sha256'],'file_name':file['name'],'version':VERSION})
    db.set_setting('pdf_rule_contexts:'+file['sha256'],{'version':VERSION,'rows':rows,'issues':issues})
    return rows


def context_source(row):
    file=db.one('SELECT * FROM files WHERE id=? AND sha256=? AND active=1',(row['file_id'],row['file_sha256']))
    if not file:raise ValueError('结构化表格对应的原件已改变')
    locator={'kind':'rule_excerpt','extractor':VERSION,'table_row':row['id'],'grade':row['grade'],'score':row['score'],'pages':row['pages'],'file_sha256':row['file_sha256'],'cells':row['cells']}
    key=hashlib.sha256(db.dumps([VERSION,row['file_sha256'],row['id'],row['text']]).encode()).hexdigest()
    old=db.one("SELECT * FROM sources WHERE file_id=? AND json_extract(locator,'$.context_key')=?",(file['id'],key))
    if old:return old
    page=db.one('SELECT id FROM pages WHERE file_id=? AND page_number=?',(file['id'],row['pages'][0]))
    sid=db.uid();locator['context_key']=key
    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,file['id'],page['id'] if page else None,db.dumps(locator),row['text'],row['text'],'rule_candidate','pdf_tagged_table',0))
    from .parsers import tokens
    db.execute('INSERT INTO source_fts VALUES(?,?)',(sid,tokens(row['text'])))
    return db.one('SELECT * FROM sources WHERE id=?',(sid,))
