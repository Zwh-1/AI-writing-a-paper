"""Deterministic checks of the actual candidate documents, never a truth verdict."""
import re
from collections import Counter
from decimal import Decimal
from . import documents
from .writing_agent.common import digest


PLACEHOLDER = re.compile(r'待补(?:证据|充|材料|数据)?|\bTODO\b|\bTBD\b', re.I)


def word_count(text):
    text = re.sub(r'\[(?:cite|figref):[^\]]*\]', '', text)
    return len(re.findall(r'[\u3400-\u9fff]|[A-Za-z0-9]+(?:[.\-\u2019\x27][A-Za-z0-9]+)*', text))


def numbers(text):
    text = re.sub(r'\[cite:[^\]]*\]', '', text)
    return Counter(str(Decimal(n).normalize()) for n in re.findall(r'(?<![A-Za-z0-9.])\d+(?:\.\d+)?(?![A-Za-z0-9]|\.\d)', text))


def review(output, plan=(), references=None):
    items = output.get('paper_sections', [])
    by_id = {s['section_id']: s for s in items}
    goals = {s['id']: s for s in plan}
    books = None if references is None else {r['id'] for r in references}
    findings, metrics, occurrences = [], [], {}
    finding_ids = set()
    placeholder_count = 0
    targets = {(s['section_id'], n.get('attrs', {}).get('nodeId'))
               for s in items for n in documents.nodes(s['document']) if n['type'] in ('figure', 'table')}

    def add(code, message, sids, *, node_id=None, excerpt='', severity='warning', action=''):
        identity = digest([code, sids, node_id, excerpt])[:24]
        if identity in finding_ids:
            return
        finding_ids.add(identity)
        findings.append({'id': identity, 'code': code, 'severity': severity, 'message': message,
                         'section_ids': sids, 'node_id': node_id, 'excerpt': excerpt[:300],
                         'suggested_action': action, 'method': 'local_document_check'})

    for sid in goals.keys() - by_id.keys():
        add('missing_section', '整篇候选缺少提纲中的章节', [sid], severity='error')
    for item in items:
        sid = item['section_id']
        doc = item['document']
        nodes = list(documents.nodes(doc))
        text = documents.plain(doc)
        count = word_count(text)
        target = goals.get(sid, {}).get('target_words', 0)
        citations = [n for n in nodes if n['type'] == 'citation']
        visuals = [n for n in nodes if n['type'] in ('figure', 'table')]
        citation_parents = {c.get('attrs', {}).get('nodeId'): p for p in nodes if p['type'] in ('paragraph', 'heading')
                            for c in documents.nodes(p) if c['type'] == 'citation'}
        proposals = {p.get('node_id'): p for p in item.get('citation_proposals', [])}
        for finding in (item.get('writing_quality') or {}).get('findings',[]):
            critical=finding.get('kind') in {'result_evidence_level','claim_mapping_invalid','citation_mapping_invalid','citation_source_invalid','claim_span_invalid','citation_evidence_gap'}
            add('writing_'+str(finding.get('kind','quality')),finding.get('message','写作质量仍待核对'),[sid],
                node_id=finding.get('node_id'),excerpt=finding.get('text',''),severity='error' if critical else 'warning',
                action=finding.get('repair_query','按定位问题补读并局部修订'))
        metrics.append({'section_id': sid, 'title': item['title'], 'words': count,
                        'target_words': target, 'citations': len(citations), 'visuals': len(visuals)})
        if item.get('degraded'):
            add('degraded_output', '本节模型未按JSON契约返回，已按纯文本保留：没有引用、来源与事实标注', [sid],
                severity='error', action='重新生成本节，或人工补齐依据后再采用；不要直接当作有据正文提交。')
        if not text.strip():
            add('empty_section', '章节没有有效正文', [sid], severity='error')
        elif target and count < target * .4:
            add('short_section', f'本节约{count}字，明显低于目标{target}字', [sid],
                action='据已有依据补足问题、方法、解释和局限，材料不足时明确列出缺口，避免凑字数。')
        if item.get('missing_materials'):
            add('missing_materials', '本节有尚缺资料或未完成的阅读范围', [sid],
                excerpt='；'.join(item['missing_materials']), action='核对缺口；不能据尚未读取的范围推断结果。')
        for node in nodes:
            attrs = node.get('attrs', {})
            nid = attrs.get('nodeId')
            kind = node['type']
            if kind in ('paragraph', 'heading'):
                passage = documents.plain(node)
                marks = len(PLACEHOLDER.findall(passage))
                placeholder_count += marks
                if marks:
                    add('placeholder', f'本段包含{marks}处待补标记', [sid], node_id=nid, excerpt=passage,
                        action='补齐真实依据，或降低表述强度并保留未解决的缺口。')
                normalized = re.sub(r'\s+', '', passage)
                if len(normalized) >= 35:
                    occurrences.setdefault(normalized, []).append((sid, nid))
                if re.search(r'\[(?:cite|figref):[^\]]*\]', ''.join(n.get('text', '') for n in documents.nodes(node) if n['type'] == 'text')):
                    add('literal_reference', '引用标记被写成了普通文字，无法参与索引和导出', [sid], node_id=nid,
                        excerpt=passage, severity='error', action='使用实际提供的文献ID或图表ID建立结构化引用。')
            if kind == 'citation':
                if books is not None and attrs.get('citationId') not in books:
                    add('unknown_citation', '引用文献不属于本次论文文献表', [sid], node_id=nid, severity='error')
                linked = proposals.get(nid, {})
                if not any(linked.get(k) for k in ('source_ids', 'knowledge_unit_ids', 'document_reading_ids')):
                    parent = citation_parents.get(nid, {})
                    if not any(parent.get('attrs', {}).get(k) for k in ('sourceIds', 'knowledgeUnitIds', 'documentReadingIds')):
                        add('citation_without_reading', '引用没有关联已读原文，需核对支持范围', [sid], node_id=nid,
                            action='读取对应原文并关联引用依据；文献元数据不能替代原文。')
            if kind == 'crossReference':
                target_sid = attrs.get('targetSectionId') or sid
                if (target_sid, attrs.get('targetId')) not in targets:
                    add('broken_visual_reference', '图表交叉引用的目标已不存在', [sid], node_id=nid,
                        severity='error', action='保留原图表节点或重新关联实际存在的图表，禁止手写编号。')
            if kind in ('figure', 'table') and not attrs.get('caption', '').strip():
                add('missing_caption', '图表缺少题名，无法形成完整目录', [sid], node_id=nid,
                    action='依据实际图表内容补齐题名。')
        if item['kind'] == 'abstract_en':
            translation = item.get('manifest', {}).get('paper_translation', {})
            source = by_id.get(translation.get('source_section_id'))
            if not source or translation.get('source_text_hash') != digest(source['text']):
                add('stale_translation', '英文摘要与当前中文摘要候选版本不对应', [sid], severity='error',
                    action='重新翻译本次完整中文摘要，保留数字、条件和待补标记。')
            elif numbers(text) != numbers(source['text']):
                add('abstract_numbers', '中英文摘要的数字出现差异，需要逐项核对', [source['section_id'], sid],
                    action='对照当前中文摘要重新翻译，不能新增或省略数字；此检查不判断数值真实。')
    for item in items:
        for finding in (item.get('brief_check') or {}).get('findings', []):
            add(finding.get('code', 'brief_check'), finding.get('message', ''), [item['section_id']],
                excerpt=finding.get('excerpt', ''), action=finding.get('suggested_action', ''),
                severity='warning')
    repeated = []
    for passage, locations in occurrences.items():
        sids = sorted({sid for sid, _ in locations})
        if len(sids) > 1:
            repeated.append({'text': passage, 'sections': [by_id[sid]['title'] for sid in sids], 'section_ids': sids})
            add('repeated_paragraph', '多个章节出现相同长段落', sids, excerpt=passage,
                action='按章节任务消除重复，保留必要的定义与引用；需要复述时解释其新作用。')
    return {'placeholder_count': placeholder_count, 'repeated_paragraphs': repeated,
            'missing_materials': [{'section_id': s['section_id'], 'title': s['title'], 'items': s['missing_materials']}
                                  for s in items if s.get('missing_materials')],
            'findings': findings, 'counts': dict(Counter(f['severity'] for f in findings)),
            'section_metrics': metrics, 'verification': 'unverified_draft',
            'note': '本地检查覆盖章节完整性、待补内容、字数、重复、引用结构、图表目标和摘要版本；事实真实性、引用支持和论证成立仍需审阅。'}
