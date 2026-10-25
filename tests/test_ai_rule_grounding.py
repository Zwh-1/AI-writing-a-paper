import pytest
from test_ai_autopilot import client,seed,fake_model,run
from app import db,tasks,ai_autopilot as auto,ai_rule_grounding as grounding
from app.pdf_rule_contexts import grade_groups,structure


def cell(text,column,span=1,kind='TD'):
    return {'type':kind,'id':'h'+str(column) if kind=='TH' else '', 'text':text,'attributes':{'Headers':['h'+str(column)],'RowSpan':span}}


def test_tagged_spanning_grade_does_not_take_next_grade_criteria():
    table={'children':[{'type':'TR','children':[cell('等级',0,kind='TH'),cell('分数',1,kind='TH'),cell('标准',2,kind='TH'),cell('子指标',3,kind='TH')]},
        {'type':'TR','children':[cell('A级',0,2),cell('86-100分',1,2),cell('主标准A',2,2),cell('指标A1',3)]},
        {'type':'TR','children':[cell('跨页指标A2',3)]},
        {'type':'TR','children':[cell('B级',0),cell('70-85分',1),cell('主标准B',2),cell('指标B1',3)]}]}
    groups=grade_groups(table,lambda c:(c['text'],[],[]))
    assert len(groups)==2
    assert [c['text'] for c in groups[0]['columns'][3]]==['指标A1','跨页指标A2']
    assert [c['text'] for c in groups[1]['columns'][3]]==['指标B1']


def test_mcr_page_reference_is_not_flattened_into_parent_page():
    from pypdf.generic import DictionaryObject,NameObject,ArrayObject,NumberObject,IndirectObject
    class Page:
        def __init__(self,id):self.indirect_reference=IndirectObject(id,0,None)
    class Reader:pass
    r=Reader();r.pages=[Page(1),Page(2)]
    mcr=DictionaryObject({NameObject('/Type'):NameObject('/MCR'),NameObject('/Pg'):r.pages[1].indirect_reference,NameObject('/MCID'):NumberObject(17)})
    node=DictionaryObject({NameObject('/S'):NameObject('/TH'),NameObject('/Pg'):r.pages[0].indirect_reference,NameObject('/K'):ArrayObject([NumberObject(11),mcr])})
    r.trailer={'/Root':{'/StructTreeRoot':node}}
    children=structure(r)[0]['children']
    assert children[0]['refs']==[(1,11)] and children[1]['refs']==[(2,17)]


def proof(purpose='recommendation',ids=None):
    return {'purpose':purpose,'relation':'entailed','evidence_ids':ids or ['C0'],'reason':'候选完整表达原文独立要求，数量和适用对象一致','confidence':.99,**{key:True for key in grounding.CHECKS}}


def test_grounding_accepts_faithful_paraphrase_with_extra_source_numbers(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['摘要必须说明研究方法，字数不少于三百字。参考文献至少20篇。'])
    rule=db.one('SELECT * FROM rules WHERE id=?',(ids[0],));db.execute('UPDATE rules SET statement=? WHERE id=?',('摘要字数不少于300字。',rule['id']))
    rule=db.one('SELECT * FROM rules WHERE id=?',(rule['id'],));source=auto.rule_source(rule)
    item={'evidence_units':[{'id':'C0','text':source['text']}]}
    result=grounding.validate(rule,source,item,proof())
    auto.apply_rule(pid,rule,source,{**proof(),'quote':result['quote']},result['quote'],'synthetic','synthetic','synthetic',grounding=result)
    rec=auto.receipt(pid,'rule',rule['id'])
    assert rec['completed'] and rec['grounding']['checks']['numbers_preserved']


@pytest.mark.parametrize('statement',['摘要字数不少于500字。','摘要字数不少于300篇。'])
def test_grounding_rejects_unsupported_numbers_or_units(client,monkeypatch,statement):
    pid,f,ids=seed(client,monkeypatch,['摘要字数不少于300字。'])
    rule=db.one('SELECT * FROM rules WHERE id=?',(ids[0],));source=auto.rule_source(rule);rule={**rule,'statement':statement}
    with pytest.raises(ValueError,match='数值或单位'):grounding.validate(rule,source,{'evidence_units':[{'id':'C0','text':source['text']}]},proof())


