"""Explain a whole-paper run from the project's own records, without calling a model.

Everything here is read from local tables or computed arithmetically, so opening
the dialog costs nothing and the numbers can be traced back to a record. The
module never writes, never reserves budget and never replaces a preflight result.
"""
import re
from fastapi import APIRouter
from . import db, documents, security
from .paper_review_checks import word_count

router = APIRouter()

RANK = {'high': 0, 'medium': 1, 'info': 2}
GENERIC_GOAL = '按已确认规则组织本节内容'
PLACEHOLDER_TITLE = re.compile(r'^(我的毕业论文|毕业论文|论文|未命名.*|新建项目.*)$')
PLACEHOLDER_QUESTION = re.compile(r'围绕“?[^”]{0,20}(毕业论文|论文|项目)[^”]{0,10}”?组织')

# Which chapter kinds cannot be written from the outline alone.
CODE_KINDS = {'requirements', 'system_design', 'implementation'}
FACT_KINDS = {'results'}
LITERATURE_KINDS = {'literature', 'introduction'}


def role_for(kind):
    from .paper_writing import ROLES
    return ROLES.get(kind, '围绕研究问题完成本节任务，保持证据范围和章节分工。')


def active_sections(pid):
    return db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position,id", (pid,))


def levels(sections):
    by_id = {s['id']: s for s in sections}
    result = {}
    for s in sections:
        depth, cursor, seen = 1, s, {s['id']}
        while cursor.get('parent_id') and cursor['parent_id'] in by_id and cursor['parent_id'] not in seen:
            seen.add(cursor['parent_id'])
            cursor = by_id[cursor['parent_id']]
            depth += 1
        result[s['id']] = depth
    return result


def inventory(pid, snapshot_id=None):
    facts = db.rows('SELECT id,status,verification,source_ids FROM facts WHERE project_id=?', (pid,))
    reference = db.one('SELECT COUNT(*) n,COALESCE(SUM(CASE WHEN verification!=\'unverified\' THEN 1 ELSE 0 END),0) checked FROM bibliography WHERE project_id=?', (pid,))
    materials = db.one("SELECT COUNT(*) n FROM files WHERE project_id=? AND active=1 AND role!='rules'", (pid,))['n']
    rule_files = db.one("SELECT COUNT(*) n FROM files WHERE project_id=? AND active=1 AND role='rules'", (pid,))['n']
    figures = db.one("SELECT COUNT(*) n FROM artifacts WHERE project_id=? AND kind IN ('figure','table')", (pid,))['n']
    terms = db.one('SELECT COUNT(*) n FROM terms WHERE project_id=?', (pid,))['n']
    units = db.one('SELECT COUNT(*) n FROM code_units WHERE snapshot_id=?', (snapshot_id,))['n'] if snapshot_id else 0
    return {'facts': len(facts),
            'measured_facts': len([f for f in facts if f['status'] == 'measured']),
            'checked_facts': len([f for f in facts if f['verification'] != 'unverified']),
            'facts_without_source': len([f for f in facts if not db.loads(f['source_ids'], [])]),
            'references': reference['n'], 'checked_references': reference['checked'],
            'materials': materials, 'rule_materials': rule_files,
            'figures': figures, 'terms': terms, 'code_units': units,
            'code_snapshot': bool(snapshot_id)}


def brief_of(pid):
    project = security.require_project(pid)
    brief = db.loads(project['brief'], {}) or {}
    state = db.one('SELECT * FROM writing_project_state WHERE project_id=?', (pid,)) or {}
    core = db.loads(db.one('SELECT content_json FROM thesis_core_versions WHERE id=? AND project_id=?',
                            (state.get('core_version_id'), pid))['content_json'], {}) if state.get('core_version_id') else {}
    heads = db.loads(state.get('contract_heads_json'), {}) or {}
    return project, brief, core, heads, (state.get('target_snapshot_id') if state else None)


