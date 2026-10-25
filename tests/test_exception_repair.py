import pytest
from test_workbench import client,project,file
from app import db,provider,tasks,summary_billing,material_summary as summary,source_payload,ai_autopilot as auto,exception_repair as repair


def test_multibyte_material_split_fits_actual_protocol_and_keeps_every_character():
    text=('研究结论必须保留限定条件😀。'*4000)
    pages=[{'page_number':3,'title':'中文页面','text':text,'notes':''}]
    cfg={**summary.DEFAULT,'context_limit':9000,'max_output_tokens':1024}
    chunks=summary.bounded_page_chunks(pages,cfg,'中文文献.pdf')
    assert len(chunks)>2
    assert ''.join(p['text'] for chunk in chunks for p in chunk)==text
    assert {p['page'] for chunk in chunks for p in chunk}=={3}
    for chunk in chunks:
        assert source_payload.token_upper_bound(summary.SYSTEM,db.dumps({'name':'中文文献.pdf','material_pages':chunk}))+cfg['max_output_tokens']<=cfg['context_limit']


def test_fitting_chunks_keep_old_cache_identity():
    pages=[{'page_number':1,'title':'原文','text':'原文资料。','notes':''}]
    assert summary.bounded_page_chunks(pages,summary.DEFAULT,'a.pdf')==summary.split_pages(pages,16000)


def fixture_rule(client):
    pid=project(client);f=file(client,pid,'摘要必须交代研究方法和主要结论。',role='rules');source=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    rid=db.uid();db.execute('INSERT INTO rules(id,project_id,source_id,statement,state,created_at) VALUES(?,?,?,?,?,?)',(rid,pid,source['id'],'研究方法和主要结论。','candidate',db.now()))
    rule=db.one('SELECT * FROM rules WHERE id=?',(rid,));return pid,rule,auto.rule_source(rule)


def test_repair_replaces_fragment_only_with_literal_source_and_audits_it(client):
    pid,rule,source=fixture_rule(client)
    units=[{'id':'S1','text':source['text'],'source_id':source['id']}]
    repair.apply(rule,source,units,{'action':'replace_from_evidence','evidence_id':'S1','purpose':'recommendation','confidence':.99,'reason':'原文完整句补齐了摘要的对象与要求'},'fixture','fixture','fixture')
    assert db.one('SELECT statement FROM rules WHERE id=?',(rule['id'],))['statement']==source['text']
    assert auto.receipt(pid,'rule',rule['id'])['completed']
    assert db.one("SELECT detail FROM audit WHERE action='repair_exception_rule'")


def test_foreign_source_or_invented_unit_cannot_repair_current_rule(client):
    pid,rule,source=fixture_rule(client);foreign=file(client,project(client),'另一项目原文。')
    other=db.one('SELECT * FROM sources WHERE file_id=?',(foreign['id'],))
    with pytest.raises(ValueError,match='不属于'):
        repair.apply(rule,source,[{'id':'S1','text':other['text'],'source_id':other['id']}],{'action':'keep','evidence_id':'S1'},'fixture','fixture','fixture')
    assert db.one('SELECT statement FROM rules WHERE id=?',(rule['id'],))['statement']==rule['statement']


def test_human_edits_and_low_confidence_do_not_become_completed(client):
    pid,rule,source=fixture_rule(client)
    db.audit(pid,'edit_rule',{'rule_id':rule['id'],'changes':{'statement':'人工编辑'}})
    with pytest.raises(ValueError,match='人工'):
        repair.apply(rule,source,[{'id':'S1','text':source['text'],'source_id':source['id']}],{'action':'replace_from_evidence','evidence_id':'S1','purpose':'recommendation','confidence':.99,'reason':'这是完整原文'},'fixture','fixture','fixture')
    assert not auto.receipt(pid,'rule',rule['id']).get('completed')


def test_old_status_confirmation_does_not_prevent_requested_literal_repair(client):
    pid,rule,source=fixture_rule(client)
    db.audit(pid,'edit_rule',{'id':rule['id'],'changes':{'state':'confirmed'}})
    repair.apply(rule,source,[{'id':'S1','text':source['text'],'source_id':source['id']}],{'action':'replace_from_evidence','evidence_id':'S1','purpose':'recommendation','confidence':.99,'reason':'根据当前修复要求补回原文完整主语和约束'},'fixture','fixture','fixture')
    assert auto.receipt(pid,'rule',rule['id'])['completed']


def test_repair_preserves_rejected_status_and_original_text_in_audit(client):
    pid,rule,source=fixture_rule(client)
    db.execute("UPDATE rules SET state='rejected' WHERE id=?",(rule['id'],));rule=db.one('SELECT * FROM rules WHERE id=?',(rule['id'],))
    repair.apply(rule,source,[{'id':'S1','text':source['text'],'source_id':source['id']}],{'action':'replace_from_evidence','evidence_id':'S1','purpose':'recommendation','confidence':.99,'reason':'修复原文截断但保留用户的排除选择'},'fixture','fixture','fixture')
    after=db.one('SELECT * FROM rules WHERE id=?',(rule['id'],))
    assert after['state']=='rejected' and after['statement']==source['text']


