"""Block-versioned Wiki with explicit sources, editorial conflicts and exports."""
import difflib
import re
from fastapi import HTTPException
from . import db, security
from . import knowledge_lineage as lineage
from .knowledge_graph import namespace, relevance

KINDS={'document','concept','method','topic','comparison','disagreement','project','guidance'}
BLOCKS={'fact','comparison','synthesis','inference','question','note','excerpt','limitation'}

@db.atomic
def create_page(pid,body):
    security.require_project(pid);ns=namespace(body.get('namespace','literature'));kind=body.get('kind','topic')
    title=str(body.get('title','')).strip();slug=str(body.get('slug','')).strip()
    if not title or len(title)>500 or not slug or len(slug)>160 or not re.fullmatch(r'[\w\-]+',slug) or kind not in KINDS:lineage.fail('INVALID_WIKI_PAGE','页面名称、类型或标识无效',422)
    old=db.one('SELECT * FROM wiki_pages WHERE project_id=? AND namespace=? AND slug=?',(pid,ns,slug))
    if old:return old
    identity=db.uid();db.execute('INSERT INTO wiki_pages(id,project_id,namespace,kind,slug,title,created_at) VALUES(?,?,?,?,?,?,?)',(identity,pid,ns,kind,slug,title,db.now()))
    return lineage.owned(pid,'wiki_page',identity)

@lineage.validation_scope()
def get_page(pid,identity,version_id=None,remote=False):
    page=lineage.owned(pid,'wiki_page',identity);vid=version_id or page['current_version_id']
    versions=db.rows('SELECT id,parent_id,content_hash,author,state,created_at FROM wiki_versions WHERE page_id=? ORDER BY created_at DESC,id',(identity,))
    if not vid:return {**page,'version':None,'blocks':[],'versions':versions,'current':False,'sources':[]}
    version=lineage.owned(pid,'wiki_version',vid)
    if version['page_id']!=identity:lineage.fail('INVALID_WIKI_VERSION','版本不属于该页面',422)
    refs=lineage.references(pid,'wiki_version',vid)
    try:lineage.version_of(pid,'wiki_version',vid,remote);current=True;issues=[]
    except HTTPException as error:
        if remote:raise
        current=False;issues=[error.detail]
    blocks=db.rows('SELECT * FROM wiki_blocks WHERE version_id=? ORDER BY position',(vid,))
    for block in blocks:
        block['metadata']=db.loads(block.pop('metadata_json'),{})
        block['sources']=[{'type':r['target_type'],'id':r['target_id'],'version':r['target_version']} for r in db.rows('SELECT * FROM wiki_links WHERE block_id=? ORDER BY rowid',(block['id'],))]
    return {**page,'version':version,'blocks':blocks,'sources':refs,'versions':versions,'current':current,'issues':issues,
            'semantic_support':'derived_synthesis_not_primary_evidence'}

