"""Question-bound material discovery and frozen, verifiable writing inputs.

Search previews are navigation only. Reading resolves server-owned content and
never promotes guidance, generated notes or source code into execution proof.
"""
from copy import deepcopy
import time
from fastapi import HTTPException
from . import db, security, resource_refs as refs, material_policy as policy

SCHEMA = 'writing.evidence_bundle.v1'
CHANNELS = {'literature', 'guidance', 'project', 'products', 'rules'}
PRODUCT_KINDS = {'note', 'annotation', 'translation', 'guide', 'summary', 'asset',
                 'fact', 'analysis', 'execution', 'webpage', 'mentor_task'}


def _section(pid, sid):
    project = security.require_project(pid)
    section = security.require_section(sid)
    if section['project_id'] != pid:
        raise HTTPException(404, '章节不属于当前项目')
    return project, section


def _query(query, limit):
    if not isinstance(query, str) or not query.strip() or len(query) > 6000:
        raise HTTPException(422, '请填写1至6000字的写作检索问题')
    if type(limit) is not int or not 1 <= limit <= 30:
        raise HTTPException(422, '写作检索数量须为1至30')
    return query.strip()


def _score(text, query):
    from .parsers import tokens
    terms = set(tokens(query).split())
    return len(terms & set(tokens(str(text)).split()))


def _ref(pid, kind, identity, remote, **locator):
    value = refs.make(pid, kind, identity, **locator)
    refs.validate(pid, [value], remote=remote)
    return value


def _preview(pid, ref, title, text, channel, remote, **extra):
    refs.validate(pid, [ref], remote=remote)
    return {'id': ref['id'], 'title': title, 'source_ref': ref,
            'kind': ref['type'], 'channel': channel, 'preview_text': text[:240],
            'characters': len(text), 'read_scope': 'search_preview_not_evidence',
            'semantic_support': 'not_checked', **extra}


