import json
import pytest
from fastapi import HTTPException
from test_workbench import client, project, file, section, manual_package
from test_graph_wiki import library, no_remote_models, build
from app import db, tasks, knowledge_units, document_reading, documents
from app import knowledge_lineage as lineage, knowledge_graph as graph, knowledge_wiki as wiki, knowledge_builds as builds, knowledge_compiler as compiler, knowledge_router as router

def test_api_and_navigation(library):
    c,pid,f,p=library;build(pid);base=f'/api/v1/projects/{pid}/knowledge'
    assert c.get(base+'/status').status_code==200
    pages=c.get(base+'/wiki').json()['items'];assert pages and 'blocks' not in pages[0]
    detail=c.get(base+'/wiki/'+pages[0]['id']);assert detail.status_code==200
    found=c.post(base+'/graph/search',json={'query':'单用户','namespace':'literature'}).json();assert found['assertions']
    assert c.put(base+'/settings',json={'enabled':True}).json()['enabled']
    assert c.post(base+'/builds/preview',json={'mode':'literal','namespace':'all'}).json()['estimated_extraction_calls']==0
    assert c.post(base+'/builds/preview',json={'budget_limit':-1}).status_code==422

def test_cross_project_ids_rejected(library):
    c,pid,f,p=library;build(pid);other=project(c,'其他项目');row=wiki.search(pid,'单用户')['items'][0]
    assert c.get(f'/api/v1/projects/{other}/knowledge/wiki/'+row['id']).status_code==404
    with pytest.raises(HTTPException):lineage.validate_refs(other,[lineage.ref(pid,'wiki_version',row['current_version_id'])])

def test_human_and_model_assessments_remain_distinct(library):
    c,pid,f,p=library;build(pid);a=graph.search(pid,'单用户')['assertions'][0];oid=next(r['id'] for r in a['sources'] if r['type']=='observation');obs=lineage.owned(pid,'observation',oid)
    body={'observation_id':oid,'quote':obs['text'],'support':'supported','reviewer':'核对人','note':'原文限定条件保留','compared_original':True}
    model=graph.assess(pid,a['claim_version_id'],body,'model');assert model['method']=='model'
    assert not graph.get_assertion(pid,a['claim_version_id'])['human_reviewed']
    human=graph.assess(pid,a['claim_version_id'],body);assert human['method']=='human'
    assert graph.get_assertion(pid,a['claim_version_id'])['human_reviewed']
    with pytest.raises(HTTPException):graph.assess(pid,a['claim_version_id'],{**body,'quote':'不存在的结果'})
    assert not db.rows('SELECT * FROM knowledge_reviews')

def test_duplicate_build_reuses_completed_units(library):
    c,pid,f,p=library;build(pid);before={table:db.one('SELECT COUNT(*) AS n FROM '+table)['n'] for table in ('knowledge_observations','claim_versions','evidence_items')}
    _,second=build(pid)
    assert second['files'][0]['cache_hit']
    assert before=={table:db.one('SELECT COUNT(*) AS n FROM '+table)['n'] for table in before}

def test_multi_document_reading_records_actual_ranges(library):
    c,pid,f,p=library;other=file(c,pid,'单用户方案的另一项研究。\n\n并发条件没有被验证。');knowledge_units.build(pid,other['id']);build(pid);sid=section(c,pid)
    result=router.query(pid,{'query':'单用户','include_originals':False},True)
    assert len({r['file_id'] for r in result['reading_plan']})==2
    read=router.prepare_readings(pid,'单用户',sid,result['reading_plan'])
    assert len(read['reading_ids'])==2 and read['passages']
    assert all(document_reading.context(pid,rid)['claim_support']=='not_checked' for rid in read['reading_ids'])

def test_source_exclusion_invalidates_query(library):
    c,pid,f,p=library;build(pid);uid=p['units'][0]['id'];db.execute("UPDATE knowledge_units SET review_state='excluded' WHERE id=?",(uid,))
    assert not graph.search(pid,'单用户')['assertions']

