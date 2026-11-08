"""Regression cases for source semantics, readable geometry and atomic assembly."""
from copy import deepcopy

import pytest

from test_chapter_materials import structured_project,collect  # noqa: F401
from test_writing_agent import env  # noqa: F401
from app import db,documents,security,tasks,chapter_materials,chapter_visual_plans
from app import project_structures,writing_visuals,paper_visuals,figure_standards
from app.paper_visual_render import _font,_lines
from app.paper_visual_layout import layout_diagram
from app.writing_agent import repository


def materials(sql):
    value=chapter_materials.empty('snapshot')
    value['database_tables']=[{**t,'id':'m'+str(i),'evidence_ids':['ddl'],
                              'conflicting_definitions':False} for i,t in enumerate(project_structures.sql_tables(sql,'schema.sql'))]
    return value


def test_compound_constraints_default_comments_and_auto_increment_are_not_lost():
    value=materials("""CREATE TABLE accounts (
       id INT AUTO_INCREMENT PRIMARY KEY, issuer_id INT NOT NULL, external_id INT NOT NULL,
       status VARCHAR(30) DEFAULT 'not ready' COMMENT '审核状态',
       UNIQUE KEY pair_key (issuer_id, external_id));""")
    table=value['database_tables'][0]
    assert table['fields'][0]['data_type']=='INT'
    assert 'AUTO_INCREMENT' in table['fields'][0]['constraints']
    assert table['fields'][3]['default']=="'not ready'" and table['fields'][3]['comment']=='审核状态'
    spec=chapter_visual_plans.build({'kind':'system_design'},value)[0]['spec']
    assert '联合唯一（issuer_id, external_id）' in spec['note']
    assert all('唯一' not in row[2] for row in spec['rows'])
    assert spec['rows'][3][-1]=='审核状态'


def test_compound_unique_nullable_fk_allows_zero_children_and_reordered_columns():
    value=materials('''CREATE TABLE parent(a INT, b INT, PRIMARY KEY(a,b));
        CREATE TABLE child(id INT PRIMARY KEY, x INT, y INT,
        UNIQUE KEY pair_key(y,x), FOREIGN KEY(x,y) REFERENCES parent(a,b));''')
    er=next(p['spec'] for p in chapter_visual_plans.build({'kind':'system_design'},value) if p['spec']['kind']=='er')
    assert er['edges'][0]['source_cardinality']=='0..1'
    assert er['edges'][0]['target_cardinality']=='0..1'
    assert er['edges'][0]['label']=='x, y → a, b'


def test_non_unique_referenced_columns_do_not_establish_a_single_parent():
    value=materials('''CREATE TABLE parent(id INT, label VARCHAR(30));
        CREATE TABLE child(id INT PRIMARY KEY, parent_id INT,
        FOREIGN KEY(parent_id) REFERENCES parent(id));''')
    assert not any(p['spec']['kind']=='er' for p in chapter_visual_plans.build({'kind':'system_design'},value))


def test_summary_keeps_relationship_keys_and_business_status_instead_of_copying_schema():
    fields=', '.join('extra'+str(i)+' VARCHAR(100)' for i in range(25))
    value=materials('CREATE TABLE records(id INT PRIMARY KEY, '+fields+", status VARCHAR(20) DEFAULT 'pending');")
    spec=chapter_visual_plans.build({'kind':'system_design'},value)[0]['spec']
    assert len(spec['rows'])<len(value['database_tables'][0]['fields'])
    assert {'id','status'}<={row[0] for row in spec['rows']}
    assert '仅列关键字段' in spec['note']


def test_chapters_do_not_repeat_the_same_static_page_map(structured_project):
    specs=[]
    for kind in ('requirements','system_design','implementation'):
        value,_=collect(structured_project,kind)
        specs.append([p['spec'] for p in value['visual_plans']])
    assert all(a!=b for i,group in enumerate(specs) for a in group for other in specs[i+1:] for b in other)
    assert not any(spec['kind']=='structure' for group in specs[:2] for spec in group)
    assert any(spec['kind']=='structure' for spec in specs[2])


