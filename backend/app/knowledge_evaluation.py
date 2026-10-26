"""Reproducible retrieval comparison, with separate explicit human judgments."""
import time
from fastapi import APIRouter, HTTPException
from . import db, security, knowledge_units as units, knowledge_service as service

router = APIRouter(prefix='/projects/{pid}/evidence-knowledge')


@router.post('/search')
def search_api(pid: str, body: dict):
    return service.search(pid, body.get('query', ''), units.integer(body,'limit',10,1,30), collection=body.get('collection', 'all'))


@router.post('/evaluation-samples')
def samples(pid: str):
    security.require_project(pid)
    available = service.eligible(pid)
    selected = []; seen = set()
    # Prefer diverse originals, then include risk categories from the same originals.
    for row in available:
        if row['file_id'] not in seen and len(row['text'].strip()) >= 20:
            selected.append(row); seen.add(row['file_id'])
    for row in available:
        if row not in selected and len(row['text'].strip()) >= 20:
            selected.append(row)
        if len(selected) >= 20:
            break
    cases = [{'query': '定位并核对原文及其适用条件：' + row['text'].strip()[:80], 'expected_unit_ids': [row['id']], 'annotation_status': 'machine_seed_needs_human_question_and_label', 'risk_categories': row['issues']} for row in selected[:20]]
    return {'cases': cases, 'human_acceptance': False, 'note': '这些是原文定位种子，不是真实用户问题或人工标准答案。请编辑为实际写作问题并核对后再做语义验收。'}


@router.post('/evaluations')
def evaluate(pid: str, body: dict):
    security.require_project(pid)
    cases = body.get('cases', []); k = body.get('k', 10)
    if not isinstance(cases, list) or not 1 <= len(cases) <= 200 or type(k) is not int or not 1 <= k <= 30:
        raise HTTPException(422, '需提供1至200条问题，K为1至30')
    checked = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get('query'), str) or not case['query'].strip() or len(case['query']) > 5000:
            raise HTTPException(422, '每个问题需要非空query')
        ids = case.get('expected_unit_ids', [])
        if not isinstance(ids, list) or not ids or len(ids) > 100 or any(not isinstance(x, str) for x in ids):
            raise HTTPException(422, '每个问题需要当前项目的标准原文单元')
        targets = [units.get(pid, uid) for uid in ids]
        if any(not u['current'] for u in targets):
            raise HTTPException(409, '评测标准原文已过期')
        checked.append((case, targets))
    result = []
    for case, targets in checked:
        stages = {}
        for collection in ('originals', 'all'):
            started = time.perf_counter(); found = service.search(pid, case['query'], k, collection=collection)
            hits = found['items']; gold_pages = {(u['file_id'], u['locator'].get('page')) for u in targets}
            positions = [i for i, h in enumerate(hits, 1) if (h['file_id'], h.get('page_number')) in gold_pages]
            covered = {(h['file_id'], h.get('page_number')) for h in hits} & gold_pages
            stages[collection] = {'page_recall': len(covered) / len(gold_pages), 'page_mrr': 1 / positions[0] if positions else 0,
                                  'unit_recall': len({h.get('knowledge_unit_id') for h in hits} & {u['id'] for u in targets}) / len(targets),
                                  'milliseconds': round((time.perf_counter() - started) * 1000, 3), 'hits': [{'id': h['id'], 'unit_id': h.get('knowledge_unit_id'), 'file_id': h['file_id'], 'page': h.get('page_number')} for h in hits]}
        result.append({'query': case['query'], 'versions': {u['id']: {'hash': u['content_hash'], 'parse_id': u['parse_id']} for u in targets}, 'stages': stages, 'human_judgment': None})
    summary = {stage: {metric: sum(r['stages'][stage][metric] for r in result) / len(result) for metric in ('page_recall', 'page_mrr', 'unit_recall', 'milliseconds')} for stage in ('originals', 'all')}
    output = {'cases': result, 'summary': summary, 'k': k, 'human_acceptance': False, 'scope': 'locating_originals_only_not_semantic_support'}
    identity = db.uid()
    db.execute('INSERT INTO knowledge_evaluations VALUES(?,?,?,?,?)', (identity, pid, db.dumps(cases), db.dumps(output), db.now()))
    return {'id': identity, **output}


