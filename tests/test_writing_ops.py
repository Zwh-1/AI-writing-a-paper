"""The writing-operation registry, its briefs and its self-checks stay in step."""
import pytest
from fastapi import HTTPException
from test_writing_agent import env
from test_paper_writing import manuscript, finish
from test_workbench import client  # noqa: F401 — the manuscript fixture depends on it
from app import db, writing_ops, writing_brief, writing_checks

SECTION = {'id': 's1', 'title': '第四章 系统设计', 'kind': 'system_design', 'target_words': 3000, 'goal': ''}
PROJECT = {'title': '基于可进化灵魂代表的学生成长档案系统', 'brief': '{}'}


def test_registry_covers_every_writing_scenario(env):
    names = set(writing_ops.OPERATIONS)
    assert {'whole_paper', 'section', 'revise', 'expand', 'compress', 'polish', 'figure'} <= names
    for name, spec in writing_ops.OPERATIONS.items():
        assert spec['stages'] and spec['checks'] and spec['contract'] and spec['failure'], name
        assert any(stage['owner'] == 'program' for stage in spec['stages']), name
        assert any(stage['owner'] == 'model' for stage in spec['stages']), name
        assert set(spec['context']) <= set(writing_ops.TIERS), name
        assert set(spec['params']) <= set(writing_ops.PARAMS), name


def test_catalog_exposes_resolved_defaults_and_tiers(env):
    catalog = writing_ops.catalog(env['pid'])
    whole = next(item for item in catalog['operations'] if item['operation'] == 'whole_paper')
    assert whole['params'] == {'paragraph_target_words': 220, 'max_paragraphs': 30, 'cover_points': True}
    tiers = {row['key']: row['tier'] for row in whole['context']}
    assert tiers['rules'] == 'mandatory' and tiers['writing_brief'] == 'mandatory'
    assert tiers['sources'] == 'evidence' and tiers['writing_examples'] == 'optional'
    response = env['client'].get(f"/api/v1/projects/{env['pid']}/writing-operations", params={'section_id': env['sid']})
    assert response.status_code == 200, response.text
    assert response.json()['section_plan']['section']['count'] >= 3


def test_parameter_override_is_validated_persisted_and_audited(env):
    pid = env['pid']
    ok = env['client'].put(f'/api/v1/projects/{pid}/writing-operations/whole_paper',
                           json={'params': {'paragraph_target_words': 180}})
    assert ok.status_code == 200, ok.text
    assert ok.json()['params']['paragraph_target_words'] == 180
    assert writing_ops.params_for(pid, 'whole_paper')['paragraph_target_words'] == 180
    assert db.one("SELECT COUNT(*) n FROM audit WHERE project_id=? AND action='writing_parameters_saved'", (pid,))['n'] == 1
    for bad in ({'paragraph_target_words': 60}, {'paragraph_target_words': 'many'},
                {'max_paragraphs': 4.5}, {'unknown_parameter': 1}):
        response = env['client'].put(f'/api/v1/projects/{pid}/writing-operations/whole_paper', json={'params': bad})
        assert response.status_code == 422, (bad, response.text)
    # Polish does not accept paragraph settings; its own parameters are separate.
    assert env['client'].put(f'/api/v1/projects/{pid}/writing-operations/polish',
                             json={'params': {'paragraph_target_words': 180}}).status_code == 422
    assert env['client'].put(f'/api/v1/projects/{pid}/writing-operations/unknown',
                             json={'params': {'cover_points': False}}).status_code == 404


def test_paragraph_plan_sums_to_the_chapter_target(env):
    plan = writing_ops.paragraph_plan(3000, 'system_design', {'paragraph_target_words': 220, 'max_paragraphs': 30})
    assert plan['count'] == 14
    assert sum(row['target_words'] for row in plan['paragraphs']) == 3000
    assert plan['paragraphs'][0]['purpose'] == '总体架构'
    short = writing_ops.paragraph_plan(350, 'abstract', {'paragraph_target_words': 220, 'max_paragraphs': 30})
    assert short['count'] >= 3 and sum(row['target_words'] for row in short['paragraphs']) == 350
    capped = writing_ops.paragraph_plan(60000, 'body', {'paragraph_target_words': 220, 'max_paragraphs': 12})
    assert capped['count'] <= 12