def search(pid, sid, query, *, limit=8, channels=None, source_refs=None, remote=True):
    """Discover current permitted materials; returns handles, never read receipts."""
    _, section = _section(pid, sid)
    query = _query(query, limit)
    if channels is not None and (not isinstance(channels, (list, tuple, set)) or len(channels) > len(CHANNELS) or any(not isinstance(c, str) for c in channels)):
        raise HTTPException(422, '写作素材检索通道无效')
    channels = set(CHANNELS if channels is None else channels)
    if not channels <= CHANNELS:
        raise HTTPException(422, '写作素材检索通道无效')
    if source_refs is not None:
        refs.validate(pid, source_refs, remote=remote)
    allowed = {(r['type'], r['id']) for r in source_refs or []}
    hits = []; gaps = []; started = time.perf_counter()
    if 'literature' in channels:
        from .knowledge_service import search as lookup
        result = lookup(pid, query[:5000], limit, remote=remote)
        for hit in result['items']:
            kind = 'unit' if hit.get('knowledge_unit_id') else 'source'
            identity = hit.get('knowledge_unit_id') or hit['id']
            try:
                ref = _ref(pid, kind, identity, remote, file_id=hit['file_id'])
                hits.append(_preview(pid, ref, hit.get('reference_title') or hit['name'],
                                     hit.get('text', ''), 'literature', remote,
                                     locator=hit.get('locator', {}), file_id=hit['file_id']))
            except HTTPException as exc:
                if exc.status_code not in (403, 404, 409):raise
        gaps.extend({'reason': warning, 'channel': 'literature'} for warning in result.get('warnings', []))
    if 'guidance' in channels:
        from .parsers import keyword_search
        for hit in keyword_search(pid, query, kind='all', limit=30, remote=remote):
            original = db.one('SELECT * FROM files WHERE id=? AND project_id=?', (hit['file_id'], pid))
            if not original or not policy.source_context(original, 'guidance')['allowed_for_intent']:continue
            try:
                ref = _ref(pid, 'source', hit['id'], remote, file_id=hit['file_id'])
                hits.append(_preview(pid, ref, hit['name'], hit['text'], 'guidance', remote,
                                     file_id=hit['file_id'], locator=db.loads(hit['locator'], {})))
            except HTTPException as exc:
                if exc.status_code not in (403, 404, 409):raise
    if 'project' in channels:
        from .writing_agent import evidence
        rows = db.rows("SELECT id FROM evidence_items WHERE project_id=? AND validity IN ('current','historical') ORDER BY created_at DESC,id", (pid,))
        for row in rows:
            try:
                item = evidence.get(pid, row['id'], remote)
                if not item['version_current'] or item['kind'] in ('literature', 'guidance_observation'):continue
                text = item['payload'].get('text') or db.dumps(item['payload'])
                if not _score(text, query) and ('evidence', item['id']) not in allowed:continue
                ref = _ref(pid, 'evidence', item['id'], remote)
                hits.append(_preview(pid, ref, item['payload'].get('title') or item['kind'], text,
                                     'project', remote, evidence_kind=item['kind']))
            except HTTPException as exc:
                if exc.status_code not in (403, 404, 409):raise
    if 'products' in channels:
        from .product_catalog import sql
        from .product_service import content
        projection, count = sql()
        rows = db.rows('SELECT kind,id,title,search_text,section_id FROM (' + projection + ')', [pid] * count)
        ranked = sorted((row for row in rows if row['kind'] in PRODUCT_KINDS),
                        key=lambda row: (-_score(row['title'] + ' ' + row['search_text'], query), row['kind'], row['id']))
        for row in ranked:
            if not _score(row['title'] + ' ' + row['search_text'], query) and (row['kind'], row['id']) not in allowed:continue
            try:
                ref = _ref(pid, row['kind'], row['id'], remote)
                if row['kind'] in refs.RECORD_TYPES:
                    record = db.one('SELECT data FROM studio_records WHERE id=?', (row['id'],))
                    if remote and db.loads(record['data'], {}).get('egress') == 'local_only':continue
                value = content(pid, ref, remote)
                hits.append(_preview(pid, ref, value['title'], value['text'], 'products', remote))
            except HTTPException as exc:
                if exc.status_code not in (403, 404, 409, 422):raise
            if sum(h['channel'] == 'products' for h in hits) >= limit:break
    if 'rules' in channels:
        project = security.require_project(pid)
        package = db.one('SELECT * FROM rule_packages WHERE id=? AND project_id=?', (project['rule_package_id'], pid))
        if package:
            from .rules import applicable
            ref = _ref(pid, 'rule_package', package['id'], remote)
            for rule in applicable(package, section, 'generate'):
                hits.append(_preview(pid, ref, '已发布论文规则', rule['statement'], 'rules', remote,
                                     rule_id=rule['id'], modality=rule['modality']))
    if allowed:
        hits = [h for h in hits if (h['source_ref']['type'], h['source_ref']['id']) in allowed or
                h.get('file_id') and ('file', h['file_id']) in allowed]
    unique = {}
    for hit in hits:
        key = (hit['source_ref']['type'], hit['source_ref']['id'], hit.get('rule_id'))
        unique.setdefault(key, hit)
    ranked = sorted(unique.values(), key=lambda hit: (-_score(hit['preview_text'] + hit['title'], query), hit['channel'], hit['id']))
    # Round-robin the channels so large document collections cannot suppress
    # the current project's observations and research products.
    chosen = []; groups = {channel: [h for h in ranked if h['channel'] == channel] for channel in sorted(channels)}
    while len(chosen) < limit and any(groups.values()):
        for group in groups.values():
            if group and len(chosen) < limit:chosen.append(group.pop(0))
    return {'schema': 'writing.material_search.v1', 'project_id': pid, 'section_id': sid,
            'query': query, 'items': chosen, 'total_candidates': len(ranked), 'gaps': gaps,
            'read_scope': 'search_preview_not_evidence', 'external_requests': False,
            'timings_ms': {'total': round((time.perf_counter() - started) * 1000, 3)}}


def _entry(pid, ref, title, text, kind, *, locator=None, conditions=(), reference_ids=(), **extra):
    return {'id': ref['id'], 'title': title, 'text': text, 'source_ref': ref,
            'locator': locator or {}, 'kind': kind, 'conditions': list(conditions),
            'reference_ids': list(reference_ids), 'read_scope': 'complete_resolved_resource',
            'semantic_support': 'not_checked', **extra}


def _book_ids(pid, fid):
    return [r['id'] for r in db.rows('SELECT id,metadata FROM bibliography WHERE project_id=? AND file_id=?', (pid, fid)) if not db.loads(r['metadata'], {}).get('_studio_deleted')]


def _seal(bundle):
    bundle['hash'] = refs.digest({k: v for k, v in bundle.items() if k != 'hash'})
    # A digest is an integrity checksum, not proof that the server actually
    # read this content. Keep a server-owned immutable receipt as well.
    sha = security.put_blob(db.dumps(bundle).encode('utf-8'))
    db.set_setting('writing_retrieval_receipt:' + bundle['project_id'] + ':' + bundle['bundle_id'] + ':' + bundle['hash'], sha)
    return bundle


