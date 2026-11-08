"""Every writing operation in one registry: stages, context tiers, checks, parameters.

The four scenarios the workbench actually runs (whole paper, one section, a
revision/polish of existing prose, and a figure/table candidate) differ in what
they may read, what they must preserve and how they fail. Keeping that in one
table means the prompt, the packing policy, the self-check and the interface
cannot drift apart. Numbers here are defaults for local planning only; prices and
model limits stay in the provider configuration.
"""
import math
from fastapi import APIRouter, HTTPException
from . import db, security

router = APIRouter()
VERSION = 'writing_ops.v1'

# Tier meaning:
#   mandatory  never removed; the request fails instead of dropping it
#   continuity the saved manuscript used for consistency; kept as a whole
#   evidence   can be deferred as a whole document/reading and is then reported as a gap
#   reference  metadata travels with its file and disappears with it
#   optional   dropped first, never used as evidence
TIERS = {
    'rules': {'label': '适用规则', 'tier': 'mandatory', 'degrade': '从不移除；规则加载失败直接中止'},
    'writing_brief': {'label': '写作任务书', 'tier': 'mandatory', 'degrade': '从不移除；覆盖点与段落计划随请求发送'},
    'section': {'label': '章节任务', 'tier': 'mandatory', 'degrade': '超限时只保留编号、标题、类型、目标与依赖'},
    'project': {'label': '项目简报', 'tier': 'mandatory', 'degrade': '从不移除'},
    'terminology': {'label': '术语表', 'tier': 'mandatory', 'degrade': '从不移除'},
    'facts': {'label': '研究事实', 'tier': 'mandatory', 'degrade': '按登记状态全部发送，不截断、不改写'},
    'current_section_context': {'label': '当前章已保存正文', 'tier': 'continuity', 'degrade': '从不移除，改写类操作的必要前提'},
    'related_sections': {'label': '其他章节已保存正文', 'tier': 'continuity', 'degrade': '保留；整篇超出容量时整体失败并指出哪一章过大'},
    'paper_draft_sections': {'label': '本任务已完成候选', 'tier': 'continuity', 'degrade': '保留，用于跨章衔接'},
    'selection_context': {'label': '选区前后文', 'tier': 'continuity', 'degrade': '保留前两段与后两段'},
    'sources': {'label': '原文证据片段', 'tier': 'evidence', 'degrade': '按整篇或整组延后并写入缺口，不截断单条'},
    'knowledge_units': {'label': '已读原文单元', 'tier': 'evidence', 'degrade': '整组延后并写入缺口'},
    'knowledge_context': {'label': '派生知识导航', 'tier': 'evidence', 'degrade': '先延后，原文证据单独检查'},
    'project_evidence': {'label': '项目代码与观察', 'tier': 'evidence', 'degrade': '自动观察逐条延后，明确指定的保留'},
    'document_readings': {'label': '文献阅读记录', 'tier': 'evidence', 'degrade': '自动阅读整组延后'},
    'references': {'label': '参考文献表', 'tier': 'reference', 'degrade': '随所属文件一起裁剪'},
    'writing_examples': {'label': '写作指导示例', 'tier': 'optional', 'degrade': '先延后，不影响事实证据'},
    'available_visuals': {'label': '可复用图表', 'tier': 'optional', 'degrade': '保留编号，不足时不引用'},
}

KIND_PURPOSES = {
    'introduction': ['研究背景与问题', '已有工作与不足', '本文要解决的问题与范围', '方法与主要工作', '全文结构安排'],
    'literature': ['主题划分', '代表性方法与结论', '评价维度与适用条件', '局限与争议', '本文切入点'],
    'requirements': ['参与角色与权限', '使用场景与流程', '功能需求', '非功能需求与约束', '需求边界与不做的事'],
    'system_design': ['总体架构', '模块划分与职责', '数据模型与接口', '关键设计取舍', '可靠性与安全'],
    'implementation': ['关键流程与执行顺序', '模块与代码结构', '异常、边界与回退', '与设计的一致性'],
    'results': ['测试或实验设置', '执行过程与样本', '指标与结果', '对比与讨论', '局限与偏差'],
    'conclusion': ['回应研究问题', '主要成果', '限制与未完成', '后续工作'],
    'abstract': ['研究问题', '方法', '有依据的结果', '结论与关键词'],
    'abstract_en': ['与中文摘要逐段对应'],
    'body': ['按本节任务逐点展开'],
}

