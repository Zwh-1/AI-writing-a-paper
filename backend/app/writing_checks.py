"""Check a model candidate against the brief it was written for.

These checks answer three questions the reviewer actually asks: did the chapter
cover what it was supposed to cover, is every quantitative claim anchored to a
registered record, and does the shape of the text match the plan. They run after
every section writing, local only, and never rewrite the candidate.
"""
import re
from .writing_agent.common import digest

VERSION = 'writing_checks.v1'
QUANTITY = re.compile(r'\d+(?:\.\d+)?\s*(?:%|％|倍|ms|毫秒|微秒|纳秒|秒|分钟|小时|并发|qps|QPS|条|次|人|篇)'
                      r'|(?:提升|提高|降低|减少|达到|支持)\s*\d')
EVIDENCE_KEYS = ('fact_ids', 'source_ids', 'evidence_ids', 'knowledge_unit_ids', 'document_reading_ids', 'reference_ids')


def _excerpt(text, limit=120):
    body = re.sub(r'\s+', ' ', str(text or '')).strip()
    return body[:limit] + ('…' if len(body) > limit else '')


def _normalized(text):
    return re.sub(r'\s+', '', str(text or ''))


def _numbers(text):
    """Numeric tokens worth comparing. Years are dropped: a paragraph may cite a 2024
    dataset without repeating the year, and that is not a drifted measurement."""
    items = re.findall(r'\d+(?:\.\d+)?', str(text or ''))
    return {re.sub(r'^0+(?=\d)', '', item) for item in items
            if not (len(item) == 4 and item.isdigit() and 1900 <= int(item) <= 2099)}


def _norm_term(text):
    return re.sub(r'[^a-z0-9]', '', str(text or '').lower())


def cross_section_duplicate(paragraphs, related):
    """A paragraph copied verbatim from another chapter is a consistency problem, not evidence."""
    others = []
    for row in related or []:
        body = _normalized(row.get('text', ''))
        if body:
            others.append((row.get('title') or '其他章节', body))
    hits = []
    for paragraph in paragraphs:
        body = _normalized(paragraph.get('text', ''))
        if len(body) < 40:
            continue
        for title, text in others:
            if body in text:
                hits.append((title, str(paragraph.get('text', ''))))
                break
    return hits


def fact_number_drift(paragraphs, facts):
    """When a paragraph anchors a fact but quotes none of its numbers, the value may have drifted.

    All facts cited by the paragraph are pooled before comparing, so a paragraph
    that legitimately mixes two records is not flagged for quoting the other one.
    """
    statements = {row.get('id'): str(row.get('statement', '')) for row in facts or []}
    drifted = []
    for paragraph in paragraphs:
        body = str(paragraph.get('text', ''))
        written = _numbers(body)
        if not written:
            continue
        cited = [identity for identity in paragraph.get('fact_ids') or [] if statements.get(identity)]
        if not cited:
            continue
        recorded = set()
        for identity in cited:
            recorded |= _numbers(statements[identity])
        if recorded and not (recorded & written):
            drifted.append((cited[0], statements[cited[0]], body))
    return drifted


def terminology_gloss(text, terms):
    """The parenthetical English after a registered Chinese term must be the registered one.

    Only the term followed by a bracketed Latin gloss is inspected. A Chinese term
    used on its own is fine, so the check never invents a translation requirement.
    """
    mismatched = []
    for row in terms or []:
        zh, en = str(row.get('zh', '')).strip(), str(row.get('en', '')).strip()
        if not zh or not en:
            continue
        for match in re.finditer(re.escape(zh) + r'\s*[（(]([A-Za-z][^）)]{1,60})[）)]', str(text or '')):
            if _norm_term(match.group(1)) != _norm_term(en):
                mismatched.append((zh, match.group(1).strip(), en))
    return mismatched


def coverage(brief, paragraphs):
    ids = {point['id'] for point in brief.get('points', [])}
    covered = {}
    for index, paragraph in enumerate(paragraphs):
        for identity in paragraph.get('brief_points') or []:
            if isinstance(identity, str):
                covered.setdefault(identity, index)
    missing = [point for point in brief.get('points', []) if point.get('required') and point['id'] not in covered]
    unknown = [identity for identity in covered if identity not in ids]
    return {'covered': len([identity for identity in covered if identity in ids]),
            'required': len([point for point in brief.get('points', []) if point.get('required')]),
            'missing': [{'id': point['id'], 'text': point['text']} for point in missing][:8],
            'unknown': unknown[:8]}