def test_skip_and_todo_are_preserved_in_test_design_tables():
    value=chapter_materials.empty('s')
    value['test_cases']=[{**c,'id':str(i),'evidence_ids':['test']} for i,c in enumerate(
        project_structures.test_cases("it.skip('skipped',()=>{}); it.todo('pending'); test('normal',()=>{});",'a.test.ts'))]
    rows=chapter_visual_plans.build({'kind':'results'},value)[0]['spec']['rows']
    assert [r[2] for r in rows]==['已跳过（skip）','待实现（todo）','未提供执行记录']


def test_concrete_material_discussion_does_not_require_a_chart_for_every_group(structured_project):
    value,_=collect(structured_project)
    candidate={'paragraphs':[{'text':'学生记录以id作为主键。','project_material_ids':[value['database_tables'][0]['id']]}]}
    assert chapter_visual_plans.findings({'kind':'system_design'},candidate,{'operation':'generate','chapter_materials':value})==[]


def test_explicit_er_request_cannot_be_satisfied_by_an_unrelated_table(structured_project):
    value,_=collect(structured_project)
    plan=next(p for p in value['visual_plans'] if p['spec']['kind']=='three_line_table')
    candidate={'paragraphs':[{'text':'字段说明。','visual_plan_ids':[plan['id']]}]}
    findings=chapter_visual_plans.findings({'kind':'system_design'},candidate,{'operation':'generate','instruction':'请生成 ER 图','chapter_materials':value})
    assert findings and findings[0]['method']=='explicit_visual_requirement'


def test_english_identifiers_wrap_only_at_word_boundaries_when_each_word_fits():
    text='submitCredentialApplication';font=_font(20)
    width=max(font.getlength(word) for word in ('submit','Credential','Application'))+2
    lines=_lines(text,font,width)
    assert ''.join(lines)==text
    assert all(sum(len(line) for line in lines[:i]) in {0,6,16,27} for i in range(1,len(lines)))
    assert all(font.getlength(line)<=width for line in lines)


def test_unequal_er_entities_share_a_straight_clear_relation_corridor():
    spec=deepcopy(paper_visuals.TEMPLATES['er'])
    spec['entities']=spec['entities'][:2];spec['edges']=spec['edges'][:1]
    spec['entities'][1]['fields'].extend([{'name':'additional_field','data_type':'varchar(80)'}])
    scene=layout_diagram(paper_visuals.validate_spec(spec))
    nodes=scene['nodes']
    assert (nodes[0]['box'][1]+nodes[0]['box'][3])/2==(nodes[1]['box'][1]+nodes[1]['box'][3])/2
    assert len(scene['edges'][0]['points'])==2
    assert scene['width']<1300


def table_proposal(title='字段与条件'):
    return {'after_paragraph':0,'evidence_ids':['source'],'explanation':'字段约束限定记录的保存条件。',
            'spec':{'kind':'three_line_table','title':title,'headers':['字段','条件'],'rows':[['id','主键']]}}


def test_failed_render_preflight_leaves_no_assets_or_mutated_paragraph(env,monkeypatch):
    from app import paper_visual_render
    task=tasks.enqueue(env['pid'],'write',{'section_id':env['sid']})
    content=documents.text_document('说明。')['content'];original=deepcopy(content)
    first=table_proposal();second={**first,'spec':paper_visuals.TEMPLATES['flowchart']}
    def fail(spec):raise ValueError('模拟文字无法排布')
    monkeypatch.setattr(paper_visual_render,'render_diagram',fail)
    before=db.one('SELECT count(*) AS n FROM artifacts')['n']
    with pytest.raises(ValueError,match='无法排布'):
        writing_visuals.compile_proposals(task,security.require_section(env['sid']),[first,second],content,[{'id':'source'}])
    assert db.one('SELECT count(*) AS n FROM artifacts')['n']==before
    assert content==original


def test_same_task_changed_candidate_can_render_and_duplicate_proposals_reuse_asset(env):
    task=tasks.enqueue(env['pid'],'write',{'section_id':env['sid']});section=security.require_section(env['sid'])
    db.execute("UPDATE tasks SET status='running',attempt=1 WHERE id=?",(task['id'],))
    task=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    content=documents.text_document('说明。')['content']
    doc,ids=writing_visuals.compile_proposals(task,section,[table_proposal(),table_proposal()],content,[{'id':'source'}])
    assert len(ids)==1 and sum(n['type']=='table' for n in doc)==1
    _,next_ids=writing_visuals.compile_proposals(task,section,[table_proposal('修订后的字段说明')],content,[{'id':'source'}])
    assert len(next_ids)==1 and next_ids!=ids