def chapters(pid, sections, heads):
    depth = levels(sections)
    rows = []
    for s in sections:
        cid = heads.get(s['id'])
        contract = db.one('SELECT content_json FROM section_contract_versions WHERE id=? AND project_id=?', (cid, pid)) if cid else None
        content = db.loads(contract['content_json'], {}) if contract else {}
        goal = str(content.get('goal') or s['goal'] or '').strip()
        revision = documents.current(s)
        text = documents.plain(db.loads(revision['document'], {})) if revision else ''
        rows.append({'section_id': s['id'], 'title': s['title'], 'kind': s['kind'],
                     'position': s['position'], 'level': depth[s['id']],
                     'target_words': s['target_words'], 'goal': goal,
                     'goal_is_generic': (not goal) or goal.startswith(GENERIC_GOAL),
                     'role': role_for(s['kind']),
                     'contract_evidence': len(content.get('evidence_ids') or []),
                     'contract_facts': len(content.get('fact_ids') or []),
                     'contract_artifacts': len(content.get('artifact_ids') or []),
                     'existing_words': word_count(text) if text else 0,
                     'has_content': bool(s['current_revision_id'])})
    return rows


def structure_line(rows, target_words):
    if not rows:
        return '还没有建立提纲章节，整篇任务无法开始。'
    written = len([r for r in rows if r['has_content']])
    parts = [f'{len(rows)} 章', f'{target_words:,} 字']
    parts.append(f'{written} 章已有正文' if written else '全部章节尚无正文')
    biggest = max(rows, key=lambda r: r['target_words'])
    if biggest and target_words:
        parts.append(f'篇幅最大的是“{biggest["title"]}”约 {biggest["target_words"]:,} 字，占 {biggest["target_words"] * 100 / target_words:.0f}%')
    return '，'.join(parts)