@router.get('/evaluations')
def evaluations(pid: str):
    security.require_project(pid)
    return [{**r, 'result': db.loads(r['result'], {}), 'cases': db.loads(r['cases'], [])} for r in db.rows('SELECT * FROM knowledge_evaluations WHERE project_id=? ORDER BY created_at DESC LIMIT 30', (pid,))]


@router.post('/evaluations/{identity}/human-review')
def human_review(pid: str, identity: str, body: dict):
    row = db.one('SELECT * FROM knowledge_evaluations WHERE id=? AND project_id=?', (identity, pid))
    if not row:
        raise HTTPException(404, '评测不存在')
    note, reviewer = units.identity_note(body)
    judgments = body.get('judgments', [])
    result = db.loads(row['result'], {})
    if not isinstance(judgments, list) or len(judgments) != len(result['cases']):
        raise HTTPException(422, '请逐条提交人工判断')
    for case, judgment in zip(result['cases'], judgments):
        if not isinstance(judgment, dict) or judgment.get('support') not in ('correct', 'partial', 'incorrect', 'unknown') or not isinstance(judgment.get('note'), str) or not judgment['note'].strip():
            raise HTTPException(422, '每条需注明判断及对照原文的理由')
        for uid, version in case['versions'].items():
            unit = units.get(pid, uid)
            if not unit['current'] or unit['content_hash'] != version['hash']:
                raise HTTPException(409, '评测原文已变化，需重新运行')
        case['human_judgment'] = {'support': judgment['support'], 'note': judgment['note'], 'reviewer': reviewer}
    result['human_review'] = {'reviewer': reviewer, 'note': note, 'created_at': db.now()}
    result['human_acceptance'] = all(j['support'] == 'correct' for j in judgments)
    db.execute('UPDATE knowledge_evaluations SET result=? WHERE id=?', (db.dumps(result), identity))
    return result


@router.get('/citations')
def citation_map(pid: str):
    security.require_project(pid)
    rows = db.rows('SELECT c.*,s.title AS section_title FROM citations c JOIN revisions r ON r.id=c.revision_id JOIN sections s ON s.id=r.section_id WHERE s.project_id=? AND s.current_revision_id=r.id ORDER BY s.position,c.id', (pid,))
    for row in rows:
        from . import documents
        revision=db.one('SELECT document FROM revisions WHERE id=?',(row['revision_id'],))
        row['claim']=''
        for node in documents.nodes(db.loads(revision['document'],{})):
            if node['type'] in ('paragraph','heading') and any(child.get('attrs',{}).get('nodeId')==row['node_id'] for child in documents.nodes(node)):
                row['claim']=''.join(child.get('text','') if child['type']=='text' else '\n' if child['type']=='hardBreak' else '' for child in documents.nodes(node));break
        row['units'] = [units.get(pid, link['unit_id']) for link in db.rows('SELECT * FROM citation_knowledge_links WHERE citation_id=?', (row['id'],))]
        from .document_reading import context as reading_context
        row['document_readings']=[]
        for link in db.rows('SELECT * FROM citation_reading_links WHERE citation_id=?',(row['id'],)):
            current=reading_context(pid,link['reading_id'])
            row['document_readings'].append({'reading_id':link['reading_id'],'adopted_version':link['reading_version'],'current_version':current['version'],
                'current':current['current'] and current['version']==link['reading_version'],'read_scope':db.loads(link['read_scope'],{}),
                'semantic_context_status':current['semantic_context_status'],'claim_support':'claim_specific_check_required'})
        row['source_ids'] = db.loads(row['source_ids'], [])
        row['reference_snapshot'] = db.loads(row['reference_snapshot'], {})
        row['semantic_support_is_human'] = not str(row['support']).startswith('model_') and row['support'] in ('supported', 'partial', 'unsupported')
        row['effective_support']='stale' if any(not u['current'] or u['review_state']=='excluded' or any(d['state']=='open' for d in u['disputes']) for u in row['units']) or any(not r['current'] for r in row['document_readings']) else row['support']
        if row['effective_support']=='stale':row['semantic_support_is_human']=False
    return rows


