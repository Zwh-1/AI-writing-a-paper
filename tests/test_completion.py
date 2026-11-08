import io,json,pytest
from test_workbench import client,project,manual_package,section,save,file
from app import db,rules,documents,tasks,provider,security,reviews,exports,rule_engine

def test_rule_engine_word_range_is_deterministic(client):
 p=project(client);sid=section(client,p);save(client,sid,'短段落');client.post(f'/api/v1/projects/{p}/rules',json={'statement':'系统设计至少20字','scope':'system_design','checker':'word_range','value':{'min':20,'max':200}});rules.publish(p);out=reviews.run(p);assert any(x['method']=='deterministic' and x['result']=='fail' and '篇幅' in x['message'] for x in out['findings'])

@pytest.mark.parametrize('statement,checker,value',[('摘要字数300-500字','word_range',{'min':300,'max':500}),('参考文献不少于二十篇','reference_min',20),('左边距2.5厘米','format_value',{'key':'margin_left','expected':25.0}),('正文小四号','format_value',{'key':'font_size','expected':12}),('摘要不超过500字','word_max',500)])
def test_rule_inference_keeps_executable_parameters(statement,checker,value):
 result=rule_engine.infer(statement);assert result['checker']==checker;assert result['value']==value

def test_rules_reject_invalid_checker_parameters(client):
 p=project(client);r=client.post(f'/api/v1/projects/{p}/rules',json={'statement':'范围','checker':'word_range','value':{'min':500,'max':300}});assert r.status_code==422

def test_condition_is_validated_and_snapshot_loaded(client):
 p=project(client);manual_package(client,p);rid=db.one('SELECT id FROM rules')['id'];r=client.put(f'/api/v1/rules/{rid}/execution',json={'checker':'manual','conditions':{'field':'paper_type','op':'eq','value':'review'},'operations':['revise']});assert r.status_code==200,r.text;pack=rules.publish(p);pkg=db.one('SELECT * FROM rule_packages WHERE id=?',(pack['id'],));assert not rules.applicable(pkg,{'id':'s','kind':'body'},'revise')
 assert client.put(f'/api/v1/rules/{rid}/execution',json={'conditions':{'field':'__import__','op':'eq','value':'x'}}).status_code==422

def test_outline_dependency_cycle_cannot_be_saved(client):
 p=project(client);a=section(client,p);b=section(client,p);tree=client.get(f'/api/v1/projects/{p}/outline/tree').json();tree['nodes'][0]['dependencies']=[b];tree['nodes'][1]['dependencies']=[a];r=client.put(f'/api/v1/projects/{p}/outline/tree',json={'nodes':tree['nodes'],'version':tree['version']});assert r.status_code==422

def test_outline_parent_cycle_cannot_be_saved(client):
 p=project(client);a=section(client,p);b=section(client,p);tree=client.get(f'/api/v1/projects/{p}/outline/tree').json();tree['nodes'][0]['parent_id']=b;tree['nodes'][1]['parent_id']=a;assert client.put(f'/api/v1/projects/{p}/outline/tree',json=tree).status_code==422

def test_hierarchical_outline_save_uses_version_guard(client):
 p=project(client);a=section(client,p);b=section(client,p);tree=client.get(f'/api/v1/projects/{p}/outline/tree').json();tree['nodes'][1]['parent_id']=a;r=client.put(f'/api/v1/projects/{p}/outline/tree',json=tree);assert r.status_code==200,r.text;assert r.json()['nodes'][1]['level']==2;assert client.put(f'/api/v1/projects/{p}/outline/tree',json=tree).status_code==409

def test_archive_is_reversible_and_preserves_content(client):
 p=project(client);sid=section(client,p);rev=save(client,sid,'不能被删除的正文').json();assert client.post(f'/api/v1/sections/{sid}/archive',json={'base_revision_id':rev['id']}).status_code==200;assert client.get(f'/api/v1/projects/{p}/sections').json()==[];assert documents.current(security.require_section(sid))['text']=='不能被删除的正文';assert client.post(f'/api/v1/sections/{sid}/unarchive').status_code==200;assert len(client.get(f'/api/v1/projects/{p}/sections').json())==1