def test_brief_is_deterministic_and_falls_back_to_the_chapter_type(env):
    first = writing_brief.build(SECTION, PROJECT, {}, [], [], [], '')
    second = writing_brief.build(SECTION, PROJECT, {}, [], [], [], '')
    assert first['hash'] == second['hash']
    assert [point['text'] for point in first['points']][:2] == ['总体架构', '模块划分与职责']
    assert first['goal_is_default'] is True
    assert first['paragraph_plan']['count'] == 14 and first['target_words'] == 3000
    explicit = writing_brief.build({**SECTION, 'goal': '说明总体架构；给出模块职责；交代数据模型；'},
                                   PROJECT, {}, [], [], [], '')
    assert explicit['hash'] != first['hash']
    assert [point['text'] for point in explicit['points']] == ['说明总体架构', '给出模块职责', '交代数据模型']
    assert all(point['required'] for point in explicit['points'])
    assert '把设计图写成已部署运行' in explicit['forbidden']


def test_research_question_is_carried_but_placeholder_is_ignored(env):
    intro = {'id': 's2', 'title': '第一章 绪论', 'kind': 'introduction', 'target_words': 2000, 'goal': ''}
    with_question = writing_brief.build(intro, PROJECT, {}, [], [], [], '如何验证学生成长档案的可信性？')
    assert with_question['points'][0]['id'] == 'rq'
    assert '如何验证学生成长档案的可信性' in with_question['points'][0]['text']
    placeholder = writing_brief.build(intro, PROJECT, {}, [], [], [], '围绕“我的毕业论文”组织项目的需求、设计、实现与验证论证。')
    assert all(point['id'] != 'rq' for point in placeholder['points'])


def test_long_chapter_plan_does_not_pad_the_final_responsibility(env):
    plan = writing_ops.paragraph_plan(3000, 'system_design', {'paragraph_target_words': 220, 'max_paragraphs': 30})
    purposes = [row['purpose'] for row in plan['paragraphs']]
    expected = writing_ops.KIND_PURPOSES['system_design']
    assert set(purposes) == set(expected)
    assert purposes.count(expected[-1]) <= 3
    assert [expected.index(purpose) for purpose in purposes] == sorted(expected.index(purpose) for purpose in purposes)
    brief = writing_brief.build({**SECTION, 'goal': '解释授权校验；说明撤销状态读取；'}, PROJECT, {}, [], [], [], '')
    assert {row['purpose'] for row in brief['paragraph_plan']['paragraphs']} == {'解释授权校验', '说明撤销状态读取'}
    assert sum(row['target_words'] for row in brief['paragraph_plan']['paragraphs']) == 3000


def test_natural_paragraph_count_is_advisory_but_missing_content_and_evidence_remain_findings(env):
    brief = writing_brief.build(SECTION, PROJECT, {}, [], [], [], '')
    paragraphs = [{'text': '测试显示响应时间降低了40%。', 'brief_points': ['k1']}]
    result = writing_checks.check(SECTION, brief, paragraphs)
    codes = {row['code'] for row in result['findings']}
    assert 'paragraph_plan_short' not in codes
    assert {'brief_point_missing', 'claim_without_evidence', 'section_length_short'} <= codes
    legacy = {**brief, 'paragraph_plan': {**brief['paragraph_plan'], 'advisory': False}}
    assert 'paragraph_plan_short' in {row['code'] for row in writing_checks.check(SECTION, legacy, paragraphs)['findings']}


