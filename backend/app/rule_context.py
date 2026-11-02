"""Literal source context checks, independent of model confidence or OCR quality."""
import re

EXAMPLE_CONTEXT = re.compile(
    r'例如|示例|范例|案例|写作示范|实训|课堂练习|课堂任务|往届不合格意见|提示词|AI提示词|'
    r'❌|✅|错误[一二三四五六七八九十0-9]+[：:]|常见错误|错误写法|'
    r'综合评价意见|专家\s*[0-9一二三]*\s*评价|'
    r'请.{0,15}(?:生成|帮|提供)|举例|EndNote|拖拽|点击|菜单|按钮|界面', re.I)


def purpose_quote(text, quote, purpose):
    from .rule_pipeline import exact_quote
    # A two-character standalone heading is valid; it is never norm evidence.
    if purpose in ('heading', 'noise') and isinstance(quote, str) and quote.strip() and quote.strip() == text.strip():
        return text.strip()
    return exact_quote(text, quote)


def native_reference_recovery(unit, item):
    """Recover only explicit native headings or demonstration pages, never norms."""
    text=unit['text'].strip();purpose=item.get('purpose')
    if unit.get('origin')!='pptx_slide':return None
    if purpose=='heading' and item.get('quote','').strip()==text and len(text)<80:
        return {'quote':text,'purpose':'heading','reason':'原生幻灯片全文与栏目标题一致'}
    if purpose in ('example','counterexample') and re.search(r'(?m)^\s*示例[①②③④⑤⑥⑦⑧⑨⑩一二三四五六七八九十0-9]+[：:]',text[:150]) and re.search(r'(?m)^\s*写作示范\s*$',text):
        return {'quote':text,'purpose':purpose,'reason':'原生页首编号示例与独立“写作示范”栏目明确限定整页用途；保留整页逐字内容'}
    return None
