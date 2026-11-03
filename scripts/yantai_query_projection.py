"""SQL projection only: no credentials, model loading or application imports."""
import hashlib,json,re

def current_input(connection,original):
    rows=lambda sql,args=():[dict(row) for row in connection.execute(sql,args)]
    sources=rows("SELECT id,text,method,locator FROM sources WHERE file_id=? AND method!='document_rule_context' ORDER BY id",(original['id'],))
    stored=connection.execute("SELECT value FROM settings WHERE key='document_observation_supersessions'").fetchone()
    mapping=json.loads(stored[0]) if stored else {};ids={row['id'] for row in sources};related={k:v for k,v in mapping.items() if k in ids}
    base=[original['sha256'],rows('SELECT id,text,notes FROM pages WHERE file_id=? ORDER BY page_number',(original['id'],)),[{k:s[k] for k in ('id','text','method')} for s in sources]]
    value=base if original['role']!='rules' and not related else ['complete-observations-v2',base,[(s['id'],s['locator']) for s in sources],related,rows('SELECT id,images FROM pages WHERE file_id=? ORDER BY page_number',(original['id'],))]
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def query(connection,pid,args):
    rows=lambda sql,values=():[dict(row) for row in connection.execute(sql,values)]
    command=args['command'];section=args.get('section','knowledge')
    if command=='summary':return {'project':rows('SELECT id,title,created_at,updated_at FROM projects WHERE id=?',(pid,))[0],
        'counts':{table:rows('SELECT count(*) n FROM '+table+' WHERE project_id=?',(pid,))[0]['n'] for table in ('files','bibliography','sections','artifacts')},
        'query_contract':'workspace-query.v1','search_mode':'current_content_keyword'}
    if command=='search':
        text=str(args.get('query','')).strip()
        if not text or len(text)>5000:raise ValueError('搜索词须为1至5000字')
        limit=args.get('limit',50)
        if type(limit)is not int or not 1<=limit<=50:raise ValueError('检索数量须为1至50')
        pattern='%'+text.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%';items=[]
        tables={row['name'] for row in rows("SELECT name FROM sqlite_master WHERE type='table'")}
        has_units='knowledge_heads' in tables
        scope="f.role='research'"
        if {'file_usage_additions','file_source_profiles'}<=tables:
            scope="(f.role='research' OR EXISTS(SELECT 1 FROM file_usage_additions fu WHERE fu.file_id=f.id AND fu.usage='research')) AND NOT EXISTS(SELECT 1 FROM file_source_profiles sp WHERE sp.file_id=f.id AND sp.kind!='research_publication')"
        if has_units:
            items=rows("SELECT 'unit:'||u.id id,u.id unit_id,u.parse_id,p.file_id,p.file_hash,u.content_hash,u.text,u.locator,f.name FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id JOIN knowledge_heads h ON h.parse_id=p.id JOIN files f ON f.id=p.file_id LEFT JOIN pages pg ON pg.file_id=f.id AND pg.page_number=json_extract(u.locator,'$.page') WHERE p.project_id=? AND f.active=1 AND f.sha256=p.file_hash AND f.role IN ('research','example') AND u.review_state!='excluded' AND COALESCE(pg.review_state,'')!='not_applicable' AND u.text LIKE ? ESCAPE '\\' ORDER BY p.file_id,u.ordinal LIMIT 50",(pid,pattern))
        fallback=" AND NOT EXISTS (SELECT 1 FROM knowledge_heads h WHERE h.file_id=f.id)" if has_units else ''
        items+=rows("SELECT s.id,s.file_id,s.text,s.locator,f.name FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages pg ON pg.id=s.page_id WHERE f.project_id=? AND f.active=1 AND f.role IN ('research','example') AND COALESCE(pg.review_state,'')!='not_applicable' AND s.text LIKE ? ESCAPE '\\'"+fallback+' ORDER BY s.id LIMIT 50',(pid,pattern))
        eligible=[];inputs={}
        for item in items:
            fid=item['file_id']
            original=rows('SELECT * FROM files f WHERE f.id=? AND '+scope,(fid,))
            if not original:continue
            item['locator']=json.loads(item['locator']) if isinstance(item['locator'],str) else item['locator']
            excluded={row['page_number'] for row in rows("SELECT page_number FROM pages WHERE file_id=? AND review_state='not_applicable'",(fid,))}
            pages={item['locator'].get('page',1)}|{part.get('locator',{}).get('page') for part in item['locator'].get('segments',[])}
            if pages&excluded:continue
            if item.get('parse_id'):
                if fid not in inputs:inputs[fid]=current_input(connection,original[0])
                parse=rows('SELECT input_hash FROM knowledge_parses WHERE id=?',(item['parse_id'],))[0]
                if parse['input_hash']!=inputs[fid]:continue
            eligible.append(item)
        items=eligible
        return {'query':text,'items':items[:limit],'query_contract':'workspace-query.v1','mode':'current_content_keyword','semantic_search':False,'scope':'originals'}
    if section=='knowledge':items=rows("SELECT * FROM bibliography WHERE project_id=? AND COALESCE(json_extract(metadata,'$._studio_deleted'),0)=0 ORDER BY id",(pid,))
    elif section=='documents':items=rows('SELECT s.*,r.document,r.text FROM sections s LEFT JOIN revisions r ON r.id=s.current_revision_id WHERE s.project_id=? ORDER BY s.position,s.id',(pid,))
    elif section in ('notes','conversations'):items=rows('SELECT * FROM studio_records WHERE project_id=? AND kind=? AND deleted_at IS NULL ORDER BY id',(pid,'note' if section=='notes' else 'conversation'))
    else:raise ValueError('数据集合无效')
    for row in items:
        for key in ('data','metadata','document'):
            if isinstance(row.get(key),str):
                try:row[key]=json.loads(row[key])
                except ValueError:pass
    if command=='list':return items
    item=next((row for row in items if row['id']==args.get('id')),None)
    if item is None:raise ValueError('未找到指定对象')
    return item
