import pytest
from test_workbench import client,project,file
from app import db,duplicate_workflow as flow,rule_workspace,rule_duplicates,ai_autopilot as auto,security


def add(client,pid,text='参考文献不少于20篇。',**extra):
    response=client.post(f'/api/v1/projects/{pid}/rules',json={'statement':text,**extra})
    assert response.status_code==200,response.text
    return response.json()


def pair(client,sourced=False,state='confirmed'):
    pid=project(client);extra={}
    if sourced:
        f=file(client,pid,'参考文献不少于20篇。',role='rules');extra['source_id']=db.one('SELECT id FROM sources WHERE file_id=?',(f['id'],))['id']
    a=add(client,pid,state=state,**extra);b=add(client,pid,state=state,**extra)
    return pid,a,b


def review(client,pid,a,b):
    response=client.post(f'/api/v1/projects/{pid}/rules/duplicates/review',json={'canonical_id':a['id'],'duplicate_ids':[b['id']]})
    assert response.status_code==200,response.text
    return response.json()


def adopt(client,pid,proof):
    return client.post(f'/api/v1/projects/{pid}/rules/duplicates/adopt',json={'review_id':proof['id'],'expected_fingerprint':proof['fingerprint']})


def test_source_review_and_adoption_are_distinct_and_do_not_claim_human_approval(client):
    pid,a,b=pair(client,sourced=True);before=db.rows('SELECT * FROM rules ORDER BY id')
    proof=review(client,pid,a,b)
    assert proof['ready'] and proof['sources'] and all(c['passed'] for c in proof['checks'])
    assert proof['review_kind']=='local_structural_review' and proof['human_approval'] is False
    assert len(proof['sources'])==2 and proof['adoption_preview']['distinct_source_occurrences']==1
    assert proof['adoption_preview']['canonical_state_after']=='confirmed'
    assert db.rows('SELECT * FROM rules ORDER BY id')==before
    response=adopt(client,pid,proof);assert response.status_code==200,response.text
    assert db.one('SELECT state FROM rules WHERE id=?',(b['id'],))['state']=='rejected'
    assert db.one('SELECT * FROM rules WHERE id=?',(a['id'],))==a
    event=db.one("SELECT detail FROM audit WHERE action='merge_duplicate_rules'")
    assert db.loads(event['detail'])['review_id']==proof['id'] and db.loads(event['detail'])['source_manifest']


def test_unfinished_review_is_not_a_mergeable_duplicate(client):
    pid,a,b=pair(client,sourced=True,state='candidate')
    for r in (a,b):auto.save_receipt(pid,'rule',r['id'],{'purpose':'uncertain','state':'needs_full_source','completed':False})
    data=rule_workspace.workspace(pid);assert not data['duplicates'] and data['text_overlaps']
    proof=review(client,pid,a,b);assert not proof['ready']
    assert adopt(client,pid,proof).status_code==409
    assert all(r['state']=='candidate' for r in db.rows('SELECT * FROM rules'))


@pytest.mark.parametrize('change',['source_text','source_locator','source_inactive','file_role','file_bytes','page_excluded','conditions','rule_note','source_policy'])
def test_changed_source_review_or_adoption_context_invalidates_receipt(client,change):
    pid,a,b=pair(client,sourced=True);proof=review(client,pid,a,b);source=auto.rule_source(a)
    if change=='source_text':db.execute('UPDATE sources SET text=? WHERE id=?',('改写后的不同要求。',source['id']))
    elif change=='source_locator':db.execute('UPDATE sources SET locator=? WHERE id=?',(db.dumps({'page':2}),source['id']))
    elif change=='source_inactive':db.execute('UPDATE files SET active=0 WHERE id=?',(source['file_id'],))
    elif change=='file_role':db.execute("UPDATE files SET role='research' WHERE id=?",(source['file_id'],))
    elif change=='file_bytes':security.safe_object(source['file_sha256']).write_text('changed original',encoding='utf8')
    elif change=='page_excluded':db.execute("UPDATE pages SET review_state='not_applicable' WHERE id=?",(source['page_id'],))
    elif change=='conditions':db.execute('INSERT INTO rule_conditions VALUES(?,?)',(b['id'],db.dumps({'field':'paper_type','op':'eq','value':'review'})))
    elif change=='rule_note':db.execute('UPDATE rules SET decision_note=? WHERE id=?',('新的人工核对记录',b['id']))
    else:db.set_setting('rule_source_policy:'+pid,{source['file_id']:'binding'})
    assert adopt(client,pid,proof).status_code==409
    assert db.one('SELECT state FROM rules WHERE id=?',(b['id'],))['state']=='confirmed'


