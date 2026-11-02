"""Account for every image occurrence; inspected excerpts never replace image coverage."""
from collections import defaultdict
from fastapi import HTTPException
from . import db,security,material_policy
from .document_observations import observations

VERSION='document-image-coverage-v1'


def source_asset(source):
    loc=db.loads(source.get('locator'),{})
    return loc.get('evidence_asset_sha256') or loc.get('asset_sha256')


def file_report(pid,fid):
    original=db.one('SELECT * FROM files WHERE id=? AND project_id=? AND active=1',(fid,pid))
    if not original:raise HTTPException(404,'当前项目原件不存在')
    pages=db.rows('SELECT id,page_number,images,title FROM pages WHERE file_id=? ORDER BY page_number',(fid,))
    sources=db.rows('SELECT * FROM sources WHERE file_id=?',(fid,));by_page=defaultdict(list)
    current=db.setting('document_observation_supersessions',{})
    for source in sources:by_page[source['page_id']].append(source)
    units=db.rows('SELECT u.id,u.locator,u.origin,length(u.text) AS characters FROM knowledge_units u JOIN knowledge_heads h ON h.parse_id=u.parse_id WHERE h.file_id=?',(fid,))
    assets=[]
    for page in pages:
        listed={im['sha256']:{'sha256':im['sha256'],'kind':'embedded_image','name':im.get('name'),'inherited':im.get('inherited',False)} for im in db.loads(page['images'],[]) if im.get('sha256')}
        for source in by_page[page['id']]:
            sha=source_asset(source);loc=db.loads(source['locator'],{})
            if sha and (loc.get('rendered_evidence') or (loc.get('origin') or {}).get('kind') in ('office_slide','pdf_page')):
                listed.setdefault(sha,{'sha256':sha,'kind':'rendered_page','name':'原件整页渲染'})
        for sha,image in listed.items():
            matching=[s for s in by_page[page['id']] if source_asset(s)==sha]
            raw=[s for s in matching if s['method']=='windows_ocr']
            reviewed=[s for s in matching if s['id'] not in current and s['method'] in ('assistant_visual_excerpt','visual_transcription')]
            source_issues=sorted({issue for s in matching for issue in db.loads(s['locator'],{}).get('source_issues',[])})
            full=[s for s in reviewed if db.loads(s['locator'],{}).get('transcription_scope')=='complete_image_text'] if 'cropped_original' not in source_issues else []
            state='full_transcript_compared' if full else 'partial_transcript_compared' if reviewed else 'ocr_unreviewed' if raw else 'image_unread'
            related=[]
            for unit in units:
                loc=db.loads(unit['locator'],{});nested=loc.get('observation_locator',{})
                if loc.get('page')==page['page_number'] and (loc.get('asset_sha256') or loc.get('evidence_asset_sha256') or nested.get('asset_sha256') or nested.get('evidence_asset_sha256'))==sha:
                    related.append({'id':unit['id'],'origin':unit['origin'],'characters':unit['characters']})
            raw_observations=observations(raw)
            assets.append({**image,'file_id':fid,'page_id':page['id'],'page':page['page_number'],'title':page['title'],'state':state,
                'entire_image_compared':bool(full),'source_issues':source_issues,'raw_characters':sum(len(o['text']) for o in raw_observations),
                'verified_characters':sum(len(s['text']) for s in reviewed),'raw_source_ids':[s['id'] for s in raw],
                'verified_source_ids':[s['id'] for s in reviewed],'unit_views':related,
                'image_url':f'/api/v1/images/{sha}' if image['kind']=='embedded_image' else None,
                'read_url':f'/projects/{pid}/document-reading/documents/{fid}/assets/{sha}/read?page={page["page_number"]}'})
    states=('full_transcript_compared','partial_transcript_compared','ocr_unreviewed','image_unread')
    return {'file_id':fid,'file_name':original['name'],'file_hash':original['sha256'],'version':VERSION,'asset_occurrences':len(assets),
        'counts':{state:sum(a['state']==state for a in assets) for state in states},'assets':assets,
        'interpretation':'coverage_of_original_image_occurrences_not_rule_classification','all_image_text_compared':all(a['entire_image_compared'] for a in assets)}


def project_report(pid,include_assets=False):
    security.require_project(pid)
    files=db.rows("SELECT id FROM files WHERE project_id=? AND active=1 AND role='rules' ORDER BY id",(pid,))
    reports=[file_report(pid,f['id']) for f in files]
    states=('full_transcript_compared','partial_transcript_compared','ocr_unreviewed','image_unread')
    result={'version':VERSION,'file_count':len(reports),'asset_occurrences':sum(r['asset_occurrences'] for r in reports),
        'counts':{state:sum(r['counts'][state] for r in reports) for state in states},
        'all_image_text_compared':all(r['all_image_text_compared'] for r in reports) if reports else False}
    reuse=defaultdict(list)
    for report in reports:
        for asset in report['assets']:reuse[asset['sha256']].append({'file_id':report['file_id'],'page':asset['page'],'state':asset['state']})
    result.update(unique_image_assets=len(reuse),reused_image_assets=sum(len(places)>1 for places in reuse.values()),
        repeated_occurrences=sum(len(places)-1 for places in reuse.values()))
    if include_assets:
        result['documents']=reports
        result['asset_reuse']=[{'asset_sha256':sha,'occurrences':places,'relationship':'same_image_bytes_distinct_page_contexts'} for sha,places in reuse.items() if len(places)>1]
    return result


