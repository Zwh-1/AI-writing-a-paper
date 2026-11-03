"""Independent, versioned evidence collection anchored to immutable originals."""
import hashlib
import math
import re
from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
from fastapi import APIRouter, HTTPException
from . import db, security
from . import document_structure as structure
from .material_policy import research_sql,is_research,source_context,markers

router = APIRouter(prefix='/projects/{pid}/evidence-knowledge')
_INPUT_CACHE=ContextVar('knowledge_input_snapshot_cache',default=None)
_READ_CACHE=ContextVar('knowledge_read_snapshot_cache',default=None)

@contextmanager
def cache_reads(fresh=False):
    if _READ_CACHE.get() is not None and not fresh:yield;return
    token=_READ_CACHE.set({})
    try:
        with cache_inputs(fresh=fresh):yield
    finally:_READ_CACHE.reset(token)

@contextmanager
def cache_inputs(fresh=False):
    if _INPUT_CACHE.get() is not None and not fresh:
        yield
        return
    token=_INPUT_CACHE.set({})
    try:yield
    finally:_INPUT_CACHE.reset(token)


def digest(value):
    return hashlib.sha256(db.dumps(value).encode('utf-8')).hexdigest()


def observation_scope(locator):
    """A complete read of an excerpt must never mean complete image coverage."""
    observed=locator.get('observation_locator',{})
    scope=locator.get('transcription_scope') or observed.get('transcription_scope')
    source_issues=locator.get('source_issues') or observed.get('source_issues',[])
    if locator.get('evidence_role')=='unverified_full_observation':
        return {'transcription_scope':'full_saved_ocr_observation','entire_original_image_transcribed':False,'literal_scope':'unverified_observation_only',
            'requires_original_comparison':True,'cannot_support_claims':True,'human_approval':False,'semantic_support':'not_verified'}
    if not scope:return None
    return {'transcription_scope':scope,'entire_original_image_transcribed':scope=='complete_image_text',
        'literal_scope':'listed_passages_only' if scope!='complete_image_text' else 'all_visible_image_text',
        'untranscribed_image_content_retained':scope!='complete_image_text','human_approval':False,
        'source_issues':source_issues,'original_content_complete':False if 'cropped_original' in source_issues else None,
        'semantic_support':'claim_specific_check_required'}


def integer(body,key,default,minimum,maximum):
    value=body.get(key,default)
    if type(value) is not int or not minimum<=value<=maximum:
        raise HTTPException(422,f'{key}须为{minimum}至{maximum}的整数')
    return value


def index_chunks(uid, text):
    # These windows are index locations only. Evidence always reads the parent or explicit range.
    for start in range(0, len(text), 180):
        value = text[start:start + 240]
        db.execute('INSERT INTO knowledge_unit_chunks VALUES(?,?,?,?,?,?)', (db.uid(), uid, start, start + len(value), value, digest(value)))


def file_record(pid, fid, allow_material=False):
    security.require_project(pid)
    row = db.one("SELECT f.* FROM files f WHERE f.id=? AND f.project_id=? AND f.active=1"+('' if allow_material else ' AND '+research_sql()), (fid, pid))
    if not row:
        raise HTTPException(404, '当前研究文献不存在')
    return row


def input_stamp(original):
    cache=_INPUT_CACHE.get();key=(original['id'],original['sha256'])
    if cache is not None and key in cache:return cache[key]
    from .document_observations import VERSION
    sources=db.rows("SELECT id,text,method,locator FROM sources WHERE file_id=? AND method!='document_rule_context' ORDER BY id", (original['id'],))
    mapping=db.setting('document_observation_supersessions',{})
    source_ids={s['id'] for s in sources}
    related={key:value for key,value in mapping.items() if key in source_ids}
    base=[original['sha256'],db.rows('SELECT id,text,notes FROM pages WHERE file_id=? ORDER BY page_number',(original['id'],)),
          [{key:s[key] for key in ('id','text','method')} for s in sources]]
    # Rule contexts and any document with observation replacements depend on
    # coverage metadata too. Unrelated literature readings keep their versions.
    value=digest(base) if original['role']!='rules' and not related else digest([VERSION,base,[(s['id'],s['locator']) for s in sources],related,
                   db.rows('SELECT id,images FROM pages WHERE file_id=? ORDER BY page_number',(original['id'],))])
    if cache is not None:cache[key]=value
    return value


