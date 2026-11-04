import io,json,zipfile
import pytest
from fastapi import HTTPException
from test_workbench import client,project,file,section,manual_package
from app import db,security,parsers,model_install,tasks,documents,provider
from app import material_policy as policy,document_reading as reader,knowledge_units as units,knowledge_service as service
from app.writing_agent import evidence,sentences,verification


@pytest.fixture(autouse=True)
def local_models(monkeypatch):
    old=model_install.status
    monkeypatch.setattr(model_install,'status',lambda name:{**old(name),'ready':False})


@pytest.fixture
def paper(client):
    pid=project(client);sid=section(client,pid)
    text='研究问题是凭证验证的适用范围。\n\n研究方法采用案例分析。\n\n适用条件仅为单用户且使用模拟数据。\n\n实验结果只能说明当前样例的正确性。\n\n研究局限是不涉及真实多用户并发。'
    original=file(client,pid,text);parsed=units.build(pid,original['id'])
    return client,pid,sid,original,parsed


def open_paper(paper):
    c,pid,sid,original,parsed=paper
    return reader.create(pid,{'file_id':original['id'],'section_id':sid,'question':'如何准确描述文献的验证范围？'})


def synthetic_slides():
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('ppt/presentation.xml','<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="s1"/></p:sldIdLst></p:presentation>')
        z.writestr('ppt/_rels/presentation.xml.rels','<Relationships><Relationship Id="s1" Target="slides/slide1.xml" Type="slide"/></Relationships>')
        text=['摘要教学','❌ 错误示例','本文将实现一个系统。','✅ 正确示例','本文实现了证书哈希上链与链上验证功能。','示例文字不证明本项目已经完成。']
        body=''.join('<p:sp><p:txBody><a:p><a:r><a:t>'+value+'</a:t></a:r></a:p></p:txBody></p:sp>' for value in text)
        z.writestr('ppt/slides/slide1.xml','<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>'+body+'</p:spTree></p:cSld></p:sld>')
    return data.getvalue()


def test_publication_can_be_research_and_example_without_reclassification(client):
    pid=project(client);original=file(client,pid,'SBTCert credential verification.',role='example')
    ref=client.post(f'/api/v1/projects/{pid}/references',json={'file_id':original['id'],'metadata':{'title':'SBTCert'}}).json()
    source_ids=[r['id'] for r in db.rows('SELECT id FROM sources WHERE file_id=?',(original['id'],))]
    changed=policy.set_usages(pid,original['id'],{'usages':['example','research'],'note':'这篇文献同时用于研究论证和写作示例'})
    assert changed['usages']==['example','research'] and changed['primary_role']=='example'
    assert db.one('SELECT file_id FROM bibliography WHERE id=?',(ref['id'],))['file_id']==original['id']
    assert [r['id'] for r in db.rows('SELECT id FROM sources WHERE file_id=?',(original['id'],))]==source_ids
    assert service.search(pid,'credential',include_units=False)['items']
    assert reader.catalog(pid,'research')['total']==1 and reader.catalog(pid,'guidance')['total']==1


def test_withdrawing_secondary_research_use_removes_hits(client):
    pid=project(client);original=file(client,pid,'credential literature',role='example')
    policy.set_usages(pid,original['id'],{'usages':['research','example'],'source_kind':'research_publication','note':'核对为研究文献'})
    units.build(pid,original['id'])
    assert service.search(pid,'credential')['items']
    policy.set_usages(pid,original['id'],{'usages':['example'],'note':'本次仅作写作范例'})
    assert not service.search(pid,'credential')['items']


def test_course_material_cannot_gain_research_authority_from_usage_flag(client):
    pid=project(client);original=file(client,pid,'正确示例：本文实现了系统。',role='rules')
    with pytest.raises(HTTPException):policy.set_usages(pid,original['id'],{'usages':['writing_guidance','research'],'note':'不能把课件当成果'})
    assert reader.catalog(pid,'research')['total']==0


