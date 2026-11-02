"""Adopt explicit source reviews with chapter predicates and reversible provenance."""
import hashlib
from functools import lru_cache
from . import db, security, source_payload, rule_governance, rule_engine
from . import ai_autopilot as auto
from .rule_applicability import expression, reference_use
from .rule_pipeline import exact_quote

VERSION = 'scoped-source-review-v1'


@lru_cache(maxsize=1024)
def original_page(path, sha, extension, number, modified_ns, size):
    from pathlib import Path
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError('原件对象内容与指纹不一致')
    if extension == '.pptx':
        from .parsers import pptx_pages
        page = next(p for p in pptx_pages(data) if p['number'] == number)
        return page['text'] + ('\n[备注]\n' + page['notes'] if page.get('notes') else '')
    if extension == '.pdf':
        import pypdfium2 as pdfium
        pdf=pdfium.PdfDocument(data)
        try:
            page=pdf[number-1];text=page.get_textpage()
            try:return text.get_text_bounded()
            finally:text.close();page.close()
        finally:pdf.close()
    raise ValueError('不支持的原件复核类型')


def evidence(source):
    loc = db.loads(source.get('locator'), {})
    if 'cropped_original' in loc.get('source_issues',[]):raise ValueError('已记录原图裁断，先保留原件缺口，不以本次范围复核完成采用')
    unit = db.one('SELECT origin,locator FROM knowledge_units WHERE id=?', (loc.get('unit_id'),))
    origin = unit['origin'] if unit else source.get('method')
    original = db.one('SELECT * FROM files WHERE id=? AND active=1', (source['file_id'],))
    if not original:
        raise ValueError('当前来源已停用')
    if origin == 'pptx_slide':
        path=security.safe_object(original['sha256']);stat=path.stat()
        text=original_page(str(path),original['sha256'],original['extension'],source['page_number'],stat.st_mtime_ns,stat.st_size)
        if not exact_quote(text, source['text']):
            raise ValueError('保存的原生来源不等于原件文字')
        return {'method': 'assistant_native_original_review', 'origin': origin, 'original_text': text}
    if origin == 'pdfium_text_order':
        path=security.safe_object(original['sha256']);stat=path.stat()
        text=original_page(str(path),original['sha256'],original['extension'],source['page_number'],stat.st_mtime_ns,stat.st_size)
        if not exact_quote(text,source['text']):raise ValueError('原生PDF来源不能在原件逐字定位')
        return {'method':'assistant_native_pdf_field_review','origin':origin,'original_text':text}
    if source.get('method') == 'assistant_visual_excerpt' and loc.get('verification_method') == 'assistant_visual_original_comparison':
        return {'method': 'assistant_checked_passage_scope_review', 'origin': origin, 'original_text': source['text']}
    raise ValueError('该来源仍需原图或表格结构核验，不能仅按已有分类提升')


