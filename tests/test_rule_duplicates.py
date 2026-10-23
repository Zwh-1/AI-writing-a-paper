import pytest
from test_workbench import client,project,file
from app import db,rules,rule_duplicates
from app.rule_workspace import entries


def add(c,p,**extra):
    r=c.post(f'/api/v1/projects/{p}/rules',json={'statement':'参考文献不少于20篇','checker':'reference_min','value':20,**extra})
    assert r.status_code==200,r.text
    return r.json()


def work(c,p):
    r=c.get(f'/api/v1/projects/{p}/rules');assert r.status_code==200,r.text
    return r.json()


def test_duplicate_scan_prefers_confirmed_without_changing_records(client):
    p=project(client);a=add(client,p,state='candidate',modality='recommended');b=add(client,p,state='confirmed');add(client,p,state='candidate')
    before=db.rows('SELECT * FROM rules ORDER BY id');w=work(client,p)
    assert len(w['duplicates'])==1 and w['duplicate_extra_count']==2
    assert w['duplicates'][0]['canonical_id']==b['id'] and w['duplicates'][0]['review_difference']
    assert db.rows('SELECT * FROM rules ORDER BY id')==before


def test_fullwidth_and_layout_variants_group_together(client):
    p=project(client);add(client,p,statement='参考文献： 不少于２０篇。');add(client,p,statement='参考文献:不少于20篇')
    group=work(client,p)['duplicates'][0];assert group['kind']=='normalized'


@pytest.mark.parametrize('other',['参考文献不少于10篇','参考文献不少于20篇，外文比例至少25%','参考文献不少于20篇，时间要求最近3-5年'])
def test_different_quantities_and_extra_requirements_are_not_duplicates(client,other):
    p=project(client);add(client,p);add(client,p,statement=other)
    assert work(client,p)['duplicates']==[]


@pytest.mark.parametrize('first,second',[('比例≥20%','比例≤20%'),('行距1.5倍','行距15倍'),('至少10-20篇','至少1020篇'),('必须包含A、B','必须包含AB'),('variable C required','variable c required')])
def test_normalization_preserves_semantically_significant_characters(first,second):
    assert rule_duplicates.normalize_statement(first)!=rule_duplicates.normalize_statement(second)


@pytest.mark.parametrize('extra',[{'scope':'abstract'},{'operations':['revise']},{'conditions':{'field':'paper_type','op':'eq','value':'review'}},{'value':10}])
def test_different_execution_contexts_do_not_group(client,extra):
    p=project(client);add(client,p);add(client,p,**extra)
    assert work(client,p)['duplicates']==[]


def test_equivalent_condition_key_and_operation_order_do_group(client):
    p=project(client)
    add(client,p,conditions={'field':'paper_type','op':'eq','value':'review'},operations=['generate','revise'])
    add(client,p,conditions={'value':'review','op':'eq','field':'paper_type'},operations=['revise','generate'])
    assert len(work(client,p)['duplicates'])==1


def test_conflict_view_collapses_same_rule_but_keeps_different_quantity(client):
    p=project(client)
    for _ in range(4):add(client,p,state='candidate')
    add(client,p,statement='参考文献不少于10篇',value=10,state='candidate')
    conflict=work(client,p)['conflicts'][0]
    assert len(conflict['rules'])==2
    assert sorted(r['duplicate_count'] for r in conflict['rules'])==[1,4]


def test_merge_keeps_confirmed_state_all_sources_and_can_restore(client):
    p=project(client);f=file(client,p,'参考文献不少于20篇。',role='rules');source=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    a=add(client,p,source_id=source['id'],decision_note='已核对出处');b=add(client,p,source_id=source['id'],state='candidate',decision_note='候选原始依据')
    before=db.one('SELECT * FROM rules WHERE id=?',(b['id'],))
    r=client.post(f'/api/v1/projects/{p}/rules/duplicates/merge',json={'canonical_id':a['id'],'duplicate_ids':[b['id']]});assert r.status_code==200,r.text
    merge_id=r.json()['merge_id'];w=work(client,p)
    assert not w['duplicates'] and len(w['rules'])==2
    canonical=next(x for x in w['rules'] if x['id']==a['id']);assert canonical['state']=='confirmed' and canonical['decision_note']=='已核对出处'
    assert canonical['merged_sources'][0]['source_id']==source['id']
    assert db.one('SELECT * FROM rules WHERE id=?',(b['id'],))['state']=='rejected'
    restored=client.post(f'/api/v1/projects/{p}/rules/duplicates/{merge_id}/restore',json={});assert restored.status_code==200,restored.text
    assert db.one('SELECT * FROM rules WHERE id=?',(b['id'],))==before
    assert work(client,p)['duplicate_merges'][0]['restored']
    assert client.post(f'/api/v1/projects/{p}/rules/duplicates/{merge_id}/restore',json={}).status_code==409