def test_ppt_example_labels_and_example_stay_on_same_slide(client):
    pid=project(client);original,_=parsers.register_file(pid,'摘要课件.pptx',synthetic_slides(),role='rules');parsers.parse_file(original['id'])
    directory=reader.outline(pid,original['id'],'guidance',ensure=True)
    assert len(directory['units'])==1
    item=units.get(pid,directory['units'][0]['unit_id'],intent='guidance')
    assert all(text in item['text'] for text in ('错误示例','正确示例','实现了证书哈希上链'))
    assert item['locator']['slide_number']==1 and item['content_roles']['contains_teaching_examples']
    assert item['material_use']['source_purpose']=='course_guidance'
    assert not item['material_use']['can_prove_project_implementation']


def test_read_complete_does_not_claim_semantic_sufficiency(paper):
    c,pid,sid,original,parsed=paper
    result=units.read(pid,parsed['units'][0]['id'])
    assert result['requested_scope_complete'] and result['semantic_context_complete'] is None
    assert result['semantic_context_status']=='unassessed'
    assert 'not_semantic' in result['context_complete_meaning']


def test_document_reader_can_jump_beyond_neighbouring_units(paper):
    c,pid,sid,original,parsed=paper;opened=open_paper(paper)
    first=reader.read_range(pid,opened['id'],{'ordinal_start':1,'ordinal_end':1})
    last=reader.read_range(pid,opened['id'],{'ordinal_start':5,'ordinal_end':5})
    context=reader.context(pid,opened['id'])
    assert len(context['passages'])==2 and not context['parsed_text_coverage_complete']
    assert '真实多用户并发' in last['passages'][0]['text']
    assert context['semantic_context_status']=='unassessed'


def test_full_parsed_text_still_requires_question_specific_checks(paper):
    c,pid,sid,original,parsed=paper;opened=open_paper(paper)
    reader.read_range(pid,opened['id'],{'ordinal_start':1,'ordinal_end':5})
    context=reader.context(pid,opened['id'])
    assert context['parsed_text_coverage_complete'] and context['missing_topics']
    assert context['semantic_context_complete'] is None


def test_reader_never_truncates_over_budget_range(paper):
    c,pid,sid,original,parsed=paper;opened=open_paper(paper)
    result=reader.read_range(pid,opened['id'],{'ordinal_start':1,'ordinal_end':5,'max_chars':200})
    # A deliberately longer request is needed if this compact fixture fits 200 chars.
    if result['status']=='read':
        long=file(c,pid,'不能省略条件。'*200);opened=reader.create(pid,{'file_id':long['id'],'question':'完整条件是什么？'})
        result=reader.read_range(pid,opened['id'],{'ordinal_start':1,'ordinal_end':1,'max_chars':200})
    assert result['status']=='needs_narrower_range' and result['passages']==[]


def test_finding_cannot_quote_unread_or_invented_content(paper):
    c,pid,sid,original,parsed=paper;opened=open_paper(paper)
    read=reader.read_range(pid,opened['id'],{'ordinal_start':1,'ordinal_end':1})
    with pytest.raises(HTTPException):reader.add_finding(pid,opened['id'],{'topic':'conditions','status':'present','quote':'真实多用户','passage_ids':[read['passages'][0]['passage_id']],'note':'未读取的条件'},'candidate')
    with pytest.raises(HTTPException):reader.add_finding(pid,opened['id'],{'topic':'limitations','status':'not_applicable','note':'模型不能免除'},'candidate')