@db.atomic
def apply(pid, frozen_rule, frozen_source, review):
    current = db.one('SELECT * FROM rules WHERE id=? AND project_id=?', (frozen_rule['id'], pid))
    if current != frozen_rule:
        raise ValueError('审核期间条款已有变更')
    from .rule_candidate_repair import editable
    if not editable(current, ('reference_only', 'machine_verified')):
        raise ValueError('保留人工编辑或不属于本次可审核状态')
    source = auto.rule_source(current)
    if source_payload.source_hash(source) != source_payload.source_hash(frozen_source) or source['file_sha256'] != frozen_source['file_sha256']:
        raise ValueError('审核期间原件或来源已有变更')
    if review.get('compared_original') is not True or review.get('human_approval') is not False:
        raise ValueError('须明确原件核对及非人工确认')
    reason = review.get('reason', '')
    if not isinstance(reason, str) or len(reason) < 12:
        raise ValueError('缺少具体的适用性审核依据')
    checked = evidence(source)
    purpose = review.get('purpose')
    if purpose not in ('recommendation', 'conditional', 'example', 'knowledge', 'heading'):
        raise ValueError('本次只采用课程建议或保留参考，不提高来源权威性')
    quote = review.get('quote', current['statement'])
    literal = exact_quote(source['text'], quote)
    if literal is None:
        raise ValueError('修订条款不能逐字定位到已核对来源')
    scope = review.get('scope', current['scope'])
    condition = expression(scope, review.get('sections'), review.get('scope_note', ''), review.get('source_conditions'))
    if any(exact_quote(source['text'], value) is None for value in condition['source_conditions']):
        raise ValueError('适用条件不能在对应原件中定位')
    if purpose == 'recommendation':
        if rule_governance.GRADE.search(literal):raise ValueError('评分等级须保留明确的等级选择条件，不能作为通用建议启用')
        if reference_use(literal):
            raise ValueError('提示词或课堂提交任务不能提升为通用写作建议')
        from .rule_text_integrity import inspect_rule
        if inspect_rule({'statement': literal}, source)['needs_attention']:
            raise ValueError('所选引句仍不完整，不能采用')
        state, modality, outcome = 'machine_verified', 'recommended', 'machine_verified'
    else:
        state, modality = 'reference_only', 'example' if purpose == 'example' else 'pending'
        outcome = 'example' if purpose == 'example' else 'conditional_reference' if purpose == 'conditional' else 'knowledge'
    # Qualitative and multi-part clauses keep literal conditions in the model
    # input, without converting subclause quantities into whole-chapter checks.
    checker = review.get('checker', 'manual')
    value = review.get('value')
    rule_engine.validate_execution({'checker': checker, 'value': value, 'operations': db.loads(current['operations']), 'conditions': condition})
    category = review.get('category', current['category'])
    note = current['decision_note'] + '；适用范围复核：' + reason
    db.execute('UPDATE rules SET statement=?,scope=?,state=?,modality=?,checker=?,rule_key=?,value=?,category=?,decision_note=? WHERE id=?',
        (literal, scope, state, modality, checker, rule_engine.execution_key(checker, value), db.dumps(value), category, note, current['id']))
    db.execute('INSERT INTO rule_conditions VALUES(?,?) ON CONFLICT(rule_id) DO UPDATE SET expression=excluded.expression', (current['id'], db.dumps(condition)))
    after = db.one('SELECT * FROM rules WHERE id=?', (current['id'],))
    previous = auto.receipt(pid, 'rule', current['id'])
    rule_governance.record(pid, current, after, source, outcome, ['助手原件与适用范围复核：' + reason, '适用部位：' + condition['scope_note'], '原文依据：' + literal])
    auto.save_receipt(pid, 'rule', current['id'], {'fingerprint': auto.rule_fingerprint(after, source), 'completed': True, 'state': 'completed',
        'ai_covered': True, 'purpose': purpose, 'quote': literal, 'reason': reason, 'confidence': None,
        'human_approval': False, 'decision_method': VERSION, 'source_review': {'method': checked['method'], 'source_hash': source_payload.source_hash(source),
        'file_sha256': source['file_sha256'], 'original_text_sha256': hashlib.sha256(checked['original_text'].encode()).hexdigest()},
        'applicability': condition, 'previous_model_review': previous, 'before': current, 'after': after})
    db.audit(pid, 'scoped_rule_source_review', {'version': VERSION, 'before': current, 'after': after, 'review': review,
        'source_hash': source_payload.source_hash(source), 'human_approval': False})
    return after


@db.atomic
def add_clause(pid, parent, source, review):
    """Extract an independently reviewed literal clause, retaining the mixed parent."""
    if db.one('SELECT * FROM rules WHERE id=? AND project_id=?',(parent['id'],pid)) != parent:
        raise ValueError('拆分原候选已变化')
    evidence(source)
    literal=exact_quote(source['text'],review.get('quote'))
    if not literal:raise ValueError('独立条款不能定位到原件')
    identity=db.uid()
    db.execute("INSERT INTO rules(id,project_id,source_id,statement,scope,state,category,operations,decision_note,created_at) VALUES(?,?,?,?,?,'reference_only',?,?,?,?)",
        (identity,pid,source['id'],literal,review.get('scope',parent['scope']),review.get('category',parent['category']),parent['operations'],
         '从已核对混合原页提取独立条款；原记录保留：'+parent['id'],db.now()))
    before=db.one('SELECT * FROM rules WHERE id=?',(identity,))
    after=apply(pid,before,auto.rule_source(before),review)
    db.audit(pid,'scoped_clause_extracted',{'parent_rule_id':parent['id'],'rule_id':identity,'source_id':source['id'],
        'quote':literal,'source_hash':source_payload.source_hash(source),'human_approval':False})
    return after