def test_editorial_note_survives_machine_update(library):
    c,pid,f,p=library;build(pid);page=wiki.search(pid,'单用户')['items'][0];base=page['current_version_id']
    blocks=[{'key':b['block_key'],'kind':b['kind'],'text':b['text'],'sources':b['sources']} for b in page['blocks']]
    blocks.append({'key':'author-note','kind':'note','text':'人工保留的说明','sources':[]})
    draft=wiki.revise(pid,page['id'],{'base_version_id':base,'blocks':blocks});wiki.publish(pid,page['id'],{'version_id':draft['version']['id'],'base_version_id':base})
    current=wiki.get_page(pid,page['id']);automatic=wiki.revise(pid,page['id'],{'base_version_id':current['current_version_id'],'blocks':[{'key':'new','kind':'note','text':'新的机器说明'}]},'model')
    assert any(b['text']=='人工保留的说明' for b in automatic['blocks'])

def test_restore_release_preserves_later_editorial_versions(library):
    c,pid,f,p=library;_,result=build(pid);release=result['release']['id'];page=wiki.search(pid,'单用户')['items'][0];old=page['current_version_id']
    draft=wiki.revise(pid,page['id'],{'base_version_id':old,'blocks':[{'key':'note','kind':'note','text':'后来的人工修改'}]});wiki.publish(pid,page['id'],{'version_id':draft['version']['id'],'base_version_id':old})
    builds.rollback(pid,release)
    assert wiki.get_page(pid,page['id'])['current_version_id']==old
    assert lineage.owned(pid,'wiki_version',draft['version']['id'])

def test_graph_claim_trace_export_and_stale_prose(library):
    c,pid,f,p=library;build(pid);sid=section(c,pid);a=graph.search(pid,'单用户')['assertions'][0]
    document={'type':'doc','content':[{'type':'paragraph','attrs':{'knowledgeClaimIds':[a['claim_version_id']]},'content':[{'type':'text','text':'仅适用于单用户。'}]}]}
    saved=documents.save(__import__('app.security',fromlist=['require_section']).require_section(sid),document,None)
    from app.export_checks import freeze
    frozen=freeze(pid);assert frozen['graph_wiki']['objects']
    lineage.changed(pid,'file',f['id'],'原文修订')
    assert db.one('SELECT stale FROM sections WHERE id=?',(sid,))['stale']==1
    assert db.one('SELECT document FROM revisions WHERE id=?',(saved['id'],))

def test_evaluation_drafts_never_marked_gold(library):
    c,pid,f,p=library
    from app.knowledge_comparisons import seed
    result=seed(pid);assert len(result['cases'])==80
    assert all(case['state']=='unlabelled' for case in result['cases'])

def test_derived_permission_checked_before_model_call(library,monkeypatch):
    c,pid,f,p=library;build(pid);a=graph.search(pid,'单用户')['assertions'][0];refs=[lineage.ref(pid,'assertion',a['claim_version_id'])]
    c.patch('/api/v1/files/'+f['id'],json={'egress':'local_only'})
    from app.knowledge_model_bridge import complete
    def forbidden(*args,**kw):raise AssertionError('禁止调用模型')
    monkeypatch.setattr(tasks,'model_call',forbidden)
    with pytest.raises(HTTPException):complete({'project_id':pid},'test',{},refs)

def test_real_official_byog_without_model_calls(library,monkeypatch):
    from app import graphrag_adapter as adapter
    if not adapter.capabilities()['installed']:pytest.skip('可选组件未安装')
    c,pid,f,p=library;build(pid)
    def forbidden(*args,**kw):raise AssertionError('社区检测不应调用模型')
    monkeypatch.setattr(tasks,'model_call',forbidden)
    task=tasks.enqueue(pid,'knowledge_graphrag_build',{'reports':False})
    result=adapter.build(task,{'reports':False,'edge_limit':8})
    assert result['version']=='3.3.0' and result['backend']=='microsoft_graphrag'
    assert result['tables']['communities']>0
    assert result['source_mapping']

def test_unrelated_wiki_edit_does_not_invalidate_used_block(library):
    c,pid,f,p=library;build(pid);page=wiki.search(pid,'单用户')['items'][0];block=page['blocks'][0];ref=lineage.ref(pid,'wiki_block',block['id'])
    blocks=[{'key':b['block_key'],'kind':b['kind'],'text':b['text'],'sources':b['sources'],'metadata':b['metadata']} for b in page['blocks']]
    blocks.append({'key':'new-note','kind':'note','text':'无关的新备注'})
    revision=wiki.revise(pid,page['id'],{'base_version_id':page['current_version_id'],'blocks':blocks});wiki.publish(pid,page['id'],{'version_id':revision['version']['id'],'base_version_id':page['current_version_id']})
    lineage.validate_refs(pid,[ref])