def test_source_ids_do_not_allow_invented_physical_er_fields_or_wrong_cardinality():
    source='CREATE TABLE parent(id INT PRIMARY KEY); CREATE TABLE child(id INT PRIMARY KEY, pid INT NOT NULL, FOREIGN KEY(pid) REFERENCES parent(id));'
    value=materials(source)
    spec=next(p['spec'] for p in chapter_visual_plans.build({'kind':'system_design'},value) if p['spec']['kind']=='er')
    evidence=[{'id':'ddl','text':source,'path':'schema.sql'}]
    writing_visuals._check_physical_er(spec,evidence)
    invalid=deepcopy(spec);invalid['entities'][0]['fields'][0]['name']='invented_score'
    with pytest.raises(ValueError,match='不存在的字段'):writing_visuals._check_physical_er(invalid,evidence)
    invalid=deepcopy(spec);invalid['edges'][0]['source_cardinality']='1'
    with pytest.raises(ValueError,match='基数'):writing_visuals._check_physical_er(invalid,evidence)
    invalid['entities'].append({'id':'unrelated','label':'未提供的概念对象','fields':[{'name':'id'}]})
    with pytest.raises(ValueError,match='基数'):writing_visuals._check_physical_er(invalid,evidence)
    invalid=deepcopy(spec);invalid['entities'][0]['fields'][0]['data_type']='FLOAT'
    with pytest.raises(ValueError,match='字段类型'):writing_visuals._check_physical_er(invalid,evidence)


def test_table_explanation_is_inserted_bound_and_checked(env):
    section=security.require_section(env['sid'])
    task=tasks.enqueue(env['pid'],'write',{'section_id':env['sid']})
    db.execute("UPDATE tasks SET status='running',attempt=1 WHERE id=?",(task['id'],))
    task=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    nodes,_=writing_visuals.compile_proposals(task,section,[table_proposal()],documents.text_document('引出问题。')['content'],[{'id':'source'}])
    table=next(n for n in nodes if n['type']=='table')
    explanation=nodes[nodes.index(table)+1]
    assert explanation['attrs']['describesFigureId']==table['attrs']['nodeId']
    assert table['attrs']['explanationNodeId']==explanation['attrs']['nodeId']
    snap={'section':section,'revision':{'id':'preview'},'document':{'type':'doc','content':[table]}}
    report=figure_standards.evaluate(env['pid'],saved_snapshots=[snap])
    assert any(i['code']=='EXPLANATION_MISSING' and i['node_id']==table['attrs']['nodeId'] for i in report['items'])


def test_localized_page_names_are_read_from_exact_namespace_and_registered_sources(structured_project):
    e=structured_project
    component=e['root']/'frontend/src/features/CredentialView.tsx'
    component.write_text('''import { useTranslations } from 'next-intl';
import { credentialApi } from './api';
const t = useTranslations('student.apply');
export default function CredentialView(){
  const submit=()=>credentialApi.apply();
  return <PageLayout title={t('pageTitle')}><Button onClick={submit}>{t('submit')}</Button></PageLayout>;
}''',encoding='utf-8')
    locale=e['root']/'frontend/src/i18n/zh/student/apply.json';locale.parent.mkdir(parents=True,exist_ok=True)
    locale.write_text('{"student":{"apply":{"pageTitle":"申请凭证","submit":"提交申请","fake":"已全部通过"}}}',encoding='utf-8')
    snapshot=repository.capture(e['pid'],e['binding']['id'])
    paths=[r['relative_path'] for r in db.rows('SELECT relative_path FROM repository_snapshot_files WHERE snapshot_id=?',(snapshot['id'],))]
    value,items=chapter_materials.collect(e['pid'],snapshot['id'],{'kind':'implementation'},paths,max_ranges=30,max_bytes=40000)
    page=value['frontend_pages'][0]
    assert '申请凭证' in page['labels'] and '提交申请' in page['labels'] and '已全部通过' not in page['labels']
    assert any('i18n/zh/student/apply.json' in r['path'] for r in value['readings'])
    plan=next(p for p in value['visual_plans'] if p['spec']['kind']=='structure')
    assert plan['spec']['nodes'][0]['label']=='申请凭证'
    assert set(plan['evidence_ids'])<={r['id'] for r in items}
