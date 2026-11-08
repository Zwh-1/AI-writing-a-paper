"""Real-source structures -> visual plans -> explanatory thesis paragraphs."""
from copy import deepcopy

import pytest

from test_writing_agent import env  # noqa: F401
from test_project_grounded_writing import reply,synthetic_understanding
from app import db,provider,security,tasks,documents,project_structures as structures
from app import chapter_materials,chapter_visual_plans,writing_flow
from app.writing_agent import repository,contracts
from app.writing_agent.common import state,update_state

DDL='''CREATE TABLE students (
 id INT NOT NULL PRIMARY KEY,
 name VARCHAR(80) NOT NULL
);
CREATE TABLE credentials (
 id INT NOT NULL,
 student_id INT NOT NULL,
 issuer_id INT,
 status ENUM('PENDING','APPROVED') NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_student (student_id),
 FOREIGN KEY (student_id) REFERENCES students(id)
);
'''


def test_literal_sql_fields_enum_constraints_and_exact_offsets():
    rows=structures.sql_tables(DDL,'schema.sql')
    assert [row['name'] for row in rows]==['students','credentials']
    credential=rows[1]
    assert credential['fields'][3]['data_type']=="ENUM('PENDING','APPROVED')"
    assert credential['foreign_keys']==[{'columns':['student_id'],'target_table':'students','target_columns':['id']}]
    assert credential['unique_keys']==[['student_id']]
    assert 'FK' not in credential['fields'][2]['constraints']
    assert DDL[credential['source_start']:credential['source_end']].startswith('CREATE TABLE credentials')
    assert credential['complete_definition']


def test_ts_literal_concatenation_and_escaped_newlines_are_not_executed():
    source='// "CREATE TABLE fake (x INT)"\nawait db.query("CREATE TABLE roles (" + "id INT PRIMARY KEY," + "label VARCHAR(80) NOT NULL)");'
    tables=structures.sql_tables(source,'startup.ts')
    assert [row['name'] for row in tables]==['roles']
    assert [field['name'] for field in tables[0]['fields']]==['id','label']
    assert source[tables[0]['source_start']:tables[0]['source_end']].startswith('CREATE TABLE roles')
    escaped='db.query("CREATE TABLE sample (\\n id INT PRIMARY KEY,\\n title VARCHAR(80) NOT NULL\\n)");'
    assert structures.sql_tables(escaped,'db.ts')[0]['fields'][1]['name']=='title'


def test_dynamic_table_or_field_expressions_do_not_become_invented_structure():
    assert not structures.sql_tables('db.query(`CREATE TABLE ${name} (id INT)`);','db.ts')
    row=structures.sql_tables('db.query(`CREATE TABLE known (id INT, ${extra})`);','db.ts')[0]
    assert not row['complete_definition']


def test_test_definitions_and_route_fragments_never_claim_execution():
    cases=structures.test_cases("test('reject missing student', () => { expect(lookup()).toThrow(); });",'sample.test.ts')
    assert cases[0]['assertions']==['toThrow']
    assert cases[0]['execution_status']=='definition_only_no_execution_judgment'
    routes=structures.api_routes("router.post('/credentials', handler);",'route.ts')
    assert routes[0]['method']=='POST' and routes[0]['route']=='/credentials'
    assert '挂载前缀' in routes[0]['limitations'][0]


def test_comments_strings_and_todo_cases_do_not_supply_neighboring_assertions():
    source="it.todo('not implemented');\nit('works', () => { expect(value).toBe(1); });"
    rows=structures.test_cases(source,'case.test.ts')
    assert rows[0]['assertions']==[] and rows[0]['declared_mode']=='todo'
    assert rows[1]['assertions']==['toBe']
    masked=structures.code_mask('// credentialApi.fake()\nconst hint="credentialApi.demo()";\ncredentialApi.real();')
    assert 'fake' not in masked and 'demo' not in masked and 'credentialApi.real' in masked
    sql="-- CREATE TABLE wrong (id INT);\nCREATE TABLE notes (id INT, value VARCHAR(80) DEFAULT 'a--b');"
    assert [row['name'] for row in structures.sql_tables(sql,'schema.sql')]==['notes']
    row=structures.sql_tables("CREATE TABLE notes (id INT, student_id INT, hint VARCHAR(80) COMMENT 'FOREIGN KEY (student_id) REFERENCES students(id)');",'schema.sql')[0]
    assert [field['name'] for field in row['fields']]==['id','student_id','hint']
    assert not row['foreign_keys']