def _finish(pid, sid, query, groups, patch, gaps, trace, remote, operation):
    project, section = _section(pid, sid)
    all_refs = {}; file_ids = set(); readings = {}
    for group in groups.values():
        for entry in group:
            ref = entry['source_ref']; all_refs[(ref['type'], ref['id'])] = ref
            if entry.get('file_id'):file_ids.add(entry['file_id'])
    for value in patch['document_readings']:
        rid = value.get('reading_id') or value['id']
        readings[rid] = value['version']
        ref = _ref(pid, 'reading', rid, remote); all_refs[('reading', rid)] = ref
    if all_refs:file_ids.update(refs.source_files(pid, list(all_refs.values())))
    from .knowledge_units import input_stamp
    stamps = {}
    for fid in sorted(file_ids):
        row = db.one('SELECT * FROM files WHERE id=? AND project_id=?', (fid, pid))
        if not row or not row['active'] or remote and not security.file_allows_remote(fid, pid):
            raise HTTPException(403, '素材原件或外发权限已变化')
        stamps[fid] = refs.digest([row['sha256'], row['active'], row['egress'], input_stamp(row), policy.usages(row), policy.source_kind(row)])
    source_ids = [x['id'] for x in patch['sources']]
    evidence_ids = [x['id'] for x in patch['project_evidence']]
    compat = {'source_ids': source_ids, 'reference_ids': [x['id'] for x in patch['references']],
              'evidence_ids': evidence_ids, 'resource_refs': list(all_refs.values()),
              'file_ids': sorted(file_ids), 'knowledge_unit_ids': [x.get('unit_id') or x['id'] for x in patch['knowledge_units']]}
    manifest = {'resource_refs': list(all_refs.values()), 'file_stamps': stamps,
                'document_reading_versions': readings, 'rule_package_id': project['rule_package_id'],
                'section_fingerprint': refs.digest({key: section[key] for key in ('id', 'kind', 'goal', 'title')}),
                'project_brief_hash': refs.digest(project['brief']), 'operation': operation}
    used = sum(len(e['text']) for group in groups.values() for e in group)
    identity = refs.digest([SCHEMA, pid, sid, query, manifest, groups])[:32]
    bundle = {'schema': SCHEMA, 'bundle_id': identity, 'project_id': pid, 'section_id': sid,
              'query': query, **groups, 'gaps': gaps, 'retrieval_trace': trace,
              'source_manifest': manifest, 'compatibility': compat, 'context_patch': patch,
              'coverage': {'resolved_items': sum(len(g) for g in groups.values()), 'characters': used,
                           'retrieval_complete': not gaps, 'semantic_support': 'not_checked'},
              'complete': not gaps, 'remote': remote, 'usage': {'recorded': False, 'source_ids': [], 'reference_ids': [], 'evidence_ids': [], 'resource_refs': []}}
    validate(pid, _seal(bundle), remote=remote)
    return bundle


def _empty_patch():
    return {key: [] for key in ('sources', 'references', 'project_evidence', 'knowledge_units',
                                'document_readings', 'facts', 'research_products')}