def risks_for(project, brief, core, rows, inv):
    risks = []

    def add(level, code, message, section_ids=(), advice=''):
        risks.append({'level': level, 'code': code, 'message': message,
                      'section_ids': list(section_ids), 'advice': advice})

    title = str(project['title'] or '')
    question = str(brief.get('question') or '').strip()
    core_question = str(core.get('research_question') or '').strip()
    if PLACEHOLDER_TITLE.match(title.strip()):
        add('high', 'placeholder_title', f'论文题目仍是“{title}”，全篇会围绕这个占位题目展开',
            advice='先在项目信息里改成实际题目，再发起整篇写作。')
    if not question:
        if core_question and not PLACEHOLDER_QUESTION.search(core_question):
            add('info', 'question_from_core', f'项目简报没有填写研究问题，将沿用写作核心中的表述：{core_question[:60]}')
        else:
            add('high', 'missing_question', '项目简报的研究问题为空，写作核心里的研究问题也是概括性表述',
                advice='补一句“这篇论文具体要解决什么问题”，否则各章容易写成提纲扩写。')
    elif PLACEHOLDER_QUESTION.search(question):
        add('medium', 'generic_question', '研究问题更像任务描述，不是具体问题',
            advice='写明对象、场景和要解决的问题，例如面向什么角色、解决什么流程的什么问题。')

    generic = [r for r in rows if r['goal_is_generic']]
    if generic:
        add('medium', 'generic_goal', f'{len(generic)} 章的写作目标仍是默认句，章与章的分工只能靠标题猜测',
            [r['section_id'] for r in generic],
            advice='至少给正文各章补一句本节要回答的问题，整篇任务会按它分配内容。')

    results = [r for r in rows if r['kind'] in FACT_KINDS]
    if results and not inv['facts']:
        add('high', 'results_without_facts', f'{len(results)} 个结果类章节合计 {sum(r["target_words"] for r in results):,} 字，但没有登记任何研究事实',
            [r['section_id'] for r in results],
            advice='先登记测试过程与实测数值，否则这一章只能写测试设计与待补说明。')
    elif results and not inv['measured_facts']:
        add('medium', 'results_without_measurement', f'已登记 {inv["facts"]} 条研究事实，但没有一条是实测结果',
            [r['section_id'] for r in results], advice='把实测数值的条目状态标为“已测量”，结果章才会按数据写。')
    if inv['facts_without_source']:
        add('medium', 'fact_without_source', f'{inv["facts_without_source"]} 条研究事实没有关联来源或产物',
            advice='给事实补上来源页或产物，否则正文无法给出可回查的依据。')

    if any(r['kind'] in LITERATURE_KINDS for r in rows) and not inv['references']:
        add('high', 'no_references', '提纲含文献综述或绪论，但参考文献表为空',
            [r['section_id'] for r in rows if r['kind'] in LITERATURE_KINDS],
            advice='先导入文献，研究现状才有可引用的对象。')
    elif inv['references'] and not inv['checked_references']:
        add('info', 'references_unchecked', f'{inv["references"]} 篇参考文献都还是待核实状态',
            advice='整篇写入前核对关键条目的作者、年份与出处。')

    code_rows = [r for r in rows if r['kind'] in CODE_KINDS]
    if code_rows and not inv['code_snapshot']:
        add('high', 'no_code_snapshot', f'{len(code_rows)} 个需求、设计或实现类章节没有绑定项目代码',
            [r['section_id'] for r in code_rows],
            advice='绑定项目目录，设计取舍和关键流程才有实际依据，不会只停留在名词罗列。')

    if not inv['materials']:
        add('high', 'no_materials', '项目里还没有可用的研究资料',
            advice='至少导入项目说明、需求文档或参考资料。')
    if not inv['terms']:
        add('info', 'no_terms', '没有登记中英文术语，摘要翻译和正文术语容易前后不一致',
            advice='先在术语表补上核心概念的中英文，例如灵魂绑定代币 Soulbound Token。')
    if not inv['figures'] and code_rows:
        add('info', 'no_figures', '还没有可复用的图表，写作时只能新提出示意图或三线表',
            advice='架构图、模块图和数据表会在正文里按段落生成，实际截图仍需自己补。')

    total = sum(r['target_words'] for r in rows if r['kind'] != 'abstract_en')
    if total:
        for r in rows:
            if r['kind'] in ('abstract', 'abstract_en'):
                continue
            share = r['target_words'] * 100 / total
            if share > 45 and len(rows) > 3:
                add('medium', 'unbalanced_share', f'“{r["title"]}”占全篇 {share:.0f}%，其余章节被压得很薄',
                    [r['section_id']], advice='按论证需要重新分配目标字数，或拆成两章。')
            elif r['target_words'] and r['target_words'] < 400 and total > 8000:
                add('info', 'tiny_section', f'“{r["title"]}”只安排 {r["target_words"]} 字，很难写完本节问题',
                    [r['section_id']], advice='确认这是过渡性小节，否则适当加长。')
        declared = project['target_words'] or 0
        if declared and abs(total - declared) / declared > 0.15:
            add('info', 'target_mismatch', f'各章目标合计 {total:,} 字，与项目目标字数 {declared:,} 字相差较大',
                advice='两者取其一，避免导出时按不同标准校验。')
    written = [r for r in rows if r['has_content']]
    if written:
        add('info', 'existing_content', f'{len(written)} 章已有正文，本次生成会作为新版本候选，写入后替换当前正文',
            [r['section_id'] for r in written], advice='如需保留某些段落，先锁定它们。')
    # Stable within a level: the first rule that fires is the one the reader needs.
    risks.sort(key=lambda r: RANK.get(r['level'], 3))
    return risks


def first_clause(role):
    """Keep the leading clause of a role sentence so the draft never ends mid-word."""
    for mark in ('，', '；', '。'):
        if mark in role:
            return role.split(mark)[0]
    return role