PARAMS = {
    'paragraph_target_words': {'label': '每段目标字数', 'type': 'int', 'min': 80, 'max': 600, 'default': 220,
                               'hint': '只用于生成段落计划与篇幅自检，不改章节的目标字数'},
    'max_paragraphs': {'label': '单节段落上限', 'type': 'int', 'min': 3, 'max': 60, 'default': 30,
                       'hint': '章节很长时限制段落数，避免逐段过短'},
    'cover_points': {'label': '逐段回填覆盖点', 'type': 'bool', 'default': True,
                     'hint': '要求模型在每段声明覆盖了任务书的哪些点，关闭后只做字数与依据自检'},
    'manuscript_mode': {'label': '润写全文参照', 'type': 'enum', 'values': ['full', 'outline'], 'default': 'full',
                        'hint': 'outline 只在容量不足时使用：以提纲与各章摘句代替全文，并在结果中标明未通读'},
}

OPERATIONS = {
    'whole_paper': {
        'label': '整篇论文生成',
        'summary': '一次任务按提纲完成全部章节，逐章起草并自检，统一审阅后整体写入。',
        'kind': 'write_paper', 'writing_operation': 'generate', 'rule_operation': 'generate',
        'stages': [
            {'key': 'brief', 'label': '整理全篇与逐章任务书', 'owner': 'program',
             'detail': '读取提纲、章节任务、研究问题、事实与资料盘点，逐章给出覆盖点、段落计划与依据提示'},
            {'key': 'draft', 'label': '逐章起草', 'owner': 'model',
             'detail': '按任务书分段写，段落回填覆盖点编号与来源/事实编号'},
            {'key': 'check', 'label': '逐章自检', 'owner': 'program',
             'detail': '覆盖点是否齐全、论断是否锚定依据、段落数与篇幅是否接近计划'},
            {'key': 'review', 'label': '全文审阅', 'owner': 'program',
             'detail': '章节完整、引用结构、摘要对应、跨章重复与逐章自检结论合并为同一份问题清单'},
            {'key': 'adopt', 'label': '整体写入', 'owner': 'program',
             'detail': '先核对候选哈希与输入版本，再在单个事务中写入草稿'},
        ],
        'context': ['rules', 'writing_brief', 'section', 'project', 'terminology', 'facts',
                    'current_section_context', 'related_sections', 'paper_draft_sections',
                    'sources', 'knowledge_units', 'knowledge_context', 'project_evidence',
                    'document_readings', 'references', 'available_visuals', 'writing_examples'],
        'checks': ['brief_coverage', 'evidence_anchor', 'paragraph_plan', 'section_length',
                   'cross_section_duplicate', 'fact_number_drift', 'terminology_gloss'],
        'params': ['paragraph_target_words', 'max_paragraphs', 'cover_points'],
        'contract': '段落数组 + missing_materials + notes + visuals',
        'failure': '预算或容量不足时保留已完成章节；单章无法形成有效候选时保留原始响应并停止',
    },
    'section': {
        'label': '单章生成',
        'summary': '在全文参照下写出指定章节的完整候选，不写入正文。',
        'kind': 'write', 'writing_operation': 'generate', 'rule_operation': 'generate',
        'stages': [
            {'key': 'brief', 'label': '生成本节任务书', 'owner': 'program',
             'detail': '按章节目标、类型模板、已登记事实与证据给出覆盖点与段落计划'},
            {'key': 'draft', 'label': '起草本节', 'owner': 'model',
             'detail': '通读其他章节正文后只输出本节，段落回填覆盖点与依据'},
            {'key': 'check', 'label': '本节自检', 'owner': 'program',
             'detail': '覆盖点、依据锚定、篇幅，以及与已保存章节的重复、数值漂移和术语一致性'},
        ],
        'context': ['rules', 'writing_brief', 'section', 'project', 'terminology', 'facts',
                    'current_section_context', 'related_sections', 'sources', 'knowledge_units',
                    'knowledge_context', 'project_evidence', 'document_readings', 'references',
                    'available_visuals', 'writing_examples'],
        'checks': ['brief_coverage', 'evidence_anchor', 'paragraph_plan', 'section_length',
                   'cross_section_duplicate', 'fact_number_drift', 'terminology_gloss'],
        'params': ['paragraph_target_words', 'max_paragraphs', 'cover_points'],
        'contract': '段落数组 + missing_materials + notes + visuals',
        'failure': '上下文不足时保留本候选与原始响应，等待用户缩小阅读范围或更换接口',
    },
    'revise': {
        'label': '定向修改',
        'summary': '对整节或选中段落做定点修改，未涉及内容按节点原样保留。',
        'kind': 'write', 'writing_operation': 'revise', 'rule_operation': 'revise',
        'stages': [
            {'key': 'brief', 'label': '整理修改要点', 'owner': 'program',
             'detail': '把修改指令、审阅问题与选区前后文整理成可核对的要求清单'},
            {'key': 'draft', 'label': '定点改写', 'owner': 'model',
             'detail': '只改要求涉及的段落，正确内容用 preserve_node_id 原样保留'},
            {'key': 'check', 'label': '改动核对', 'owner': 'program',
             'detail': '覆盖点、依据锚定、选区连续性与未涉及内容是否被误改'},
        ],
        'context': ['rules', 'writing_brief', 'section', 'project', 'terminology', 'facts',
                    'current_section_context', 'related_sections', 'selection_context',
                    'sources', 'knowledge_units', 'project_evidence', 'references'],
        'checks': ['brief_coverage', 'evidence_anchor', 'scope_preserved', 'fact_number_drift', 'terminology_gloss'],
        'params': ['cover_points'],
        'contract': '段落数组 + preserve_node_id + missing_materials',
        'failure': '选区不连续或包含图文关联时直接拒绝，不做降级',
    },
    'expand': {
        'label': '扩写',
        'summary': '在不新增事实的前提下扩展解释、论证与过渡。',
        'kind': 'write', 'writing_operation': 'expand', 'rule_operation': 'revise',
        'stages': [
            {'key': 'brief', 'label': '确定可扩写范围', 'owner': 'program',
             'detail': '按章节任务找出尚未展开的覆盖点，明确只扩不增'},
            {'key': 'draft', 'label': '扩写', 'owner': 'model', 'detail': '补解释、举例与承接，不添加新结论'},
            {'key': 'check', 'label': '扩写核对', 'owner': 'program', 'detail': '覆盖点新增、依据锚定、篇幅增幅'},
        ],
        'context': ['rules', 'writing_brief', 'section', 'project', 'terminology', 'facts',
                    'current_section_context', 'related_sections', 'sources', 'knowledge_units', 'references'],
        'checks': ['brief_coverage', 'evidence_anchor', 'section_length', 'fact_number_drift', 'terminology_gloss'],
        'params': ['paragraph_target_words', 'cover_points'],
        'contract': '段落数组 + preserve_node_id',
        'failure': '没有可依据的扩写空间时明确说明缺口，不凑字数',
    },
    'compress': {
        'label': '压缩',
        'summary': '压缩冗余表达，保留定义、限制、关键论证与引用。',
        'kind': 'write', 'writing_operation': 'compress', 'rule_operation': 'revise',
        'stages': [
            {'key': 'brief', 'label': '标记必须保留的内容', 'owner': 'program',
             'detail': '列出本节必须保留的覆盖点与引用，作为压缩下限'},
            {'key': 'draft', 'label': '压缩', 'owner': 'model', 'detail': '删重复与套话，不改结论强度'},
            {'key': 'check', 'label': '压缩核对', 'owner': 'program', 'detail': '覆盖点是否仍完整、引用是否保留'},
        ],
        'context': ['rules', 'writing_brief', 'section', 'project', 'terminology',
                    'current_section_context', 'related_sections', 'sources', 'references'],
        'checks': ['brief_coverage', 'section_length', 'terminology_gloss'],
        'params': ['cover_points'],
        'contract': '段落数组 + preserve_node_id',
        'failure': '压缩后覆盖点缺失时拒绝候选并说明缺了哪一点',
    },
    'polish': {
        'label': '学术润写',
        'summary': '只改语言：语体、用词、句式与承接，不改事实、数值与结论强度。',
        'kind': 'write', 'writing_operation': 'polish', 'rule_operation': 'polish',
        'stages': [
            {'key': 'brief', 'label': '整理语言任务书', 'owner': 'program',
             'detail': '术语对照、前后段衔接、段落数与原顺序，作为唯一可写范围'},
            {'key': 'draft', 'label': '逐段润写', 'owner': 'model',
             'detail': '按原顺序逐段返回，受保护片段用占位符原样带回'},
            {'key': 'check', 'label': '程序核对', 'owner': 'program',
             'detail': '段落对应、占位符、数值单位、否定与条件标记、长度变化'},
        ],
        'context': ['rules', 'writing_brief', 'section', 'project', 'terminology',
                    'current_section_context', 'related_sections', 'selection_context'],
        'checks': ['paragraph_identity', 'protected_spans', 'numbers', 'qualifiers', 'length_change'],
        'params': ['manuscript_mode'],
        'contract': 'source_node_id + text（保留 ⟦PW-…⟧ 占位符）',
        'failure': '任一段落核对不通过即整份候选拒绝，不部分采用',
    },
    'figure': {
        'label': '图表生成',
        'summary': '把已保存正文里的真实结构转成流程图、用例图、ER 图或三线表。',
        'kind': 'paper_visual', 'writing_operation': None, 'rule_operation': None,
        'stages': [
            {'key': 'brief', 'label': '整理图表任务书', 'owner': 'program',
             'detail': '给出可引用正文、必须体现的结构要素与图中不得出现的成果'},
            {'key': 'draft', 'label': '生成结构', 'owner': 'model',
             'detail': '按类型约束输出节点、关系或表行列，标签取自正文'},
            {'key': 'check', 'label': '结构与标签审计', 'owner': 'program',
             'detail': '类型约束、标签是否能在正文中找到、缺口清单'},
        ],
        'context': ['section', 'related_sections'],
        'checks': ['spec_schema', 'label_grounding', 'missing_materials'],
        'params': [],
        'contract': 'spec（节点/关系或表头行列）+ missing_materials',
        'failure': '标签无法在正文核对时保留候选并标注可疑节点，不静默改写',
    },
}