def test_assessment_change_invalidates_old_knowledge_reference(library):
    c,pid,f,p=library;build(pid);a=graph.search(pid,'单用户')['assertions'][0];ref=lineage.ref(pid,'assertion',a['claim_version_id']);oid=next(r['id'] for r in a['sources'] if r['type']=='observation')
    graph.assess(pid,a['claim_version_id'],{'observation_id':oid,'support':'insufficient','quote':'','note':'尚缺条件','reviewer':'核对人','compared_original':True})
    with pytest.raises(HTTPException):lineage.validate_refs(pid,[ref])

def test_roles_values_and_split_keep_original_evidence(library):
    c,pid,f,p=library;uid=p['units'][0]['id'];obs=compiler.observe_unit(pid,uid);ref=lineage.ref(pid,'observation',obs['id'])
    doc=graph.entity(pid,{'namespace':'literature','kind':'document','name':f['name'],'key':'file:'+f['id'],'sources':[ref]})
    proposal={'statement':obs['text'],'quote':obs['text'],'entities':[{'name':'方法','kind':'method','quote':'方法'}],'subject_entity_name':'方法','object_value':{'value':'未知'}}
    adopted=compiler.adopt_candidates(pid,obs,[proposal],doc,'llm');old=graph.get_assertion(pid,adopted['assertion_ids'][0])
    assert old['subject_id']!=doc['id'] and old['object']['value']=='未知'
    changed=graph.split_entity(pid,old['subject_id'],{'name':'方法的另一含义','claim_ids':[old['claim_version_id']],'reviewer':'合成核对','note':'合成实体消歧'})
    assert changed['claim_ids'] and not changed['human_support_checks_copied']
    assert db.one('SELECT lifecycle FROM kg_assertions WHERE claim_version_id=?',(old['claim_version_id'],))['lifecycle']=='superseded'
    assert db.one('SELECT * FROM claim_versions WHERE id=?',(old['claim_version_id'],))

def test_full_export_has_real_document_mapping(library):
    c,pid,f,p=library;other=file(c,pid,'另一份研究的明确条件。');knowledge_units.build(pid,other['id']);build(pid)
    from app.graphrag_adapter import tables
    data,mapping,refs=tables(pid,{'full_index':True,'reports':False,'namespace':'literature'})
    assert len(data['documents'])==2
    assert {u['document_id'] for u in data['text_units']}=={d['id'] for d in data['documents']}
    assert len({u['human_readable_id'] for u in data['text_units']})==len(data['text_units'])

def test_model_cannot_claim_human_authorship(library):
    c,pid,f,p=library;page=wiki.create_page(pid,{'namespace':'literature','kind':'topic','slug':'author-guard','title':'作者标签检查'})
    version=wiki.revise(pid,page['id'],{'base_version_id':None,'blocks':[{'key':'x','kind':'note','text':'模型候选备注','author':'human'}]},'model')
    assert version['blocks'][0]['author']=='model'

def test_stale_human_source_is_preserved_as_unpublishable_candidate(library):
    c,pid,f,p=library;build(pid);a=graph.search(pid,'单用户')['assertions'][0];ref=lineage.ref(pid,'assertion',a['claim_version_id'])
    page=wiki.create_page(pid,{'namespace':'literature','kind':'topic','slug':'manual-source','title':'保留人工来源'})
    first=wiki.revise(pid,page['id'],{'base_version_id':None,'blocks':[{'key':'manual','kind':'note','text':'人工保留的条件说明','sources':[ref]}]})
    wiki.publish(pid,page['id'],{'version_id':first['version']['id'],'base_version_id':None})
    oid=next(r['id'] for r in a['sources'] if r['type']=='observation');graph.assess(pid,a['claim_version_id'],{'observation_id':oid,'support':'insufficient','note':'条件需复查','reviewer':'合成核对','compared_original':True})
    candidate=wiki.revise(pid,page['id'],{'base_version_id':first['version']['id'],'blocks':[{'key':'new','kind':'note','text':'新的模型候选'}]},'model')
    manual=next(b for b in candidate['blocks'] if b['block_key']=='manual')
    assert manual['author']=='human' and manual['metadata']['requires_source_review']
    with pytest.raises(HTTPException):wiki.publish(pid,page['id'],{'version_id':candidate['version']['id'],'base_version_id':first['version']['id']})