def test_coverage_assessment_is_candidate_until_explicit_human_review(paper):
    c,pid,sid,original,parsed=paper;opened=open_paper(paper)
    read=reader.read_range(pid,opened['id'],{'ordinal_start':1,'ordinal_end':5})
    for topic,passage in zip(reader.TOPICS['research'],read['passages']):
        reader.add_finding(pid,opened['id'],{'topic':topic,'quote':passage['text'].strip(),'passage_ids':[passage['passage_id']],'note':'仅记录原文位置，仍需判断是否支持论断'},'candidate')
    context=reader.context(pid,opened['id'])
    assert context['semantic_context_status']=='candidate_assessed' and context['state']!='reviewed'
    reviewed=reader.review(pid,opened['id'],{'expected_version':context['version'],'compared_original':True,'reviewer':'测试核对人','note':'逐项对照全文的问题、方法、条件、结果及局限'})
    assert reviewed['semantic_context_status']=='human_reviewed_for_question' and reviewed['claim_support']=='not_checked'


def test_scope_or_permission_change_invalidates_reading(paper):
    c,pid,sid,original,parsed=paper;opened=open_paper(paper)
    reader.read_range(pid,opened['id'],{'ordinal_start':1,'ordinal_end':1})
    observed=evidence.sync_reading(pid,opened['id'])
    assert evidence.allowed_remote(pid,observed['id'])
    c.patch('/api/v1/files/'+original['id'],json={'egress':'local_only'})
    assert not evidence.allowed_remote(pid,observed['id'])
    with pytest.raises(HTTPException):reader.context(pid,opened['id'],remote=True)


def test_reader_is_project_isolated(paper):
    c,pid,sid,original,parsed=paper;opened=open_paper(paper);other=project(c,'另一个项目')
    with pytest.raises(HTTPException):reader.context(other,opened['id'])


def test_ordinary_preparation_reads_whole_document_not_top_chunk(paper,monkeypatch):
    c,pid,sid,original,parsed=paper
    monkeypatch.setattr(service,'search',lambda *a,**k:{'items':[{'id':'unit:'+parsed['units'][3]['id'],'knowledge_unit_id':parsed['units'][3]['id'],'file_id':original['id'],'name':original['name']}]})
    context=service.prepare_writing(pid,'实验结果',sid)
    text=''.join(u['text'] for u in context['knowledge_units'])
    assert '研究方法' in text and '适用条件' in text and '研究局限' in text
    assert context['reading_policy']=='document_first_no_fragment_fallback'
    assert all(u['semantic_context_status']=='unassessed' for u in context['knowledge_units'])


def test_oversized_document_does_not_fall_back_to_ranked_fragments(paper):
    from app.document_preparation import prepare
    c,pid,sid,original,parsed=paper
    db.execute('INSERT INTO knowledge_selections VALUES(?,?)',(sid,parsed['units'][0]['id']))
    result=prepare(pid,'验证范围',sid,max_chars=20)
    assert not result['knowledge_units'] and not result['sources']
    assert any('未将检索片段冒充全文' in gap['reason'] for gap in result['gaps'])


def test_oversized_candidate_is_rejected_before_reading_each_unit(paper,monkeypatch):
    from app.document_preparation import prepare
    c,pid,sid,original,parsed=paper
    def unexpected_read(*args,**kwargs):
        raise AssertionError('An oversized document must be rejected from its directory before unit reads')
    monkeypatch.setattr(units,'get',unexpected_read)
    result=prepare(pid,'验证范围',sid,max_chars=20,
                   findings={'items':[{'file_id':original['id'],'name':original['name']}]})
    assert not result['knowledge_units'] and not result['sources']
    assert any('未将检索片段冒充全文' in gap['reason'] for gap in result['gaps'])


def test_document_preparation_hashes_each_file_once_and_refreshes_next_call(paper,monkeypatch):
    from app.document_preparation import prepare
    c,pid,sid,original,parsed=paper
    scans=[];original_rows=db.rows
    def measured_rows(sql,args=()):
        if sql.startswith('SELECT id,text,method,locator FROM sources WHERE file_id='):
            scans.append(args[0])
        return original_rows(sql,args)
    monkeypatch.setattr(db,'rows',measured_rows)
    findings={'items':[{'file_id':original['id'],'name':original['name']}]}
    first=prepare(pid,'验证范围',sid,findings=findings)
    assert len(first['knowledge_units'])==len(parsed['units'])>1
    assert scans==[original['id']]
    db.execute('UPDATE sources SET text=text||? WHERE file_id=?',('原文已更新',original['id']))
    scans.clear()
    second=prepare(pid,'验证范围',sid,findings=findings)
    assert not second['knowledge_units']
    assert scans==[original['id']]