def named(operation):
    if operation not in OPERATIONS:
        raise HTTPException(404, '未知的写作操作：' + str(operation))
    return OPERATIONS[operation]


def validate_value(name, value):
    rule = PARAMS[name]
    if rule['type'] == 'bool':
        if not isinstance(value, bool):
            raise HTTPException(422, f'{rule["label"]}只能是是或否')
        return value
    if rule['type'] == 'enum':
        if value not in rule['values']:
            raise HTTPException(422, f'{rule["label"]}只能是：' + '、'.join(rule['values']))
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise HTTPException(422, f'{rule["label"]}须为数值')
    number = int(value)
    if number != value or not rule['min'] <= number <= rule['max']:
        raise HTTPException(422, f'{rule["label"]}须为{rule["min"]}–{rule["max"]}的整数')
    return number


def overrides(pid):
    stored = db.setting('writing_ops:' + pid, {}) or {}
    return stored if isinstance(stored, dict) else {}


def params_for(pid, operation):
    spec = named(operation)
    values = {name: PARAMS[name]['default'] for name in spec['params']}
    for name, value in (overrides(pid).get(operation) or {}).items():
        if name in spec['params']:
            try:
                values[name] = validate_value(name, value)
            except HTTPException:
                continue  # A stored value that no longer validates falls back to the default.
    return values