def test_checks_flag_missing_points_and_unanchored_numbers(env):
    brief = writing_brief.build(SECTION, PROJECT, {}, [], [], [], '')
    paragraphs = [{'text': '本系统采用分层架构，接口层负责鉴权。', 'brief_points': ['k1']},
                  {'text': '测试显示响应时间降低了 40%，并发达到 200。'}]
    verdict = writing_checks.check(SECTION, brief, paragraphs, '本系统采用分层架构，接口层负责鉴权。测试显示响应时间降低了 40%。')
    codes = {finding['code'] for finding in verdict['findings']}
    assert 'brief_point_missing' in codes and 'claim_without_evidence' in codes
    assert verdict['coverage']['covered'] == 1 and verdict['coverage']['required'] == 5
    assert verdict['passed'] is False
    covered = [{'text': '总体架构为三层。', 'brief_points': ['k1', 'k2', 'k3', 'k4', 'k5']}]
    covered_result = writing_checks.check(SECTION, brief, covered, '总体架构为三层。')
    assert covered_result['coverage']['covered'] == covered_result['coverage']['required']
    assert 'brief_point_missing' not in {finding['code'] for finding in covered_result['findings']}
    assert covered_result['passed'] is False  # Coverage labels do not prove evidence or sufficient content.
    unknown = writing_checks.check(SECTION, brief, [{'text': '架构。', 'brief_points': ['zz']}], '架构。')
    assert 'brief_point_unknown' in {finding['code'] for finding in unknown['findings']}


def test_figure_labels_must_be_found_in_the_quoted_text(env):
    spec = {'kind': 'flowchart', 'title': '存证流程', 'nodes': [
        {'id': 'a', 'label': '开始'}, {'id': 'b', 'label': '链上存证服务'},
        {'id': 'c', 'label': '分布式缓存中间件'}], 'edges': []}
    missing = writing_brief.ungrounded_labels(spec, ['本文实现链上存证服务，负责写入哈希。'])
    assert missing == ['分布式缓存中间件']
    assert writing_brief.ungrounded_labels(spec, ['链上存证服务与分布式缓存中间件均已实现。']) == []
    assert writing_brief.ungrounded_labels(spec, ['链上存证服务'], '另外请画出分布式缓存中间件') == []


def test_section_writing_carries_the_brief_and_records_the_check(manuscript):
    e = manuscript
    finish(e)
    sent = e['calls'][0]
    assert sent['writing_brief']['version'] == writing_brief.VERSION
    assert sent['writing_brief']['points'] and sent['writing_brief']['paragraph_plan']['count'] >= 3
    assert 'hash' not in sent['writing_brief']
    task = db.one("SELECT result FROM tasks WHERE kind='write_paper'")
    result = db.loads(task['result'], {})
    first = result['paper_sections'][0]
    assert first['writing_brief']['hash'] and first['writing_brief']['points'] >= 1
    assert first['brief_check']['version'] == writing_checks.VERSION
    codes = {finding['code'] for finding in result['review']['findings']}
    assert 'brief_point_missing' in codes


def test_whole_paper_plan_exposes_paragraph_plan(env):
    preview = env['client'].get(f"/api/v1/projects/{env['pid']}/paper-writing/preflight")
    assert preview.status_code == 200, preview.text
    plan = preview.json()['writing_plan']
    assert plan and plan[0]['paragraphs'] >= 3
    assert plan[0]['paragraph_plan'][0]['purpose']
    assert plan[0]['paragraph_plan_advisory'] is True


def test_cross_section_duplicate_flags_a_paragraph_copied_from_another_chapter():
    copied = '系统采用三层架构，分别表示层、业务逻辑层与数据访问层，各层之间通过接口通信，职责清晰。'
    related = [{'title': '第三章 需求分析', 'text': '前文。' + copied + '后文。'}]
    assert writing_checks.cross_section_duplicate([{'text': copied}], related)
    assert writing_checks.cross_section_duplicate([{'text': '本章重写了这一段的表述。'}], related) == []
    assert writing_checks.cross_section_duplicate([{'text': copied}], []) == []


