"""Versioned, source-bound argument plans and explicit manuscript coverage."""
from copy import deepcopy
import re

from fastapi import HTTPException
from . import db, security
from .writing_agent.common import digest

VERSION = 'writing.argument_plan.v1'
RELATIONS = {'supports', 'requires', 'derives', 'contrasts', 'limits', 'explains'}


def _terms(text):
    text = str(text or '').lower()
    return set(re.findall(r'[a-z][a-z0-9_]+|[\u3400-\u9fff]{2}', text))


def _score(a, b):
    left, right = _terms(a), _terms(b)
    return len(left & right) / max(1, len(left))


def build(pid, section, context, brief):
    """Map registered propositions and actual sources onto the chapter's goals.

    A relevance match is a proposed reading/argument assignment, never support.
    Existing contract argument_edges and their registered claim IDs are reused.
    """
    security.require_project(pid)
    contract = context.get('section_contract') or {}
    core = context.get('thesis_core') or {}
    claims = []
    for identity in dict.fromkeys([*core.get('claim_versions', []), *contract.get('claim_versions', [])]):
        row = db.one('SELECT * FROM claim_versions WHERE id=? AND project_id=?', (identity, pid))
        if not row:
            raise HTTPException(409, '论证计划中的登记主张已变化')
        proposition = db.loads(row['proposition_json'], {})
        claims.append({'id': identity, 'statement': proposition.get('statement', ''),
                       'conditions': proposition.get('conditions', []),
                       'evidence_ids': [r['evidence_id'] for r in db.rows(
                           'SELECT evidence_id FROM claim_evidence_links WHERE claim_version_id=?', (identity,))]})
    edges = deepcopy(contract.get('argument_edges', core.get('argument_edges', [])))
    points = deepcopy(brief.get('points', []))
    available = []
    for key, label in (('sources', 'source'), ('project_evidence', 'evidence'), ('facts', 'fact')):
        for row in context.get(key, []):
            payload = row.get('payload') or {}
            available.append({'id': row.get('id'), 'type': label,
                              'text': row.get('text') or row.get('statement') or payload.get('text', '')})
    cards = []
    previous = None
    for index, point in enumerate(points):
        matched = sorted(claims, key=lambda c: -_score(point['text'], c['statement']))
        chosen_claims = [c['id'] for c in matched if _score(point['text'], c['statement']) > 0][:4]
        ranked = sorted(available, key=lambda e: -_score(point['text'], e['text']))
        candidates = [e for e in ranked if _score(point['text'], e['text']) > 0][:4]
        role = {'result': 'evidence_and_interpretation', 'limit': 'limitations', 'scope': 'definition',
                'method': 'method_and_rationale'}.get(point.get('kind'), 'claim_and_analysis')
        card = {'id': 'a' + str(index + 1), 'point_ids': [point['id']], 'question': point['text'],
                'role': role, 'claim_ids': chosen_claims,
                'candidate_sources': [{'id': e['id'], 'type': e['type'], 'support': 'not_checked'} for e in candidates],
                'relation_to_previous': {'from': previous, 'relation': 'explains'} if previous else None,
                'analysis_goal': '回答本任务点，说明依据如何支持主张、适用条件和不能推出的结论',
                'expected_information_gain': point['text'],
                'gaps': [] if candidates or chosen_claims else ['本任务点尚未绑定具体依据，需要取材或保留缺口']}
        cards.append(card)
        previous = card['id']
    value = {'schema': VERSION, 'project_id': pid, 'section_id': section['id'],
             'base_revision_id': section.get('current_revision_id'), 'research_question': core.get('research_question', ''),
             'section_goal': contract.get('goal') or section.get('goal', ''), 'claims': claims,
             'argument_edges': edges, 'paragraph_cards': cards,
             'forbidden': brief.get('forbidden', []), 'basis': 'registered_claims_and_current_task',
             'support_status': 'not_checked', 'brief_hash': brief.get('hash'),
             'source_versions': {'core': context.get('core_version_id'), 'contract': context.get('contract_version_id')}}
    # Author plans survive different source-ranking budgets and the prompt's
    # removal of local-only brief hashes. Real task/core/claim changes expire it.
    value['basis_hash']=digest([VERSION,pid,section['id'],value['research_question'],value['section_goal'],
                                 points,claims,edges,value['source_versions']])
    saved = db.setting('writing_argument_plan:' + pid + ':' + section['id'])
    if saved and saved.get('basis_hash') == value['basis_hash']:
        value = {**value, 'paragraph_cards': saved['paragraph_cards'], 'author_note': saved.get('author_note', '')}
    value['hash'] = digest({k: v for k, v in value.items() if k != 'hash'})
    return value


