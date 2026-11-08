"""Project-specific reading, composite rule scope and pre-draft understanding."""
from copy import deepcopy
import json

import pytest
from fastapi import HTTPException
from test_workbench import client, project, manual_package  # noqa: F401
from test_writing_agent import env  # noqa: F401
from app import db, rules, tasks, provider, security, writing_live, writing_flow
from app import project_grounding as grounding, project_understanding as understanding, writing_rule_use
from app.writing_agent import repository
from app.writing_agent.common import state,update_state


def add_rule(c,pid,scope,text,conditions=None):
    response=c.post(f'/api/v1/projects/{pid}/rules',json={'scope':scope,'statement':text,
        'conditions':conditions or {},'modality':'mandatory'})
    assert response.status_code==200,response.text
    return response.json()['id']


def test_composite_chapter_loads_both_parts_and_preserves_each_rules_scope(client):
    pid=project(client)
    technical=add_rule(client,pid,'methodology','只介绍当前系统实际使用的技术。',
                       {'field':'section_kind','op':'eq','value':'methodology','scope_note':'只约束相关技术介绍部分'})
    review=add_rule(client,pid,'literature_review','比较已读取的代表研究。')
    design=add_rule(client,pid,'system_design','解释模块设计。')
    package=rules.publish(pid);frozen=db.one('SELECT * FROM rule_packages WHERE id=?',(package['id'],))
    before=deepcopy(frozen)
    selected=rules.applicable(frozen,{'id':'mixed','title':'第二章 相关技术与研究现状','kind':'literature'},'generate')
    assert {r['id'] for r in selected}=={technical,review}
    spec=writing_rule_use.for_context(selected)
    assert next(r for r in spec if r['id']==technical)['applies_to']==[{'kind':'methodology','target':'本章“相关技术”部分'}]
    assert next(r for r in spec if r['id']==technical)['conditions']['scope_note']=='只约束相关技术介绍部分'
    only_review=rules.applicable(frozen,{'id':'pure','title':'文献综述','kind':'literature'},'generate')
    assert {r['id'] for r in only_review}=={review}
    assert db.one('SELECT * FROM rule_packages WHERE id=?',(package['id'],))==before


def test_title_parts_do_not_override_operation_or_language_conditions(client):
    pid=project(client)
    rid=add_rule(client,pid,'methodology','英文相关技术要求。',{'field':'language','op':'eq','value':'en'})
    pack=rules.publish(pid);package=db.one('SELECT * FROM rule_packages WHERE id=?',(pack['id'],))
    assert not rules.applicable(package,{'id':'s','title':'相关技术与研究现状','kind':'literature'},'generate')


def seed_project(env):
    files={'README.md':'# Campus Record\n学生提交记录，审核者审批后保存。\n',
           'backend/routes/records.ts':'import { approve } from "../services/records";\nexport function route(){ return approve(); }\n',
           'backend/services/records.ts':'export function approve(){ return "approved"; }\n',
           'backend/models/record.ts':'export const status = ["pending", "approved"];\n',
           'backend/security/guard.ts':'export function mayApprove(role: string){ return role === "reviewer"; }\n',
           'backend/tests/record.test.ts':'export function testApproval(){ return "test definition only"; }\n',
           'vendor/services/unrelated.ts':'export function misleading(){ return "not our workflow"; }\n',
           'package.json':'{"name":"campus-record","scripts":{"start":"node backend/main.js"}}'}
    for name,text in files.items():
        path=env['root']/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
    snapshot=repository.capture(env['pid'],env['binding']['id'])
    return snapshot


def test_source_classification_does_not_confuse_archives_tests_or_module_configs_with_business_code():
    assert grounding.categories('docs/archive/DATABASE-MIGRATION.md')==[]
    assert grounding.categories('backend/tests/auth-service.test.ts')==['tests']
    assert 'business' not in grounding.categories('circuits/scripts/modules.config.js')
    assert 'business' in grounding.categories('backend/src/domains/records/service.ts')
    assert grounding.DEPENDENCY.search('logs/verify-auth-hardening.mjs')
    assert grounding._rank('backend/src/security/verify.ts','')<grounding._rank('scripts/auth.ts','')