def parse_current(parse, original=None):
    original = original or db.one('SELECT * FROM files WHERE id=?', (parse['file_id'],))
    return bool(original and original['active'] and original['sha256'] == parse['file_hash'] and input_stamp(original) == parse['input_hash'])


def get(pid, uid, remote=False, intent='research'):
    cache=_READ_CACHE.get();key=('unit',pid,uid,remote,intent)
    if cache is not None and key in cache:return deepcopy(cache[key])
    result=_get_uncached(pid,uid,remote,intent)
    if cache is not None:cache[key]=deepcopy(result)
    return result


def _get_uncached(pid, uid, remote=False, intent='research'):
    row = db.one('SELECT u.*,p.project_id,p.file_id,p.file_hash,p.input_hash,p.engine,p.strategy,p.created_at,f.name,f.extension,f.role,h.parse_id AS head_id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id JOIN files f ON f.id=p.file_id LEFT JOIN knowledge_heads h ON h.file_id=f.id WHERE u.id=? AND p.project_id=?', (uid, pid))
    if not row:
        raise HTTPException(404, '证据单元不属于当前项目')
    current = row['head_id'] == row['parse_id'] and parse_current(row)
    policy=source_context({'id':row['file_id'],'role':row['role'],'extension':row['extension']},intent)
    if remote and not policy['allowed_for_intent']:raise HTTPException(403,'该资料未获准用于本次阅读用途')
    if remote and (not current or row['review_state'] == 'excluded' or not security.file_allows_remote(row['file_id'], pid)):
        raise HTTPException(403, '证据单元已过期、已排除或其来源禁止外发')
    locator = db.loads(row['locator'], {})
    page = db.one('SELECT id,review_state FROM pages WHERE file_id=? AND page_number=?', (row['file_id'], locator.get('page', 1)))
    segment_pages={s.get('locator',{}).get('page') for s in locator.get('segments',[])}|{locator.get('page',1)}
    excluded={r['page_number'] for r in db.rows("SELECT page_number FROM pages WHERE file_id=? AND review_state='not_applicable'",(row['file_id'],))}
    if segment_pages & excluded:
        current = False
        if remote:
            raise HTTPException(403, '证据所在原页已排除')
    disputes = db.rows('SELECT * FROM knowledge_disputes WHERE unit_id=? ORDER BY created_at,id', (uid,))
    checks=db.rows('SELECT * FROM knowledge_claim_checks WHERE unit_id=? ORDER BY created_at DESC',(uid,))
    for check in checks:
        check['effective_support']=check['support'] if current and row['review_state']=='reviewed' and not any(d['state']=='open' for d in disputes) else 'stale'
    return {**row, 'locator': locator, 'heading_path': db.loads(row['heading_path'], []), 'issues': db.loads(row['issues'], []),'material_use':policy,'content_roles':markers(row['text'],policy['source_purpose']),
            'current': current, 'disputes': disputes, 'page_id': page['id'] if page else None,
            'original_reviewed': row['review_state'] == 'reviewed', 'semantic_support': 'claim_specific_check_required',
            'claim_checks': checks}


def changed(pid, ids, note):
    ids=list(ids)
    from .knowledge_lineage import changed as knowledge_changed
    for uid in ids:knowledge_changed(pid,'unit',uid,note)
    from .writing_agent.impact import changed as propagate
    for uid in ids:
        db.execute("UPDATE citations SET support='unknown' WHERE id IN (SELECT citation_id FROM citation_knowledge_links WHERE unit_id=?)",(uid,))
        db.execute("UPDATE evidence_items SET validity='stale' WHERE project_id=? AND origin_type='knowledge_unit' AND origin_id=? AND validity='current'", (pid, uid))
        propagate(pid, 'knowledge_unit', uid, note)


def invalidate_file(pid,fid,note):
    from .knowledge_lineage import changed as knowledge_changed
    knowledge_changed(pid,'file',fid,note)
    db.execute("UPDATE evidence_items SET validity='stale' WHERE project_id=? AND origin_type='knowledge_unit' AND origin_id IN (SELECT u.id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE p.file_id=?)",(pid,fid))
    db.execute("UPDATE citations SET support='unknown' WHERE id IN (SELECT l.citation_id FROM citation_knowledge_links l JOIN knowledge_units u ON u.id=l.unit_id JOIN knowledge_parses p ON p.id=u.parse_id WHERE p.file_id=?)",(fid,))
    from .writing_agent.impact import changed as propagate
    propagate(pid,'file',fid,note)


