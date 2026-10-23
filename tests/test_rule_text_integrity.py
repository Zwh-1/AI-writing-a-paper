"""Wrapped layout lines and quoted separators must not become incomplete rules."""
import pytest
from fastapi import HTTPException
from test_workbench import client,project,file
from test_rule_review_integrity import submit,choice,fake_response
from app import db,rules,rule_governance,rule_pipeline,rule_duplicates,tasks
from app import rule_text_integrity as integrity,rule_text_repair as repair
from app.rule_review_contract import validate
from app.rule_verification import guards

FRAGMENT='3 ． 关键词：从标题或正文中挑选 3 、 5 个最能表达主要内容的词作为关键词，同'
FULL=FRAGMENT+'\n时有中、英文对照，分别附于中、英文摘要后。'


def setup(client,text=FULL,statement=FRAGMENT,state='candidate'):
    pid=project(client);f=file(client,pid,text,role='rules');source=integrity.source_for_rule({'source_id':db.one('SELECT id FROM sources WHERE file_id=?',(f['id'],))['id']})
    rid=db.uid();db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,state,created_at) VALUES(?,?,?,?,?,?,?)',(rid,pid,source['id'],statement,'recommended',state,db.now()))
    return pid,f,source,rid


def test_keyword_continuation_is_preserved_without_merging_next_ocr_item():
    text=FULL+'\n4 ，目录：写出目录，标明页码。\n5 ，正文：论文正文。'
    values=integrity.units(text,True)
    assert len(values)==3 and values[0][2]==FULL
    result=integrity.assess(FRAGMENT,text,text,True)
    assert result['needs_attention'] and result['replacement']==FULL


def test_quoted_semicolon_is_not_a_sentence_boundary(client):
    text='17. 从正文中挑选3～5个关键词，各关键词使用分号“；”分隔。关键词用小四号楷体。'
    pid=project(client);file(client,pid,text,role='rules');result=rules.extract(pid)
    assert result['added']==1 and db.one('SELECT statement FROM rules')['statement']==text
    assert integrity.assess(text.split('”')[0],text)['replacement']==text


def test_local_extraction_keeps_full_wrapped_keyword_requirement(client):
    pid=project(client);file(client,pid,FULL+'\n4．目录：标明页码。',role='rules')
    rules.extract(pid)
    assert [r['statement'] for r in db.rows('SELECT statement FROM rules')]==[FULL]


def test_independent_lines_and_complete_atomic_requirements_are_not_expanded():
    text='作者身份明确\n有完整参考文献列表\n经过同行评审'
    assert len(integrity.units(text))==3
    assert not integrity.assess('有完整参考文献列表',text)['needs_attention']
    text='32. 字体用宋体，\n固定行距22pt。\n段首缩进2字符。'
    assert not integrity.assess('固定行距22pt。',text)['needs_attention']
    assert not integrity.assess('不得造假。','更不得造假。')['needs_attention']


def test_page_context_can_repair_a_source_that_ends_mid_sentence():
    info=integrity.assess(FRAGMENT,FRAGMENT,FULL)
    assert info['needs_attention'] and info['replacement']==FULL


def test_ambiguous_repeated_fragment_is_not_guessed():
    text=FRAGMENT+'\n时有英文对照。\n\n'+FRAGMENT+'\n时附于中文摘要后。'
    result=integrity.assess(FRAGMENT,FRAGMENT,text)
    assert result['needs_attention'] and result['replacement'] is None


def test_repair_preserves_human_and_merged_rows_and_is_idempotent(client):
    pid,f,source,rid=setup(client);ids={}
    for state in ('machine_verified','confirmed','rejected'):
        identity=db.uid();ids[state]=identity
        db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,state,created_at) VALUES(?,?,?,?,?,?,?)',(identity,pid,source['id'],FRAGMENT,'recommended',state,db.now()))
    kept={key:db.one('SELECT * FROM rules WHERE id=?',(ids[key],)) for key in ('confirmed','rejected')}
    result=repair.apply(pid)
    assert set(result['repaired_rule_ids'])=={rid,ids['machine_verified']}
    assert result['quarantined_rule_ids']==[ids['machine_verified']]
    assert db.one('SELECT statement,state FROM rules WHERE id=?',(rid,))=={'statement':FULL,'state':'candidate'}
    assert all(db.one('SELECT * FROM rules WHERE id=?',(value['id'],))==value for value in kept.values())
    count=db.one("SELECT COUNT(*) n FROM audit WHERE action='repair_rule_text_integrity'")['n'];repair.apply(pid)
    assert db.one("SELECT COUNT(*) n FROM audit WHERE action='repair_rule_text_integrity'")['n']==count
    assert not db.rows('SELECT * FROM generation_runs')


def test_manual_candidate_edit_is_preserved(client):
    pid,f,source,rid=setup(client);before=db.one('SELECT * FROM rules WHERE id=?',(rid,))
    db.audit(pid,'edit_rule',{'id':rid})
    assert repair.apply(pid)['repaired_rule_ids']==[]
    assert db.one('SELECT * FROM rules WHERE id=?',(rid,))==before


def test_repair_evidence_endpoint_contains_full_context_and_previous_fragment(client):
    pid,f,source,rid=setup(client);repair.apply(pid)
    response=client.get('/api/v1/rules/'+rid+'/evidence')
    assert response.status_code==200
    evidence=response.json();assert evidence['context']==FULL and evidence['before_statement']==FRAGMENT
    assert not evidence['integrity']['needs_attention']