def read(pid, sid, candidates, query, *, operation='generate', max_chars=24000, remote=True):
    """Resolve exact source handles. Client previews/text are never accepted."""
    _section(pid, sid); query = _query(query, 8)
    if not isinstance(candidates, list) or len(candidates) > 40 or type(max_chars) is not int or not 200 <= max_chars <= 200000:
        raise HTTPException(422, '素材读取范围或预算无效')
    groups = {key: [] for key in ('constraints', 'primary_evidence', 'guidance', 'derived_context')}
    patch = _empty_patch(); gaps = []; trace = []; remaining = max_chars; seen = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):raise HTTPException(422, '素材候选无效')
        ref = candidate.get('source_ref', candidate)
        refs.validate(pid, [ref], remote=remote)
        key = (ref['type'], ref['id'])
        if key in seen:continue
        seen.add(key); kind, identity = key
        entry = None; target = 'derived_context'; additions = _empty_patch()
        try:
            if kind in ('unit', 'source'):
                from . import knowledge_units as units
                if kind == 'unit':
                    source = db.one('SELECT u.*,p.file_id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE u.id=? AND p.project_id=?', (identity, pid))
                else:source = db.one('SELECT s.* FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?', (identity, pid))
                original = db.one('SELECT * FROM files WHERE id=? AND project_id=?', (source['file_id'], pid))
                intent = 'research' if policy.is_research(original) else 'guidance'
                if not policy.source_context(original, intent)['allowed_for_intent']:raise HTTPException(403, '该资料用途未获准用于写作读取')
                title = original['name']; text = source['text']; locator = db.loads(source['locator'], {})
                entry = _entry(pid, ref, title, text, 'literature' if intent == 'research' else 'guidance_observation',
                               file_id=original['id'], locator=locator, reference_ids=_book_ids(pid, original['id']) if intent == 'research' else [],
                               material_use=policy.source_context(original, intent))
                target = 'primary_evidence' if intent == 'research' else 'guidance'
                if kind == 'unit':
                    checked = units.read(pid, identity, remote=remote, before=0, after=0, max_chars=max(200, remaining), intent=intent)
                    if checked['status'] != 'read':
                        gaps.append({'source_ref': ref, 'reason': checked.get('action', '完整单元超过剩余预算，未裁剪')});continue
                    entry['text'] = checked['text']; entry['read_scope'] = checked.get('scope', 'complete_unit')
                    if intent == 'research':additions['knowledge_units'].append(checked)
                elif intent == 'research':
                    additions['sources'].append({'id': identity, 'file_id': original['id'], 'location': locator, 'text': text})
                if intent == 'guidance':
                    # A ranked source can separate a teaching label from its
                    # example. Provide the full parsed teaching document.
                    from .document_reading import outline
                    directory = outline(pid, original['id'], 'guidance', ensure=False)
                    if directory.get('needs_structure') or not directory.get('current'):
                        full = [r['text'] for r in db.rows("SELECT s.text FROM sources s LEFT JOIN pages p ON p.id=s.page_id WHERE s.file_id=? AND COALESCE(p.review_state,'')!='not_applicable' ORDER BY p.page_number,s.id", (original['id'],))]
                    else:
                        full = [units.get(pid, row['unit_id'], remote=remote, intent='guidance')['text'] for row in directory['units'] if row['review_state'] != 'excluded']
                    entry['text'] = '\n'.join(full)
                    entry['read_scope'] = 'whole_parsed_guidance_document'
                    entry['source_ref'] = _ref(pid, 'file', original['id'], remote)
            elif kind == 'evidence':
                from .writing_agent import evidence
                value = evidence.get(pid, identity, remote)
                if not value['version_current'] or value['validity'] not in ('current', 'historical'):raise HTTPException(409, '证据版本已变化')
                entry = _entry(pid, ref, value['payload'].get('title') or value['kind'], value['payload'].get('text') or db.dumps(value['payload']),
                               value['kind'], locator=db.loads(value['locator_json'], {}), conditions=value['conditions'], evidence_id=identity,
                               file_id=value['payload'].get('file_id'))
                target = 'guidance' if value['kind'] == 'guidance_observation' else 'primary_evidence'
                if target == 'primary_evidence':additions['project_evidence'].append(value)
            else:
                from .product_service import content
                if kind in refs.RECORD_TYPES:
                    row = db.one('SELECT data FROM studio_records WHERE id=? AND project_id=?', (identity, pid))
                    if remote and db.loads(row['data'], {}).get('egress') == 'local_only':raise HTTPException(403, '研究记录不允许外发')
                value = content(pid, ref, remote)
                entry = _entry(pid, ref, value['title'], value['text'], kind,
                               conditions=['复用内容的事实、范围与执行状态仍需核对'], classification=value['classification'])
                if value.get('visual'):
                    entry['read_scope'] = 'asset_description_only_image_not_supplied'
                    entry['conditions'].append('本次只提供图像资产的文字说明，图像内容未送入模型，不能据说明推断图像细节')
                    gaps.append({'source_ref': ref, 'reason': '图像资产仅提供文字说明；需查看原图并核对视觉内容'})
                if kind == 'rule_package':target = 'constraints'
                elif kind == 'fact':
                    row = db.one('SELECT * FROM facts WHERE id=? AND project_id=?', (identity, pid));additions['facts'].append(row)
                additions['research_products'].append({key: value[key] for key in ('title', 'text', 'source_ref', 'kind', 'classification')})
            size = len(entry['text'])
            if size > remaining:
                gaps.append({'source_ref': ref, 'reason': '完整素材超过剩余读取预算，未截取内容'});continue
            remaining -= size; groups[target].append(entry)
            for name in patch:patch[name].extend(additions[name])
            trace.append({'action': 'read', 'source_ref': ref, 'characters': size, 'read_scope': entry['read_scope'], 'actual_use': False})
        except HTTPException as exc:
            # Explicit invalid handles/permissions fail closed; only a bounded
            # valid resource that cannot fit is represented as an optional gap.
            raise
    fids = {e.get('file_id') for group in groups.values() for e in group if e.get('reference_ids')}
    patch['references'] = [row for row in db.rows('SELECT * FROM bibliography WHERE project_id=?', (pid,)) if row['file_id'] in fids and not db.loads(row['metadata'], {}).get('_studio_deleted')]
    return _finish(pid, sid, query, groups, patch, gaps, trace, remote, operation)