def test_native_rule_source_survives_unrelated_ocr_exclusion_without_bypassing_revocation(client):
    from app import resource_refs
    pid=project(client)
    original,_=parsers.register_file(pid,'原生规则.pptx',synthetic_slides(),role='rules')
    parsers.parse_file(original['id'])
    parsed=units.build(pid,original['id'],allow_material=True)
    native=units.get(pid,parsed['units'][0]['id'],intent='guidance')
    page=db.one('SELECT id FROM pages WHERE file_id=? AND page_number=1',(original['id'],))
    sid=db.uid();locator={'unit_id':native['id'],'parse_id':native['parse_id'],
                         'unit_hash':native['content_hash'],'page':1,'read_scope':'complete_original_unit'}
    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',
        (sid,original['id'],page['id'],db.dumps(locator),native['text'],native['text'],'rule_context','document_rule_context',1))
    def add_unit(parse_id,origin,text,review_state,ordinal):
        db.execute('INSERT INTO knowledge_units(id,parse_id,ordinal,kind,text,content_hash,locator,heading_path,origin,review_state,issues) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (db.uid(),parse_id,ordinal,'image_observation',text,units.digest(text),db.dumps({'page':1}),'[]',origin,review_state,'[]'))
    add_unit(native['parse_id'],'windows_ocr','已排除的独立OCR', 'excluded',20)
    assert not service.source_is_excluded(pid,sid)
    ref=resource_refs.make(pid,'source',sid)
    resource_refs.validate(pid,[ref],remote=True)
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(original['id'],))
    with pytest.raises(HTTPException):resource_refs.validate(pid,[ref],remote=True)
    db.execute("UPDATE files SET egress='selected_content' WHERE id=?",(original['id'],))
    db.execute("UPDATE pages SET review_state='not_applicable' WHERE id=?",(page['id'],))
    assert service.source_is_excluded(pid,sid)
    db.execute("UPDATE pages SET review_state='reviewed' WHERE id=?",(page['id'],))
    db.set_setting('document_observation_supersessions',{sid:{'replacement_source_id':'other'}})
    assert service.source_is_excluded(pid,sid)
    db.set_setting('document_observation_supersessions',{})
    # Excluding the selected unit cannot be bypassed by an identical duplicate.
    db.execute("UPDATE knowledge_units SET review_state='excluded' WHERE id=?",(native['id'],))
    add_unit(native['parse_id'],'pptx_slide',native['text'],'pending',21)
    assert service.source_is_excluded(pid,sid)
    # Rebuilding the same slide preserves an exact immutable source mapping.
    fresh=units.build(pid,original['id'],strategy='page',allow_material=True)
    assert fresh['id']!=native['parse_id']
    # A clean new parse cannot restore a specifically excluded old source.
    assert service.source_is_excluded(pid,sid)
    db.execute("UPDATE knowledge_units SET review_state='pending' WHERE id=?",(native['id'],))
    add_unit(fresh['id'],'windows_ocr','本轮排除的独立OCR','excluded',20)
    assert not service.source_is_excluded(pid,sid)
    current_id=next(r['id'] for r in fresh['units'] if r['origin']=='pptx_slide')
    db.execute("UPDATE knowledge_units SET review_state='excluded' WHERE id=?",(current_id,))
    assert service.source_is_excluded(pid,sid)


