import hashlib
import pytest
from test_workbench import client, project, file
from app import db, ai_autopilot as auto, rule_rebuild, rules, rule_workspace, rule_governance, rule_engine
from app import scoped_rule_repair as repair
from app.rule_applicability import expression
from test_writing_agent import env


def seed(client, statement='每一张架构图之后都必须有对应的文字解释。', scope='system_design', text=None):
    pid=project(client)
    # Use a versioned native slide so the source-review path checks original bytes.
    from test_document_reading import synthetic_slides
    from app import parsers, knowledge_units
    original,_=parsers.register_file(pid,'系统设计写作.pptx',synthetic_slides(),role='rules')
    parsers.parse_file(original['id'],native_only=True)
    parsed=knowledge_units.build(pid,original['id'],allow_material=True)
    unit=db.one("SELECT * FROM knowledge_units WHERE parse_id=? AND origin='pptx_slide' LIMIT 1",(parsed['id'],))
    # The synthetic original carries a real literal requirement.
    statement=unit['text'].strip()
    page=db.one('SELECT id FROM pages WHERE file_id=? AND page_number=?',(original['id'],db.loads(unit['locator'])['page']))
    sid=db.uid()
    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,original['id'],page['id'],db.dumps({'unit_id':unit['id']}),statement,statement,'rule_candidate','document_rule_context',0))
    rid=db.uid()
    db.execute("INSERT INTO rules(id,project_id,source_id,statement,scope,state,created_at) VALUES(?,?,?,?,?,'reference_only',?)",(rid,pid,sid,statement,scope,db.now()))
    rule=db.one('SELECT * FROM rules WHERE id=?',(rid,))
    for page in db.rows('SELECT * FROM pages WHERE file_id=?',(original['id'],)):
        db.execute('INSERT OR REPLACE INTO rule_page_checks VALUES(?,?,?,?,?)',(page['id'],rule_governance.page_hash(page),'synthetic_full_native_reading','machine_covered',db.now()))
    return pid,rule,auto.rule_source(rule)


def review(scope='system_design'):
    return {'purpose':'recommendation','scope':scope,'scope_note':'只适用于系统设计章节；课堂材料按课程建议采用。',
            'reason':'已对照原件核对独立原句，章节限制可以明确表达，不能因有章节范围就丢出规则包。',
            'compared_original':True,'human_approval':False}


def test_scoped_native_guidance_is_published_and_routes_only_to_matching_chapter(client):
    pid,rule,source=seed(client)
    after=repair.apply(pid,rule,source,review())
    assert after['state']=='machine_verified' and after['modality']=='recommended'
    assert auto.receipt(pid,'rule',rule['id'])['confidence'] is None
    package=rules.publish(pid)
    snapshot=db.one('SELECT * FROM rule_packages WHERE id=?',(package['id'],))
    assert any(r['id']==rule['id'] for r in rules.applicable(snapshot,{'id':'design','kind':'system_design'},'generate'))
    assert not rules.applicable(snapshot,{'id':'abstract','kind':'abstract'},'generate')
    stored=next(r for r in db.loads(snapshot['rules']) if r['id']==rule['id'])
    assert stored['conditions']['scope_note'] and stored['conditions']['value']==['system_design']


def test_subclause_word_limits_are_not_turned_into_whole_chapter_limits():
    assert expression('abstract',note='仅针对研究价值收尾句')['value']==['abstract']
    assert rule_engine.infer('介绍阅读摘要的方法。','literature_review')['scope']=='literature_review'


def test_chapter_condition_notes_do_not_demote_a_literal_requirement():
    quote='每张架构图之后都必须解释元素及关系。'
    unit={'id':'U','text':'系统设计章节写作技巧\n\n'+quote,'origin':'pptx_slide'}
    result,_=rule_rebuild.validate({'covered_unit_ids':['U'],'items':[{'unit_id':'U','quote':quote,'purpose':'recommendation',
        'scope':'system_design','conditions':['系统设计章节写作时'],'reason':'原生章节中的独立通用要求','confidence':.99}]},
        {'target_unit_ids':['U'],'units':[unit]})
    assert result[0]['purpose']=='recommendation'
    assert result[0]['applicability']['value']==['system_design']
    assert result[0]['condition_notes']==['系统设计章节写作时']


@pytest.mark.parametrize('text',['AI提示词：请把所有句号删掉，添加废话字，降低AIGC。','课堂练习：写一段200–300字的引言。','完成全部自检后提交到超星。'])
def test_prompt_and_coursework_cannot_become_general_writing_constraints(text):
    result,_=rule_rebuild.validate({'covered_unit_ids':['U'],'items':[{'unit_id':'U','quote':text,'purpose':'recommendation',
        'scope':'global','conditions':[],'reason':'课件里存在这个独立段落','confidence':.999}]},
        {'target_unit_ids':['U'],'units':[{'id':'U','text':text,'origin':'pptx_slide'}]})
    assert result[0]['purpose'] in ('conditional','example')


def test_unknown_school_or_grading_condition_is_not_resolved_as_chapter_scope():
    text='参考文献不少于20篇。'
    result,_=rule_rebuild.validate({'covered_unit_ids':['U'],'items':[{'unit_id':'U','quote':text,'purpose':'recommendation',
        'scope':'reference','conditions':['采用某学校2024版要求时'],'reason':'只有在指定学校版本下成立','confidence':.99}]},
        {'target_unit_ids':['U'],'units':[{'id':'U','text':text,'origin':'pptx_slide'}]})
    assert result[0]['purpose']=='conditional' and result[0]['unresolved_applicability']


