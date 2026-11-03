"""A shared unit locator connects reading, translation, highlights and references."""
import hashlib
import re
from fastapi import HTTPException
from . import db
from . import research_records as store

COLORS={'yellow','green','blue','pink','purple'}
DOI=re.compile(r'\b10\.\d{4,9}/[^\s<>]+',re.I)
REFERENCE_HEADING=re.compile(r'^\s*(references|bibliography|参考文献|引用文献)\s*[:：]?\s*$',re.I)
HEADING=re.compile(r'^\s*(?:(\d+(?:\.\d+){0,4})[.、]?\s+(.{2,160})|第.{1,12}[章节].{0,120}|Abstract|Introduction|Methods?|Results?|Discussion|Conclusions?|摘要|绪论|引言|结论|研究方法|实验结果)\s*$',re.I)

def text_hash(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()

def reference_entries(units):
    active=False;entries=[];current=None
    for unit in units:
        for line in unit['text'].splitlines():
            if REFERENCE_HEADING.fullmatch(line.strip()):active=True;continue
            match=re.match(r'^\s*\[(\d+)\]\s*(.+)',line)
            if match:
                active=True;current={'number':int(match[1]),'text':match[2].strip(),'unit_id':unit['id'],'ordinal':unit['ordinal'],'locator':unit['locator']};entries.append(current)
            elif active and current and line.strip():current['text']+=' '+line.strip()
    for entry in entries:
        doi=DOI.search(entry['text']);url=re.search(r'https?://[^\s<>]+',entry['text'])
        entry['url']='https://doi.org/'+doi.group().rstrip('.,;)]') if doi else url.group().rstrip('.,;)]') if url else None
    return entries

def citation_numbers(text):
    result=[]
    for piece in re.split(r'[,，;；]',text.strip('[]')):
        piece=piece.strip();match=re.fullmatch(r'(\d+)\s*[-–—]\s*(\d+)',piece)
        if match:
            start,end=map(int,match.groups())
            if 0<start<=end and end-start<100:result.extend(range(start,end+1))
        elif piece.isdigit() and 0<int(piece)<100000:result.append(int(piece))
    return list(dict.fromkeys(result))

def structure(units):
    root={'id':'root','label':'论文结构','type':'root','children':[]};stack=[(0,root)]
    for unit in units:
        heading=next((line.strip() for line in unit['text'].splitlines() if len(line)<180 and HEADING.fullmatch(line)),None)
        paths=unit.get('heading_path',[])
        if heading:
            number=re.match(r'\d+(?:\.\d+)*',heading);level=min(5,number.group().count('.')+1) if number else 1
            while len(stack)>1 and stack[-1][0]>=level:stack.pop()
            node={'id':'section:'+unit['id'],'type':'section','label':heading,'unit_id':unit['id'],'ordinal':unit['ordinal'],'children':[]}
            stack[-1][1]['children'].append(node);stack.append((level,node))
        node={'id':unit['id'],'type':'paragraph','label':unit['text'].strip().splitlines()[0][:100] if unit['text'].strip() else '空单元',
              'unit_id':unit['id'],'ordinal':unit['ordinal'],'locator':unit['locator'],'heading_path':paths,'children':[]}
        stack[-1][1]['children'].append(node)
    return root

def document(pid,fid,language='zh-CN'):
    original,parse_id,units=store.file_units(pid,fid)
    translations=[store.unpack(row) for row in db.rows("SELECT * FROM studio_records WHERE project_id=? AND kind='translation' AND owner_id=? AND deleted_at IS NULL ORDER BY created_at",(pid,fid))]
    translated={}
    for row in translations:
        data=row['data']
        if data.get('language')==language:translated.setdefault(data.get('unit_id'),[]).append(data)
    result=[];current_ids=set()
    for unit in units:
        current_ids.add(unit['id']);stamp=text_hash(unit['text'])
        parts=sorted([item for item in translated.get(unit['id'],[]) if item.get('source_hash')==stamp and item.get('parse_id')==parse_id],key=lambda item:item.get('part',0))
        complete=bool(parts) and len(parts)==parts[0].get('parts',1)
        result.append({'id':unit['id'],'ordinal':unit['ordinal'],'kind':unit['kind'],'text':unit['text'],'locator':unit['locator'],'source_hash':stamp,
                       'translation':'\n'.join(item['text'] for item in parts) if complete else '', 'translation_complete':complete})
    annotations=[]
    for row in store.list_records(pid,'annotation',fid,500):
        data=row['data'];unit=next((u for u in result if u['id']==data.get('unit_id')),None)
        annotations.append({**row,'current':bool(unit and data.get('parse_id')==parse_id and data.get('source_hash')==unit['source_hash'])})
    progress=db.setting('studio:reading:'+pid+':'+fid,{'unit_id':None,'completed_units':[]})
    progress={**progress,'unit_id':progress.get('unit_id') if progress.get('unit_id') in current_ids else None,'completed_units':[identity for identity in progress.get('completed_units',[]) if identity in current_ids]}
    guides=store.list_records(pid,'guide',fid,500);guide=next((row for row in reversed(guides) if row['data'].get('parse_id')==parse_id),None)
    pages=db.rows("SELECT id,page_number,title FROM pages WHERE file_id=? AND review_state!='not_applicable' ORDER BY page_number",(fid,))
    return {'file':{'id':fid,'name':original['name'],'extension':original['extension'],'sha256':original['sha256']},'parse_id':parse_id,'units':result,'pages':pages,'guide':guide,
            'structure':structure(units),'references':reference_entries(units),'annotations':annotations,'language':language,
            'progress':progress}

def annotate(pid,fid,body):
    original,parse_id,units=store.file_units(pid,fid)
    unit=next((u for u in units if u['id']==body.get('unit_id')),None)
    if not unit:raise HTTPException(404,'选区不属于当前原文版本')
    start,end=body.get('start'),body.get('end')
    if type(start) is not int or type(end) is not int or not 0<=start<end<=len(unit['text']):raise HTTPException(422,'原文选区范围无效')
    stamp=text_hash(unit['text'])
    if body.get('source_hash')!=stamp or body.get('parse_id')!=parse_id or body.get('quote')!=unit['text'][start:end]:raise HTTPException(409,'原文或选区已变化，请重新选择')
    color=body.get('color','yellow');note=body.get('note','')
    if color not in COLORS or not isinstance(note,str) or len(note)>12000:raise HTTPException(422,'批注颜色或笔记无效')
    return store.save(pid,'annotation',{'unit_id':unit['id'],'parse_id':parse_id,'source_hash':stamp,'start':start,'end':end,'quote':body['quote'],'color':color,'note':note,'locator':unit['locator']},owner=fid)

def progress(pid,fid,body):
    _,_,units=store.file_units(pid,fid);ids={u['id'] for u in units};completed=body.get('completed_units',[])
    if body.get('unit_id') not in ids or not isinstance(completed,list) or any(identity not in ids for identity in completed):raise HTTPException(422,'阅读进度包含失效位置')
    data={'unit_id':body['unit_id'],'completed_units':list(dict.fromkeys(completed)),'updated_at':db.now()}
    db.set_setting('studio:reading:'+pid+':'+fid,data)
    return data

def validate_canvas(value):
    if not isinstance(value,dict) or value.get('version')!=1 or not isinstance(value.get('elements'),list) or len(value['elements'])>3000:raise HTTPException(422,'画布场景无效')
    import math
    for item in value['elements']:
        if not isinstance(item,dict) or item.get('type') not in ('path','rect','ellipse','line','arrow','text'):raise HTTPException(422,'画布元素无效')
        for key in ('x','y','width','height','strokeWidth'):
            number=item.get(key,1 if key=='strokeWidth' else 0)
            if isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number) or abs(number)>100000:raise HTTPException(422,'画布坐标无效')
        if not re.fullmatch(r'#[0-9a-fA-F]{3,8}|none',item.get('stroke','#111111')):raise HTTPException(422,'画布颜色无效')
        if len(str(item.get('text','')))>12000:raise HTTPException(422,'画布文字过长')
        points=item.get('points',[])
        if not isinstance(points,list) or len(points)>10000 or any(not isinstance(p,list) or len(p)!=2 or any(not isinstance(n,(int,float)) or not math.isfinite(n) or abs(n)>100000 for n in p) for p in points):raise HTTPException(422,'画布笔迹无效')
    return value