def prepare(pid, sid, query, *, operation='generate', limit=8, max_chars=24000,
            source_refs=None, channels=None, remote=True, library_context=None, guidance_context=None):
    """Prepare all writing lanes, reusing already read library/guidance contexts."""
    project, section = _section(pid, sid); query = _query(query, limit)
    if type(max_chars) is not int or not 200 <= max_chars <= 200000:raise HTTPException(422, '素材读取预算须为200至200000字')
    findings = search(pid, sid, query, limit=limit, channels=channels, source_refs=source_refs, remote=remote)
    # Explicit handles are selected by the human/runtime; automatic search
    # results are navigation candidates and require server-side resolution.
    candidates = [{'source_ref': ref} for ref in source_refs] if source_refs else findings['items']
    optional = candidates if source_refs else [hit for hit in candidates if hit['source_ref']['type'] not in ('unit', 'source', 'rule_package')]
    bundle = read(pid, sid, optional, query, operation=operation, max_chars=max_chars, remote=remote)
    groups = {key: bundle[key] for key in ('constraints', 'primary_evidence', 'guidance', 'derived_context')}
    patch = bundle['context_patch']; gaps = bundle['gaps'] + findings['gaps']; trace = bundle['retrieval_trace']
    trace.insert(0, {'action': 'search', 'query': query, 'candidates': findings['total_candidates'],
                     'source_refs': [hit['source_ref'] for hit in findings['items']], 'timings_ms': findings['timings_ms'], 'actual_use': False})
    enabled = set(CHANNELS if channels is None else channels)
    remaining = max(0, max_chars - sum(len(e['text']) for group in groups.values() for e in group))
    if 'literature' in enabled and remaining and not source_refs:
        if library_context is None:
            from .document_preparation import prepare as prepare_documents
            hits = [hit for hit in findings['items'] if hit['channel'] == 'literature']
            selected = {'items': [{'file_id': h['file_id'], 'name': h['title'], 'knowledge_unit_id': h['id'] if h['kind'] == 'unit' else None} for h in hits]}
            library_context = prepare_documents(pid, query[:5000], sid, limit=limit, max_chars=remaining, findings=selected)
        from .knowledge_service import for_model
        for unit in library_context.get('knowledge_units', []):
            if len(unit['text']) > remaining:
                gaps.append({'file_id': unit['file_id'], 'reason': '完整已读单元超过共享素材预算'});continue
            ref = _ref(pid, 'unit', unit['unit_id'], remote, file_id=unit['file_id'])
            groups['primary_evidence'].append(_entry(pid, ref, '已读文献原文', unit['text'], 'literature', file_id=unit['file_id'],
                                                    locator=unit['locator'], reference_ids=_book_ids(pid, unit['file_id']),
                                                    material_use=unit.get('material_use'), read_scope=unit.get('scope', 'question_bound_reading_range')))
            patch['knowledge_units'].append(for_model(unit));remaining -= len(unit['text'])
        included_fids = {entry.get('file_id') for entry in groups['primary_evidence'] if entry['kind'] == 'literature'}
        for source in library_context.get('sources', []):
            if source['file_id'] not in included_fids and len(source['text']) > remaining:continue
            patch['sources'].append({'id': source['id'], 'file_id': source['file_id'], 'location': db.loads(source['locator'], {}), 'text': source['text']})
            if source['file_id'] not in included_fids:
                ref = _ref(pid, 'source', source['id'], remote, file_id=source['file_id'])
                groups['primary_evidence'].append(_entry(pid, ref, source.get('name', '文献原文'), source['text'], 'literature', file_id=source['file_id'], locator=db.loads(source['locator'], {}), reference_ids=_book_ids(pid, source['file_id'])))
                remaining -= len(source['text'])
        patch['document_readings'].extend(library_context.get('document_readings', []));gaps.extend(library_context.get('gaps', []))
    if 'guidance' in enabled and remaining and not source_refs:
        if guidance_context is None:
            from .guidance_preparation import prepare as prepare_guidance
            guidance_context = prepare_guidance(pid, sid, query, max_chars=remaining)
        for example in guidance_context.get('examples', []):
            fid = example.get('file_id')
            if not fid and example.get('reading_id'):
                row = db.one('SELECT file_id FROM document_readings WHERE id=? AND project_id=?', (example['reading_id'], pid));fid = row['file_id'] if row else None
            text = '\n'.join(p['text'] for p in example['passages'])
            if not fid or len(text) > remaining:continue
            ref = _ref(pid, 'file', fid, remote)
            groups['guidance'].append(_entry(pid, ref, example.get('name', '写作指导'), text, 'guidance_observation', file_id=fid,
                                            conditions=['仅用于指导与示例，不能证明本项目成果'], material_use=example['material_use']))
            remaining -= len(text)
        gaps.extend(guidance_context.get('gaps', []))
    package = db.one('SELECT * FROM rule_packages WHERE id=? AND project_id=?', (project['rule_package_id'], pid))
    if package and 'rules' in enabled:
        from .rules import applicable
        ref = _ref(pid, 'rule_package', package['id'], remote)
        for rule in applicable(package, section, operation):
            groups['constraints'].append(_entry(pid, ref, '论文规则', rule['statement'], 'rule', rule_id=rule['id'],
                                                modality=rule['modality'], conditions=[rule.get('scope', 'global')]))
    fids = {entry.get('file_id') for entry in groups['primary_evidence'] if entry.get('reference_ids')}
    patch['references'] = [row for row in db.rows('SELECT * FROM bibliography WHERE project_id=?', (pid,)) if row['file_id'] in fids and not db.loads(row['metadata'], {}).get('_studio_deleted')]
    trace.extend({'action': 'read', 'source_ref': entry['source_ref'], 'characters': len(entry['text']),
                  'read_scope': entry['read_scope'], 'actual_use': False} for key in ('primary_evidence', 'guidance', 'constraints') for entry in groups[key]
                 if not any(t.get('action') == 'read' and t.get('source_ref') == entry['source_ref'] for t in trace))
    return _finish(pid, sid, query, groups, patch, gaps, trace, remote, operation)