def save(pid, sid, plan, body):
    security.require_project(pid)
    section = security.require_section(sid)
    if section['project_id'] != pid:
        raise HTTPException(404, '章节不属于当前项目')
    if body.get('base_hash') != plan['hash']:
        raise HTTPException(409, '论证计划依据已变化，请重新读取')
    cards = body.get('paragraph_cards')
    known = {point for card in plan['paragraph_cards'] for point in card['point_ids']}
    if not isinstance(cards, list) or not 1 <= len(cards) <= 60:
        raise HTTPException(422, '论证计划需要1至60项段落任务')
    ids, allowed_claims = set(), {c['id'] for c in plan['claims']}
    for card in cards:
        if not isinstance(card, dict) or not isinstance(card.get('id'), str) or not 1 <= len(card['id']) <= 80 or card['id'] in ids:
            raise HTTPException(422, '段落任务编号无效或重复')
        ids.add(card['id'])
        if not isinstance(card.get('question'), str) or not 1 <= len(card['question']) <= 3000:
            raise HTTPException(422, '请填写段落应回答的问题')
        if set(card.get('point_ids', [])) - known or set(card.get('claim_ids', [])) - allowed_claims:
            raise HTTPException(422, '段落任务引用未知任务点或主张')
        # Authors can edit goals and relations, not supply invented source text.
        card.pop('candidate_sources', None)
    graph = {}
    for card in cards:
        relation = card.get('relation_to_previous')
        if relation:
            if relation.get('from') not in ids or relation.get('relation') not in RELATIONS:
                raise HTTPException(422, '段落论证关系无效')
            graph.setdefault(card['id'], []).append(relation['from'])
    active, done = set(), set()
    def visit(key):
        if key in active:
            raise HTTPException(422, '段落论证依赖不能成环')
        if key in done:
            return
        active.add(key)
        for parent in graph.get(key, []):
            visit(parent)
        active.remove(key)
        done.add(key)
    for identity in ids:
        visit(identity)
    record = {'basis_hash': plan.get('basis_hash',plan['hash']), 'paragraph_cards': cards,
              'author_note': str(body.get('author_note', ''))[:6000]}
    db.set_setting('writing_argument_plan:' + pid + ':' + sid, record)
    db.audit(pid, 'writing_argument_plan_saved', {'section_id': sid, 'basis_hash': plan['hash']})
    result={**plan,'paragraph_cards':cards,'author_note':record['author_note'],'basis_hash':record['basis_hash']}
    result['hash']=digest({k:v for k,v in result.items() if k!='hash'})
    return result


def manuscript_state(context):
    sections = []
    for row in [*context.get('related_sections', []), *context.get('paper_draft_sections', [])]:
        text = str(row.get('text', ''))
        sentences = [m.group(0).strip() for m in re.finditer(r'[^。！？!?\n]+[。！？!?]?', text) if m.group(0).strip()]
        sections.append({'section_id': row.get('section_id') or row.get('id'), 'title': row.get('title', ''),
                         'revision_id': row.get('revision_id'), 'text_hash': digest(text),
                         'status': row.get('status', 'saved_draft'),
                         'navigation_sentences': sentences[:2] + (sentences[-1:] if len(sentences) > 2 else []),
                         'open_gaps': [s for s in sentences if '待补' in s][:6],
                         'scope': 'navigation_not_fact_evidence'})
    value = {'schema': 'writing.manuscript_state.v1', 'sections': sections,
             'terminology': context.get('terminology', []), 'scope': 'version_bound_navigation_not_evidence'}
    return {**value, 'hash': digest(value)}


def for_model(plan):
    """Compact task-specific cards; evidence text and forbidden rules live once."""
    return {'schema':plan['schema'],'hash':plan['hash'],'research_question':plan['research_question'],
            'section_goal':plan['section_goal'],'claims':plan['claims'],'argument_edges':plan['argument_edges'],
            'paragraph_cards':[{key:card[key] for key in ('id','point_ids','question','role','claim_ids','relation_to_previous') if card.get(key)} for card in plan['paragraph_cards']],
            'scope':'候选论证分工；登记主张和相关性不等于来源支持'}


def scoped_context(context, mode):
    """Explicit opt-in scope. Current chapter, rules and evidence stay complete."""
    if mode == 'full_manuscript':
        return context, {'mode': mode, 'scope': 'full_manuscript', 'complete': True}
    scoped = deepcopy(context)
    section = context.get('section', {})
    dependencies = set(section.get('dependencies', []) if isinstance(section.get('dependencies'), list)
                       else db.loads(section.get('dependencies'), []))
    rows = context.get('related_sections', [])
    needed = {row.get('section_id') or row.get('id') for row in rows if row.get('relation') in ('previous', 'dependency')}
    needed.update(dependencies)
    earlier=[row for row in rows if row.get('relation')=='earlier']
    if earlier:needed.add(earlier[-1].get('section_id') or earlier[-1].get('id'))
    needed.update(row.get('section_id') or row.get('id') for row in rows if row.get('scope')=='full_required_dependency')
    # Direct dependencies are read in full. The navigation state records every
    # omitted chapter, so neither the model nor UI can claim a full reading.
    selected = [row for row in rows if (row.get('section_id') or row.get('id')) in needed]
    scoped['related_sections'] = selected
    if 'continuity' in scoped:
        scoped['continuity']={**scoped['continuity'],'scope':'argument_scoped','policy':'只实际提供当前章、前置依赖和相邻章节；其他章节仅导航，未通读。需要关键前提时请求读取原文。'}
    if 'paper_context' in scoped:
        scoped['paper_context'] = {**scoped['paper_context'], 'complete': len(selected) == len(rows),
                                   'mode': 'argument_scoped'}
    omitted = [row.get('section_id') or row.get('id') for row in rows if row not in selected]
    scoped['context_coverage'] = {'mode': mode, 'complete': not omitted,
                                  'provided_sections': [r.get('section_id') or r.get('id') for r in selected],
                                  'navigation_only_sections': omitted,
                                  'policy': '缺少关键前提时请求读取原文，导航句不得充当已核实依据'}
    return scoped, scoped['context_coverage']


def dependency_projection(claims, changed_claim_ids=(), changed_source_ids=()):
    """Expose impact analysis for observation; current live guards remain authoritative."""
    from .writing_quality_protocols import dependency_shadow
    return dependency_shadow(claims,changed_claim_ids,changed_source_ids)