def check(section, brief, paragraphs, text='', related=(), facts=(), terms=()):
    """Return one compact verdict plus the findings that deserve a reviewer's attention.

    `related`, `facts` and `terms` are optional: when they are absent the three
    consistency checks are skipped rather than guessed, because a cross-chapter
    verdict built on missing context would be worse than no verdict at all.
    """
    from .paper_review_checks import word_count
    findings = []
    brief = brief or {}
    paragraphs = [paragraph for paragraph in paragraphs or [] if isinstance(paragraph, dict)]
    if not brief or not paragraphs:
        return {'version': VERSION, 'brief_hash': brief.get('hash'), 'passed': True, 'findings': []}

    def add(code, message, excerpt='', action=''):
        findings.append({'id': digest([code, section['id'], excerpt])[:24], 'code': code,
                         'severity': 'warning', 'message': message, 'section_ids': [section['id']],
                         'excerpt': _excerpt(excerpt), 'suggested_action': action,
                         'method': 'writing_brief_check'})

    cover = coverage(brief, paragraphs)
    if cover['missing']:
        names = '、'.join(item['text'][:24] for item in cover['missing'][:3])
        add('brief_point_missing',
            f'任务书有 {len(cover["missing"])} 个必须覆盖的点没有在段落里回填：{names}',
            action='补写对应段落，或在 missing_materials 说明该点为什么无法展开；不要用套话占位。')
    if cover['unknown']:
        add('brief_point_unknown', f'段落回填了任务书里不存在的编号：{"、".join(cover["unknown"][:4])}',
            action='按任务书给出的编号重新回填；编号必须来自本次 brief。')

    plan = (brief.get('paragraph_plan') or {}).get('count') or 0
    written = len([paragraph for paragraph in paragraphs if str(paragraph.get('text', '')).strip()])
    if plan and written < max(2, plan - max(2, plan // 3)):
        add('paragraph_plan_short', f'任务书计划 {plan} 段，候选只有 {written} 段',
            action='按计划的段落职责补足说明、论证或过渡，避免把多点压进一段。')

    unanchored = []
    for paragraph in paragraphs:
        body = str(paragraph.get('text', ''))
        if QUANTITY.search(body) and not any(paragraph.get(key) for key in EVIDENCE_KEYS):
            unanchored.append(body)
    if unanchored:
        add('claim_without_evidence',
            f'{len(unanchored)} 段出现数值或效果表述但没有回填事实、来源或证据编号',
            unanchored[0],
            action='为该段补上实际提供的 fact_ids / evidence_ids；没有实测记录时改成现状描述或列入待补。')

    words = word_count(text) if text else sum(word_count(str(paragraph.get('text', ''))) for paragraph in paragraphs)
    target = int(brief.get('target_words') or section.get('target_words') or 0)
    if target and words < target * 0.6:
        add('section_length_short', f'本节约 {words} 字，只到目标 {target} 字的 {round(words * 100 / target)}%',
            action='按任务书剩余覆盖点补足；依据不足时保留缺口说明，不虚构内容。')

    body_text = text or '\n'.join(str(paragraph.get('text', '')) for paragraph in paragraphs)

    duplicates = cross_section_duplicate(paragraphs, related)
    if duplicates:
        title, sample = duplicates[0]
        add('cross_section_duplicate',
            f'{len(duplicates)} 段与已保存章节《{title}》的正文整段重复',
            sample,
            action='按本章视角重写该段，或改为引用式承接；整段照搬会破坏章节分工。')

    drifted = fact_number_drift(paragraphs, facts)
    if drifted:
        identity, source, sample = drifted[0]
        add('fact_number_drift',
            f'{len(drifted)} 段引用了事实 {identity}，但段落里的数值与该事实记录不一致',
            f'记录：{source}',
            action=f'核对事实 {identity} 的实测数值后改写；确属新数据时先补登记为新事实，不要直接改写记录。')

    mismatched = terminology_gloss(body_text, terms)
    if mismatched:
        zh, written, registered = mismatched[0]
        names = '、'.join(f'{item[0]}→{item[2]}' for item in mismatched[:3])
        add('terminology_gloss', f'{len(mismatched)} 处术语英文标注与术语表不一致：{names}',
            f'写成了「{written}」，术语表登记为「{registered}」',
            action=f'把「{zh}」后的英文改回术语表登记写法；确需新增写法时先更新术语表。')

    return {'version': VERSION, 'brief_hash': brief.get('hash'), 'words': words, 'target_words': target,
            'coverage': cover, 'findings': findings,
            'passed': not cover['missing'] and not unanchored}