def combine(pid, sid, bundles, query, *, operation='generate', remote=True):
    """Combine actually read packets without losing earlier provenance or use."""
    _section(pid, sid); query = _query(query, 8)
    if not isinstance(bundles, list) or len(bundles) > 60:
        raise HTTPException(422, '合并素材包数量无效')
    groups = {name: [] for name in ('constraints', 'primary_evidence', 'guidance', 'derived_context')}
    patch = _empty_patch(); gaps = []; trace = []; seen_groups = {}; seen_patch = {}
    versions = {}; usage = {'source_ids': set(), 'reference_ids': set(), 'evidence_ids': set(), 'knowledge_unit_ids': set(), 'resource_refs': []}
    recorded = False
    for bundle in bundles:
        validate(pid, bundle, remote=remote)
        if bundle['section_id'] != sid:raise HTTPException(422, '不能合并不同章节的素材包')
        for ref in bundle['source_manifest']['resource_refs']:
            key = (ref['type'], ref['id']); previous = versions.get(key)
            if previous and previous != ref['version']:raise HTTPException(409, '同一素材的读取版本冲突，请重新读取')
            versions[key] = ref['version']
        for name in groups:
            for entry in bundle[name]:
                ref = entry['source_ref']
                # Multiple question-bound ranges from one unit are distinct
                # observations; a rule package likewise contains many rules.
                key = (name, ref['type'], ref['id'], ref['version'], entry.get('rule_id'), refs.digest([entry['text'], entry.get('locator')]))
                if key not in seen_groups:
                    seen_groups[key] = True;groups[name].append(deepcopy(entry))
        for name in patch:
            for value in bundle['context_patch'].get(name, []):
                ref = value.get('source_ref')
                identity = (ref['type'], ref['id'], ref['version']) if ref else value.get('unit_id') or value.get('reading_id') or value.get('id')
                # Preserve disjoint read ranges instead of claiming the union
                # was read in a single invented observation.
                if name == 'knowledge_units':identity = (identity, value.get('char_start'), value.get('char_end'), value.get('text'))
                key = (name, identity)
                if key not in seen_patch:seen_patch[key] = True;patch[name].append(deepcopy(value))
        for item in bundle['gaps']:
            if item not in gaps:gaps.append(deepcopy(item))
        for item in bundle['retrieval_trace']:
            if item not in trace:trace.append(deepcopy(item))
        prior_use = bundle.get('usage', {})
        recorded = recorded or bool(prior_use.get('recorded'))
        for name in ('source_ids', 'reference_ids', 'evidence_ids', 'knowledge_unit_ids'):usage[name].update(prior_use.get(name, []))
        for ref in prior_use.get('resource_refs', []):
            if ref not in usage['resource_refs']:usage['resource_refs'].append(ref)
    result = _finish(pid, sid, query, groups, patch, gaps, trace, remote, operation)
    if recorded:
        result = attach_usage(result, **{key: sorted(value) if isinstance(value, set) else value for key, value in usage.items()})
    return result