def test_only_actual_execution_records_supply_results_plans_and_keep_their_scope():
    base=chapter_materials.empty('current')
    rows=[{'id':'definition','kind':'test_definition','payload':{'observed':{'actual_execution':True,'exit_code':0}}},
          {'id':'planned','kind':'test_run','payload':{'observed':{'actual_execution':False,'exit_code':0}}},
          {'id':'actual','kind':'test_run','payload':{'observed':{'actual_execution':True,'exit_code':1,
             'command':['pytest','case.py'],'fixture':True,'snapshot_id':'older','scope':'单模块执行'}}}]
    actual=chapter_materials.with_execution_records(base,{'kind':'results'},rows)
    assert len(actual['execution_records'])==1
    record=actual['execution_records'][0]
    assert record['evidence_ids']==['actual'] and record['outcome']=='exit_code=1'
    assert '其他代码快照' in record['scope'] and '夹具' in record['scope']
    plan=actual['visual_plans'][0]
    assert plan['group']=='execution' and '通过率' in plan['spec']['note']
    assert not chapter_materials.available(actual,[])['execution_records']


@pytest.fixture
def structured_project(env):
    files={'backend/schema.sql':DDL,
           'backend/src/routes/credentials.ts':"router.post('/credentials', createCredential);\nrouter.get('/students', listStudents);\n",
           'frontend/tsconfig.json':'{"compilerOptions":{"paths":{"@/*":["./src/*"]}}}',
           'frontend/src/app/student/credentials/page.tsx':'import CredentialView from "@/features/CredentialView";\nexport default CredentialView;\n',
           'frontend/src/features/CredentialView.tsx':'''import { credentialApi } from './api';
import { submitCredentialApplication } from './submission';
export default function CredentialView() {
 const submit = () => credentialApi.apply();
 const confirm = () => submitCredentialApplication();
 return <form><h1>凭证申请</h1><input name="student_id"/><button>提交申请</button></form>;
}
''',
           'backend/tests/credentials.test.ts':"test('reject missing student', () => { expect(findStudent()).toThrow(); });\nit('keep pending status', () => { expect(status()).toBe('PENDING'); });\n"}
    for name,text in files.items():
        path=env['root']/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
    snapshot=repository.capture(env['pid'],env['binding']['id'])
    update_state(env['pid'],state(env['pid'])['revision'],target_snapshot_id=snapshot['id'])
    paths=[row['relative_path'] for row in db.rows('SELECT relative_path FROM repository_snapshot_files WHERE snapshot_id=?',(snapshot['id'],))]
    return {**env,'snapshot':snapshot,'paths':paths}


def collect(e,kind='system_design',goal=''):
    return chapter_materials.collect(e['pid'],e['snapshot']['id'],{'kind':kind,'goal':goal},e['paths'],max_bytes=40000,max_ranges=30)


def test_actual_schema_page_component_and_call_point_become_source_bound_plans(structured_project):
    e=structured_project;materials,items=collect(e)
    assert {row['name'] for row in materials['database_tables']}=={'students','credentials'}
    page=materials['frontend_pages'][0]
    assert page['route']=='/student/credentials'
    assert page['components'][0]['name']=='CredentialView'
    assert page['api_calls'][0]['symbol']=='credentialApi.apply'
    assert any(call['symbol']=='submitCredentialApplication' and call['call_kind']=='imported_function_call'
               for call in page['api_calls'])
    provided={row['id'] for row in items}
    assert all(set(plan['evidence_ids'])<=provided for plan in materials['visual_plans'])
    er=next(plan['spec'] for plan in materials['visual_plans'] if plan['spec']['kind']=='er')
    assert len(er['edges'])==1 and er['edges'][0]['label']=='student_id → id'
    assert er['edges'][0]['source_cardinality']=='0..1'  # Unique does not require a child row.
    assert materials['coverage']['complete_project_structure'] is False