def test_archive_rejects_dependent_chapters(client):
 p=project(client);a=section(client,p);b=section(client,p);client.patch('/api/v1/sections/'+b,json={'dependencies':[a]});assert client.post('/api/v1/sections/'+a+'/archive',json={'base_revision_id':None}).status_code==409

def test_required_sections_checker_reports_missing(client):
 p=project(client);section(client,p);client.post(f'/api/v1/projects/{p}/rules',json={'statement':'必须有结论','checker':'required_sections','value':['conclusion']});rules.publish(p);r=reviews.run(p);assert any('缺少 conclusion' in x['message'] and x['result']=='fail' for x in r['findings'])

def test_manual_signoff_invalidates_after_edit(client):
 p=project(client);sid=section(client,p);rev=save(client,sid,'初稿').json();manual_package(client,p);r=reviews.run(p);f=next(x for x in r['findings'] if x['rule_id']);signed=client.post('/api/v1/findings/'+f['id']+'/review',json={'result':'pass','note':'已对照原文核对'});assert signed.status_code==200,signed.text;assert any(x['rule_id'] and x['result']=='pass' for x in signed.json()['findings']);save(client,sid,'内容已变化',rev['id']);assert any(x['rule_id'] and x['result']=='unknown' for x in reviews.run(p)['findings'])

def test_cannot_override_deterministic_failure_with_manual_pass(client):
 p=project(client);section(client,p);client.post(f'/api/v1/projects/{p}/rules',json={'statement':'必须有100字','checker':'word_min','value':100});rules.publish(p);f=next(x for x in reviews.run(p)['findings'] if x['rule_id']);assert client.post('/api/v1/findings/'+f['id']+'/review',json={'result':'pass','note':'尝试绕过规则'}).status_code==422

def test_format_rules_apply_and_profile_switch(client):
 p=project(client);client.post(f'/api/v1/projects/{p}/rules',json={'statement':'正文14pt','category':'format','checker':'format_value','value':{'key':'font_size','expected':14}});pack=rules.publish(p);r=client.post(f'/api/v1/projects/{p}/format/apply-rules',json={'package_id':pack['id']});assert r.status_code==200,r.text;assert exports.format_config(p)['font_size']==14;profile=client.post(f'/api/v1/projects/{p}/format-profiles',json={'name':'学校方案'}).json();client.put(f'/api/v1/projects/{p}/format',json={'font_size':10});assert client.post('/api/v1/format-profiles/'+profile['id']+'/activate',json={'project_id':p}).status_code==200;assert exports.format_config(p)['font_size']==14

def test_format_profile_cannot_be_applied_to_other_project(client):
 p=project(client);other=project(client,'另一个');profile=client.post(f'/api/v1/projects/{p}/format-profiles',json={'name':'A'}).json();assert client.post('/api/v1/format-profiles/'+profile['id']+'/activate',json={'project_id':other}).status_code==404

def test_source_unit_counterexample_is_excluded_from_research_search(client):
 p=project(client);file(client,p,'模块维护的研究证据');sid=db.one('SELECT id FROM sources')['id'];client.patch('/api/v1/source-units/'+sid+'/usage',json={'kind':'counterexample'});from app.parsers import search
 assert not search(p,'模块维护',kind='research');assert search(p,'模块维护',kind='example')

def mock_candidate(p,sid,doc,base):
 task=tasks.enqueue(p,'write',{'section_id':sid});package=security.require_project(p)['rule_package_id'];result={'section_id':sid,'base_revision_id':base,'document':doc,'manifest':{'rule_package_id':package},'citation_proposals':[]};db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?",(db.dumps(result),task['id']));return task

def test_partial_candidate_replaces_original_location(client):
 p=project(client);manual_package(client,p);sid=section(client,p);base=save(client,sid,'第一段保留\n第二段待修改\n第三段保留').json();old=db.loads(base['document']);candidate=documents.text_document('第二段修改后');task=mock_candidate(p,sid,candidate,base['id']);r=client.post('/api/v1/sections/'+sid+'/accept',json={'task_id':task['id'],'base_revision_id':base['id'],'node_ids':[candidate['content'][0]['attrs']['nodeId']],'source_node_ids':[old['content'][1]['attrs']['nodeId']],'mode':'replace_selected'});assert r.status_code==200,r.text;assert r.json()['text']=='第一段保留\n第二段修改后\n第三段保留'