def validate(pid, bundle, *, remote=True):
    """Fail closed on altered content, cross-project use or stale provenance."""
    if not isinstance(bundle, dict) or bundle.get('schema') != SCHEMA or bundle.get('project_id') != pid:
        raise HTTPException(422, '写作素材包无效或不属于当前项目')
    if refs.digest({k: v for k, v in bundle.items() if k != 'hash'}) != bundle.get('hash'):
        raise HTTPException(409, '写作素材包内容校验失败')
    receipt = db.setting('writing_retrieval_receipt:' + pid + ':' + str(bundle.get('bundle_id')) + ':' + str(bundle.get('hash')))
    if not receipt or db.loads(security.safe_object(receipt).read_text(encoding='utf-8'), {}) != bundle:
        raise HTTPException(409, '素材包缺少服务端实际读取回执，请重新检索读取')
    project, section = _section(pid, bundle['section_id']); manifest = bundle['source_manifest']
    if refs.digest({key: section[key] for key in ('id', 'kind', 'goal', 'title')}) != manifest['section_fingerprint'] or refs.digest(project['brief']) != manifest['project_brief_hash']:
        raise HTTPException(409, '写作任务或研究简报已变化')
    if project['rule_package_id'] != manifest['rule_package_id']:raise HTTPException(409, '论文规则包已更新')
    refs.validate(pid, manifest['resource_refs'], remote=remote)
    from .knowledge_units import input_stamp
    for fid, stamp in manifest['file_stamps'].items():
        row = db.one('SELECT * FROM files WHERE id=? AND project_id=?', (fid, pid))
        if not row or remote and not security.file_allows_remote(fid, pid):raise HTTPException(403, '素材原件当前禁止外发')
        current = refs.digest([row['sha256'], row['active'], row['egress'], input_stamp(row), policy.usages(row), policy.source_kind(row)])
        if current != stamp:raise HTTPException(409, '素材原件、解析内容或用途已变化')
    for ref in manifest['resource_refs']:
        if remote and ref['type'] in refs.RECORD_TYPES:
            row = db.one('SELECT data FROM studio_records WHERE id=? AND project_id=?', (ref['id'], pid))
            if row and db.loads(row['data'], {}).get('egress') == 'local_only':raise HTTPException(403, '研究记录当前禁止外发')
    return True


def for_writing(bundle):
    """Return an independent patch; source IDs alone never authorize new text."""
    patch = deepcopy(bundle['context_patch'])
    for row in patch['references']:row['metadata'] = db.loads(row['metadata'], {})
    patch['writing_examples'] = [{'id': row.get('file_id') or row['id'], 'file_id': row.get('file_id'), 'name': row['title'],
                                  'passages': [{'text': row['text'], 'locator': row['locator']}],
                                  'material_use': row.get('material_use', {}), 'scope': row['read_scope'],
                                  'instruction': '仅作指导与示例，不能证明本项目成果'} for row in bundle['guidance']]
    return patch


def for_model(bundle):
    """Compact catalogue; actual text is supplied once in the context patch."""
    view = {key: deepcopy(bundle[key]) for key in ('schema', 'bundle_id', 'project_id', 'section_id', 'query', 'coverage', 'complete')}
    for name in ('constraints', 'primary_evidence', 'guidance', 'derived_context'):
        view[name] = [{key: deepcopy(value) for key, value in item.items() if key != 'text'} for item in bundle[name]]
    view['gaps'] = deepcopy(bundle['gaps'])
    view['policy'] = '目录仅说明本次素材范围；具体原文位于sources/knowledge_units/project_evidence/research_products，未提供原文的项不能作为事实依据。指导资料不能证明本项目成果。'
    return view