def test_named_id_columns_do_not_create_foreign_keys_or_er_edges(structured_project):
    materials,_=collect(structured_project)
    credentials=next(row for row in materials['database_tables'] if row['name']=='credentials')
    credentials['foreign_keys']=[]
    plans=chapter_visual_plans.build({'kind':'system_design'},materials)
    assert not any(plan['spec']['kind']=='er' for plan in plans)


def test_omitted_sources_remove_materials_and_plans_instead_of_preserving_unsupported_data(structured_project):
    materials,_=collect(structured_project)
    empty=chapter_materials.available(materials,[])
    assert not empty['database_tables'] and not empty['frontend_pages'] and not empty['visual_plans']
    assert empty['coverage']['supplied_materials']==0


def test_missing_or_too_small_range_budget_does_not_claim_complete_tables(structured_project):
    e=structured_project
    materials,items=chapter_materials.collect(e['pid'],e['snapshot']['id'],{'kind':'system_design'},e['paths'],max_bytes=10,max_ranges=1)
    assert not materials['database_tables'] and not materials['visual_plans'] and materials['gaps']


def test_unknown_plan_missing_evidence_and_duplicate_uses_are_rejected(structured_project):
    materials,_=collect(structured_project);plan=materials['visual_plans'][0];context={'chapter_materials':materials}
    assert chapter_visual_plans.candidate_problem({'paragraphs':[{'text':'解释','visual_plan_ids':['invented']}]},context)
    assert chapter_visual_plans.candidate_problem({'paragraphs':[{'text':'解释','visual_plan_ids':[plan['id']]}]},context)
    row={'text':'解释','visual_plan_ids':[plan['id']],'evidence_ids':plan['evidence_ids']}
    assert not chapter_visual_plans.candidate_problem({'paragraphs':[row]},context)
    assert chapter_visual_plans.candidate_problem({'paragraphs':[row,deepcopy(row)]},context)
    assert chapter_visual_plans.candidate_problem({'paragraphs':[{**row,'preserve_node_id':'locked'}]},context)
    assert chapter_visual_plans.candidate_problem({'paragraphs':[row]}, {})


def test_results_plan_contains_real_test_names_but_no_fabricated_measurements(structured_project):
    materials,_=collect(structured_project,'results')
    assert len(materials['test_cases'])==2
    plan=next(plan for plan in materials['visual_plans'] if plan['group']=='testing')
    assert plan['spec']['rows'][0][0]=='reject missing student'
    assert all(row[2]=='未提供执行记录' for row in plan['spec']['rows'])


def test_narrow_contract_does_not_force_unrelated_database_or_frontend_visuals(structured_project):
    materials,_=collect(structured_project,goal='解释操作的合取授权条件')
    assert not materials['visual_plans']


def test_argument_card_assignment_keeps_table_explanation_in_the_data_task(structured_project):
    materials,_=collect(structured_project)
    cards=[{'question':'总体架构','point_ids':['a']},{'question':'数据模型与接口','point_ids':['b']},{'question':'前端模块与代码结构','point_ids':['c']}]
    assigned=chapter_visual_plans.attach_cards(cards,materials['visual_plans'])
    database={plan['id'] for plan in materials['visual_plans'] if plan['group']=='database'}
    assert database<=set(assigned[1]['visual_plan_ids'])
    assert not database & set(assigned[0]['visual_plan_ids'])