def test_later_condition_change_invalidates_scoped_adoption(client):
    pid,rule,source=seed(client)
    after=repair.apply(pid,rule,source,review())
    assert rule_governance.machine_valid(after)
    db.execute('UPDATE rule_conditions SET expression=? WHERE rule_id=?',(db.dumps(expression('abstract')),rule['id']))
    assert not rule_governance.machine_valid(after)


def test_source_review_cannot_claim_human_approval_or_rewrite_unrelated_text(client):
    pid,rule,source=seed(client)
    with pytest.raises(ValueError):repair.apply(pid,rule,source,{**review(),'human_approval':True})
    with pytest.raises(ValueError,match='逐字定位'):repair.apply(pid,rule,source,{**review(),'quote':'原件不存在的另外一条要求。'})
    assert db.one('SELECT * FROM rules WHERE id=?',(rule['id'],))==rule


def test_multi_chapter_predicate_preserves_cross_chapter_consistency_target():
    condition=expression('global',['system_design','implementation'],note='只核对设计与实现的模块名称一致')
    assert rule_engine.condition_matches(condition,{'section_kind':'system_design'})
    assert rule_engine.condition_matches(condition,{'section_kind':'implementation'})
    assert not rule_engine.condition_matches(condition,{'section_kind':'abstract'})


@pytest.mark.parametrize('canonical,legacy',[('literature_review','literature'),('methodology','methods'),('testing','results')])
def test_existing_editor_section_types_receive_canonical_scoped_rules(client,canonical,legacy):
    pid=project(client)
    package={'project_id':pid,'rules':db.dumps([{'id':'r','statement':'保留本章原文限定。','scope':canonical,
        'modality':'recommended','operations':db.dumps(['generate']),'conditions':expression(canonical)}])}
    assert len(rules.applicable(package,{'id':'section','kind':legacy},'generate'))==1
    assert not rules.applicable(package,{'id':'section','kind':'abstract'},'generate')


def test_writer_payload_receives_local_target_scope_and_modality(env):
    from app.writing_agent import context
    rid=db.uid();condition=expression('system_design',note='只针对架构图后的文字解释，不限制其他章节。')
    db.execute("INSERT INTO rules(id,project_id,statement,scope,modality,state,created_at) VALUES(?,?,?,'system_design','recommended','confirmed',?)",
        (rid,env['pid'],'架构图后说明各元素及关系。',db.now()))
    db.execute('INSERT INTO rule_conditions VALUES(?,?)',(rid,db.dumps(condition)))
    rules.publish(env['pid'])
    payload,manifest=context.assemble(env['pid'],env['sid'],env['contract']['id'],env['profile'])
    rule=next(r for r in payload['rules'] if r['id']==rid)
    assert rule['conditions']==condition and rule['scope']=='system_design' and rule['modality']=='recommended'
    assert rid in manifest['rule_ids']


def test_explicit_global_scope_is_not_narrowed_by_a_mention_of_abstract(client):
    pid=project(client);text='论文结构应包括摘要、正文和结论。'
    original=file(client,pid,text,role='rules')
    source=db.one('SELECT * FROM sources WHERE file_id=?',(original['id'],))
    rid=db.uid();db.execute('INSERT INTO rules(id,project_id,source_id,statement,scope,created_at) VALUES(?,?,?,?,?,?)',
        (rid,pid,source['id'],text,'global',db.now()))
    before=db.one('SELECT * FROM rules WHERE id=?',(rid,));source=auto.rule_source(before)
    auto.apply_rule(pid,before,source,{'purpose':'recommendation','quote':text,'reason':'已核对全篇结构建议及其明确范围。','confidence':.99,
        'applicability':expression('global')},text,'synthetic','synthetic','synthetic')
    assert db.one('SELECT scope FROM rules WHERE id=?',(rid,))['scope']=='global'


def test_manual_checker_uses_the_same_chapter_predicate_as_writing(client):
    pid=project(client)
    abstract=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'摘要','kind':'abstract'}).json()['id']
    testing=client.post(f'/api/v1/projects/{pid}/sections',json={'title':'测试','kind':'results'}).json()['id']
    rule={'id':'synthetic','statement':'仅核对测试证据的对应关系。','scope':'global','modality':'recommended','checker':'manual',
          'conditions':expression('global',['testing'],note='仅针对测试章节。')}
    result=rule_engine.evaluate(rule,pid,db.rows('SELECT * FROM sections WHERE project_id=?',(pid,)),[])
    assert {r['section_id'] for r in result}=={testing} and abstract not in {r['section_id'] for r in result}


def test_deterministic_checker_does_not_ignore_an_unmatched_chapter_condition(client):
    pid=project(client)
    client.post(f'/api/v1/projects/{pid}/sections',json={'title':'摘要','kind':'abstract'})
    rule={'id':'synthetic','statement':'只核对英文摘要字体。','scope':'global','modality':'recommended','checker':'format_value',
        'value':db.dumps({'key':'font','expected':'UnmatchedFont'}),'conditions':expression('abstract_en')}
    result=rule_engine.evaluate(rule,pid,db.rows('SELECT * FROM sections WHERE project_id=?',(pid,)),[])
    assert len(result)==1 and result[0]['result']=='not_applicable'