def requirement_draft(project, brief, core, rows, inv, risks):
    """Compose an editable whole-paper requirement from records, never invent content."""
    if not rows:
        return ''
    title = str(project['title'] or '本文')
    total = sum(r['target_words'] for r in rows if r['kind'] != 'abstract_en')
    question = str(brief.get('question') or '').strip()
    if not question:
        question = str(core.get('research_question') or '').strip()
    if PLACEHOLDER_QUESTION.search(question):
        question = ''
    objective = str(brief.get('objective') or '').strip()
    method = str(brief.get('method') or '').strip()
    scope = str(brief.get('scope') or '').strip()

    opening = f'一次写完《{title}》的全部 {len(rows)} 章，正文合计约 {total:,} 字。'
    opening += f'全文围绕“{question}”展开，' if question else '全文按提纲确定的问题和范围展开，'
    details = []
    if objective:
        details.append(f'目标是{objective}')
    if method:
        details.append(f'采用{method}')
    if scope:
        details.append(f'范围限定为{scope}')
    if details:
        opening += '，'.join(details) + '；没有在项目资料、研究事实或实测记录中出现的结果、指标与部署效果一律不写。'
    else:
        opening += '凡是项目资料、研究事实或实测记录中没有出现的结果、指标与部署效果一律不写。'

    division = []
    for r in rows:
        if r['kind'] == 'abstract_en':
            continue
        goal = '' if r['goal_is_generic'] else re.sub(r'[。；;]$', '', r['goal'])
        piece = f'{r["title"]}（约 {r["target_words"]:,} 字）'
        piece += f'写{goal}' if goal else f'负责{first_clause(r["role"])}'
        division.append(piece)
    body = '；'.join(division) + '。各章只承担本节任务，定义和背景在同一处讲清，其余章节引用而不复述。'

    basis = (f'可用依据是已登记研究事实 {inv["facts"]} 条、参考文献 {inv["references"]} 篇、'
             f'项目资料 {inv["materials"]} 份')
    basis += f'、项目代码快照中的 {inv["code_units"]:,} 个代码单元' if inv['code_snapshot'] else ''
    basis += '。'
    codes = {r['code'] for r in risks}
    limits = []
    if 'results_without_facts' in codes or 'results_without_measurement' in codes:
        limits.append('结果章目前没有实测数值，只写测试设计、验证方式与现状说明，不给出通过率或性能结论')
    if 'no_references' in codes:
        limits.append('研究现状只按参考文献表中已有的条目展开')
    if 'generic_goal' in codes:
        limits.append('章节分工以标题和上面的角色说明为准，遇到两章都可能写的内容归到更专门的一章')
    basis += '；'.join(limits) + '。' if limits else ''
    basis += '暂缺依据的地方写成【待补证据】并说明缺什么，不推测流程细节，不编造数值、截图的运行结果。'

    terms = f'术语沿用项目术语表当前的 {inv["terms"]} 条登记' if inv['terms'] else '术语表尚未登记，中文首次出现时附英文原词并保持全篇一致'
    style = terms + '，句式以连贯长句为主，不用编号罗列代替论证；图表、编号与引用格式由程序按已发布规则包统一处理。'
    return '\n'.join([opening, body, basis, style])


def estimate(pid, rows, cfg):
    currency = cfg.get('currency', 'USD')
    if not rows:
        return {'method': 'arithmetic', 'priced': False, 'currency': currency, 'total': None,
                'input_tokens': 0, 'output_tokens': 0, 'per_section': [],
                'note': '尚未建立提纲章节，无法估算。'}
    prices_known = all(isinstance(cfg.get(k), (int, float)) and not isinstance(cfg.get(k), bool)
                       for k in ('input_price', 'output_price'))
    context_limit = int(cfg.get('context_limit', 32000) or 32000)
    max_output = int(cfg.get('max_output_tokens', 8192) or 8192)
    history = db.one("""SELECT COUNT(*) n,AVG(json_extract(g.usage,'$.input_tokens')) input_tokens,
                        AVG(json_extract(g.usage,'$.output_tokens')) output_tokens,AVG(g.cost) cost
                        FROM generation_runs g JOIN tasks t ON t.id=g.task_id
                        WHERE t.project_id=? AND t.kind='write_paper' AND g.status='completed'
                        AND json_extract(g.usage,'$.input_tokens') IS NOT NULL""", (pid,))
    measured = bool(history and history['n'] and history['n'] >= 3)
    per_section, cumulative = [], 0
    for r in rows:
        output = min(max_output, 2048 if r['kind'] == 'abstract_en' else max(2048, r['target_words'] * 2 + 1024))
        if measured:
            input_tokens = int(round(history['input_tokens'] or 0))
        else:
            input_tokens = int(min(context_limit * .9, 8000 + cumulative * .9))
        cost = None
        if prices_known:
            cost = (input_tokens * cfg['input_price'] + output * cfg['output_price']) / 1_000_000
        elif measured and history['cost']:
            cost = float(history['cost'])
        per_section.append({'section_id': r['section_id'], 'title': r['title'],
                            'input_tokens': input_tokens, 'output_tokens': output, 'cost': cost})
        if r['kind'] != 'abstract_en':
            cumulative += r['target_words']
    total = sum(p['cost'] for p in per_section) if all(p['cost'] is not None for p in per_section) else None
    return {'method': 'measured_previous_runs' if measured else 'arithmetic',
            'priced': total is not None, 'currency': currency,
            'total': round(total, 6) if total is not None else None,
            'input_tokens': sum(p['input_tokens'] for p in per_section),
            'output_tokens': sum(p['output_tokens'] for p in per_section),
            'per_section': per_section,
            'note': ('按本项目此前整篇请求的实际用量推算，实际按请求计费。' if measured
                     else '按各章目标字数与此前章节全文推算上下文，实际按请求计费。')
                    + ('' if prices_known else '当前接口未登记单价，只能给出 token 预算。')}