def test_review_reuse_and_adoption_are_idempotent_but_later_edits_block_replay(client):
    pid,a,b=pair(client);proof=review(client,pid,a,b)
    assert review(client,pid,a,b)['id']==proof['id']
    assert adopt(client,pid,proof).status_code==200
    again=adopt(client,pid,proof);assert again.status_code==200 and again.json()['already_applied']
    assert len(db.rows("SELECT id FROM audit WHERE action='merge_duplicate_rules'"))==1
    db.execute('UPDATE rules SET decision_note=? WHERE id=?',('后来编辑',a['id']))
    assert adopt(client,pid,proof).status_code==409


@pytest.mark.parametrize('state,modality',[('reference_only','pending'),('candidate','pending'),('reference_only','example')])
def test_merging_does_not_promote_adoption_state_or_enable_a_new_requirement(client,state,modality):
    pid,a,b=pair(client,state=state)
    for r in (a,b):db.execute('UPDATE rules SET modality=? WHERE id=?',(modality,r['id']))
    proof=review(client,pid,a,b);assert proof['ready']
    response=adopt(client,pid,proof);assert response.status_code==200,response.text
    row=db.one('SELECT * FROM rules WHERE id=?',(a['id'],))
    assert row['state']==state and row['modality']==modality
    assert response.json()['effective_requirement_added'] is False


def test_nested_merges_keep_grandchild_sources_in_the_final_record(client):
    pid,a,b=pair(client);c=add(client,pid)
    first=adopt(client,pid,review(client,pid,a,b));assert first.status_code==200
    second=review(client,pid,c,a)
    assert second['adoption_preview']['retained_rule_occurrences']==3
    assert {s['rule_id'] for s in second['sources']}=={a['id'],b['id'],c['id']}
    merged=adopt(client,pid,second);assert merged.status_code==200
    row=next(r for r in rule_workspace.workspace(pid)['rules'] if r['id']==c['id'])
    assert {r['id'] for r in row['merged_sources']}=={a['id'],b['id']}
    grandchild=next(r for r in row['merged_sources'] if r['id']==b['id']);assert grandchild['depth']==2
    restored=client.post(f'/api/v1/projects/{pid}/rules/duplicates/{merged.json()["merge_id"]}/restore',json={});assert restored.status_code==200
    rows={r['id']:r for r in rule_workspace.workspace(pid)['rules']}
    assert not rows[c['id']].get('merged_sources') and rows[a['id']]['merged_sources'][0]['id']==b['id']


def test_changed_descendant_source_invalidates_a_nested_adoption_review(client):
    pid,a,b=pair(client,sourced=True);c=add(client,pid,source_id=a['source_id'])
    assert adopt(client,pid,review(client,pid,a,b)).status_code==200
    proof=review(client,pid,c,a);assert proof['ready']
    db.execute('UPDATE rules SET source_id=NULL WHERE id=?',(b['id'],))
    assert adopt(client,pid,proof).status_code==409


def test_review_cannot_cross_projects_or_swap_the_chosen_canonical(client):
    pid,a,b=pair(client);other=project(client);foreign=add(client,other)
    assert client.post(f'/api/v1/projects/{pid}/rules/duplicates/review',json={'canonical_id':a['id'],'duplicate_ids':[foreign['id']]}).status_code==422
    proof=review(client,pid,a,b)
    response=client.post(f'/api/v1/projects/{pid}/rules/duplicates/merge',json={'canonical_id':b['id'],'duplicate_ids':[a['id']],
        'review_id':proof['id'],'expected_fingerprint':proof['fingerprint']})
    assert response.status_code==409