def test_candidate_diff_has_replacement_hunk(client):
 p=project(client);manual_package(client,p);sid=section(client,p);base=save(client,sid,'旧内容').json();task=mock_candidate(p,sid,documents.text_document('新内容'),base['id']);diff=client.get('/api/v1/tasks/'+task['id']+'/diff');assert diff.status_code==200;assert diff.json()['changes'][0]['kind']=='replace'

def test_selected_paragraph_generation_preserves_unselected_content(client,monkeypatch):
 p=project(client);manual_package(client,p);sid=section(client,p);base=save(client,sid,'首段保留\n只改这一段\n尾段保留').json();doc=db.loads(base['document']);target=doc['content'][1]['attrs']['nodeId'];cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg)
 def fake(system,user,images=None):
  ctx=db.loads(user)
  if ctx.get('operation')=='review_candidate_discourse':
   return {'text':db.dumps({'coverage_complete':True,'findings':[],'summary':'仅核对给定候选',
    'rule_reviews':[{'rule_id':row['id'],'status':'satisfied','paragraph_indices':[0],
     'quote':ctx['candidate_paragraphs'][0]['text'],'reason':'合成夹具保留当前规则范围'} for row in ctx.get('rule_applications',[])]}),
    'usage':{},'cost':0,'remote_id':'review-fixture','request_hash':'review-fixture'}
  assert ctx['scope']=='selected_paragraphs';assert ctx['existing_text']=='只改这一段';return {'text':db.dumps({'paragraphs':[{'text':'中间段落已修改'}]}),'usage':{},'cost':0,'remote_id':'t','request_hash':'h'}
 monkeypatch.setattr(provider,'generate',fake);t=tasks.enqueue(p,'write',{'section_id':sid,'base_revision_id':base['id'],'operation':'revise','target_node_ids':[target]});tasks.run_one();task=db.one('SELECT * FROM tasks WHERE id=?',(t['id'],));assert task['status']=='succeeded',task['error'];assert documents.plain(db.loads(task['result'])['document'])=='首段保留\n中间段落已修改\n尾段保留'

def test_selected_generation_rejects_locked_target(client):
 p=project(client);manual_package(client,p);sid=section(client,p);base=save(client,sid,'锁定段落').json();node=db.loads(base['document'])['content'][0]['attrs']['nodeId'];client.put('/api/v1/sections/'+sid+'/locks',json={'node_ids':[node]});r=client.post('/api/v1/sections/'+sid+'/writing-tasks',json={'base_revision_id':base['id'],'operation':'revise','target_node_ids':[node]});assert r.status_code==409

def test_section_can_be_explicitly_reviewed_only_at_current_revision(client):
 p=project(client);sid=section(client,p);base=save(client,sid,'待审阅正文').json();assert client.post('/api/v1/sections/'+sid+'/reviewed',json={'revision_id':'old'}).status_code==409;assert client.post('/api/v1/sections/'+sid+'/reviewed',json={'revision_id':base['id'],'note':'已核对'}).status_code==200;assert security.require_section(sid)['state']=='user_reviewed'

def test_rule_ai_rejects_invented_source_quotation(client,monkeypatch):
 p=project(client);file(client,p,'摘要必须说明研究问题。',role='rules');source=db.one('SELECT * FROM sources');cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg);monkeypatch.setattr(tasks,'model_call',lambda *a,**kw:{'text':db.dumps({'rules':[{'source_id':source['id'],'quote':'原文里并不存在','statement':'新规则','modality':'mandatory'}]}),'usage':{},'cost':0,'remote_id':'t','request_hash':'h'});t=tasks.enqueue(p,'rules_ai',{'source_limit':40,'file_ids':[]});tasks.run_one();row=db.one('SELECT * FROM tasks WHERE id=?',(t['id'],));assert row['status']=='failed';assert not db.rows('SELECT * FROM rules')