def test_guidance_observation_cannot_support_project_implementation(client):
    pid=project(client);sid=section(client,pid);original=file(client,pid,'✅ 正确示例\n本文实现了证书上链功能。',role='rules')
    opened=reader.create(pid,{'file_id':original['id'],'question':'摘要应如何表述？','intent':'guidance','section_id':sid})
    reader.read_range(pid,opened['id'],{'ordinal_start':1,'ordinal_end':1})
    observed=evidence.sync_reading(pid,opened['id']);assert observed['kind']=='guidance_observation'
    document=documents.text_document('本系统实现了证书上链功能。');revision=documents.save(security.require_section(sid),document,None)
    analysis=sentences.analyze(pid,db.loads(revision['document']),revision_id=revision['id']);span=sentences.get(pid,analysis['id'])['sentences'][0]
    _,result=verification.apply_annotation(pid,analysis['id'],{'sentences':[{'sentence_id':span['id'],'role':'fact','claims':[{'statement':'本系统实现了证书上链功能','evidence_ids':[observed['id']],'support':'supported'}]}],'coverage_complete':True},{'evidence_versions':{observed['id']:observed['origin_version']}})
    assert any(f['kind']=='wrong_evidence_purpose' for f in result['findings'])
    assert result['sentences'][0]['claims'][0]['support']=='unknown'


def test_outline_get_is_read_only_before_structure(client):
    pid=project(client);original=file(client,pid,'方法与限制。')
    response=client.get(f'/api/v1/projects/{pid}/document-reading/documents/{original["id"]}/outline')
    assert response.status_code==200 and response.json()['needs_structure']
    assert not db.rows('SELECT * FROM knowledge_parses')


def test_explicit_stale_reading_does_not_silently_fall_back(paper,monkeypatch):
    c,pid,sid,original,parsed=paper;opened=open_paper(paper)
    reader.read_range(pid,opened['id'],{'ordinal_start':1,'ordinal_end':1})
    db.execute('UPDATE sections SET goal=? WHERE id=?',('改变了本次阅读问题适用的任务',sid))
    monkeypatch.setattr(service,'search',lambda *a,**k:{'items':[{'file_id':original['id'],'name':original['name']}]})
    result=service.prepare_writing(pid,'验证范围',sid)
    assert not result['knowledge_units'] and not result['sources']
    assert any('未用检索片段替代' in g['reason'] for g in result['gaps'])


def test_partial_ranges_union_reports_text_coverage_only(client):
    pid=project(client);original=file(client,pid,'只适用于单用户。'*100)
    opened=reader.create(pid,{'file_id':original['id'],'question':'完整适用条件是什么？'})
    item=units.get(pid,reader.outline(pid,original['id'])['units'][0]['unit_id']);middle=len(item['text'])//2
    for a,b in ((0,middle),(middle,len(item['text']))):
        reader.read_range(pid,opened['id'],{'ordinal_start':1,'char_start':a,'char_end':b})
    current=reader.context(pid,opened['id'])
    assert current['parsed_text_coverage_complete'] and current['read_unit_count']==1
    assert current['semantic_context_status']=='unassessed' and current['claim_support']=='not_checked'


def test_open_boundary_dispute_invalidates_document_reading(paper):
    c,pid,sid,original,parsed=paper;opened=open_paper(paper)
    reader.read_range(pid,opened['id'],{'ordinal_start':1,'ordinal_end':1})
    units.dispute(pid,parsed['units'][0]['id'],{'kind':'interpretation','reviewer':'测试核对人','note':'研究问题与示例含义存在分歧'})
    assert not reader.context(pid,opened['id'])['current']
    with pytest.raises(HTTPException):reader.context(pid,opened['id'],remote=True)