def read_asset(pid,fid,sha,page,intent='guidance',remote=False,max_chars=80000):
    original=db.one('SELECT * FROM files WHERE id=? AND project_id=? AND active=1',(fid,pid))
    if not original:raise HTTPException(404,'原件不属于本项目')
    profile=material_policy.source_context(original,intent)
    if not profile['allowed_for_intent'] or remote and not security.file_allows_remote(fid,pid):raise HTTPException(403,'原件不允许用于本次阅读')
    report=file_report(pid,fid)
    asset=next((a for a in report['assets'] if a['sha256']==sha and a['page']==page),None)
    if not asset:raise HTTPException(404,'图片不属于指定原页')
    rows=db.rows('SELECT * FROM sources WHERE page_id=?',(asset['page_id'],))
    raw=observations([s for s in rows if s['id'] in set(asset['raw_source_ids'])])
    verified=[s for s in rows if s['id'] in set(asset['verified_source_ids'])]
    count=sum(len(o['text']) for o in raw)+sum(len(s['text']) for s in verified)
    base={'file_id':fid,'file_hash':original['sha256'],'asset':asset,'source_purpose':profile['source_purpose'],'semantic_support':'claim_specific_check_required',
        'entire_image_compared':asset['entire_image_compared'],'human_approval':False,'requested_characters':count,
        'scope_note':'完整OCR观察与已核对摘录并列；OCR可能有错字和列顺序问题，未核对部分只能定位，不能证明事实。'}
    if type(max_chars) is not int or not 200<=max_chars<=500000:raise HTTPException(422,'完整图像观察读取预算无效')
    if count>max_chars:return {**base,'status':'needs_narrower_range','raw_observations':[],'verified_passages':[],'requested_scope_complete':False,
        'action':'请按unit_views选择明确单元或字符范围分步读取；未返回截断原文'}
    return {**base,'status':'read','requested_scope_complete':True,'raw_observations':[{'text':o['text'],'source_ids':o['source_ids'],
        'issues':o['issues'],'evidence_role':'unverified_full_observation','cannot_support_claims':True} for o in raw],
        'verified_passages':[{'source_id':s['id'],'text':s['text'],'coverage':db.loads(s['locator'],{}).get('transcription_scope','unassessed')} for s in verified]}


def locate(pid,fid,query,intent='guidance',limit=20):
    """Search the full observation index; return locations, never OCR as proof."""
    from .parsers import tokens
    original=db.one('SELECT * FROM files WHERE id=? AND project_id=? AND active=1',(fid,pid))
    if not original or not material_policy.source_context(original,intent)['allowed_for_intent']:raise HTTPException(403,'原件不属于本次阅读范围')
    if not isinstance(query,str) or not query.strip() or len(query)>1000 or type(limit) is not int or not 1<=limit<=50:raise HTTPException(422,'图像观察定位参数无效')
    terms=list(dict.fromkeys(tokens(query).split()))[:35]
    if not terms:return {'items':[],'evidence_role':'locations_only'}
    match=' OR '.join('"'+term.replace('"','')+'"' for term in terms)
    rows=db.rows('''SELECT u.id,u.locator,u.origin,bm25(knowledge_unit_fts) AS rank FROM knowledge_unit_fts ft
        JOIN knowledge_units u ON u.id=ft.unit_id JOIN knowledge_parses p ON p.id=u.parse_id JOIN knowledge_heads h ON h.parse_id=p.id
        WHERE knowledge_unit_fts MATCH ? AND p.project_id=? AND p.file_id=? AND u.review_state!='excluded'
        AND u.origin IN ('unverified_image_transcript','assistant_visual_excerpt','windows_ocr','visual_transcription') ORDER BY rank LIMIT ?''',(match,pid,fid,limit))
    result=[];seen=set()
    for row in rows:
        loc=db.loads(row['locator'],{});nested=loc.get('observation_locator',{})
        sha=loc.get('asset_sha256') or loc.get('evidence_asset_sha256') or nested.get('asset_sha256') or nested.get('evidence_asset_sha256')
        key=(loc.get('page'),sha)
        if not sha or key in seen:continue
        seen.add(key);result.append({'unit_id':row['id'],'file_id':fid,'page':loc.get('page'),'asset_sha256':sha,'matching_view':row['origin'],
            'evidence_role':'location_only','cannot_support_claims':True,'requires_original_comparison':True})
    return {'items':result,'evidence_role':'locations_only','query_scope':'complete_saved_image_observations_and_verified_passages','text_truncated':False}