def test_rule_ai_candidates_are_not_automatically_confirmed(client,monkeypatch):
 p=project(client);file(client,p,'摘要必须说明研究问题。',role='rules');source=db.one('SELECT * FROM sources');cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg);monkeypatch.setattr(tasks,'model_call',lambda *a,**kw:{'text':db.dumps({'rules':[{'source_id':source['id'],'quote':'摘要必须说明研究问题','statement':'摘要必须说明研究问题','modality':'mandatory','scope':'abstract','category':'content'}]}),'usage':{},'cost':0,'remote_id':'t','request_hash':'h'});t=tasks.enqueue(p,'rules_ai',{'source_limit':40,'file_ids':[]});tasks.run_one();row=db.one('SELECT * FROM tasks WHERE id=?',(t['id'],));assert row['status']=='succeeded',row['error'];assert db.one('SELECT state FROM rules')['state']=='candidate'

def test_local_ocr_real_chinese_image_never_marks_reviewed(client):
 from app.local_ocr import capabilities,page_ocr
 if not capabilities()['available']:pytest.skip('Windows OCR language pack unavailable')
 from PIL import Image,ImageDraw,ImageFont
 image=Image.new('RGB',(1000,180),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',40);draw.text((25,45),'论文摘要必须说明研究方法。',fill='black',font=font);buf=io.BytesIO();image.save(buf,format='PNG');sha=security.put_blob(buf.getvalue());p=project(client);f=file(client,p,'图片规则',role='rules');page=db.one('SELECT * FROM pages');db.execute('UPDATE pages SET images=?,has_visual=1 WHERE id=?',(db.dumps([{'sha256':sha,'name':'test.png'}]),page['id']));result=page_ocr(page['id']);assert '摘要' in result['text'] and '研究方法' in result['text'];assert result['external_requests'] is False;assert db.one('SELECT review_state FROM pages')['review_state']=='unreviewed';assert db.one("SELECT verified FROM sources WHERE method='windows_ocr'")['verified']==0

def test_export_checks_docx_properties_and_rich_markdown(client):
 p=project(client);manual_package(client,p);sid=section(client,p);doc=documents.text_document('正文内容');doc['content'].append({'type':'table','content':[{'type':'tableRow','content':[{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'列一'}]}]},{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'列二'}]}]}]}]});doc['content'].append({'type':'formula','attrs':{'expression':'x+y=1'}});documents.save(security.require_section(sid),doc,None);out=exports.export_project(p,{});folder=exports.DATA/'exports'/out['export_id'];report=json.loads((folder/'检查报告.json').read_text(encoding='utf-8'));assert report['snapshot_consistency']=='read_transaction';assert report['docx_property_checks']['fail']==0;assert report['docx_property_checks']['pass']>=10;md=(folder/'论文.md').read_text(encoding='utf-8');assert '| 列一 | 列二 |' in md and '$$\nx+y=1\n$$' in md

def test_nested_section_exports_as_heading_two(client):
 from docx import Document
 p=project(client);manual_package(client,p);a=section(client,p);b=section(client,p);tree=client.get(f'/api/v1/projects/{p}/outline/tree').json();tree['nodes'][1]['parent_id']=a;client.put(f'/api/v1/projects/{p}/outline/tree',json=tree);save(client,a,'上级章节');save(client,b,'下级内容');out=exports.export_project(p,{});doc=Document(exports.DATA/'exports'/out['export_id']/'论文.docx');heads=[para for para in doc.paragraphs if para.style.name=='Heading 2'];assert heads

def test_manual_review_of_one_section_does_not_mark_another_pass(client):
 p=project(client);a=section(client,p);b=section(client,p);save(client,a,'第一章');save(client,b,'第二章');manual_package(client,p);findings=reviews.run(p)['findings'];f=next(x for x in findings if x['rule_id'] and x['section_id']==a);r=client.post('/api/v1/findings/'+f['id']+'/review',json={'result':'pass','note':'只核对了第一章'});assert r.status_code==200;assert any(x['rule_id'] and x['section_id']==b and x['result']=='unknown' for x in r.json()['findings'])