def test_citation_freezes_reading_and_reports_later_changes(paper):
    from app import export_checks,knowledge_evaluation
    c,pid,sid,original,parsed=paper;opened=open_paper(paper)
    reader.read_range(pid,opened['id'],{'ordinal_start':1,'ordinal_end':2})
    adopted=reader.context(pid,opened['id'])
    ref=c.post(f'/api/v1/projects/{pid}/references',json={'file_id':original['id'],'metadata':{'title':'范围研究'}}).json()
    doc=documents.text_document('本文献采用案例分析。');doc['content'][0]['attrs']={'nodeId':'reader-paragraph','documentReadingIds':[opened['id']]}
    doc['content'][0]['content'].append({'type':'citation','attrs':{'citationId':ref['id'],'nodeId':'reader-citation'}})
    rev=documents.save(security.require_section(sid),doc,None)
    link=db.one('SELECT * FROM citation_reading_links')
    frozen=export_checks.freeze(pid)
    assert link['reading_version']==adopted['version'] and db.loads(link['read_scope'])['passages']==adopted['passages']
    assert frozen['citation_reading_links'][0]['reading_version']==adopted['version']
    reader.read_range(pid,opened['id'],{'ordinal_start':5,'ordinal_end':5})
    citation=knowledge_evaluation.citation_map(pid)[0]
    assert citation['effective_support']=='stale' and not citation['document_readings'][0]['current']
    assert frozen['document_readings'][opened['id']]['passages']!=reader.context(pid,opened['id'])['passages']
    documents.save(security.require_section(sid),doc,rev['id'])
    current_link=db.rows('SELECT * FROM citation_reading_links')[-1]
    assert current_link['reading_version']==adopted['version']


def test_support_check_uses_frozen_reading_not_new_neighbours(paper,monkeypatch):
    c,pid,sid,original,parsed=paper;opened=open_paper(paper)
    reader.read_range(pid,opened['id'],{'ordinal_start':2,'ordinal_end':2})
    ref=c.post(f'/api/v1/projects/{pid}/references',json={'file_id':original['id'],'metadata':{'title':'案例研究'}}).json()
    doc=documents.text_document('本文献采用案例分析。');doc['content'][0]['attrs']={'documentReadingIds':[opened['id']]}
    doc['content'][0]['content'].append({'type':'citation','attrs':{'citationId':ref['id'],'nodeId':'case-citation'}})
    documents.save(security.require_section(sid),doc,None)
    seen={}
    def fake(task,system,user,manifest,images=None):
        seen.update(db.loads(user));seen['manifest']=manifest
        return {'text':db.dumps({'support':'supported','explanation':'候选检查'})}
    monkeypatch.setattr(tasks,'model_call',fake)
    citation=db.one('SELECT * FROM citations');result=tasks.support_check({'project_id':pid},{'citation_id':citation['id']})
    assert len(seen['evidence'])==1 and len(seen['evidence'][0]['passages'])==1
    assert '研究方法' in seen['evidence'][0]['passages'][0]['text']
    assert result['support']=='unknown' and result['unresolved_context']
    assert opened['id'] in seen['manifest']['document_reading_versions']
    reader.read_range(pid,opened['id'],{'ordinal_start':5,'ordinal_end':5})
    with pytest.raises(ValueError,match='阅读范围'):tasks.support_check({'project_id':pid},{'citation_id':citation['id']})


def test_guidance_preparation_preserves_entire_teaching_scope(client):
    from app.guidance_preparation import prepare
    pid=project(client);sid=section(client,pid)
    original=file(client,pid,'摘要教学\n\n错误示例：系统计划实现功能。\n\n正确示例：实现了证书哈希上链。\n\n示例不构成本项目成果。',role='example')
    result=prepare(pid,sid,'证书哈希上链')
    assert len(result['examples'])==1
    texts='\n'.join(p['text'] for p in result['examples'][0]['passages'])
    assert all(t in texts for t in ('摘要教学','错误示例','正确示例','不构成本项目成果'))
    assert not prepare(pid,sid,'证书哈希上链',max_chars=10)['examples']