def test_units_only_offer_the_visual_plans_assigned_to_their_tasks(structured_project):
    materials,_=collect(structured_project)
    cards=[{'question':'数据模型','point_ids':['a']},{'question':'前端模块','point_ids':['b']}]
    cards=chapter_visual_plans.attach_cards(cards,materials['visual_plans'])
    context={'chapter_materials':materials,'writing_brief':{'points':[{'id':'a'},{'id':'b'}]},
             'argument_plan':{'paragraph_cards':cards}}
    first=writing_flow._unit_request_context({'id':'s','target_words':3000},context,cards,0,2,[])
    second=writing_flow._unit_request_context({'id':'s','target_words':3000},context,cards,1,2,[])
    a={plan['id'] for plan in first['chapter_materials']['visual_plans']}
    b={plan['id'] for plan in second['chapter_materials']['visual_plans']}
    assert a and b and a.isdisjoint(b) and a|b=={plan['id'] for plan in materials['visual_plans']}


def test_material_coverage_reports_missing_chart_explanations_but_respects_text_only(structured_project):
    materials,_=collect(structured_project)
    section={'kind':'system_design','title':'第四章 系统设计'}
    context={'operation':'generate','chapter_materials':materials,'instruction':''}
    candidate={'paragraphs':[{'text':'架构提升了系统质量。'}]}
    findings=chapter_visual_plans.findings(section,candidate,context)
    assert findings and all(row['kind']=='chapter_material_unused' for row in findings)
    assert any('字段' in row['repair_query'] for row in findings)
    assert chapter_visual_plans.findings(section,candidate,{**context,'instruction':'本次仅输出文字，不生成图表'})==[]
    assert chapter_visual_plans.findings(section,candidate,{**context,'operation':'translate'})==[]


def test_table_definition_conflicts_never_create_a_guessed_er(structured_project):
    materials,_=collect(structured_project)
    for table in materials['database_tables']:table['conflicting_definitions']=True
    plans=chapter_visual_plans.build({'kind':'system_design'},materials)
    assert not any(plan['spec']['kind']=='er' for plan in plans)


def test_existing_explanation_plan_compiles_in_agent_path(structured_project):
    from app.writing_agent import drafting,evidence
    e=structured_project;materials,items=collect(e)
    plan=next(plan for plan in materials['visual_plans'] if plan['spec']['kind']=='three_line_table')
    text='students表用id标识学生记录；name的非空声明要求保存时提供该字段。这里描述DDL定义，不证明数据库已经迁移。'
    row={'local_id':'p1','text':text,'visual_plan_ids':[plan['id']],
         'project_material_ids':plan['material_ids'],'evidence_ids':plan['evidence_ids']}
    task=tasks.enqueue(e['pid'],'write',{'section_id':e['sid']})
    context={'section':security.require_section(e['sid']),'chapter_materials':materials,
             'evidence':[evidence.for_model(item) for item in items]}
    doc,draft=drafting.document({'paragraphs':[row]},references=[],evidence_ids=[item['id'] for item in items],
                               pid=e['pid'],context=context,manifest={},task=task)
    assert len(doc['content'])==2 and doc['content'][0]['type']=='table'
    assert doc['content'][1]['attrs']['describesFigureId']==doc['content'][0]['attrs']['nodeId']
    assert draft['created_visual_ids']


