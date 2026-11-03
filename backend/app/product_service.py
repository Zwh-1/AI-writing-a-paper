"""Discover and reuse existing project outputs through their owning services."""
import copy
from fastapi import HTTPException
from . import db, security, documents, resource_refs as refs, research_records as records
from .product_sources import TABLES as OUTPUT_TYPES

LABELS = {'file': '原始资料', 'summary': '资料总结', 'reference': '文献书目', 'note': '思维随记',
          'annotation': '阅读批注', 'translation': '学术译文', 'guide': '阅读导读', 'message': '助手回复',
          'webpage': '交互网页', 'asset': '研究资产', 'fact': '研究事实', 'analysis': '统计分析',
          'wiki_page': 'Wiki 页面', 'assertion': '图谱论断', 'revision': '章节正文',
          'rule':'论文规则','rule_package':'已发布规则包','outline':'章节计划','finding':'检查意见',
          'mentor_task':'导师意见','reading':'问题阅读','evidence':'证据','source':'原文片段','unit':'结构化原文',
          'canvas':'自由画布','parse_result':'结构化解析结果','proposal':'正文候选','search':'文献发现结果',
          'comment':'正文评论','export':'交付记录','execution':'代码与截图执行结果'}
MAX_TEXT = 40000


def _error(error):
    return str(error.detail.get('message', '来源不可用') if isinstance(error.detail, dict) else error.detail)


