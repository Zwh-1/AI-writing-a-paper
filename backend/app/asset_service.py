"""Shared asset catalog, editable sources and immutable content versions."""
import base64
import io
from fastapi import HTTPException
from . import db, security, documents


@db.atomic
def snapshot(pid, identity):
    security.require_project(pid)
    row=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(identity,pid))
    if not row:raise HTTPException(404,'资产不属于当前项目')
    prior=db.one('SELECT * FROM asset_versions WHERE asset_id=? ORDER BY revision DESC LIMIT 1',(identity,))
    if prior and all(prior[key]==row[key] for key in ('name','kind','sha256','metadata')):return prior
    version={'id':db.uid(),'project_id':pid,'asset_id':identity,'revision':prior['revision']+1 if prior else 1,
        **{key:row[key] for key in ('name','kind','sha256','metadata')},'created_at':db.now() if prior else row['created_at']}
    db.execute('INSERT INTO asset_versions VALUES(?,?,?,?,?,?,?,?,?)',tuple(version[key] for key in ('id','project_id','asset_id','revision','name','kind','sha256','metadata','created_at')))
    return version


def version(pid, identity, version_id):
    row=db.one('SELECT * FROM asset_versions WHERE id=? AND asset_id=? AND project_id=?',(version_id,identity,pid))
    if not row:raise HTTPException(404,'资产版本不属于当前资产或项目')
    return row


def get(pid, identity):
    current=snapshot(pid,identity)
    return describe(pid,identity,current)


def describe(pid,identity,current):
    metadata=db.loads(current['metadata'],{})
    value={'id':identity,'project_id':pid,'name':current['name'],'kind':current['kind'],'sha256':current['sha256'],
        'metadata':metadata,'asset_version_id':current['id'],'revision':current['revision'],
        'editor_type':'canvas' if metadata.get('studio_canvas_id') else 'rich_text' if metadata.get('studio_rich_text') else 'python' if metadata.get('code_sha256') else metadata.get('editor_type','image'),
        'created_at':current['created_at']}
    if current['kind']=='table' and metadata.get('spec',{}).get('kind')=='three_line_table':
        from .paper_tables import table_node
        value['node']=table_node(metadata['spec'],identity)
        value['node']['attrs']['assetVersionId']=current['id']
    return value


def catalog(pid, query='', tag=''):
    security.require_project(pid);result=[]
    for row in db.rows('SELECT id FROM artifacts WHERE project_id=? ORDER BY created_at DESC',(pid,)):
        value=get(pid,row['id']);metadata=value['metadata']
        if metadata.get('studio_deleted') or query and query.casefold() not in (value['name']+db.dumps(metadata)).casefold() or tag and tag not in metadata.get('tags',[]):continue
        result.append(value)
    return result


@db.atomic
def publish_derived(pid, record, raw, kind, name):
    from .resource_refs import version_of
    metadata={'editor_type':'webpage' if kind=='webpage' else 'derived_text','source_record_id':record['id'],
        'source_refs':[{'project_id':pid,'type':record['kind'],'id':record['id'],'version':version_of(pid,record['kind'],record['id'])}],
        'source_file_id':record['owner_id'],'egress':'selected_content','review_state':'unverified'}
    existing=db.one("SELECT id FROM artifacts WHERE project_id=? AND json_extract(metadata,'$.source_record_id')=?",(pid,record['id']))
    if existing:return get(pid,existing['id'])
    identity=db.uid();sha=security.put_blob(raw)
    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(identity,pid,name,kind,sha,db.dumps(metadata),db.now()))
    return get(pid,identity)


def rich_content(body, fallback=None, pid=None):
    fallback=fallback or {};title=body.get('title',fallback.get('name',''));text=body.get('text','');tags=body.get('tags',[])
    if not isinstance(title,str) or not title.strip() or len(title)>500 or not isinstance(text,str) or len(text)>150000 or not isinstance(tags,list) or any(not isinstance(t,str) or len(t)>80 for t in tags):raise HTTPException(422,'资产标题、正文或标签无效')
    document=documents.normalize(body['document']) if body.get('document') else documents.text_document(text)
    from . import resource_refs
    linked=list(body.get('source_refs',[])) if isinstance(body.get('source_refs',[]),list) else body['source_refs']
    resource_refs.validate(pid,linked)
    for node in documents.nodes(document):
        attrs=node.get('attrs',{})
        resource_refs.validate(pid,attrs.get('sourceRefs',[]))
        linked.extend(attrs.get('sourceRefs',[]))
        if attrs.get('artifactId'):
            if attrs['artifactId']==fallback.get('id'):raise HTTPException(422,'资产不能嵌入自身')
            linked.append(resource_refs.make(pid,'asset',attrs['artifactId']))
        if node['type']=='citation':linked.append(resource_refs.make(pid,'reference',attrs.get('citationId','')))
    metadata={**fallback.get('metadata',{}),'studio_rich_text':True,'editor_type':'rich_text','document':document,'tags':list(dict.fromkeys(t.strip() for t in tags if t.strip())),
        'egress':body.get('egress',fallback.get('metadata',{}).get('egress','selected_content'))}
    from .resource_refs import validate
    metadata['source_refs']=list({(ref['type'],ref['id'],ref['version']):ref for ref in [*fallback.get('metadata',{}).get('source_refs',[]),*linked]}.values())
    validate(pid,metadata['source_refs'])
    if metadata['egress'] not in ('selected_content','local_only','allowed'):raise HTTPException(422,'资产外发策略无效')
    return title.strip(),security.put_blob(documents.plain(document).encode()),metadata