def restrict(bundle, context):
    """Align an immutable bundle with the text actually surviving request packing."""
    validate(bundle['project_id'], bundle, remote=bundle.get('remote', True))
    result = deepcopy(bundle)
    source_ids = {r['id'] for r in context.get('sources', [])}
    unit_ids = {r.get('unit_id') or r.get('id') for r in context.get('knowledge_units', [])}
    evidence_ids = {r['id'] for r in context.get('project_evidence', context.get('evidence', []))}
    product_refs = {(r['source_ref']['type'], r['source_ref']['id']) for r in context.get('research_products', []) if r.get('source_ref')}
    kept = []
    for name in ('primary_evidence', 'derived_context'):
        selected = []
        for item in result[name]:
            ref = item['source_ref']; key = (ref['type'], ref['id'])
            available = key in product_refs or ref['type'] == 'source' and ref['id'] in source_ids or ref['type'] == 'unit' and ref['id'] in unit_ids or ref['type'] == 'evidence' and ref['id'] in evidence_ids
            if available:selected.append(item);kept.append(key)
            else:result['gaps'].append({'source_ref': ref, 'reason': '请求打包后原文未提供，不允许用于本次正文'})
        result[name] = selected
    # Guidance text is delivered through the established writing_examples lane.
    guidance_files = {r.get('file_id') for r in context.get('writing_examples', [])}
    result['guidance'] = [r for r in result['guidance'] if r.get('file_id') in guidance_files or (r['source_ref']['type'], r['source_ref']['id']) in product_refs]
    for r in result['guidance']:kept.append((r['source_ref']['type'], r['source_ref']['id']))
    for r in result['constraints']:kept.append((r['source_ref']['type'], r['source_ref']['id']))
    patch = result['context_patch']
    patch['sources'] = [r for r in patch['sources'] if r['id'] in source_ids]
    patch['knowledge_units'] = [r for r in patch['knowledge_units'] if (r.get('unit_id') or r.get('id')) in unit_ids]
    patch['project_evidence'] = [r for r in patch['project_evidence'] if r['id'] in evidence_ids]
    patch['research_products'] = [r for r in patch['research_products'] if (r['source_ref']['type'], r['source_ref']['id']) in product_refs]
    patch['facts'] = [r for r in patch['facts'] if r['id'] in {f['id'] for f in context.get('facts', [])}]
    actual_refs = [r for r in result['source_manifest']['resource_refs'] if (r['type'], r['id']) in kept]
    result['source_manifest']['resource_refs'] = actual_refs
    result['compatibility'].update(source_ids=[r['id'] for r in patch['sources']], evidence_ids=[r['id'] for r in patch['project_evidence']],
                                   knowledge_unit_ids=[r.get('unit_id') or r['id'] for r in patch['knowledge_units']], resource_refs=actual_refs)
    allowed_books = {rid for group in ('primary_evidence',) for entry in result[group] for rid in entry['reference_ids']}
    patch['references'] = [r for r in patch['references'] if r['id'] in allowed_books]
    result['compatibility']['reference_ids'] = [r['id'] for r in patch['references']]
    result['coverage'].update(resolved_items=sum(len(result[k]) for k in ('constraints', 'primary_evidence', 'guidance', 'derived_context')),
                              characters=sum(len(e['text']) for k in ('constraints', 'primary_evidence', 'guidance', 'derived_context') for e in result[k]),
                              retrieval_complete=not result['gaps'])
    result['complete'] = not result['gaps']
    return _seal(result)


def attach_usage(bundle, *, source_ids=(), reference_ids=(), evidence_ids=(), resource_refs=(), knowledge_unit_ids=()):
    validate(bundle['project_id'], bundle, remote=bundle.get('remote', True))
    result = deepcopy(bundle); allowed = result['compatibility']
    result['usage']['recorded'] = True
    for key, values in (('source_ids', source_ids), ('reference_ids', reference_ids), ('evidence_ids', evidence_ids)):
        values = list(dict.fromkeys(values))
        if set(values) - set(allowed[key]):raise HTTPException(422, '正文使用了未实际读取的素材编号')
        result['usage'][key] = values
    selected = {(r['type'], r['id'], r['version']) for r in resource_refs}
    known = {(r['type'], r['id'], r['version']) for r in allowed['resource_refs']}
    if selected - known:raise HTTPException(422, '正文使用了未实际读取的资源版本')
    result['usage']['resource_refs'] = list(resource_refs)
    if set(knowledge_unit_ids) - set(allowed['knowledge_unit_ids']):raise HTTPException(422, '正文使用了未实际读取的原文单元')
    result['usage']['knowledge_unit_ids'] = list(dict.fromkeys(knowledge_unit_ids))
    by_ref = {(e['source_ref']['type'], e['source_ref']['id']): e for name in ('constraints', 'primary_evidence', 'guidance', 'derived_context') for e in result[name]}
    for item in result['retrieval_trace']:
        ref = item.get('source_ref')
        entry = by_ref.get((ref['type'], ref['id']), {}) if ref else {}
        item['actual_use'] = bool(ref and ((ref['type'], ref['id'], ref['version']) in selected or
                                 ref['type'] == 'source' and ref['id'] in source_ids or ref['type'] == 'evidence' and ref['id'] in evidence_ids or
                                 ref['type'] == 'unit' and ref['id'] in knowledge_unit_ids or set(entry.get('reference_ids', [])) & set(reference_ids)))
    return _seal(result)