def content(pid, ref, remote=False):
    """Resolve the exact selected version. Never accept client-supplied content."""
    if not isinstance(ref, dict) or ref.get('type') not in LABELS:
        raise HTTPException(422, '该类型不支持产物复用')
    refs.validate(pid, [ref], remote)
    kind, identity = ref['type'], ref['id']
    images = []
    visual = False
    native_table = False
    fragment=None
    if kind in OUTPUT_TYPES:
        from .product_sources import text
        title, text = text(pid, kind, identity)
    elif kind in records.KINDS:
        row = records.get(pid, identity, kind)
        data = row['data']
        title = data.get('title') or data.get('quote', '')[:60] or LABELS[kind]
        if kind == 'annotation':
            text = data['quote'] + ('\n批注：' + data['note'] if data.get('note') else '')
        elif kind == 'guide':
            text = data.get('summary', '') + '\n' + '\n'.join(point.get('text', '') for point in data.get('keypoints', []))
        elif kind == 'webpage':
            from .product_text import html_text
            text=html_text(data['html'])
        elif kind=='canvas':text=db.dumps(data['scene'])
        elif kind=='search':text=db.dumps(data.get('items',[]))
        elif kind=='parse_result':
            if data.get('status')!='completed':raise HTTPException(409,'结构化解析尚未完成')
            payload=db.loads(security.safe_object(data['blob_sha256']).read_text(encoding='utf-8'),{})
            text='\n'.join(block.get('text','') for block in payload.get('blocks',[]))
        elif kind=='proposal':
            if data.get('adopted_revision_id'):raise HTTPException(409,'该候选已采用，请使用对应正文')
            text=documents.plain(data['document']) if data.get('document') else db.dumps(data.get('operations',[]))
        else:
            text = documents.plain(data['document']) if data.get('document') else data.get('text', '')
        if data.get('document'):fragment=data['document']
    elif kind == 'asset':
        from .asset_service import get
        asset = get(pid, identity)
        title = asset['name']
        metadata = asset['metadata']
        refs.validate(pid, metadata.get('source_refs', []), remote)
        visual = asset['kind'] in ('figure', 'image', 'screenshot')
        text = documents.plain(metadata['document']) if metadata.get('document') else metadata.get('caption', '')
        native_table=asset['kind']=='table' and metadata.get('spec',{}).get('kind')=='three_line_table'
        if native_table:
            spec=metadata['spec']
            text='\n'.join(' | '.join(map(str,row)) for row in [spec['headers'],*spec['rows']])+'\n'+spec.get('note','')
            fragment={'type':'doc','content':[asset['node']]}
        elif metadata.get('document'):fragment=metadata['document']
        if remote:
            from .assistant_context import artifact
            value, images, _, _ = artifact(pid, identity)
            text = value.get('text', text)
        elif not visual and not text and asset['sha256']:
            raw = security.safe_object(asset['sha256']).read_bytes()
            if len(raw) <= 24*1024*1024:
                if raw.lstrip().startswith(b'%PDF-') or raw.startswith(b'PK') and (asset['name'].lower().endswith('.docx') or metadata.get('media_type')=='application/vnd.openxmlformats-officedocument.wordprocessingml.document'):
                    from .parsers import parse_pages
                    text='\n\n'.join(page['text'] for page in parse_pages(raw,'.pdf' if raw.lstrip().startswith(b'%PDF-') else '.docx'))
                else:
                    for encoding in ('utf-8-sig','utf-16','gb18030'):
                        try:text=raw.decode(encoding);break
                        except UnicodeError:pass
        if asset['kind']=='webpage' and text:
            from .product_text import html_text
            text=html_text(text)
        if not text and visual:
            text = title + '\n图像资产；请查看原图。'
    elif kind == 'file':
        from .source_access import require_file
        from .content_view import summary_pages
        original = require_file(pid,identity,remote)
        pages=db.rows("SELECT id,page_number,title,text,notes FROM pages WHERE file_id=? AND review_state!='not_applicable' ORDER BY page_number",(identity,))
        pages,_=summary_pages(original,pages)
        title,text=original['name'],'\n\n'.join(page['text'] + ('\n'+page['notes'] if page.get('notes') else '') for page in pages)
    elif kind == 'reference':
        row = records.require_reference(pid, identity)
        data = row['metadata']
        title = data.get('title', '文献书目')
        text = db.dumps(data)
        fragment={'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':title+' '},{'type':'citation','attrs':{'citationId':identity}}]}]}
    elif kind == 'revision':
        row = db.one('SELECT r.*,s.title FROM revisions r JOIN sections s ON s.id=r.section_id WHERE r.id=? AND s.project_id=?', (identity, pid))
        title, text = row['title'], row['text']
        fragment=db.loads(row['document'],{})
    elif kind=='source':
        row=db.one('SELECT s.*,f.name FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(identity,pid))
        title,text=row['name']+' · 原文片段',row['text']
    elif kind=='unit':
        from .knowledge_units import get
        row=get(pid,identity,remote);title,text='结构化原文 '+str(row['ordinal']),row['text']
    elif kind=='evidence':
        from .writing_agent.evidence import get
        row=get(pid,identity,remote);payload=row['payload'];title=payload.get('title') or '证据 · '+row['kind']
        text=db.dumps({'payload':payload,'conditions':row['conditions'],'validity':row['validity']})
    elif kind == 'wiki_page':
        row = db.one('SELECT * FROM wiki_pages WHERE id=? AND project_id=?', (identity, pid))
        title = row['title']
        text = '\n\n'.join(block['text'] for block in db.rows('SELECT text FROM wiki_blocks WHERE version_id=? ORDER BY position', (row['current_version_id'],)))
    else:
        row = db.one('SELECT proposition_json FROM claim_versions WHERE id=? AND project_id=?', (identity, pid))
        title, text = '图谱论断', db.dumps(db.loads(row['proposition_json'], {}))
    if not text.strip():
        raise HTTPException(422, '产物没有可复用的文本，请在原功能中完善内容')
    if len(text) > MAX_TEXT:
        raise HTTPException(422, '产物超过40000字符，请先在原功能中整理为较小范围')
    refs.validate(pid,[ref],remote)
    return {'title': title, 'text': text, 'source_ref': ref, 'kind': kind,
            'classification': 'unverified_reused_content', 'visual': visual, 'native_table':native_table, 'images': images,
            'document':fragment or documents.text_document(text)}


def catalog(pid, query='', kind='', section_id='', limit=40, offset=0):
    security.require_project(pid)
    if kind and kind not in LABELS or len(query) > 500 or not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
        raise HTTPException(422, '产物筛选或分页参数无效')
    if section_id:
        section = security.require_section(section_id)
        if section['project_id'] != pid:
            raise HTTPException(404, '目标章节不属于项目')
    from .product_catalog import sql as projection
    sql,count=projection()
    where = " WHERE (?='' OR kind=?) AND (?='' OR instr(lower(title||' '||search_text),lower(?))>0) AND (?='' OR section_id=?)"
    args = [pid] * count + [kind, kind, query, query, section_id, section_id]
    total = db.one('SELECT count(*) n FROM (' + sql + ')' + where, args)['n']
    rows = db.rows('SELECT kind,id,title,stamp,section_id FROM (' + sql + ')' + where + ' ORDER BY stamp DESC,kind,id LIMIT ? OFFSET ?', [*args, limit, offset])
    items = [describe(pid,row) for row in rows]
    return {'items': items, 'total': total, 'offset': offset, 'limit': limit,
            'kinds': [{'id': key, 'label': label} for key, label in LABELS.items()]}


def describe(pid,row):
    try:
        source=refs.make(pid,row['kind'],row['id'])
        actions=['note','asset','draft','chat','material','plan','review','execute']
        if row['kind']=='asset':
            asset=db.one('SELECT kind,metadata FROM artifacts WHERE id=? AND project_id=?',(row['id'],pid))
            if asset['kind'] in ('figure','image','screenshot'):actions=['figure','chat','execute']
            elif asset['kind']=='table' and db.loads(asset['metadata'],{}).get('spec',{}).get('kind')=='three_line_table':actions.append('figure')
        if row['kind']=='parse_result':
            data=db.loads(db.one('SELECT data FROM studio_records WHERE id=?',(row['id'],))['data'],{})
            if data.get('status')!='completed':actions=[]
        if row['kind']=='proposal':
            data=db.loads(db.one('SELECT data FROM studio_records WHERE id=?',(row['id'],))['data'],{})
            if data.get('adopted_revision_id'):return {**row,'label':LABELS[row['kind']],'source_ref':None,'state':'stale','reason':'候选已采用，请使用当前正文','actions':[]}
        return {**row,'label':LABELS[row['kind']],'source_ref':source,'state':'current','reason':'','actions':actions,'open_location':open_location(pid,row)}
    except HTTPException as error:
        return {**row,'label':LABELS[row['kind']],'source_ref':None,'state':'stale','reason':_error(error),'actions':[]}


def open_location(pid,row):
    kind,identity=row['kind'],row['id']
    if kind=='canvas':return {'page':'studio','view':'canvas','canvasId':identity}
    if kind=='asset':return {'page':'studio','view':'assets','assetId':identity}
    if kind=='note':return {'page':'studio','view':'notes','noteId':identity}
    if kind=='search':return {'page':'studio','view':'discover'}
    if kind=='message':
        record=records.get(pid,identity,kind)
        return {'page':'studio','view':'chat','conversationId':record['owner_id']}
    if kind=='proposal':return {'page':'products','proposalId':identity,'sectionId':row['section_id']}
    if kind in ('rule','rule_package'):return {'page':'rules'}
    if kind=='outline':return {'page':'outline','sectionId':identity}
    if kind in ('finding','mentor_task'):return {'page':'review','sectionId':row['section_id']}
    if kind=='export':return {'page':'exports'}
    if kind=='execution':return {'page':'advanced'}
    if kind in ('wiki_page','assertion'):return {'page':'references','view':'knowledge'}
    if kind=='evidence':return {'page':'references','view':'evidence'}
    if kind=='reference':return {'page':'references','view':'library'}
    if kind=='revision':return {'page':'editor','sectionId':row['section_id']}
    if kind in ('file','summary'):return {'page':'references','view':'parallel','fileId':identity}
    if kind in ('source','unit','reference','reading','translation','annotation','guide','webpage','parse_result'):
        ref=refs.make(pid,kind,identity)
        location=refs.resolve(pid,ref)['location']
        if location.get('file_id'):return {'page':'references','view':'parallel','fileId':location['file_id'],'unitId':location.get('unit_id'),'parseId':location.get('parse_id')}
    return {'page':'products','productType':kind,'productId':identity}


def item(pid,kind,identity):
    security.require_project(pid)
    if kind not in LABELS:raise HTTPException(422,'产物类型无效')
    from .product_catalog import sql as projection
    sql,count=projection()
    row=db.one('SELECT kind,id,title,stamp,section_id FROM ('+sql+') WHERE kind=? AND id=?',[*([pid]*count),kind,identity])
    if not row:raise HTTPException(404,'产物不存在、已归档或不属于当前项目')
    return describe(pid,row)


def local_context(pid,sid,base_revision_id):
    section=security.require_section(sid)
    if section['project_id']!=pid:raise HTTPException(404,'目标章节不属于项目')
    if section['current_revision_id']!=base_revision_id:raise HTTPException(409,'正文已更新，请重新读取')
    current=documents.current(section)
    document=db.loads(current['document'],{}) if current else {'type':'doc','content':[]}
    return section,current,document,refs.digest([base_revision_id,document])


def fragment(value,section_id=None):
    result=copy.deepcopy(value['document'])
    identities={node.get('attrs',{}).get('nodeId'):db.uid() for node in documents.nodes(result) if node.get('attrs',{}).get('nodeId')}
    for node in documents.nodes(result):
        if node['type']=='text':continue
        attrs=node.setdefault('attrs',{})
        attrs['nodeId']=identities.get(attrs.get('nodeId'),db.uid())
        if attrs.get('targetId') in identities and not attrs.get('targetSectionId'):
            attrs['targetId']=identities[attrs['targetId']]
            if section_id:attrs['targetSectionId']=section_id
    for node in result.get('content',[]):
        sources=node.setdefault('attrs',{}).setdefault('sourceRefs',[])
        if value['source_ref'] not in sources:sources.append(value['source_ref'])
    return result.get('content',[])


def selection(pid, sources, remote=False):
    if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
        raise HTTPException(422, '请选择1至20个产物')
    unique = {}
    for ref in sources:
        value = content(pid, ref, remote)
        unique[(ref['type'], ref['id'], ref['version'])] = value
    values = list(unique.values())
    if sum(len(value['text']) for value in values) > MAX_TEXT:
        raise HTTPException(422, '所选产物合计超过40000字符，请减少选择')
    return values


def section_inputs(pid,sid,remote=False):
    from .section_materials import snapshot
    return snapshot(pid,sid,remote)[0]


@db.atomic
def reuse(pid, body):
    from .product_contracts import reuse_command
    body=reuse_command(body)
    target = body.get('target')
    if target not in ('note', 'asset', 'draft', 'figure','material','plan','review'):
        raise HTTPException(422, '产物用途无效')
    values = selection(pid, body.get('sources'))
    source_refs = [value['source_ref'] for value in values]
    sid = body.get('section_id') or None
    if sid:
        section = security.require_section(sid)
        if section['project_id'] != pid:
            raise HTTPException(404, '目标章节不属于当前项目')
    title = body.get('title') or values[0]['title'] + (' 等' if len(values) > 1 else '')+{'material':' · 写作材料','plan':' · 计划来源','review':' · 修改依据'}.get(target,'')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 500:
        raise HTTPException(422, '产物标题须为1至500字')
    if target in ('draft', 'figure','material','plan') and not sid:
        raise HTTPException(422, '请先选择目标章节')
    if target == 'figure':
        if len(values) != 1 or values[0]['kind'] != 'asset' or not (values[0]['visual'] or values[0]['native_table']):
            raise HTTPException(422, '请选择一个图像或原生表格资产插入正文')
        from .workspace_intents import create
        return {'target': target, 'intent': create(pid, {'section_id': sid, 'artifact_id': source_refs[0]['id'], **({'base_revision_id': body['revision_id']} if 'revision_id' in body else {})})}
    if any(value['visual'] for value in values):
        raise HTTPException(422, '图像资产请使用“插入图表”，保留实际图像')
    reuse_key = refs.digest([sorted((ref['type'], ref['id'], ref['version']) for ref in source_refs), target, sid, title, body.get('revision_id')])
    if target in ('note', 'draft','material','plan','review'):
        previous = db.one("SELECT id FROM studio_records WHERE project_id=? AND kind=? AND json_extract(data,'$.reuse_key')=? AND deleted_at IS NULL", (pid, 'proposal' if target == 'draft' else 'note', reuse_key))
        if previous:
            return {'target': target, 'record': records.get(pid, previous['id']), 'reused': True}
    else:
        previous = db.one("SELECT id FROM artifacts WHERE project_id=? AND json_extract(metadata,'$.reuse_key')=? AND COALESCE(json_extract(metadata,'$.studio_deleted'),0)=0", (pid, reuse_key))
        if previous:
            from .asset_service import get
            return {'target': target, 'asset': get(pid, previous['id']), 'reused': True}
    text = '\n\n'.join(value['title'] + '\n' + value['text'] for value in values)
    combined={'type':'doc','content':[node for value in values for node in fragment(value,sid)]}
    if target in ('note','material','plan','review'):
        extra={}
        if target=='plan':
            if any(ref['type']=='outline' and ref['id']==sid for ref in source_refs):raise HTTPException(422,'本章计划不能作为修改自身的来源，请选择其他产物')
            goal=(section['goal']+'\n\n' if section['goal'] else '')+text
            if len(goal)>40000:raise HTTPException(422,'章节目标超过40000字符，请减少选择')
            db.execute('INSERT INTO project_revisions VALUES(?,?,?,?,?)',(db.uid(),pid,'product_plan',db.dumps({'section_id':sid,'previous_goal':section['goal'],'source_refs':source_refs}),db.now()))
            db.execute('UPDATE sections SET goal=? WHERE id=?',(goal,sid))
            db.execute('UPDATE projects SET outline_revision=outline_revision+1,updated_at=? WHERE id=?',(db.now(),pid))
            from .knowledge_lineage import changed
            changed(pid,'outline',sid,'章节计划已更新，相关内容需复查')
            documents.mark_downstream(pid,sid,'章节计划已更新')
        if target=='review':
            identity=db.uid()
            db.execute('INSERT INTO mentor_tasks VALUES(?,?,?,?,?,?,?)',(identity,pid,sid,text,'open','',db.now()))
            extra['mentor_task_id']=identity
        if target in ('material','plan'):extra['use_for_writing']=True
        record = records.notes.save(pid, {'title': title, 'text': text, 'document':combined, 'source_refs': source_refs, 'section_id': sid, 'reuse_key': reuse_key,**extra})
        result = {'record': record}
    elif target == 'asset':
        from .asset_service import create, get
        asset = create(pid, {'title': title, 'text': text, 'document':combined, 'source_refs': source_refs})
        metadata = {**asset['metadata'], 'reuse_key': reuse_key, 'section_id': sid, 'review_state': 'unverified'}
        db.execute('UPDATE artifacts SET metadata=? WHERE id=?', (db.dumps(metadata), asset['id']))
        result = {'asset': get(pid, asset['id'])}
    else:
        if 'revision_id' not in body or body['revision_id'] is not None and not isinstance(body['revision_id'],str):
            raise HTTPException(422, '请重新读取目标章节的正文版本')
        _, current, document, permission = local_context(pid,sid,body['revision_id'])
        candidate = copy.deepcopy(document)
        candidate.setdefault('content', []).extend(combined['content'])
        proposal = records.save(pid, 'proposal', {'title': title, 'document': candidate, 'operations': [],
            'origin':'product_reuse','base_revision_id': current['id'] if current else None, 'permission_stamp': permission, 'source_refs': source_refs, 'reuse_key': reuse_key}, owner=sid)
        result = {'record': proposal}
    db.audit(pid, 'product_reused', {'target': target, 'source_refs': source_refs, 'section_id': sid})
    return {'target': target, **result, 'reused': False}