def test_project_reading_spans_business_data_and_permissions_without_claiming_full_read(env):
    snapshot=seed_project(env)
    dossier,items=grounding.collect(env['pid'],snapshot['id'],{'kind':'implementation'},'records')
    paths={r['path'] for r in dossier['readings']}
    assert {'README.md','backend/routes/records.ts','backend/services/records.ts',
            'backend/models/record.ts','backend/security/guard.ts'}<=paths
    assert all(not path.startswith('vendor/') for path in paths)
    assert not any(path.endswith('AGENTS.md') or path.endswith('.env') for path in paths)
    assert dossier['coverage']['complete_repository_read'] is False
    assert all(row['support']=='not_assessed' for row in dossier['questions'])
    assert any(item['kind']=='test_definition' for item in items)
    assert not any(item['kind'] in {'test_run','deployment'} for item in items)
    again,other=grounding.collect(env['pid'],snapshot['id'],{'kind':'implementation'},'records',items)
    assert {r['id'] for r in other}=={r['id'] for r in items}


def test_existing_code_does_not_suppress_current_chapter_project_reading(env):
    snapshot=seed_project(env)
    update_state(env['pid'],state(env['pid'])['revision'],target_snapshot_id=snapshot['id'])
    section=security.require_section(env['sid'])
    payload,manifest,items=writing_live.prepare(env['pid'],section,'records')
    assert payload['project_grounding']['snapshot_id']==snapshot['id']
    required=set(payload['project_grounding']['required_evidence_ids'])
    assert required and required.isdisjoint(payload['automatic_project_evidence_ids'])
    assert required<={row['id'] for row in items}
    assert manifest['project_grounding_hash']==payload['project_grounding']['hash']
    assert any(row['payload'].get('path')=='backend/services/records.ts' for row in items)


def test_project_reading_rechecks_egress_and_packed_navigation_never_claims_missing_evidence(env):
    dossier,items=grounding.collect(env['pid'],env['snapshot']['id'],{'kind':'body'},'allowed')
    hidden=grounding.available(dossier,[])
    assert hidden['readings']==[] and all(not q['evidence_ids'] for q in hidden['questions'])
    repository.set_egress(env['pid'],env['binding']['id'],'local_only')
    with pytest.raises(HTTPException):grounding.collect(env['pid'],env['snapshot']['id'],{'kind':'body'},'allowed')


def synthetic_understanding(dossier,evidence):
    ids=[row['id'] for row in evidence]
    return {'observations':[{'question_id':q['id'],'status':'partial' if q['evidence_ids'] else 'unknown',
             'summary':'只描述给定范围。','evidence_ids':q['evidence_ids'],'limitations':['没有执行记录。']}
             for q in dossier['questions']],
            'workflows':[{'name':'权限判断','steps':[{'description':'检查两项合取条件。','evidence_ids':ids[:1]}]}],
            'identity_gaps':[]}


@pytest.mark.parametrize('mutation',['unknown_evidence','missing_question','unbound_step'])
def test_understanding_rejects_unread_or_unaccounted_claims(env,mutation):
    dossier,items=grounding.collect(env['pid'],env['snapshot']['id'],{'kind':'body'},'allowed')
    response=synthetic_understanding(dossier,items)
    if mutation=='unknown_evidence':response['observations'][0]['evidence_ids']=['invented']
    elif mutation=='missing_question':response['observations'].pop()
    else:response['workflows'][0]['steps'][0]['evidence_ids']=[]
    with pytest.raises(ValueError):understanding.parse(db.dumps(response),dossier,items)