@db.atomic
def create(pid, body):
    security.require_project(pid);name,sha,metadata=rich_content(body,pid=pid);identity=db.uid();metadata['revision']=1
    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(identity,pid,name,'note',sha,db.dumps(metadata),db.now()))
    return get(pid,identity)


@db.atomic
def update(pid, identity, body):
    old=get(pid,identity)
    if not old['metadata'].get('studio_rich_text'):raise HTTPException(422,'该资产不是富文本资产')
    if body.get('revision')!=old['metadata'].get('revision',1):raise HTTPException(409,'资产已更新，请重新读取')
    name,sha,metadata=rich_content(body,old,pid);metadata['revision']=old['metadata'].get('revision',1)+1
    db.execute('INSERT INTO project_revisions VALUES(?,?,?,?,?)',(db.uid(),pid,'studio_asset',db.dumps(old),db.now()))
    db.execute('UPDATE artifacts SET name=?,sha256=?,metadata=? WHERE id=?',(name,sha,db.dumps(metadata),identity))
    from .knowledge_lineage import changed
    changed(pid,'asset',identity,'资产新版本已保存，历史正文继续使用原版本')
    return get(pid,identity)


@db.atomic
def publish_canvas(pid, identity, body):
    from .research_records import get as record
    scene=record(pid,identity,'canvas')
    if scene['revision']!=body.get('revision'):raise HTTPException(409,'画布已更新，请重新导出')
    try:
        raw=base64.b64decode(str(body.get('png','')).split(',')[-1],validate=True)
        if len(raw)>12*1024*1024:raise ValueError('Image too large')
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as image:
            if image.format!='PNG' or image.width*image.height>16000000:raise ValueError('Invalid image')
            image.verify()
    except Exception as error:raise HTTPException(422,'画布图片无效') from error
    aid=db.uid();sha=security.put_blob(raw);metadata={'studio_canvas_id':identity,'scene_revision':scene['revision'],'editor_type':'canvas','egress':'selected_content','source':'user_canvas'}
    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,scene['data'].get('title') or '自由画布','figure',sha,db.dumps(metadata),db.now()))
    asset=get(pid,aid)
    return {**asset,'artifact_id':aid,'canvas_id':identity,'scene_revision':scene['revision'],'preview_url':f'/api/v1/projects/{pid}/studio/assets/{aid}'}


def pin_document(pid, doc, previous=None):
    old={n.get('attrs',{}).get('nodeId'):n for n in documents.nodes(previous or {})}
    for node in documents.nodes(doc):
        attrs=node.get('attrs',{})
        if node.get('type') not in ('figure','table') or not attrs.get('artifactId'):continue
        aid=attrs['artifactId']
        if not db.one('SELECT id FROM artifacts WHERE id=? AND project_id=?',(aid,pid)):
            raise HTTPException(422,'正文资产不属于当前项目')
        if attrs.get('assetVersionId'):version(pid,aid,attrs['assetVersionId']);continue
        before=old.get(attrs.get('nodeId'),{}).get('attrs',{})
        if before.get('artifactId')==aid:
            prior=before.get('assetVersionId')
            current=snapshot(pid,aid)
            attrs['assetVersionId']=prior or db.one('SELECT id FROM asset_versions WHERE asset_id=? ORDER BY revision LIMIT 1',(aid,))['id']
        else:attrs['assetVersionId']=snapshot(pid,aid)['id']


def frozen_assets(connection, pid):
    rows={r['id']:dict(r) for r in connection.execute('SELECT * FROM artifacts WHERE project_id=?',(pid,))}
    for raw in connection.execute('SELECT * FROM asset_versions WHERE project_id=?',(pid,)):
        row=dict(raw);rows[row['id']]={**row,'id':row['asset_id']}
    return rows