def test_fact_number_drift_flags_a_value_that_left_the_record():
    facts = [{'id': 'f1', 'statement': '链上写入平均耗时 320 毫秒，成功率 99.2%。'}]
    drifted = [{'text': '实测写入平均耗时为 45 毫秒。', 'fact_ids': ['f1']}]
    assert writing_checks.fact_number_drift(drifted, facts)
    ok = [{'text': '实测写入平均耗时为 320 毫秒，成功率 99.2%。', 'fact_ids': ['f1']}]
    assert writing_checks.fact_number_drift(ok, facts) == []
    pooled = [{'text': '写入耗时 210 毫秒。', 'fact_ids': ['f1', 'f2']}]
    assert writing_checks.fact_number_drift(pooled, facts + [{'id': 'f2', 'statement': '峰值 210 毫秒。'}]) == []
    assert writing_checks.fact_number_drift([{'text': '未引用任何事实。'}], facts) == []


def test_terminology_gloss_only_checks_the_bracketed_english():
    terms = [{'zh': '灵魂绑定代币', 'en': 'Soulbound Token'}, {'zh': '可进化凭证', 'en': 'Evolvable Credential'}]
    assert writing_checks.terminology_gloss('灵魂绑定代币（Non Fungible Token）不可转让。', terms)
    assert writing_checks.terminology_gloss('灵魂绑定代币（Soulbound Token）不可转让。', terms) == []
    assert writing_checks.terminology_gloss('可进化凭证已在系统中使用。', terms) == []


def test_consistency_findings_reach_the_section_verdict():
    """The three consistency checks are wired into the verdict, and only fire on real context."""
    brief = writing_brief.build({'id': 's1', 'title': '第四章 系统设计', 'kind': 'system_design', 'target_words': 0, 'goal': ''},
                                {'title': '基于可进化灵魂代表的学生成长档案系统', 'brief': '{}'},
                                {}, [], [], [{'zh': '灵魂绑定代币', 'en': 'Soulbound Token'}], '', {}, 'generate')
    points = [point['id'] for point in brief['points']]
    body = ('灵魂绑定代币（Non Fungible Token）在本系统中承担凭证载体，'
            '由发行方合约统一铸造并写入持有者地址，学生在校期间的每一次成长记录都对应一枚独立凭证，'
            '凭证一经签发即不可转账，只能通过升级接口在原有编号上追加新的属性层。')
    verdict = writing_checks.check(SECTION, brief, [{'text': body, 'brief_points': points}], body,
                                   related=[{'title': '第三章 需求分析', 'text': '前文。' + body + '后文。'}],
                                   facts=[{'id': 'f1', 'statement': '链上写入耗时 320 毫秒。'}],
                                   terms=[{'zh': '灵魂绑定代币', 'en': 'Soulbound Token'}])
    codes = {finding['code'] for finding in verdict['findings']}
    assert {'cross_section_duplicate', 'terminology_gloss'} <= codes
    assert all(finding['severity'] == 'warning' for finding in verdict['findings'])
    assert all(finding.get('excerpt') or finding.get('message') for finding in verdict['findings'])

    clean_body = body.replace('Non Fungible Token', 'Soulbound Token')
    bare = writing_checks.check(SECTION, brief, [{'text': clean_body, 'brief_points': points}], clean_body)
    assert {'cross_section_duplicate', 'terminology_gloss', 'fact_number_drift'} & {f['code'] for f in bare['findings']} == set()
    drifted = writing_checks.check(SECTION, brief, [{'text': clean_body + '写入耗时 45 毫秒。',
                                                     'brief_points': points, 'fact_ids': ['f1']}],
                                   clean_body + '写入耗时 45 毫秒。',
                                   facts=[{'id': 'f1', 'statement': '链上写入耗时 320 毫秒。'}])
    assert 'fact_number_drift' in {finding['code'] for finding in drifted['findings']}