def test_rule_loading_is_not_treated_as_compliance_and_unlocated_pass_is_unknown():
    ruleset=[{'id':'r','statement':'解释适用条件。'}];paragraphs=[{'text':'原文。'}]
    reviews,findings,complete=writing_rule_use.parse_reviews({},ruleset,paragraphs)
    assert not complete
    reviews,findings,complete=writing_rule_use.parse_reviews({'rule_reviews':[
        {'rule_id':'r','status':'satisfied','paragraph_indices':[0],'quote':'并不存在的原句','reason':'已加载'}]},ruleset,paragraphs)
    assert not complete and reviews[0]['status']=='unknown'


def test_rule_violation_is_located_and_enters_revision_findings():
    reviews,findings,complete=writing_rule_use.parse_reviews({'rule_reviews':[
        {'rule_id':'r','status':'violation','paragraph_indices':[0],'quote':'原文。','reason':'没有解释条件。'}]},
        [{'id':'r'}],[{'text':'原文。'}])
    assert complete and findings[0]['kind']=='rule_application' and findings[0]['rule_id']=='r'


def test_mandatory_rule_violation_uses_the_paragraph_containing_its_actual_quote():
    _,findings,complete=writing_rule_use.parse_reviews({'rule_reviews':[
        {'rule_id':'r','status':'violation','paragraph_indices':[0,1],'quote':'真正的问题。','reason':'条件被删。'}]},
        [{'id':'r','modality':'mandatory'}],[{'text':'正确原文。'},{'text':'真正的问题。'}])
    assert complete and findings[0]['paragraph_index']==1 and findings[0]['severity']=='blocker'


def test_design_cannot_substitute_generic_prose_for_supplied_project_sources():
    context={'project_grounding':{'snapshot_id':'s'},'project_evidence':[{'id':'code','origin_type':'code'}]}
    candidate=[{'text':'分层架构有助于提高系统质量。'}]
    assert writing_flow._project_anchor_findings({'kind':'system_design'},candidate,context)[0]['kind']=='project_grounding_missing'
    linked=[{'text':'本项目采用明确的权限流程。','claim_spans':[{'evidence_ids':['code']}]}]
    assert not writing_flow._project_anchor_findings({'kind':'system_design'},linked,context)
    assert not writing_flow._project_anchor_findings({'kind':'literature'},candidate,context)


def test_rule_review_batches_cover_every_applicable_rule_without_truncation(env,monkeypatch):
    from test_writing_flow_safety import _running
    task=_running(env);seen=[]
    applications=[{'id':'r'+str(index),'modality':'mandatory'} for index in range(25)]
    paragraphs=[{'text':'本段仅解释给定项目的合取授权条件。'}]
    def transport(system,user,images=None):
        payload=db.loads(user);seen.append(payload)
        return reply({'coverage_complete':True,'findings':[],'rule_reviews':[
            {'rule_id':row['id'],'status':'satisfied','paragraph_indices':[0],
             'quote':paragraphs[0]['text'],'reason':'合成规则的适用条件保留。'} for row in payload['rule_applications']]})
    monkeypatch.setattr(provider,'generate',transport)
    review=writing_flow._discourse(task,{},paragraphs,{'rules':'合成条款正文','rule_applications':applications},{})
    assert [len(row['rule_applications']) for row in seen]==[12,12,1]
    assert [row['review_paragraph_organization'] for row in seen]==[True,False,False]
    assert review['coverage_complete'] and review['rule_coverage_complete']
    assert {row['rule_id'] for row in review['rule_reviews']}=={row['id'] for row in applications}