def test_merge_does_not_promote_unreviewed_rules(client):
    p=project(client);a=add(client,p,state='candidate');b=add(client,p,state='candidate')
    r=client.post(f'/api/v1/projects/{p}/rules/duplicates/merge',json={'canonical_id':a['id'],'duplicate_ids':[b['id']]});assert r.status_code==200
    assert db.one('SELECT state FROM rules WHERE id=?',(a['id'],))['state']=='candidate'


def test_merge_refuses_discarding_confirmed_in_favor_of_candidate(client):
    p=project(client);a=add(client,p,state='candidate');b=add(client,p)
    assert client.post(f'/api/v1/projects/{p}/rules/duplicates/merge',json={'canonical_id':a['id'],'duplicate_ids':[b['id']]}).status_code==422
    assert db.one('SELECT state FROM rules WHERE id=?',(b['id'],))['state']=='confirmed'


def test_merge_validation_and_cross_project_are_atomic(client):
    p=project(client);other=project(client);a=add(client,p);b=add(client,p,statement='参考文献不少于10篇',value=10);foreign=add(client,other)
    for rid in (b['id'],foreign['id']):
        r=client.post(f'/api/v1/projects/{p}/rules/duplicates/merge',json={'canonical_id':a['id'],'duplicate_ids':[rid]});assert r.status_code in (409,422)
    assert all(r['state']=='confirmed' for r in db.rows('SELECT * FROM rules'))


def test_restore_does_not_overwrite_later_edits(client):
    p=project(client);a=add(client,p);b=add(client,p,state='candidate')
    merge=client.post(f'/api/v1/projects/{p}/rules/duplicates/merge',json={'canonical_id':a['id'],'duplicate_ids':[b['id']]}).json()
    client.patch('/api/v1/rules/'+b['id'],json={'decision_note':'后续修改'})
    r=client.post(f'/api/v1/projects/{p}/rules/duplicates/{merge["merge_id"]}/restore',json={});assert r.status_code==409
    assert db.one('SELECT decision_note FROM rules WHERE id=?',(b['id'],))['decision_note']=='后续修改'


def test_confirmed_duplicates_checked_before_publish(client):
    p=project(client);a=add(client,p);b=add(client,p)
    r=client.post(f'/api/v1/projects/{p}/rule-packages');assert r.status_code==422
    assert r.json()['detail']['code']=='RULE_DUPLICATES'
    client.post(f'/api/v1/projects/{p}/rules/duplicates/merge',json={'canonical_id':a['id'],'duplicate_ids':[b['id']]})
    r=client.post(f'/api/v1/projects/{p}/rule-packages');assert r.status_code==200,r.text
    package=db.one('SELECT * FROM rule_packages WHERE id=?',(r.json()['id'],));assert len(db.loads(package['rules']))==1


def test_local_reextraction_skips_same_source_layout_variants(client):
    p=project(client);file(client,p,'参考文献：不少于20篇。',role='rules');rules.extract(p)
    before=len(db.rows('SELECT * FROM rules'));assert before==1
    db.execute("UPDATE rules SET statement='参考文献: 不少于20篇' WHERE project_id=?",(p,))
    assert rules.extract(p)['added']==0 and len(db.rows('SELECT * FROM rules'))==before


def test_repeated_merge_history_does_not_repeat_provenance(client):
    p=project(client);a=add(client,p);b=add(client,p,state='candidate')
    route=f'/api/v1/projects/{p}/rules/duplicates/merge'
    payload={'canonical_id':a['id'],'duplicate_ids':[b['id']]}
    assert client.post(route,json=payload).status_code==200
    assert client.patch('/api/v1/rules/'+b['id'],json={'state':'candidate'}).status_code==200
    assert client.post(route,json=payload).status_code==200
    canonical=next(r for r in work(client,p)['rules'] if r['id']==a['id'])
    assert len(canonical['merged_sources'])==1