@router.post('/citations/{cid}/check')
def queue_citation_check(pid: str, cid: str):
    from . import tasks
    row=db.one('SELECT c.id FROM citations c JOIN revisions r ON r.id=c.revision_id JOIN sections s ON s.id=r.section_id WHERE c.id=? AND s.project_id=? AND s.current_revision_id=r.id',(cid,pid))
    if not row:raise HTTPException(404,'当前正文引用不存在')
    return tasks.enqueue(pid,'support_check',{'citation_id':cid})


@router.post('/citations/{cid}/human-review')
@db.atomic
def review_citation(pid: str,cid: str,body: dict):
    citation=next((r for r in citation_map(pid) if r['id']==cid),None)
    if not citation:raise HTTPException(404,'当前正文引用不存在')
    note,reviewer=units.identity_note(body)
    if body.get('expected_revision_id')!=citation['revision_id'] or body.get('claim')!=citation['claim']:
        raise HTTPException(409,'正文论断已经变化，请重新对照')
    support=body.get('support','unknown');checks=body.get('checks',[])
    if citation['effective_support']=='stale':raise HTTPException(409,'引用的原文或阅读范围已变化，请重新选择依据并核对')
    if support not in ('supported','partial','unsupported','contradicted','unknown') or not isinstance(checks,list) or any(not isinstance(c,dict) for c in checks):
        raise HTTPException(422,'支持性核对字段无效')
    expected={u['id'] for u in citation['units']}
    if not expected and not citation['document_readings'] or {c.get('unit_id') for c in checks}!=expected or len(checks)!=len(expected):
        raise HTTPException(422,'需逐项核对该引用关联的全部知识单元')
    reading_checks=body.get('reading_checks',[])
    expected_readings={r['reading_id']:r for r in citation['document_readings']}
    if not isinstance(reading_checks,list) or any(not isinstance(c,dict) for c in reading_checks) or {c.get('reading_id') for c in reading_checks}!=set(expected_readings) or len(reading_checks)!=len(expected_readings):
        raise HTTPException(422,'需逐项核对引用采用的阅读问题、范围及原文引句')
    for check in reading_checks:
        reading=expected_readings[check['reading_id']];quote=check.get('quote','')
        if check.get('expected_version')!=reading['adopted_version']:raise HTTPException(409,'阅读版本已经变化')
        if not isinstance(quote,str) or not quote.strip() or not any(quote in p['text'] for p in reading['read_scope'].get('passages',[])):
            raise HTTPException(422,'阅读引句必须逐字来自该引用采用时已读的原文范围')
    for check in checks:
        item=units.get(pid,check['unit_id'])
        units.check_claim(pid,item['id'],{'expected_hash':check.get('expected_hash'),'claim':citation['claim'],'quote':check.get('quote',''),'conditions':check.get('conditions',[]),'support':support,'note':note,'reviewer':reviewer})
    db.execute('UPDATE citations SET support=? WHERE id=?',(support,cid))
    db.audit(pid,'human_knowledge_citation_review',{'citation_id':cid,'revision_id':citation['revision_id'],'support':support,'reviewer':reviewer,'note':note,'reading_checks':reading_checks})
    return {'citation_id':cid,'support':support,'method':'human','revision_id':citation['revision_id']}