@db.atomic
def build(pid, fid, strategy='paragraphs', engine=structure.ENGINE, activate=True, allow_material=False):
    original = file_record(pid, fid,allow_material)
    with security.safe_object(original['sha256']).open('rb') as content:
        if hashlib.file_digest(content, 'sha256').hexdigest() != original['sha256']:
            raise HTTPException(409, '原件哈希校验失败，不能建立新解析版本')
    if strategy not in structure.STRATEGIES or engine not in (structure.ENGINE, 'legacy'):
        raise HTTPException(422, '解析引擎或分段方案无效')
    if original['status'] != 'ready':
        raise HTTPException(409, '请先完成原文件解析，再建立结构化版本')
    stamp = input_stamp(original)
    old = db.one('SELECT * FROM knowledge_parses WHERE file_id=? AND input_hash=? AND engine=? AND strategy=?', (fid, stamp, engine, strategy))
    if old:
        if activate:
            activate_parse(pid, old['id'])
        return parse_detail(pid, old['id'])
    pages = structure.extract(original, engine)
    parse_id = db.uid()
    db.execute('INSERT INTO knowledge_parses(id,project_id,file_id,file_hash,input_hash,engine,strategy,created_at) VALUES(?,?,?,?,?,?,?,?)',
               (parse_id, pid, fid, original['sha256'], stamp, engine, strategy, db.now()))
    from .parsers import tokens
    ordinal = 0
    for page in pages:
        for candidate in structure.candidates(page, strategy):
            uid = db.uid(); ordinal += 1
            db.execute('INSERT INTO knowledge_units(id,parse_id,ordinal,kind,text,content_hash,locator,heading_path,origin,issues) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (uid, parse_id, ordinal, candidate['kind'], candidate['text'], digest(candidate['text']), db.dumps(candidate['locator']), db.dumps(candidate['heading_path']), candidate['origin'], db.dumps(candidate['issues'])))
            db.execute('INSERT INTO knowledge_unit_fts VALUES(?,?)', (uid, tokens(candidate['text'])))
            index_chunks(uid, candidate['text'])
    if activate:
        activate_parse(pid, parse_id)
    db.audit(pid, 'knowledge_parse_created', {'parse_id': parse_id, 'file_id': fid, 'units': ordinal, 'engine': engine, 'strategy': strategy})
    return parse_detail(pid, parse_id)


def parse_detail(pid, identity):
    parse = db.one('SELECT * FROM knowledge_parses WHERE id=? AND project_id=?', (identity, pid))
    if not parse:
        raise HTTPException(404, '解析版本不存在')
    return {**parse, 'current_input': parse_current(parse), 'units': db.rows('SELECT id,ordinal,kind,content_hash,locator,origin,review_state,issues,length(text) AS characters FROM knowledge_units WHERE parse_id=? ORDER BY ordinal', (identity,))}


@db.atomic
def activate_parse(pid, identity):
    parse = db.one('SELECT * FROM knowledge_parses WHERE id=? AND project_id=?', (identity, pid))
    if not parse or not parse_current(parse):
        raise HTTPException(409, '该解析版本的原文已变化，不能设为当前版本')
    prior = db.one('SELECT parse_id FROM knowledge_heads WHERE file_id=?', (parse['file_id'],))
    if prior and prior['parse_id'] == identity:
        return {'parse_id': identity, 'unchanged': True}
    db.execute('INSERT INTO knowledge_heads VALUES(?,?) ON CONFLICT(file_id) DO UPDATE SET parse_id=excluded.parse_id', (parse['file_id'], identity))
    if prior:
        changed(pid, [r['id'] for r in db.rows('SELECT id FROM knowledge_units WHERE parse_id=?', (prior['parse_id'],))], '文献解析或分段版本已切换，需复查引用范围')
        from .knowledge_lineage import changed as invalidate
        invalidate(pid,'file',parse['file_id'],'当前解析版本已切换，相关派生内容需复查')
    db.audit(pid, 'knowledge_parse_activated', {'parse_id': identity, 'previous': prior})
    return {'parse_id': identity, 'previous': prior}


def list_units(pid, state='all', fid='', query='', offset=0, limit=20, _all=False):
    security.require_project(pid)
    if state not in ('all', 'pending', 'reviewed', 'excluded', 'disputed', 'stale') or offset < 0 or not 1 <= limit <= 100 or len(query) > 5000:
        raise HTTPException(422, '筛选或分页参数无效')
    rows = db.rows('SELECT u.*,p.file_id,p.project_id,p.file_hash,p.input_hash,f.name,f.extension,p.engine,p.strategy,(SELECT count(*) FROM knowledge_disputes d WHERE d.unit_id=u.id AND d.state=\'open\') AS open_disputes,(SELECT review_state FROM pages pg WHERE pg.file_id=f.id AND pg.page_number=json_extract(u.locator,\'$.page\')) AS page_review_state FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id JOIN knowledge_heads h ON h.parse_id=p.id JOIN files f ON f.id=p.file_id WHERE p.project_id=? AND f.active=1 AND '+research_sql()+' ORDER BY f.name,u.ordinal', (pid,))
    stamps = {}; result = []
    excluded={(r['file_id'],r['page_number']) for r in db.rows("SELECT pg.file_id,pg.page_number FROM pages pg JOIN files f ON f.id=pg.file_id WHERE f.project_id=? AND pg.review_state='not_applicable'",(pid,))}
    for row in rows:
        if fid and row['file_id'] != fid or query and query.casefold() not in (row['text'] + row['name']).casefold():
            continue
        if row['file_id'] not in stamps:
            stamps[row['file_id']] = parse_current(row)
        row['current'] = stamps[row['file_id']]
        row['locator'] = db.loads(row['locator'], {}); row['issues'] = db.loads(row['issues'], [])
        segment_pages={s.get('locator',{}).get('page') for s in row['locator'].get('segments',[])}|{row['locator'].get('page',1)}
        if any((row['file_id'],p) in excluded for p in segment_pages):
            row['current'] = False
        if state == 'stale' and row['current'] or state == 'disputed' and not row['open_disputes']:
            continue
        if state not in ('all', 'stale', 'disputed') and (row['review_state'] != state or not row['current']):
            continue
        row['heading_path'] = db.loads(row['heading_path'], [])
        result.append(row)
    return {'items': result if _all else result[offset:offset + limit], 'total': len(result), 'offset': offset, 'limit': limit}


def read(pid, uid, remote=False, before=1, after=1, max_chars=16000, start=None, end=None,intent='research'):
    cache=_READ_CACHE.get();key=('read',pid,uid,remote,before,after,max_chars,start,end,intent)
    if cache is not None and key in cache:return deepcopy(cache[key])
    result=_read_uncached(pid,uid,remote,before,after,max_chars,start,end,intent)
    if cache is not None:cache[key]=deepcopy(result)
    return result


def _read_uncached(pid, uid, remote=False, before=1, after=1, max_chars=16000, start=None, end=None,intent='research'):
    item = get(pid, uid, remote,intent)
    if not 0 <= before <= 3 or not 0 <= after <= 3 or not 200 <= max_chars <= 40000:
        raise HTTPException(422, '上下文范围或读取预算无效')
    cache=_READ_CACHE.get();rows_key=('parse_rows',item['parse_id'])
    all_rows=cache.get(rows_key) if cache is not None else None
    if all_rows is None:
        all_rows = db.rows('SELECT id,ordinal,text,content_hash,locator,kind,origin,review_state FROM knowledge_units WHERE parse_id=? ORDER BY ordinal', (item['parse_id'],))
        if cache is not None:cache[rows_key]=all_rows
    stream=item['locator'].get('context_stream',item['origin'])
    all_rows=[r for r in all_rows if db.loads(r['locator'],{}).get('context_stream',r['origin'])==stream]
    position=next(i for i,r in enumerate(all_rows) if r['id']==uid)
    rows=all_rows[max(0,position-before):position+after+1]
    # Never read across extraction streams (e.g. native text into a separate OCR transcript).
    permitted = []; omitted=[]
    for row in rows:
        loc = db.loads(row['locator'], {})
        page = db.one('SELECT review_state FROM pages WHERE file_id=? AND page_number=?', (item['file_id'], loc.get('page', 1)))
        if row['review_state']!='excluded' and (not page or page['review_state'] != 'not_applicable'):
            permitted.append({**row, 'locator': loc})
        else:omitted.append(row['id'])
    if start is not None or end is not None:
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(item['text']):
            raise HTTPException(422, '原文区间无效')
        permitted = [{'id': uid, 'ordinal': item['ordinal'], 'text': item['text'][start:end], 'locator': item['locator'], 'range_start': start, 'range_end': end}]
    total = sum(len(r['text']) for r in permitted)
    base = {'unit_id': uid, 'unit_hash': item['content_hash'], 'parse_id': item['parse_id'], 'file_id': item['file_id'], 'file_hash': item['file_hash'],
            'locator': item['locator'], 'current': item['current'], 'review_state': item['review_state'], 'issues': item['issues'],
            'disputes': item['disputes'], 'semantic_support': 'claim_specific_check_required', 'requested_characters': total}
    if observation_scope(item['locator']):base['observation_scope']=observation_scope(item['locator'])
    base.update(requested_scope_complete=not omitted,semantic_context_status='unassessed',semantic_context_complete=None,
                material_use=item['material_use'],content_roles=item['content_roles'],omitted_context_ids=omitted,
                reading_scope_note='完整只指所请求文本已读出，不表示语义上下文充分；需按具体问题查阅方法、条件、结果与局限')
    base['context_complete']=not omitted
    base['context_complete_meaning']='deprecated_alias_of_requested_scope_complete_not_semantic_sufficiency'
    if total > max_chars:
        return {**base, 'status': 'needs_narrower_range', 'complete': False, 'passages': [], 'missing_unit_ids': [r['id'] for r in permitted], 'action': '请缩小邻接范围或按字符区间分步读取；当前未提供原文，不得声称已完整阅读'}
    return {**base, 'status': 'read', 'complete': start is None,'text_read_complete':start is None, 'scope': 'complete_requested_units' if start is None else 'explicit_partial_range', 'passages': permitted,
            'text': '\n\n'.join(r['text'] for r in permitted), 'text_is_model_summary': False}


def identity_note(body):
    note = body.get('note', ''); reviewer = body.get('reviewer', '')
    if not isinstance(note, str) or not note.strip() or len(note) > 6000 or not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 200:
        raise HTTPException(422, '请填写核对人和具体核对说明')
    return note.strip(), reviewer.strip()


@db.atomic
def review(pid, uid, body):
    item = get(pid, uid); note, reviewer = identity_note(body)
    if not item['current'] or body.get('expected_hash') != item['content_hash']:
        raise HTTPException(409, '原文或版本已变化，请重新读取后核对')
    state = body.get('state')
    if state not in ('pending', 'reviewed', 'excluded'):
        raise HTTPException(422, '核对状态无效')
    if state == 'reviewed':
        acknowledged=body.get('acknowledged_issues',[])
        if not isinstance(acknowledged,list) or any(not isinstance(x,str) for x in acknowledged):
            raise HTTPException(422,'质量问题确认须为字符串列表')
        if any(d['state'] == 'open' for d in item['disputes']):
            raise HTTPException(409, '请先处理影响该单元的争议')
        if body.get('compared_original') is not True or set(item['issues']) - set(acknowledged):
            raise HTTPException(422, '需对照原件并逐项记录质量问题的处理情况')
    db.execute('UPDATE knowledge_units SET review_state=? WHERE id=?', (state, uid))
    db.execute('INSERT INTO knowledge_reviews VALUES(?,?,?,?,?,?,?)', (db.uid(), uid, state, note, reviewer, item['content_hash'], db.now()))
    changed(pid, [uid], '证据单元核对状态变化，需复查依赖内容')
    return get(pid, uid)


@db.atomic
def dispute(pid, uid, body):
    item = get(pid, uid); note, reviewer = identity_note(body)
    kind = body.get('kind', 'boundary')
    if kind not in ('boundary', 'ocr', 'reading_order', 'table', 'interpretation', 'other'):
        raise HTTPException(422, '争议类型无效')
    identity = db.uid()
    db.execute('INSERT INTO knowledge_disputes(id,unit_id,kind,note,created_at) VALUES(?,?,?,?,?)', (identity, uid, kind, note + '（' + reviewer + '）', db.now()))
    db.execute("UPDATE knowledge_units SET review_state='pending' WHERE id=?", (uid,))
    changed(pid, [uid], '原文或分段存在未处理争议')
    return get(pid, uid)


@db.atomic
def resolve(pid, uid, did, body):
    get(pid, uid); note, reviewer = identity_note(body)
    if not db.one('SELECT id FROM knowledge_disputes WHERE id=? AND unit_id=?', (did, uid)):
        raise HTTPException(404, '争议不存在')
    db.execute("UPDATE knowledge_disputes SET state='resolved',resolution=?,resolved_at=? WHERE id=?", (note + '（' + reviewer + '）', db.now(), did))
    db.audit(pid, 'knowledge_dispute_resolved', {'unit_id': uid, 'dispute_id': did, 'note': note, 'reviewer': reviewer})
    return get(pid, uid)


@db.atomic
def revise(pid, uid, body):
    item = get(pid, uid); note, reviewer = identity_note(body)
    if not item['current'] or body.get('expected_hash') != item['content_hash']:
        raise HTTPException(409, '版本已变化，请重新核对')
    boundary = None
    if body.get('boundary'):
        boundary = read(pid, uid, before=integer(body,'before',1,0,3), after=integer(body,'after',1,0,3), start=body.get('start'), end=body.get('end'), max_chars=40000)
        if boundary['status'] != 'read':
            raise HTTPException(422, '边界范围过大，请先缩小阅读范围')
    text = boundary['text'] if boundary else body.get('text', '')
    if not isinstance(text, str) or not text.strip() or len(text) > 200000:
        raise HTTPException(422, '修订原文为空或过长')
    parse_id = db.uid(); original = file_record(pid, item['file_id'])
    db.execute('INSERT INTO knowledge_parses(id,project_id,file_id,file_hash,input_hash,engine,strategy,created_at) VALUES(?,?,?,?,?,?,?,?)',
               (parse_id, pid, item['file_id'], item['file_hash'], input_stamp(original), 'manual:' + parse_id, item['strategy'], db.now()))
    from .parsers import tokens
    replacement = None
    merged_ids = {r['id'] for r in boundary['passages']} if boundary and boundary['complete'] else {uid}
    first_id = boundary['passages'][0]['id'] if boundary and boundary['complete'] else uid
    for old in db.rows('SELECT * FROM knowledge_units WHERE parse_id=? ORDER BY ordinal', (item['parse_id'],)):
        if old['id'] in merged_ids and old['id'] != first_id:
            continue
        replacing = old['id'] == first_id
        new_id = db.uid(); value = text if replacing else old['text']
        loc = db.loads(old['locator'], {}); loc['previous_unit_id'] = old['id']
        loc['context_stream']=loc.get('context_stream',old['origin'])
        if replacing:
            loc['manual_scope_note'] = note; replacement = new_id
            if boundary:
                loc['segments'] = [{'unit_id': r['id'], 'locator': r['locator'], 'range_start': r.get('range_start'), 'range_end': r.get('range_end')} for r in boundary['passages']]
        issues = db.loads(old['issues'], []) + (['manual_transcription_needs_review'] if replacing else [])
        db.execute('INSERT INTO knowledge_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (new_id, parse_id, old['ordinal'], old['kind'], value, digest(value), db.dumps(loc), old['heading_path'], 'manual_boundary_selection' if replacing and boundary else 'manual_correction' if replacing else old['origin'], db.dumps(list(dict.fromkeys(issues))), 'pending', old['id']))
        db.execute('INSERT INTO knowledge_unit_fts VALUES(?,?)', (new_id, tokens(value)))
        index_chunks(new_id, value)
    activate_parse(pid, parse_id)
    db.execute('INSERT INTO knowledge_reviews VALUES(?,?,?,?,?,?,?)', (db.uid(), replacement, 'corrected', note, reviewer, digest(text), db.now()))
    return get(pid, replacement)


@db.atomic
def check_claim(pid, uid, body):
    item = get(pid, uid); note, reviewer = identity_note(body)
    claim = body.get('claim', ''); quote = body.get('quote', ''); support = body.get('support', 'unknown'); conditions = body.get('conditions', [])
    if not item['current'] or body.get('expected_hash') != item['content_hash']:
        raise HTTPException(409, '证据版本已变化')
    if not isinstance(claim, str) or not claim.strip() or len(claim) > 6000 or not isinstance(quote, str) or not quote.strip() or quote not in item['text']:
        raise HTTPException(422, '请填写具体论断和逐字可定位的原文引句')
    if support not in ('supported', 'partial', 'contradicted', 'unsupported', 'unknown') or not isinstance(conditions, list) or len(conditions) > 30 or any(not isinstance(x, str) or len(x) > 1000 for x in conditions):
        raise HTTPException(422, '支持性或条件字段无效')
    if support == 'supported' and (item['review_state'] != 'reviewed' or any(d['state'] == 'open' for d in item['disputes'])):
        raise HTTPException(409, '完整支持判定前需完成原文核对并解决争议')
    identity = db.uid()
    db.execute('INSERT INTO knowledge_claim_checks VALUES(?,?,?,?,?,?,?,?,?,?,?)', (identity, pid, uid, item['content_hash'], claim, quote, db.dumps({'conditions': conditions, 'note': note}), support, 'human', reviewer, db.now()))
    return {'id': identity, 'unit_id': uid, 'support': support, 'method': 'human', 'applies_only_to_claim': claim}


def epoch(pid):
    return digest([db.rows('SELECT p.id,p.file_hash,p.input_hash,h.parse_id FROM knowledge_parses p LEFT JOIN knowledge_heads h ON h.file_id=p.file_id WHERE p.project_id=? ORDER BY p.id', (pid,)),
                   db.rows('SELECT u.id,u.content_hash,u.review_state FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE p.project_id=? ORDER BY u.id', (pid,)),
                   db.rows('SELECT d.id,d.state,d.resolution FROM knowledge_disputes d JOIN knowledge_units u ON u.id=d.unit_id JOIN knowledge_parses p ON p.id=u.parse_id WHERE p.project_id=? ORDER BY d.id', (pid,))])


@router.get('')
def overview(pid: str):
    rows = list_units(pid, _all=True)
    counts = {s: sum(r['current'] and r['review_state'] == s for r in rows['items']) for s in ('pending', 'reviewed')}
    counts.update(disputed=sum(bool(r['open_disputes']) for r in rows['items']), stale=sum(not r['current'] for r in rows['items']))
    from . import retrieval,model_install
    cfg=retrieval.config(pid);ready=model_install.status(cfg['model'])['ready'];namespace=retrieval.model_key(cfg['model']) if ready else ''
    active_ids={r['id'] for r in rows['items'] if r['current'] and r['review_state']!='excluded'}
    indexed=db.rows('SELECT ch.unit_id,(SELECT count(*) FROM knowledge_unit_vectors v WHERE v.chunk_id=ch.id AND v.model=? AND v.text_hash=ch.text_hash) AS indexed FROM knowledge_unit_chunks ch JOIN knowledge_units u ON u.id=ch.unit_id JOIN knowledge_parses p ON p.id=u.parse_id WHERE p.project_id=?',(namespace,pid))
    counts['search_windows']=sum(r['unit_id'] in active_ids for r in indexed)
    counts['indexed_windows']=sum(r['unit_id'] in active_ids and bool(r['indexed']) for r in indexed)
    return {'total': rows['total'], **counts, 'files': db.rows("SELECT f.id,f.name,f.status,h.parse_id FROM files f LEFT JOIN knowledge_heads h ON h.file_id=f.id WHERE f.project_id=? AND "+research_sql()+" AND f.active=1 ORDER BY f.name", (pid,)), 'task':db.one("SELECT id,status,phase,progress FROM tasks WHERE project_id=? AND kind='evidence_knowledge_build' AND status IN ('queued','running') ORDER BY created_at LIMIT 1",(pid,)), 'engines': [{'id': structure.ENGINE, 'available': True, 'external_requests': False}, {'id': 'legacy', 'available': True, 'external_requests': False}], 'strategies': sorted(structure.STRATEGIES)}


@router.get('/units')
def units_api(pid: str, state: str = 'all', file_id: str = '', query: str = '', offset: int = 0, limit: int = 20):
    return list_units(pid, state, file_id, query, offset, limit)


@router.post('/build')
def build_api(pid: str, body: dict):
    from . import tasks
    security.require_project(pid)
    if body.get('file_id') is not None and not isinstance(body['file_id'],str):raise HTTPException(422,'原件编号无效')
    if type(body.get('activate',True)) is not bool:raise HTTPException(422,'是否启用版本须为布尔值')
    if not isinstance(body.get('strategy','paragraphs'),str) or not isinstance(body.get('engine',structure.ENGINE),str):raise HTTPException(422,'解析方案无效')
    if body.get('file_id'):
        file_record(pid, body['file_id'])
    if body.get('strategy', 'paragraphs') not in structure.STRATEGIES or body.get('engine', structure.ENGINE) not in (structure.ENGINE, 'legacy'):
        raise HTTPException(422, '解析方案无效')
    return tasks.enqueue(pid, 'evidence_knowledge_build', {'file_id': body.get('file_id'), 'strategy': body.get('strategy', 'paragraphs'), 'engine': body.get('engine', structure.ENGINE), 'activate': body.get('activate', True)})


@router.get('/documents/{fid}/versions')
def versions(pid: str, fid: str):
    file_record(pid, fid)
    return [{**p, 'current_input': parse_current(p)} for p in db.rows('SELECT p.*,h.parse_id AS active_parse_id FROM knowledge_parses p LEFT JOIN knowledge_heads h ON h.file_id=p.file_id WHERE p.file_id=? ORDER BY p.created_at DESC,p.id', (fid,))]


@router.get('/versions/{identity}')
def version_api(pid: str, identity: str):
    return parse_detail(pid, identity)


@router.get('/versions/{identity}/compare')
def compare_versions(pid: str, identity: str, other: str):
    left=parse_detail(pid,identity);right=parse_detail(pid,other)
    if left['file_id']!=right['file_id']:raise HTTPException(422,'只能比较同一原件的解析版本')
    def view(parse):
        return [{**r,'locator':db.loads(r['locator'],{}),'preview':r.pop('preview')} for r in db.rows('SELECT id,ordinal,kind,locator,content_hash,substr(text,1,300) AS preview FROM knowledge_units WHERE parse_id=? ORDER BY ordinal',(parse['id'],))]
    return {'left':{'id':identity,'engine':left['engine'],'strategy':left['strategy'],'units':view(left)},'right':{'id':other,'engine':right['engine'],'strategy':right['strategy'],'units':view(right)},'semantic_winner':None,'note':'这是边界和原文差异，不自动选择语义正确的版本'}


@router.post('/versions/{identity}/activate')
def activate_api(pid: str, identity: str):
    return activate_parse(pid, identity)


@router.get('/units/{uid}')
def unit_api(pid: str, uid: str):
    return get(pid, uid)


@router.post('/units/{uid}/read')
def read_api(pid: str, uid: str, body: dict):
    return read(pid, uid, before=integer(body,'before',1,0,3), after=integer(body,'after',1,0,3), max_chars=integer(body,'max_chars',16000,200,40000), start=body.get('start'), end=body.get('end'))


@router.post('/units/{uid}/review')
def review_api(pid: str, uid: str, body: dict):
    return review(pid, uid, body)


@router.post('/units/{uid}/revise')
def revise_api(pid: str, uid: str, body: dict):
    return revise(pid, uid, body)


@router.post('/units/{uid}/disputes')
def dispute_api(pid: str, uid: str, body: dict):
    return dispute(pid, uid, body)


@router.post('/units/{uid}/disputes/{did}/resolve')
def resolve_api(pid: str, uid: str, did: str, body: dict):
    return resolve(pid, uid, did, body)


@router.post('/units/{uid}/claims')
def claim_api(pid: str, uid: str, body: dict):
    return check_claim(pid, uid, body)


@router.put('/sections/{sid}/selection')
@db.atomic
def select_units(pid: str, sid: str, body: dict):
    section = security.require_section(sid)
    if section['project_id'] != pid:
        raise HTTPException(404, '章节不属于本项目')
    ids = body.get('unit_ids', [])
    if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(x, str) for x in ids):
        raise HTTPException(422, '证据选择无效')
    for uid in ids:
        item = get(pid, uid)
        if not item['current'] or item['review_state'] == 'excluded':
            raise HTTPException(409, '不能关联过期或排除的单元')
    with db.transaction():
        db.execute('DELETE FROM knowledge_selections WHERE section_id=?', (sid,))
        for uid in set(ids):
            db.execute('INSERT INTO knowledge_selections VALUES(?,?)', (sid, uid))
    db.execute("UPDATE writing_sessions SET status='stale',updated_at=? WHERE project_id=? AND section_id=? AND status NOT IN ('completed','cancelled','failed')",(db.now(),pid,sid))
    if section['current_revision_id']:
        from .writing_agent.impact import add_reason
        add_reason(pid,sid,'section',sid,digest(sorted(ids)),'章节选择的文献证据发生变化')
    return {'section_id': sid, 'unit_ids': ids}


@router.get('/sections/{sid}/selection')
def selection_api(pid: str, sid: str):
    if security.require_section(sid)['project_id'] != pid:
        raise HTTPException(404, '章节不属于本项目')
    return {'unit_ids': [r['unit_id'] for r in db.rows('SELECT unit_id FROM knowledge_selections WHERE section_id=?', (sid,))]}