def material_review_fixture(client):
    pid=project(client);f=file(client,pid,'本文研究数字身份的隐私保护方法，报告原型测试的局限。')
    task=tasks.enqueue(pid,repair.KIND,{'selected':[{'kind':'material','id':f['id']}]})
    db.execute("UPDATE tasks SET status='incomplete' WHERE id=?",(task['id'],))
    _,pages,fp=summary.source(f['id'])
    data={'overview':'研究数字身份隐私保护及原型局限。','key_points':[{'text':'结果限于原型测试。','pages':[1]}],'topics':[{'title':'研究边界','points':[{'text':'结果限于原型测试。','pages':[1]}]}]}
    review={'reviewed_pages':{1:summary.digest(pages[0])},'cached_parts':{},'excluded_pages':{}}
    decision={'role':'research','page':1,'quote':pages[0]['text'],'reason':'正文讨论研究方法及原型测试，是研究文献','confidence':.99}
    return pid,f,task,fp,data,review,decision


def test_source_review_is_audited_reusable_and_never_fakes_a_paid_run(client):
    pid,f,task,fp,data,review,decision=material_review_fixture(client);before=summary_billing.budget(pid)
    repair.recover_material(task['id'],f['id'],fp,data,review,decision)
    state=summary.status(f['id'])
    assert not state['stale'] and state['summary']['model']=='Codex 原文复核'
    assert not db.rows('SELECT * FROM generation_runs')
    assert summary_billing.budget(pid)==before
    assert not state['summary']['source_review']['human_approval']
    assert db.one("SELECT id FROM audit WHERE action='repair_exception_material_source_review'")
    db.set_setting('material_summary_provider',{'model':'deepseek-other'})
    assert not summary.status(f['id'])['stale']
    assert summary.enqueue(f['id'])['cached']
    assert summary.generate(task,{'file_id':f['id']})['cached']
    result=repair.reconcile_result(task['id'],finish=True)
    assert result['complete'] and result['fixed_ids']==[f['id']]
    assert db.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']=='succeeded'
    db.execute('UPDATE pages SET text=? WHERE file_id=?',('原文已改变',f['id']))
    assert summary.status(f['id'])['stale']


@pytest.mark.parametrize('problem',['changed_source','unread_page','bad_citation','excluded_citation','bad_role'])
def test_unread_changed_or_unsupported_material_cannot_be_marked_repaired(client,problem):
    pid,f,task,fp,data,review,decision=material_review_fixture(client)
    if problem=='changed_source':db.execute('UPDATE pages SET text=? WHERE file_id=?',('修改后的原文',f['id']))
    elif problem=='unread_page':review['reviewed_pages']={}
    elif problem=='bad_citation':data['key_points'][0]['pages']=[99]
    elif problem=='excluded_citation':
        review['excluded_pages']={1:{'source_hash':review['reviewed_pages'][1],'reason':'参考文献'}};review['reviewed_pages']={}
    else:decision['quote']='原文不存在的依据'
    with pytest.raises(ValueError):repair.recover_material(task['id'],f['id'],fp,data,review,decision)
    assert not db.one('SELECT * FROM material_summaries WHERE file_id=?',(f['id'],))
    assert not auto.receipt(pid,'material',f['id']).get('completed')


def test_removed_material_does_not_count_as_repaired(client):
    pid,f,task,*_=material_review_fixture(client)
    db.execute('UPDATE files SET active=0 WHERE id=?',(f['id'],))
    result=repair.reconcile_result(task['id'],finish=True)
    assert not result['complete'] and result['remaining'][0]['id']==f['id']
    assert db.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']=='incomplete'


def test_cached_review_rejects_changed_paid_parts(client):
    pid,f,task,fp,data,review,decision=material_review_fixture(client)
    chunks=summary.bounded_page_chunks(summary.source(f['id'])[1],summary.config(),f['name'])
    key=summary.digest([summary.configuration_hash(summary.config()),chunks[0]])
    db.execute('INSERT INTO material_summary_parts VALUES(?,?,?,?)',(f['id'],key,db.dumps(data),db.now()))
    review['reviewed_pages']={};review['cached_parts']={key:'wrong-content-hash'}
    with pytest.raises(ValueError,match='分段摘要'):repair.recover_material(task['id'],f['id'],fp,data,review,decision)
    assert not auto.receipt(pid,'material',f['id']).get('completed')


def test_repaired_exact_duplicates_are_reversibly_merged_without_deleting_rows(client):
    pid,rule,source=fixture_rule(client)
    db.execute("UPDATE rules SET state='confirmed',modality='recommended',statement=? WHERE id=?",(source['text'],rule['id']))
    duplicate=db.uid()
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,state,modality,created_at) VALUES(?,?,?,?,?,?,?)',(duplicate,pid,source['id'],source['text'],'confirmed','recommended',db.now()))
    result=repair.refresh_rule_package({'id':'fixture','project_id':pid},{'selected':[{'kind':'rule','id':rule['id']}],'auto_publish':False})
    assert len(result['duplicate_merges'])==1
    assert db.one('SELECT COUNT(*) n FROM rules WHERE project_id=?',(pid,))['n']==2
    assert db.one("SELECT COUNT(*) n FROM rules WHERE project_id=? AND state='confirmed'",(pid,))['n']==1
    assert db.one("SELECT id FROM audit WHERE action='merge_duplicate_rules'")


def test_material_retry_exposes_current_failure_instead_of_old_context_error(client,monkeypatch):
    pid,f,task,fp,*_=material_review_fixture(client)
    auto.save_receipt(pid,'material',f['id'],{'fingerprint':auto.material_fingerprint(f['id']),'completed':False,'state':'blocked','reason':'旧的上下文溢出原因'})
    def failed(*args,**kwargs):raise provider.ProviderError('本次输出被截断，分段结果已保存')
    monkeypatch.setattr(summary,'generate',failed)
    repair.process(task,db.loads(task['payload']))
    error=auto.report(pid)['exceptions'][0]
    assert error['reason']=='本次输出被截断，分段结果已保存'
    assert error['state']=='blocked'