def test_incomplete_old_package_is_blocked_but_corrected_approved_snapshot_is_allowed(client):
    pid,f,source,rid=setup(client,state='machine_verified');old_rule=db.one('SELECT * FROM rules WHERE id=?',(rid,))
    package={'project_id':pid,'rules':db.dumps([old_rule])};section={'kind':'abstract','id':'s'}
    repair.apply(pid)
    with pytest.raises(HTTPException) as error:rules.applicable(package,section,'generate')
    assert error.value.detail['code']=='RULE_TEXT_INCOMPLETE'
    db.execute("UPDATE rules SET state='confirmed' WHERE id=?",(rid,))
    package['rules']=db.dumps(db.rows('SELECT * FROM rules WHERE id=?',(rid,)))
    assert len(rules.applicable(package,section,'generate'))==1


def test_cached_machine_verdict_cannot_keep_a_truncated_rule_active(client):
    pid,f,source,rid=setup(client,state='machine_verified');rule=db.one('SELECT * FROM rules WHERE id=?',(rid,))
    rule_governance.record(pid,rule,rule,source,'machine_verified',['legacy'])
    rule_governance.review_project(pid)
    assert db.one('SELECT state FROM rules WHERE id=?',(rid,))['state']=='candidate'


def test_incomplete_candidates_are_not_merged_by_their_shared_prefix(client):
    pid,f,source,rid=setup(client);other=db.uid()
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,state,created_at) VALUES(?,?,?,?,?,?,?)',(other,pid,source['id'],FRAGMENT,'recommended','candidate',db.now()))
    assert rule_duplicates.detect(db.rows('SELECT * FROM rules'))==[]
    response=client.post(f'/api/v1/projects/{pid}/rules/duplicates/merge',json={'canonical_id':rid,'duplicate_ids':[other]})
    assert response.status_code==409


@pytest.mark.parametrize('relation,kind',[('equivalent','requirement'),('not_a_rule','knowledge')])
def test_semantic_review_requires_complete_candidate_context(relation,kind):
    source={'id':'s','text':FULL};rule={'id':'r','statement':FRAGMENT}
    value=choice('r',FRAGMENT,relation=relation,kind=kind,reason='这是一条示例说明')
    valid,issues,_=validate({'decisions':[value]},[(rule,source,'fp')],guards,rule_pipeline.exact_quote)
    assert not valid and 'r' in issues


def test_model_extraction_cannot_accept_a_literal_but_truncated_clause():
    source={'id':'s','text':FULL}
    value={'source_id':'s','quote':FRAGMENT,'statement':FRAGMENT,'modality':'pending'}
    with pytest.raises(rule_pipeline.RuleOutputError):rule_pipeline.validated({'text':db.dumps({'rules':[value]})},[source],{'s':source})


@pytest.mark.parametrize('repair_tag',[True,False])
def test_completed_derived_context_is_available_to_deepseek_review(client,monkeypatch,repair_tag):
    pid,f,source,rid=setup(client)
    db.execute('UPDATE sources SET text=?,normalized=? WHERE id=?',(FRAGMENT,FRAGMENT,source['id']))
    repair.apply(pid);rule=db.one('SELECT * FROM rules WHERE id=?',(rid,));assert rule['source_id']!=source['id']
    if not repair_tag:
        location=db.loads(db.one('SELECT locator FROM sources WHERE id=?',(rule['source_id'],))['locator']);location.pop('repair',None)
        db.execute('UPDATE sources SET locator=? WHERE id=?',(db.dumps(location),rule['source_id']))
    seen=[]
    def fake(task,system,user,manifest,**kw):
        seen.extend(x['source_id'] for x in db.loads(user)['sources'])
        return fake_response([choice(rid,FULL,kind='criterion')])
    monkeypatch.setattr(tasks,'model_call',fake);task=submit(client,pid,provider_profile='primary')
    assert task['status']=='succeeded' and rule['source_id'] in seen


def test_visual_correction_keeps_ocr_source_and_original_fragment_history(client):
    pid,f,source,rid=setup(client);original_source=db.one('SELECT * FROM sources WHERE id=?',(source['id'],))
    location=db.loads(original_source['locator'],{});location['asset_sha256']='verified-image'
    db.execute('UPDATE sources SET locator=? WHERE id=?',(db.dumps(location),source['id']))
    repair.apply(pid)
    proof={'rule_id':rid,'before_statement':FULL,'after_statement':FULL.replace('3 、 5','3～5'),'asset_sha256':'verified-image','reason':'原图显示范围连接符'}
    assert repair.apply_visual_corrections(pid,[proof])==[rid]
    assert db.one('SELECT text FROM sources WHERE id=?',(source['id'],))['text']==FULL
    rule=db.one('SELECT * FROM rules WHERE id=?',(rid,));assert rule['state']=='candidate' and '3～5' in rule['statement']
    evidence=client.get('/api/v1/rules/'+rid+'/evidence').json()
    assert evidence['visual_correction'] and evidence['before_statement']==FRAGMENT


def test_visual_correction_cannot_use_an_unrelated_image(client):
    pid,f,source,rid=setup(client)
    with pytest.raises(HTTPException):repair.apply_visual_corrections(pid,[{'rule_id':rid,'before_statement':FRAGMENT,'after_statement':FULL,'asset_sha256':'unrelated','reason':'not valid'}])
    assert db.one('SELECT statement FROM rules WHERE id=?',(rid,))['statement']==FRAGMENT
