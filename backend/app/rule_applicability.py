"""Keep chapter scope executable without equating scope with an unresolved condition."""
import re
from . import rule_engine

SECTION_LABELS = {
    'abstract': ('中文摘要', '摘要'), 'abstract_en': ('英文摘要', 'Abstract'),
    'introduction': ('绪论', '引言', '研究背景', '研究意义'),
    'literature_review': ('文献综述', '研究现状'),
    'methodology': ('研究方法', '相关技术介绍', '技术介绍', '理论基础'),
    'system_design': ('系统设计', '需求分析', '架构设计', '功能模块设计'),
    'implementation': ('系统实现', '功能实现'), 'testing': ('系统测试', '测试结果', '测试分析'),
    'conclusion': ('总结与展望', '结论'), 'reference': ('参考文献', '书目'),
}

PROMPT = re.compile(r'AI\s*提示词|提示词模板|我是.{0,30}学生.{0,60}请|下面的语句.{0,30}(?:润色|AIGC)', re.I | re.S)
COURSEWORK = re.compile(r'课堂(?:任务|练习)|上一节课|这节课和上节课|提交到超星|提交压缩包|学号\s*\+\s*姓名')
EXAMPLE_CLAIM = re.compile(r'本系统(?:设计)?填补|实验结果表明.{0,120}(?:该系统|提高|达到|实现)', re.S)


def section_note(note, scope):
    """Resolve only a literal chapter name and surrounding scope grammar."""
    scope=rule_engine.section_kind(scope)
    value = re.sub(r'\s+|[，,。；;：:（）()“”"—-]', '', note)
    labels = SECTION_LABELS.get(scope, ())
    found = any(label.lower() in value.lower() for label in labels)
    if not found:
        return False
    for label in sorted(labels, key=len, reverse=True):
        value = re.sub(re.escape(label), '', value, flags=re.I)
    value = re.sub(r'适用于|用于|撰写|编写|写作|章节|部分|范围|要求|条款|内容|当|在|中|时|的|对', '', value)
    return not value


def expression(scope, sections=None, note='', source_conditions=None):
    scope=rule_engine.section_kind(scope)
    if sections:sections=[rule_engine.section_kind(s) for s in sections]
    selected = sections or ([] if scope == 'global' else [scope])
    if selected and (not isinstance(selected, list) or any(s not in SECTION_LABELS for s in selected)):
        raise ValueError('章节适用范围无效')
    if not isinstance(note, str) or len(note) > 1200:
        raise ValueError('适用部位说明无效')
    if not isinstance(source_conditions or [], list) or any(not isinstance(c, str) for c in source_conditions or []):
        raise ValueError('原文条件无效')
    result = ({'field': 'section_kind', 'op': 'in', 'value': list(dict.fromkeys(selected))} if selected else
              {'field': 'operation', 'op': 'in', 'value': list(rule_engine.DEFAULT_OPERATIONS)})
    result.update(scope_note=note, source_conditions=source_conditions or [])
    rule_engine.validate_condition(result)
    return result


def resolve(scope, quote, conditions, notes):
    scope=rule_engine.section_kind(scope)
    unresolved = [note for note in notes if not section_note(note, scope)]
    unresolved.extend(c for c in conditions if c not in quote and not section_note(c, scope))
    return expression(scope, note='仅适用于' + (SECTION_LABELS.get(scope, ('全文',))[0]) + '，原文内的对象、例外和数量限定继续保留。',
                      source_conditions=conditions), list(dict.fromkeys(unresolved))


def reference_use(text):
    if PROMPT.search(text):
        return 'example', '课件内的AI提示词属于教学资料，不能作为写作代理的全局指令。'
    if COURSEWORK.search(text):
        return 'conditional', '这是课堂练习或提交任务，只适用于对应作业，不作为整篇论文的写作约束。'
    if EXAMPLE_CLAIM.search(text):
        return 'example', '该段包含特定系统的成果或效果断言，属于案例资料，不能成为当前论文要求或成果。'
    return None