def save_params(pid, operation, values):
    spec = named(operation)
    if not isinstance(values, dict) or not values:
        raise HTTPException(422, '请提交需要修改的参数')
    unknown = set(values) - set(spec['params'])
    if unknown:
        raise HTTPException(422, f'“{spec["label"]}”不接受这些参数：' + '、'.join(sorted(unknown)))
    cleaned = {name: validate_value(name, value) for name, value in values.items()}
    stored = dict(overrides(pid))
    stored[operation] = {**stored.get(operation, {}), **cleaned}
    db.set_setting('writing_ops:' + pid, stored)
    db.audit(pid, 'writing_parameters_saved', {'operation': operation, 'params': cleaned})
    return params_for(pid, operation)


def paragraph_plan(target_words, kind, params, purposes=None, point_ids=None):
    """Suggest a budget across ordered tasks without padding the final topic."""
    target = max(0, int(target_words or 0))
    purposes = list(purposes or KIND_PURPOSES.get(kind) or KIND_PURPOSES['body'])
    if point_ids is not None and len(point_ids) != len(purposes):
        raise ValueError('段落计划的任务编号与职责数量不一致')
    per = int(params.get('paragraph_target_words', PARAMS['paragraph_target_words']['default']))
    ceiling = int(params.get('max_paragraphs', PARAMS['max_paragraphs']['default']))
    count = max(3, min(ceiling, max(len(purposes), int(round(target / per)) if per else len(purposes))))
    if target and count > max(3, int(target / 60)):
        count = max(3, int(target / 60))  # never plan paragraphs shorter than ~60 characters
    base, extra = divmod(target, count) if count else (0, 0)
    rows = []
    for index in range(count):
        # Group neighbouring slots by task; a long chapter must not spend all
        # remaining slots repeatedly expanding only the final responsibility.
        start=index*len(purposes)//count
        end=max(start+1,(index+1)*len(purposes)//count)
        purpose='；'.join(purposes[start:end])
        row={'index': index, 'purpose': purpose, 'target_words': base + (1 if index < extra else 0)}
        if point_ids is not None:row['point_ids']=list(point_ids[start:end])
        rows.append(row)
    return {'count': count, 'total_words': target, 'paragraphs': rows, 'advisory': True}


def resolve(pid, operation, section=None):
    spec = named(operation)
    params = params_for(pid, operation)
    payload = {
        'operation': operation, 'label': spec['label'], 'summary': spec['summary'],
        'kind': spec['kind'], 'writing_operation': spec['writing_operation'],
        'rule_operation': spec['rule_operation'], 'stages': spec['stages'],
        'checks': spec['checks'], 'contract': spec['contract'], 'failure': spec['failure'],
        'context': [{**TIERS.get(key, {'label': key, 'tier': 'unknown', 'degrade': ''}), 'key': key}
                    for key in spec['context']],
        'params': params,
        'params_spec': [{'key': name, **PARAMS[name]} for name in spec['params']],
        'overridden': sorted((overrides(pid).get(operation) or {}).keys()),
    }
    if section is not None:
        payload['paragraph_plan'] = paragraph_plan(section.get('target_words'), section.get('kind'), params)
        payload['section'] = {'id': section['id'], 'title': section['title'], 'kind': section['kind'],
                              'target_words': section.get('target_words', 0)}
    return payload


def catalog(pid):
    return {'version': VERSION, 'operations': [resolve(pid, name) for name in OPERATIONS],
            'params': PARAMS}


@router.get('/projects/{pid}/writing-operations')
def operations(pid: str, section_id: str | None = None):
    security.require_project(pid)
    section = security.require_section(section_id) if section_id else None
    if section and section['project_id'] != pid:
        raise HTTPException(404, '章节不属于当前项目')
    return {**catalog(pid),
            'section_plan': {name: paragraph_plan(section.get('target_words'), section.get('kind'),
                                                   params_for(pid, name))
                             for name in OPERATIONS if name in ('whole_paper', 'section', 'expand')} if section else None}


@router.put('/projects/{pid}/writing-operations/{operation}')
def save(pid: str, operation: str, body: dict):
    security.require_project(pid)
    values = body.get('params', body)
    saved = save_params(pid, operation, values)
    return {**resolve(pid, operation), 'params': saved}