def study(pid, route='primary'):
    from .paper_writing import config_for
    project, brief, core, heads, snapshot = brief_of(pid)
    sections = active_sections(pid)
    rows = chapters(pid, sections, heads)
    inv = inventory(pid, snapshot)
    risks = risks_for(project, brief, core, rows, inv)
    total = sum(r['target_words'] for r in rows if r['kind'] != 'abstract_en')
    try:
        cfg, _ = config_for(route, pid)
    except Exception:
        # 未配置密钥时预检会报错；研判只退回默认参数，不用它阻止任何操作。
        from . import provider
        cfg = provider.config()
    return {'summary': {'sections': len(rows), 'target_words': total,
                        'with_content': len([r for r in rows if r['has_content']]),
                        'line': structure_line(rows, total)},
            'chapters': rows, 'inventory': inv, 'risks': risks,
            'attention': len([r for r in risks if r['level'] == 'high']),
            'requirement_draft': requirement_draft(project, brief, core, rows, inv, risks),
            'estimate': estimate(pid, rows, cfg),
            'basis': '本页数字来自本项目提纲、章节任务、研究事实、文献、资料、代码快照与历史请求记录，未调用模型。'}


@router.get('/projects/{pid}/paper-writing/study')
def study_route(pid: str, provider_profile: str = 'primary'):
    security.require_project(pid)
    return study(pid, provider_profile)


def revision_advice(report, plan=()):
    """Turn local findings and word-count shortfalls into one editable instruction."""
    titles = {s['id']: s.get('title', '') for s in plan}
    metrics = {m['section_id']: m for m in report.get('section_metrics', [])}
    problems = {}
    for finding in report.get('findings', []):
        for sid in finding['section_ids']:
            problems.setdefault(sid, []).append(finding)

    def weight(item):
        items = item[1]
        errors = [f for f in items if f['severity'] == 'error']
        return (0 if errors else 1, -len(items))

    lines = []
    for sid, items in sorted(problems.items(), key=weight)[:5]:
        title = titles.get(sid) or '本章'
        facts = []
        errors = [f['message'] for f in items if f['severity'] == 'error']
        if errors:
            facts.append('结构问题须先修：' + '；'.join(list(dict.fromkeys(errors))[:2]))
        warnings = [f['message'] for f in items if f['severity'] != 'error']
        if warnings:
            facts.append('；'.join(list(dict.fromkeys(warnings))[:3]))
        metric = metrics.get(sid, {})
        if metric.get('target_words') and metric.get('words') is not None and metric['words'] < metric['target_words'] * .7:
            facts.append(f'现约 {metric["words"]} 字，不足目标 {metric["target_words"]} 字')
        if facts:
            lines.append(f'“{title}”' + '，'.join(facts))
    if not lines:
        return ''
    return ('按本次审阅结果集中处理以下问题：' + '；'.join(lines) +
            '。修订只用项目已有的资料、事实与实测记录，缺依据处保留待补说明；其余章节保持不变，结论与中英文摘要按修改后的正文联动更新，不要重复其他章节的内容。')