@pytest.mark.parametrize('directed_read',[False,True])
def test_real_writer_understands_code_before_draft_and_audits_rules(env,monkeypatch,directed_read):
    calls=[]
    def transport(system,user,images=None):
        payload=db.loads(user);calls.append(payload)
        if payload.get('operation')=='understand_project':
            dossier={'questions':[{'id':q['id'],'evidence_ids':q['evidence_ids']} for q in payload['question_map']]}
            result=synthetic_understanding(dossier,payload['project_evidence'])
            if directed_read and sum(p.get('operation')=='understand_project' for p in calls)==1:
                result['read_requests']=[{'question_id':'permissions','path':'gate.ts','line_start':1,'line_end':2}]
            return reply(result)
        if payload.get('operation')=='review_candidate_discourse':
            text=payload['candidate_paragraphs'][0]['text']
            return reply({'coverage_complete':True,'findings':[],'rule_reviews':[
                {'rule_id':r['id'],'status':'satisfied','paragraph_indices':[0],'quote':text,
                 'reason':'保留合取条件及静态代码边界。'} for r in payload['rule_applications']]})
        if payload.get('operation')=='verify_claim_batch':
            return reply({'claim_reviews':[{'claim_id':r['claim_id'],'support':'supported',
                'source_quotes':[{'source_id':payload['actual_sources'][0]['id'],'quote':payload['actual_sources'][0]['text']}]} for r in payload['claims']],
                'point_reviews':[{'point_id':p['id'],'status':'complete','paragraph_indices':[0],
                                 'evidence_text':payload['candidate_paragraphs'][0]['text']} for p in payload['task_points']],
                'findings':[]})
        assert payload.get('project_understanding') and payload['rule_applications']
        code=next(row for row in payload['project_evidence'] if row.get('origin_type')=='code')
        text='只有发行方且开关启用时操作才被允许，此处仅描述静态代码条件。'
        return reply({'paragraphs':[{'text':text,'evidence_ids':[code['id']],
            'brief_points':[p['id'] for p in payload['writing_brief']['points']],
            'claim_spans':[{'local_id':'c','text':text,'evidence_ids':[code['id']]}]}]})
    monkeypatch.setattr(provider,'generate',transport)
    task=tasks.enqueue(env['pid'],'write',{'section_id':env['sid'],'base_revision_id':None,'operation':'generate'})
    assert tasks.run_one()
    stored=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert stored['status']=='succeeded',stored['error']
    result=db.loads(stored['result'])
    assert calls[0]['operation']=='understand_project'
    assert result['writing_quality']['discourse']['rule_coverage_complete']
    assert result['manifest']['project_understanding_hash']
    assert sum(p.get('operation')=='understand_project' for p in calls)==(2 if directed_read else 1)
    assert len(result['writing_quality']['project_understanding']['run_ids'])==(2 if directed_read else 1)


def test_failed_project_understanding_stops_before_generic_drafting(env,monkeypatch):
    calls=[]
    def transport(system,user,images=None):
        payload=db.loads(user);calls.append(payload)
        assert payload['operation']=='understand_project'
        return reply({'paragraphs':[{'text':'通用模板不能代替项目理解。'}]})
    monkeypatch.setattr(provider,'generate',transport)
    task=tasks.enqueue(env['pid'],'write',{'section_id':env['sid'],'base_revision_id':None,'operation':'generate'})
    assert tasks.run_one()
    result=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert result['status']=='failed' and '项目理解' in result['error']
    assert len(calls)==writing_flow.MAX_FORMAT_ATTEMPTS


def test_diagnostic_frozen_binding_is_visible_in_reading_and_preflight(env):
    from app.paper_study import inventory,risks_for
    db.execute('UPDATE repository_bindings SET root_path_local=? WHERE id=?',
               ('D:/workspace/.runtime/diagnostics/frozen-source-example',env['binding']['id']))
    dossier,_=grounding.collect(env['pid'],env['snapshot']['id'],{'kind':'body'},'allowed')
    assert dossier['source_binding_kind']=='diagnostic_frozen_copy'
    inv=inventory(env['pid'],env['snapshot']['id'])
    assert inv['diagnostic_source_binding']
    project={'title':'实际课题','target_words':1000}
    risks=risks_for(project,{'question':'真实研究问题'}, {},[],inv)
    assert any(row['code']=='diagnostic_source_binding' for row in risks)


def reply(value):
    return {'text':db.dumps(value),'usage':{'input_tokens':1,'output_tokens':1},'cost':0,
            'remote_id':'synthetic','request_hash':'synthetic'}