def test_retrieval_evaluation_does_not_invent_gold_labels(client):
 p=project(client);file(client,p,'模块维护与架构设计');source_id=db.one('SELECT id FROM sources')['id'];bad=client.post(f'/api/v1/projects/{p}/retrieval/evaluate',json={'cases':[{'query':'模块维护'}]});assert bad.status_code==422;r=client.post(f'/api/v1/projects/{p}/retrieval/evaluate',json={'cases':[{'query':'模块维护','expected_source_ids':[source_id]}]});assert r.status_code==200,r.text;assert r.json()['recall_at_10']==1

def test_readiness_exposes_missing_goals_instead_of_complete_flag(client):
 p=project(client);r=client.get(f'/api/v1/projects/{p}/readiness');assert r.status_code==200;assert {'rule_package','outline'}<={b['kind'] for b in r.json()['blockers']}

def test_known_rate_limit_retries_are_recorded_without_retrying_unknown(client,monkeypatch):
 p=project(client);cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg);calls=[]
 def fake(*args,**kwargs):
  calls.append(1)
  if len(calls)==1:raise provider.ProviderError('rate limit',retryable=True,retry_after=0)
  return {'text':'{"ok":true}','usage':{},'cost':0,'remote_id':'x','request_hash':'h'}
 monkeypatch.setattr(provider,'generate',fake);t=tasks.enqueue(p,'provider_probe',{});tasks.run_one();row=db.one('SELECT * FROM tasks WHERE id=?',(t['id'],));assert row['status']=='succeeded',row['error'];assert len(calls)==2;runs=db.rows('SELECT status FROM generation_runs');assert {x['status'] for x in runs}=={'rejected','completed'}

def test_unknown_provider_outcome_is_not_automatically_retried(client,monkeypatch):
 p=project(client);cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg);calls=[]
 def fake(*args,**kwargs):calls.append(1);raise provider.ProviderError('unknown',unknown=True,retryable=True)
 monkeypatch.setattr(provider,'generate',fake);t=tasks.enqueue(p,'provider_probe',{});tasks.run_one();assert len(calls)==1;assert db.one('SELECT status FROM tasks WHERE id=?',(t['id'],))['status']=='outcome_unknown'

def test_chapter_creation_rejects_cross_project_dependency(client):
 p=project(client);other=project(client,'other');sid=section(client,other);r=client.post(f'/api/v1/projects/{p}/sections',json={'title':'坏依赖','dependencies':[sid]});assert r.status_code==422;assert client.get(f'/api/v1/projects/{p}/sections').json()==[]

def test_rule_creation_origin_validation_is_atomic(client):
 p=project(client);other=project(client,'other');file(client,other);source=db.one('SELECT id FROM sources')['id'];r=client.post(f'/api/v1/projects/{p}/rules',json={'statement':'不能借用他项目来源','source_id':source});assert r.status_code==422;assert db.one('SELECT COUNT(*) AS n FROM rules')['n']==0

def test_editing_chapter_invalidates_old_outline_tree_version(client):
 p=project(client);sid=section(client,p);tree=client.get(f'/api/v1/projects/{p}/outline/tree').json();client.patch('/api/v1/sections/'+sid,json={'title':'新的章节标题'});r=client.put(f'/api/v1/projects/{p}/outline/tree',json=tree);assert r.status_code==409


def test_retrieval_recall_counts_all_expected_sources_and_does_not_assert_human_review(client):
 p=project(client);file(client,p,'唯一相关术语ZXY');first=db.one('SELECT id FROM sources')['id'];file(client,p,'完全不同的材料ABC');second=next(x['id'] for x in db.rows('SELECT id FROM sources') if x['id']!=first)
 r=client.post(f'/api/v1/projects/{p}/retrieval/evaluate',json={'cases':[{'query':'ZXY','expected_source_ids':[first,second]}]})
 assert r.status_code==200,r.text
 assert r.json()['hit_rate_at_10']==1 and r.json()['recall_at_10']==.5
 assert r.json()['human_confirmation']=='not_recorded' and r.json()['formal_human_acceptance'] is False

def test_retrieval_rejects_malformed_cases_and_source_list(client):
 p=project(client)
 for case in ['bad',{'query':'x','expected_source_ids':'bad'},{'query':'x','expected_source_ids':[{}]}]:
  assert client.post(f'/api/v1/projects/{p}/retrieval/evaluate',json={'cases':[case]}).status_code==422