def test_grounding_rejects_mixed_grades_even_when_model_says_supported(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['A级86-100分：术语准确。B级70-85分：偶有术语问题。'])
    rule=db.one('SELECT * FROM rules WHERE id=?',(ids[0],));source=auto.rule_source(rule);rule={**rule,'statement':'A级86-100分：术语准确。'}
    with pytest.raises(ValueError,match='不同评分等级'):grounding.validate(rule,source,{'evidence_units':[{'id':'C0','text':source['text']}]},proof('conditional'))


def test_grounding_rejects_missing_scope_attestation(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['摘要必须说明研究方法。'])
    rule=db.one('SELECT * FROM rules WHERE id=?',(ids[0],));source=auto.rule_source(rule)
    with pytest.raises(ValueError,match='范围'):grounding.validate(rule,source,{'evidence_units':[{'id':'C0','text':source['text']}]},{**proof(),'scope_preserved':False})


def test_arbitrary_truthy_proof_cannot_bypass_normal_rule_guards(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['摘要字数不少于300字。'])
    rule=db.one('SELECT * FROM rules WHERE id=?',(ids[0],));source=auto.rule_source(rule)
    with pytest.raises(ValueError):auto.apply_rule(pid,rule,source,{**proof(),'quote':'摘要字数不少于500字。'},'摘要字数不少于500字。','synthetic','synthetic','synthetic',grounding={'version':grounding.VERSION})


def test_grade_context_cannot_become_unconditional_requirement(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['A级标准：摘要应当交代研究方法。'])
    rule=db.one('SELECT * FROM rules WHERE id=?',(ids[0],));source=auto.rule_source(rule);rule={**rule,'statement':'摘要应当交代研究方法。'}
    decision=proof();result=grounding.validate(rule,source,{'evidence_units':[{'id':'C0','text':source['text']}]},decision)
    assert result['resolved_purpose']=='conditional' and result['purpose_policy']
    assert decision['purpose']=='recommendation'


def test_unicode_decimal_and_unit_normalization_does_not_guess_ocr_commas():
    assert grounding.numbers('页眉距离２．２厘米')==grounding.numbers('页眉距离2.2厘米')
    assert grounding.numbers('线宽2，25磅')!=grounding.numbers('线宽2.25磅')


def test_format_value_without_subject_cannot_be_a_global_requirement(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['摘要标题格式：段前、段后各0.5行。'])
    rule=db.one('SELECT * FROM rules WHERE id=?',(ids[0],));source=auto.rule_source(rule);rule={**rule,'statement':'段前、段后各0.5行。'}
    with pytest.raises(ValueError,match='适用对象'):grounding.validate(rule,source,{'evidence_units':[{'id':'C0','text':source['text']}]},proof())


def test_literal_repair_keeps_adoption_state_and_audits_before_after(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['摘要必须交代研究方法和主要结论。'])
    rid=ids[0];db.execute("UPDATE rules SET statement='研究方法和主要结论。',state='rejected' WHERE id=?",(rid,))
    rule=db.one('SELECT * FROM rules WHERE id=?',(rid,));source=auto.rule_source(rule)
    after=grounding.restore_literal_context(rule,source)
    assert after['statement']=='摘要必须交代研究方法和主要结论。' and after['state']=='rejected'
    event=db.one("SELECT * FROM audit WHERE action='autopilot_restore_literal_context'")
    assert db.loads(event['detail'])['before']==rule
    assert not auto.receipt(pid,'rule',rid).get('completed')


def test_literal_repair_respects_user_statement_edits(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['摘要必须交代研究方法和主要结论。'])
    rid=ids[0];db.execute("UPDATE rules SET statement='研究方法和主要结论。' WHERE id=?",(rid,))
    rule=db.one('SELECT * FROM rules WHERE id=?',(rid,));source=auto.rule_source(rule)
    db.audit(pid,'edit_rule',{'id':rid,'changes':{'statement':rule['statement']}})
    assert grounding.restore_literal_context(rule,source)==rule


def test_unchanged_known_invalid_context_is_not_paid_for_again(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['参考文献至少300篇。'])
    db.execute("UPDATE rules SET statement='参考文献至少500篇。' WHERE id=?",(ids[0],))
    calls=[];monkeypatch.setattr(tasks,'model_call',fake_model(calls))
    assert run(client,pid)['status']=='incomplete'
    count=len(calls)
    assert run(client,pid)['status']=='incomplete'
    assert len(calls)==count
