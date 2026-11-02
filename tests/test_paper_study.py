"""The whole-paper study must be readable, cheap and free of invented content."""
from test_writing_agent import env
from app import db, paper_study


def test_study_reads_outline_and_flags_missing_evidence(env):
    pid = env['pid']
    env['client'].post(f'/api/v1/projects/{pid}/sections',
                       json={'title': '测试与结果', 'kind': 'results', 'target_words': 2000})
    data = paper_study.study(pid)
    assert data['summary']['sections'] == 2
    assert data['summary']['target_words'] >= 2000
    codes = {r['code'] for r in data['risks']}
    assert 'results_without_facts' in codes
    # The fixture bound a repository, so the code gap must not fire.
    assert 'no_code_snapshot' not in codes
    assert data['inventory']['code_units'] >= 1
    assert data['inventory']['facts'] == 0
    assert '如何准确描述功能的授权条件' in data['requirement_draft']
    assert '【待补证据】' in data['requirement_draft']
    chapter = next(c for c in data['chapters'] if c['kind'] == 'results')
    assert chapter['target_words'] == 2000 and chapter['role']


def test_study_does_not_spend_or_write(env):
    pid = env['pid']
    before = (db.one('SELECT COUNT(*) n FROM generation_runs')['n'],
              db.one('SELECT COUNT(*) n FROM tasks')['n'],
              db.one('SELECT COUNT(*) n FROM project_revisions')['n'])
    paper_study.study(pid)
    after = (db.one('SELECT COUNT(*) n FROM generation_runs')['n'],
             db.one('SELECT COUNT(*) n FROM tasks')['n'],
             db.one('SELECT COUNT(*) n FROM project_revisions')['n'])
    assert before == after


def test_study_estimate_uses_configured_price_and_word_target(env):
    pid = env['pid']
    env['client'].post(f'/api/v1/projects/{pid}/sections',
                       json={'title': '结论', 'kind': 'conclusion', 'target_words': 1000})
    data = paper_study.study(pid)
    estimate = data['estimate']
    assert estimate['priced'] is True
    # The fixture prices both sides at zero, so a priced estimate is exactly zero.
    assert estimate['total'] == 0
    assert estimate['method'] == 'arithmetic'
    assert estimate['output_tokens'] >= 1000
    assert [p['section_id'] for p in estimate['per_section']] == [c['section_id'] for c in data['chapters']]


def test_draft_never_quotes_a_placeholder_question(env):
    pid = env['pid']
    db.execute('UPDATE projects SET title=? WHERE id=?', ('我的毕业论文', pid))
    db.execute('UPDATE projects SET brief=? WHERE id=?', ('{}', pid))
    core = db.one('SELECT * FROM thesis_core_versions WHERE project_id=?', (pid,))
    db.execute('UPDATE thesis_core_versions SET content_json=? WHERE id=?', (
        db.dumps({'research_question': '围绕“我的毕业论文”组织项目的需求、设计、实现与验证论证。'}), core['id']))
    draft = paper_study.study(pid)['requirement_draft']
    assert '围绕“我的毕业论文”' not in draft
    assert '“展开' not in draft
    assert '我的毕业论文' in draft  # the title is still reported honestly


def test_revision_advice_names_chapters_and_stays_editable(env):
    report = {'findings': [{'id': 'a', 'severity': 'error', 'message': '整篇候选缺少提纲中的章节', 'section_ids': ['s1']},
                           {'id': 'b', 'severity': 'warning', 'message': '本节约800字，明显低于目标2000字', 'section_ids': ['s1']},
                           {'id': 'c', 'severity': 'warning', 'message': '本段包含2处待补标记', 'section_ids': ['s1']}],
              'section_metrics': [{'section_id': 's1', 'title': '第一章', 'words': 800, 'target_words': 2000}]}
    text = paper_study.revision_advice(report, [{'id': 's1', 'title': '第一章 绪论'}])
    assert '第一章 绪论' in text
    assert '结构问题须先修' in text
    assert '不足目标 2000 字' in text
    assert text.endswith('不要重复其他章节的内容。')
    assert paper_study.revision_advice({'findings': [], 'section_metrics': []}) == ''