@db.atomic
def revise(pid,identity,body,author='human'):
    page=lineage.owned(pid,'wiki_page',identity);expected=body.get('base_version_id')
    if expected!=page['current_version_id']:lineage.fail('WIKI_EDIT_CONFLICT','页面在编辑期间变化，已保留现有版本，请重新比较')
    blocks=body.get('blocks',[])
    if not isinstance(blocks,list) or not 1<=len(blocks)<=250:lineage.fail('INVALID_WIKI_BLOCKS','页面需要1至250个完整内容块',422)
    if author not in ('human','model','literal','import'):lineage.fail('INVALID_AUTHOR','页面作者类型无效',422)
    existing_blocks={b['block_key']:b for b in get_page(pid,identity)['blocks']} if expected else {}
    manual={b['block_key']:b for b in existing_blocks.values() if b['author']=='human'}
    if author!='human' and expected:
        blocks=[b for b in blocks if b.get('key',b.get('block_key')) not in manual]
        blocks.extend({'key':b['block_key'],'kind':b['kind'],'text':b['text'],'sources':b['sources'],'author':'human','metadata':b['metadata']} for b in manual.values())
    clean=[];keys=set();all_refs={};historical=set()
    previous_by_key=existing_blocks if author=='human' else manual
    for index,block in enumerate(blocks):
        kind=block.get('kind','note');text=block.get('text');key=str(block.get('key',block.get('block_key',index)))
        refs=block.get('sources',[])
        if kind not in BLOCKS or not isinstance(text,str) or not text.strip() or len(text)>30000 or key in keys:lineage.fail('INVALID_WIKI_BLOCK','内容块类型、文字或稳定标识无效',422)
        if kind in ('fact','comparison','synthesis','excerpt','limitation') and not refs:lineage.fail('PROVENANCE_MISSING','事实、比较及综合块需要具体来源',422)
        metadata=dict(block.get('metadata',{}));previous=previous_by_key.get(key)
        try:
            lineage.validate_refs(pid,refs);metadata.pop('requires_source_review',None)
        except HTTPException:
            if not previous or previous['sources']!=refs:raise
            historical.update((r['type'],r['id'],r['version']) for r in refs);metadata['requires_source_review']=True
        for r in refs:
            if r['type']=='wiki_page' and r['id']==identity:lineage.fail('KNOWLEDGE_CYCLE','页面不能引用自身作为依据')
            if r['type']=='wiki_version' and lineage.owned(pid,'wiki_version',r['id'])['page_id']==identity:lineage.fail('KNOWLEDGE_CYCLE','页面不能以历史自身作为新证据')
            if r['type']=='wiki_block' and lineage.owned(pid,'wiki_block',r['id'])['page_id']==identity:lineage.fail('KNOWLEDGE_CYCLE','页面不能以自身历史内容块作为新证据')
            all_refs[(r['type'],r['id'],r['version'])]=r
        block_author=author
        if previous and previous['kind']==kind and previous['text']==text and previous['sources']==refs:block_author=previous['author']
        keys.add(key);clean.append({'key':key,'kind':kind,'text':text,'sources':refs,'author':block_author,'metadata':metadata})
    content=lineage.digest(clean)
    if expected and lineage.owned(pid,'wiki_version',expected)['content_hash']==content:return get_page(pid,identity)
    old=db.one('SELECT id FROM wiki_versions WHERE page_id=? AND parent_id IS ? AND content_hash=? AND state IN (?,?)',(identity,expected,content,'candidate','published'))
    if old:return get_page(pid,identity,old['id'])
    vid=db.uid();db.execute('INSERT INTO wiki_versions VALUES(?,?,?,?,?,?,?,?,?)',(vid,pid,identity,expected,content,'wiki.v1',author,'candidate',db.now()))
    for position,block in enumerate(clean):
        bid=db.uid();db.execute('INSERT INTO wiki_blocks VALUES(?,?,?,?,?,?,?,?)',(bid,vid,block['key'],block['kind'],block['text'],block['author'],db.dumps(block['metadata']),position))
        for r in block['sources']:db.execute('INSERT OR IGNORE INTO wiki_links VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,bid,r['type'],r['id'],r['version'],'based_on'))
    lineage.add_dependencies(pid,'wiki_version',vid,content,[r for k,r in all_refs.items() if k not in historical])
    for k in historical:
        r=all_refs[k]
        db.execute('INSERT OR IGNORE INTO knowledge_dependencies VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),pid,'wiki_version',vid,content,r['type'],r['id'],r['version'],'preserved_historical_source'))
    return get_page(pid,identity,vid)

@db.atomic
def publish(pid,identity,body):
    page=lineage.owned(pid,'wiki_page',identity);version=lineage.owned(pid,'wiki_version',body.get('version_id'))
    if version['page_id']!=identity:lineage.fail('INVALID_WIKI_VERSION','版本不属于页面',422)
    if version['id']==page['current_version_id'] and body.get('base_version_id')==page['current_version_id']:
        lineage.validate_refs(pid,lineage.references(pid,'wiki_version',version['id']))
        return get_page(pid,identity)
    if page['current_version_id']!=body.get('base_version_id') or version['parent_id']!=page['current_version_id']:lineage.fail('WIKI_EDIT_CONFLICT','发布基础版本已变化')
    lineage.validate_refs(pid,lineage.references(pid,'wiki_version',version['id']))
    if version['state'] not in ('candidate','published'):lineage.fail('WIKI_STALE','过期版本需重新整理')
    old=page['current_version_id'];db.execute("UPDATE wiki_versions SET state='published' WHERE id=?",(version['id'],))
    old_blocks=db.rows('SELECT * FROM wiki_blocks WHERE version_id=?',(old,)) if old else []
    db.execute('UPDATE wiki_pages SET current_version_id=? WHERE id=?',(version['id'],identity))
    for previous in old_blocks:
        current=db.one('SELECT * FROM wiki_blocks WHERE version_id=? AND block_key=?',(version['id'],previous['block_key']))
        if not current or lineage.block_hash(pid,current)!=lineage.block_hash(pid,previous):lineage.changed(pid,'wiki_block',previous['id'],'已使用的Wiki内容块已修订')
    if old and old!=version['id']:lineage.changed(pid,'wiki_version',old,'Wiki已发布新版本')
    return get_page(pid,identity)

def diff(pid,identity,left,right):
    a=get_page(pid,identity,left);b=get_page(pid,identity,right)
    return {'left':left,'right':right,'diff':list(difflib.unified_diff(markdown(a).splitlines(),markdown(b).splitlines(),fromfile=left,tofile=right,lineterm=''))}

def search(pid,query='',ns='all',limit=20,remote=False,include_indexes=False,file_ids=None):
    security.require_project(pid)
    if not isinstance(query,str) or len(query)>5000 or type(limit) is not int or not 1<=limit<=100:lineage.fail('INVALID_QUERY','Wiki查询范围无效',422)
    if ns!='all':namespace(ns)
    rows=db.rows('SELECT p.*,GROUP_CONCAT(b.text, char(10)) AS body FROM wiki_pages p LEFT JOIN wiki_blocks b ON b.version_id=p.current_version_id WHERE p.project_id=?'+(' AND p.namespace=?' if ns!='all' else '')+' GROUP BY p.id',[pid]+([ns] if ns!='all' else []))
    scope=lineage.descendants_of_files(pid,file_ids or [])
    if scope is not None:
        rows=[r for r in rows if r['current_version_id'] in scope.get('wiki_version',set()) and set(lineage.source_files(pid,lineage.references(pid,'wiki_version',r['current_version_id'])))<=set(file_ids)]
    rows.sort(key=lambda r:(-relevance(r['title']+' '+(r['body'] or ''),query),r['id']));items=[]
    with lineage.validation_scope():
        for row in rows:
            if row['slug']=='source-index' and not include_indexes:continue
            if query and not relevance(row['title']+' '+(row['body'] or ''),query):continue
            try:page=get_page(pid,row['id'],remote=remote)
            except HTTPException:continue
            if not page['current']:continue
            items.append(page)
            if len(items)>=limit:break
    return {'items':items,'scope':'current_valid_pages','semantic_support':'derived_synthesis_not_primary_evidence'}

def markdown(page):
    parts=['# '+page['title'],'',f"页面：{page['id']}；版本：{page['version']['id'] if page.get('version') else '未发布'}",'','状态：'+('当前有效' if page.get('current') else '候选或待复查'),'']
    for block in page['blocks']:
        parts.extend([block['text'],''])
        if block['sources']:
            parts.append('来源：'+'；'.join(f"{r['type']}:{r['id']}@{r['version']}" for r in block['sources']));parts.append('')
    return '\n'.join(parts)