def test_oversized_guidance_defers_whole_document_before_reading_every_unit(client, monkeypatch):
    from app.guidance_preparation import prepare
    pid=project(client);sid=section(client,pid)
    original=file(client,pid,'摘要教学\n\n错误示例：系统计划实现功能。\n\n正确示例：实现了证书哈希上链。\n\n示例不构成本项目成果。',role='example')
    reader.outline(pid,original['id'],'guidance',ensure=True)
    calls=[];get=units.get
    def observe(*args,**kwargs):
        calls.append(args)
        return get(*args,**kwargs)
    monkeypatch.setattr(units,'get',observe)
    result=prepare(pid,sid,'证书哈希上链',max_chars=10)
    assert not result['examples'] and not calls
    assert any(g.get('file_id')==original['id'] and '完整' in g['reason'] for g in result['gaps'])
    assert prepare(pid,sid,'证书哈希上链')['examples'] and calls
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(original['id'],))
    assert not prepare(pid,sid,'证书哈希上链')['examples']


def test_human_reading_citation_review_checks_exact_adopted_passage(paper):
    from app import knowledge_evaluation,export_checks
    c,pid,sid,original,parsed=paper;opened=open_paper(paper)
    read=reader.read_range(pid,opened['id'],{'ordinal_start':2,'ordinal_end':2});current=reader.context(pid,opened['id'])
    ref=c.post(f'/api/v1/projects/{pid}/references',json={'file_id':original['id'],'metadata':{'title':'方法研究'}}).json()
    doc=documents.text_document('该文献采用案例分析。');doc['content'][0]['attrs']={'documentReadingIds':[opened['id']]}
    doc['content'][0]['content'].append({'type':'citation','attrs':{'citationId':ref['id'],'nodeId':'human-case-citation'}})
    revision=documents.save(security.require_section(sid),doc,None);citation=knowledge_evaluation.citation_map(pid)[0]
    body={'expected_revision_id':revision['id'],'claim':citation['claim'],'support':'supported','reviewer':'合成测试核对人','note':'仅核对此处采用案例分析的具体论断','reading_checks':[{'reading_id':opened['id'],'expected_version':current['version'],'quote':'凭空编造引句'}]}
    with pytest.raises(HTTPException):knowledge_evaluation.review_citation(pid,citation['id'],body)
    body['reading_checks'][0]['quote']=read['passages'][0]['text']
    result=knowledge_evaluation.review_citation(pid,citation['id'],body)
    assert result['support']=='supported'
    assert db.loads(export_checks.freeze(pid)['reading_citation_reviews'][-1]['detail'])['reading_checks']==body['reading_checks']


def test_teaching_ocr_requires_original_slide_scope(client):
    pid=project(client);original,_=parsers.register_file(pid,'教学示例.pptx',synthetic_slides(),role='rules');parsers.parse_file(original['id'])
    page=db.one('SELECT id FROM pages WHERE file_id=?',(original['id'],));identity=db.uid()
    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(identity,original['id'],page['id'],db.dumps({'page':1}),'本文实现了证书上链。','本文实现了证书上链。','example','ocr',0))
    opened=reader.create(pid,{'file_id':original['id'],'question':'该句是什么语境？','intent':'guidance'})
    directory=reader.outline(pid,original['id'],'guidance');alternative=next(u for u in directory['units'] if u['origin']=='ocr');native=next(u for u in directory['units'] if u['origin']=='pptx_slide')
    result=reader.read_range(pid,opened['id'],{'ordinal_start':alternative['ordinal'],'ordinal_end':alternative['ordinal']})
    assert result['status']=='needs_original_slide' and result['passages']==[]
    reader.read_range(pid,opened['id'],{'ordinal_start':native['ordinal'],'ordinal_end':native['ordinal']})
    result=reader.read_range(pid,opened['id'],{'ordinal_start':alternative['ordinal'],'ordinal_end':alternative['ordinal']})
    assert result['status']=='read' and len(reader.context(pid,opened['id'])['passages'])==2