@pytest.mark.parametrize('include_duplicate',[False,True])
def test_writer_creates_real_tables_diagrams_and_one_explanation_per_plan(structured_project,monkeypatch,include_duplicate):
    from app import figure_standards
    e=structured_project;sid=e['sid'];pid=e['pid'];goal='按已确认规则组织本节内容，材料不足时列出缺口。'
    db.execute('UPDATE sections SET title=?,goal=?,target_words=? WHERE id=?',('第四章 系统设计',goal,1500,sid))
    contracts.save_contract(pid,sid,{'goal':goal,'evidence_ids':[]},state(pid)['revision'])
    db.set_setting('provider',{**provider.config(),'context_limit':256000,'max_output_tokens':8192})
    seen=[]
    def transport(system,user,images=None):
        payload=db.loads(user);seen.append(payload)
        if payload.get('operation')=='understand_project':
            dossier={'questions':payload['question_map']}
            return reply(synthetic_understanding(dossier,payload['project_evidence']))
        if payload.get('operation')=='verify_claim_batch':
            reviews=[]
            for claim in payload['claims']:
                allowed=payload.get('verification_scope',{}).get('claim_sources',{}).get(claim['claim_id'],{}).get('allowed_source_ids')
                source=next((row for row in payload['actual_sources'] if not allowed or row['id'] in allowed),payload['actual_sources'][0])
                reviews.append({'claim_id':claim['claim_id'],'support':'supported',
                                'source_quotes':[{'source_id':source['id'],'quote':source['text']}]})
            return reply({'claim_reviews':reviews,'point_reviews':[{'point_id':point['id'],'status':'complete','paragraph_indices':[0],
                         'evidence_text':payload['candidate_paragraphs'][0]['text']} for point in payload['task_points']],'findings':[]})
        if payload.get('operation')=='review_candidate_discourse':
            return reply({'coverage_complete':True,'findings':[],'rule_reviews':[
                {'rule_id':rule['id'],'status':'satisfied','paragraph_indices':[0],
                 'quote':payload['candidate_paragraphs'][0]['text'],'reason':'保留静态定义与执行边界。'} for rule in payload['rule_applications']]})
        plans=payload['chapter_materials']['visual_plans'];assert plans
        paragraphs=[]
        for index,plan in enumerate(plans):
            terms='、'.join(plan['terms'][:3])
            text=f'本段围绕{terms}解释{plan["spec"]["title"]}中的声明关系，字段和模块均对应已读源码；这里只讨论静态定义，不将其当作部署或实测结果。'
            paragraphs.append({'text':text,'visual_plan_ids':[plan['id']],'project_material_ids':plan['material_ids'],
                'evidence_ids':plan['evidence_ids'],'brief_points':[point['id'] for point in payload['writing_brief']['points']],
                'claim_spans':[{'local_id':'p'+str(index),'text':text,'evidence_ids':plan['evidence_ids']}]})
        candidate={'paragraphs':paragraphs}
        if include_duplicate:
            candidate['visuals']=[{'after_paragraph':0,'spec':plans[0]['spec'],
                                  'evidence_ids':plans[0]['evidence_ids'],'explanation':paragraphs[0]['text']}]
        return reply(candidate)
    monkeypatch.setattr(provider,'generate',transport)
    task=tasks.enqueue(pid,'write',{'section_id':sid,'operation':'generate','base_revision_id':None})
    assert tasks.run_one()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],));assert saved['status']=='succeeded',saved['error']
    output=db.loads(saved['result']);doc=output['document']
    plans=next(row['chapter_materials']['visual_plans'] for row in seen if row.get('operation')=='generate')
    visuals=[node for node in doc['content'] if node['type'] in {'figure','table'}]
    assert len(visuals)==len(plans)
    assert len([node for node in doc['content'] if node['type']=='paragraph'])==len(plans)
    assert any(node['type']=='figure' for node in visuals) and any(node['type']=='table' for node in visuals)
    paragraphs={node['attrs']['nodeId']:node for node in doc['content'] if node['type']=='paragraph'}
    for node in visuals:
        explanation=paragraphs[node['attrs']['explanationNodeId']]
        assert explanation['attrs']['describesFigureId']==node['attrs']['nodeId']
        assert any(child['type']=='crossReference' and child['attrs']['targetId']==node['attrs']['nodeId'] for child in explanation['content'])
        meta=db.loads(db.one('SELECT metadata FROM artifacts WHERE id=?',(node['attrs']['artifactId'],))['metadata'])
        assert meta['visual_plan_id'] and meta['project_material_ids'] and meta['evidence_ids']
        assert doc['content'].index(node)<doc['content'].index(explanation)
    assert not output['writing_quality']['chapter_material_use']['unresolved']
    assert output['writing_quality']['chapter_material_use']['provided']['database_tables']==2