def test_restored_adoption_requires_a_new_review_and_preserves_audit_snapshot(client):
    pid,a,b=pair(client);proof=review(client,pid,a,b);merged=adopt(client,pid,proof).json()
    assert client.post(f'/api/v1/projects/{pid}/rules/duplicates/{merged["merge_id"]}/restore',json={}).status_code==200
    assert adopt(client,pid,proof).status_code==409
    historical=client.get(f'/api/v1/projects/{pid}/rules/duplicates/reviews/{proof["id"]}').json()
    assert historical['status']=='restored' and historical['snapshots']
    fresh=review(client,pid,a,b);assert fresh['id']!=proof['id'] and fresh['ready']
    assert review(client,pid,a,b)['id']==fresh['id']


def test_same_heading_in_different_source_occurrences_is_only_an_overlap(client):
    pid=project(client);f=file(client,pid,'内容',role='rules');first=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    second_page=db.uid();db.execute('INSERT INTO pages(id,file_id,page_number,text) VALUES(?,?,?,?)',(second_page,f['id'],2,'内容'))
    second_source=db.uid();db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(second_source,f['id'],second_page,'{}','内容','内容','rule_candidate','native',0))
    rows=[]
    for sid in (first['id'],second_source):
        r=add(client,pid,text='内容',source_id=sid,state='reference_only',modality='pending');source=auto.rule_source(r)
        auto.save_receipt(pid,'rule',r['id'],{'purpose':'heading','state':'completed','completed':True,'fingerprint':auto.rule_fingerprint(r,source)})
        rows.append(r)
    assert not rule_workspace.workspace(pid)['duplicates']
    assert not review(client,pid,*rows)['ready']


def test_reference_sources_with_different_roles_do_not_become_the_same_context(client):
    pid=project(client);rows=[]
    for role in ('research','rules'):
        f=file(client,pid,'相同的完整参考语句。',role=role);sid=db.one('SELECT id FROM sources WHERE file_id=?',(f['id'],))['id']
        r=add(client,pid,text='相同的完整参考语句。',source_id=sid,state='reference_only',modality='pending');source=auto.rule_source(r)
        auto.save_receipt(pid,'rule',r['id'],{'purpose':'knowledge','state':'completed','completed':True,'fingerprint':auto.rule_fingerprint(r,source)})
        rows.append(r)
    assert not review(client,pid,*rows)['ready']


def test_a_low_existing_confidence_cannot_be_accepted_as_a_reviewed_duplicate(client):
    pid,a,b=pair(client,sourced=True,state='reference_only')
    for r in (a,b):
        auto.save_receipt(pid,'rule',r['id'],{'purpose':'conditional','state':'completed','completed':True,'fingerprint':auto.rule_fingerprint(r,auto.rule_source(r)),
            'confidence':.5,'required_confidence':.8})
    assert not rule_workspace.workspace(pid)['duplicates']
    assert not review(client,pid,a,b)['ready']


def test_whole_source_layout_equivalence_preserves_source_text_in_review(client):
    pid,a,b=pair(client,sourced=True)
    db.execute('UPDATE rules SET statement=? WHERE id=?',('参考文献不少于２０篇',b['id']))
    proof=review(client,pid,a,b);assert proof['ready']
    assert next(s for s in proof['sources'] if s['rule_id']==b['id'])['literal_binding']=='complete_source_layout_equivalent'
    assert all(s['text']=='参考文献不少于20篇。' for s in proof['sources'])


def test_an_unavailable_grandchild_source_blocks_adoption_even_if_primary_sources_are_ready(client):
    pid,a,b=pair(client,sourced=True);c=add(client,pid,source_id=a['source_id'])
    assert adopt(client,pid,review(client,pid,a,b)).status_code==200
    # A historical member can acquire an unavailable origin; never lose it by
    # selecting just the currently visible canonical rows.
    prior=db.one('SELECT * FROM sources WHERE id=?',(a['source_id'],));missing=db.uid()
    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(missing,prior['file_id'],prior['page_id'],'{}','原文已变化。','原文已变化。','rule_candidate','native',0))
    db.execute('UPDATE rules SET source_id=? WHERE id=?',(missing,b['id']))
    proof=review(client,pid,c,a)
    assert any(s['rule_id']==b['id'] and s['origin_changed'] for s in proof['sources'])
    assert not proof['ready'] and adopt(client,pid,proof).status_code==409
