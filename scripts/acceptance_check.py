"""Acceptance rehearsal on a private SQLite snapshot. --live-probe sends ONE bounded request.
Never marks the original user's sources, rules, research, or thesis as human reviewed.
"""
from pathlib import Path
import argparse, json, os, shutil, sqlite3, sys, datetime
ROOT=Path(__file__).resolve().parents[1]
cli=argparse.ArgumentParser();cli.add_argument('--source-data',default=str(ROOT/'data'));cli.add_argument('--live-probe',action='store_true');args=cli.parse_args()
original=Path(args.source_data).resolve();out=original/'acceptance-2026-10-21'/datetime.datetime.now().strftime('run-%H%M%S');clone=out/'rehearsal';clone.mkdir(parents=True)
assert original != clone and original in clone.parents
src=sqlite3.connect(original/'workbench.sqlite3');dst=sqlite3.connect(clone/'workbench.sqlite3');src.backup(dst);dst.close();src.close()
shutil.copytree(original/'objects',clone/'objects',dirs_exist_ok=True)
os.environ['PAPER_DATA_DIR']=str(clone);os.environ['PAPER_PORT']='9997'
sys.path[:0]=[str(ROOT/'.runtime/python-deps'),str(ROOT/'backend')]
from app import db,parsers,rules,security,provider,tasks,documents,exports,local_ocr
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app

def write(name,value):
 (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def show(message): print(message,flush=True)
def inventory(connection):
 connection.row_factory=sqlite3.Row
 return {table:[dict(x) for x in connection.execute(sql)] for table,sql in {
  'projects':'SELECT id,title,paper_type,stage,rule_package_id FROM projects',
  'files':'SELECT id,name,role,status,sha256 FROM files',
  'rules':'SELECT id,state,modality FROM rules',
  'pages':'SELECT id,review_state,review_note FROM pages',
  'sections':'SELECT id,current_revision_id,state FROM sections',
  'facts':'SELECT id,status,verification FROM facts',
  'artifacts':'SELECT id,sha256 FROM artifacts'}.items()}
connection=sqlite3.connect(original/'workbench.sqlite3');before=inventory(connection);connection.close();write('original-before.json',before)
pid=before['projects'][0]['id'];write('inventory.json',{'counts':{k:len(v) for k,v in before.items()},'human_confirmed_rules':sum(x['state']=='confirmed' for x in before['rules']),'reviewed_pages':sum(x['review_state']=='reviewed' for x in before['pages']),'official_template':bool(db.setting('template:'+pid)),'source_data':str(original),'snapshot':str(clone),'created_at':db.now()})
write('conflicts.json',rules.conflicts(pid))
try:rules.publish(pid);blocked=False;details='unexpectedly published'
except HTTPException as error:blocked=True;details=error.detail
write('rule-publication-gate.json',{'blocked_as_expected':blocked,'detail':details,'original_project_unchanged':True})
show('Snapshot ready; original rule-publication gate tested.')
# Every annotation is authored by the test preparer; NONE is human-confirmed gold.
A='文献检索与利用C-实训一：论文选题与论文结构设计-摘要.pptx'
S='文献检索与利用C-实训一：论文选题与论文结构设计-论文结构.pptx'
M='文献检索与利用C-第6章 研究方法与技术路线1.pptx'
T='文献检索与利用C-第7章 实验设计与结果分析.pptx'
Q='文献检索与利用C-第二章 文献检索基础与检索策略1.pptx'
L='文献检索与利用C-第5章 研究背景与国内外研究现状2.pptx'
specs=[
 ('摘要的五要素分别是什么？',[(A,8)]),
 ('摘要的研究背景建议控制在多少字？',[(A,9)]),
 ('摘要中的技术方法需要明确哪些平台和实现信息？',[(A,10)]),
 ('摘要的系统成果应该写具体功能还是笼统满足需求？',[(A,11)]),
 ('研究价值的收尾句最多多少字，空泛表述有什么问题？',[(A,12)]),
 ('摘要总字数和背景方法成果价值的篇幅如何分配？',[(A,13)]),
 ('缺少技术路线的摘要错误示例如何改正？',[(A,14)]),
 ('摘要列出的技术指标能直接当作我的实测结果吗？',[(A,11)]),
 ('论文结构课件要求参考文献最少多少篇？',[(S,4)]),
 ('论文参考文献的外文比例至少为多少？',[(S,4)]),
 ('发表文章在引用文献中的比例要求是多少？',[(S,4)]),
 ('论文结构里参考文献的发表时间要求是什么？',[(S,4)]),
 ('系统设计的二级标题至少几个，应展开多少页？',[(S,9)]),
 ('使用AI生成目录草稿后还需要做哪些人工修改？',[(S,13)]),
 ('需求分析系统设计系统实现系统测试应按什么逻辑衔接？',[(S,14)]),
 ('用户角色分析如何区分普通用户管理员和系统管理员？',[(M,8)]),
 ('功能需求除了用户行为还要写哪些系统响应和异常处理？',[(M,11)]),
 ('绘制用例图的找角色找功能建立关系三步是什么？',[(M,14)]),
 ('实验设计课件的二十篇参考文献是正式强制要求还是建议？',[(T,3)]),
 ('区块链功能测试要验证哪四个核心维度？',[(T,12)]),
 ('测试结果分析应从哪些维度解释结果？',[(T,15)]),
 ('系统测试章节常见的截图堆砌和结论无依据错误有哪些？',[(T,18)]),
 ('文献检索从确定主题到筛选评估的基本流程是什么？',[(Q,7)]),
 ('AI是否能保证引用文献存在并替代原始文献阅读？',[(Q,11)]),
 ('文献综述为什么会越写越偏，应怎样保持同一个研究问题？',[(L,23)]),
 ('同一套资料中十篇与二十篇参考文献要求冲突在哪里？',[(S,4),(T,3)]),
 ('abstract 技术方法 platform 智能合约 Solidity 示例在哪页？',[(A,10)]),
 ('交易Hash和链上记录应该怎样用于功能测试验证？',[(T,12)]),
 ('登录功能需求为什么不能只列一个页面名称？',[(M,11)]),
 ('背景讲区块链历史能否替代摘要中具体问题的说明？',[(A,9)]),
 ('摘要课件的百分比加起来是否等于百分之百，需核对哪些数？',[(A,13)]),
 ('截图没有说明且没有测试结果表为什么不足以支撑测试结论？',[(T,18)]),
]
cases=[];evaluation=[]
for number,(query,expected_pages) in enumerate(specs,1):
 annotations=[]
 for filename,page in expected_pages:
  source=db.one('SELECT s.*,p.page_number,p.review_state,f.name,f.sha256 FROM sources s JOIN files f ON f.id=s.file_id JOIN pages p ON p.id=s.page_id WHERE f.project_id=? AND f.name=? AND p.page_number=? ORDER BY length(s.text) DESC LIMIT 1',(pid,filename,page))
  if not source:raise RuntimeError('Missing expected original source: '+filename+':'+str(page))
  annotations.append({k:source[k] for k in ('id','file_id','page_id','name','sha256','page_number','text','review_state')})
 expected=[x['id'] for x in annotations];found=parsers.search(pid,query,kind='all',limit=10);ids=[x['id'] for x in found];intersection=set(expected)&set(ids)
 cases.append({'id':f'Q{number:02}','query':query,'kind':'all','expected_source_ids':expected,'annotations':annotations,'annotation_origin':'assistant_prepared_from_actual_sources','human_review_state':'pending','human_reviewer':None,'human_reviewed_at':None})
 evaluation.append({'id':f'Q{number:02}','query':query,'hit_at_10':bool(intersection),'recall_at_10':len(intersection)/len(set(expected)),'expected_source_ids':expected,'retrieved':[{'id':x['id'],'name':x['name'],'page_number':x['page_number'],'kind':x['kind'],'excerpt':x['text'][:220]} for x in found]})
write('retrieval-candidates.json',{'human_confirmed_count':0,'cases':cases})
metrics={'total':len(cases),'hits':sum(x['hit_at_10'] for x in evaluation),'hit_rate_at_10':sum(x['hit_at_10'] for x in evaluation)/len(cases),'macro_recall_at_10':sum(x['recall_at_10'] for x in evaluation)/len(cases),'formal_human_gold_acceptance':False,'human_confirmed_count':0,'cases':evaluation};write('retrieval-results.json',metrics)
lines=['# 实际资料检索样本待人工确认','', '32条问题均由本次测试整理，人工确认数为0。请逐条核对文件、页码、答案范围，并记录确认人和日期。命中率仅是候选集诊断结果。','']
for case in cases:
 lines+=['## '+case['id']+' '+case['query'],'','- 人工确认：□ 未确认　确认人：________　日期：________','']
 for annotation in case['annotations']:lines += ['来源：'+annotation['name']+'，第'+str(annotation['page_number'])+'页；source_id='+annotation['id'],'','> '+annotation['text'].replace('\n','\n> '),'']
(out/'检索样本_32条_待人工确认.md').write_text('\n'.join(lines),encoding='utf-8')
show(f'Retrieval candidates: {metrics["hits"]}/{len(cases)} hit at 10; human confirmations: 0.')
# OCR actual slide images and one PDF page, in the isolated snapshot only.
ocr=[]
for filename,page in [('实训3：文献整理与文献综述结构设计.pptx',53),(A,4),('智评综合等级划分说明.pdf',3)]:
 item=db.one('SELECT p.*,f.name FROM pages p JOIN files f ON f.id=p.file_id WHERE f.project_id=? AND f.name=? AND p.page_number=?',(pid,filename,page))
 try:
  result=local_ocr.page_ocr(item['id']);result['review_state_after']=db.one('SELECT review_state FROM pages WHERE id=?',(item['id'],))['review_state']
  if result['review_state_after']!='unreviewed':raise AssertionError('OCR must not imply human review')
  if filename.endswith('.pptx'):
   from PIL import Image
   images=[]
   for image in db.loads(item['images'],[]):
    try:
     path=security.safe_object(image['sha256'])
     with Image.open(path) as pic:images.append((pic.width*pic.height,path))
    except (OSError,ValueError):pass
   if images:
    _,path=max(images,key=lambda x:x[0]);image_path=out/('ocr-original-'+str(len(ocr)+1)+'.png')
    with Image.open(path) as pic:pic.convert('RGB').save(image_path)
    result['original_image']=str(image_path)
  ocr.append({'filename':filename,'page_number':page,'success':True,**result})
 except Exception as error:ocr.append({'filename':filename,'page_number':page,'success':False,'error':security.redact(error)})
write('ocr-results.json',ocr);show('Actual OCR samples completed; all stay unreviewed.')

with TestClient(app) as client:
 auth=client.post('/api/v1/session',json={'token':security.BOOTSTRAP_TOKEN});client.headers['X-CSRF-Token']=auth.json()['csrf']
 prep=client.get('/api/v1/projects/'+pid+'/readiness').json();write('actual-project-readiness.json',prep)
 p=client.post('/api/v1/projects',json={'title':'[自动化演练 非用户实际论文] 规则加载机制','target_words':1000}).json()['id']
 live={'requested':args.live_probe,'attempt_limit':1,'output_token_limit':256,'uses_user_materials':False,'human_acceptance':False}
 if args.live_probe:
  stored_config=provider.config()
  if stored_config['base_url'].rstrip('/') not in ('https://api.hanhegufei.online','https://www.ecoapi.cc'):raise RuntimeError('Unexpected relay host; refusing live credential use')
  if os.environ.get('PAPER_API_KEY'):real_key=os.environ['PAPER_API_KEY']
  else:real_key=security.protect((original/'credential.dpapi').read_bytes(),decrypt=True).decode('utf-8')
  original_get_key=security.get_key;original_provider_key=provider.get_key
  security.get_key=lambda:real_key;provider.get_key=security.get_key
  db.set_setting('provider',{**stored_config,'max_output_tokens':256,'timeout_seconds':60,'max_attempts':1})
  try:
   try:live['balance_before']=provider.account_usage()
   except Exception as error:live['balance_before_error']=security.redact(error)
   task=tasks.enqueue(p,'provider_probe',{});tasks.run_one();row=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],));live.update({'task_id':task['id'],'status':row['status'],'error':row['error'],'result':db.loads(row['result'])})
   live['calls']=db.rows('SELECT id,status,model,protocol,usage,cost,reserved,created_at FROM generation_runs WHERE task_id=?',(task['id'],))
   try:live['balance_after']=provider.account_usage()
   except Exception as error:live['balance_after_error']=security.redact(error)
   if live.get('balance_before',{}).get('remaining_exact') is not None and live.get('balance_after',{}).get('remaining_exact') is not None:
    from decimal import Decimal
    live['observed_account_balance_decrease']=str(Decimal(live['balance_before']['remaining_exact'])-Decimal(live['balance_after']['remaining_exact']))
    live['balance_delta_is_not_verified_per_call_price']=True
  finally:
   db.set_setting('provider',stored_config);security.get_key=original_get_key;provider.get_key=original_provider_key;real_key=None
 write('live-provider-test.json',live);show('Live provider test: '+live.get('status','not requested')+'; at most one model request.')
 # Full local workflow with an explicitly labelled deterministic fixture; no invented research.
 rule=client.post(f'/api/v1/projects/{p}/rules',json={'statement':'此为自动化演练，缺失研究结果必须标注待补；禁止把示例当作真实成果。','modality':'mandatory','scope':'global'});assert rule.status_code==200,rule.text
 package=client.post(f'/api/v1/projects/{p}/rule-packages');assert package.status_code==200,package.text
 s=client.post(f'/api/v1/projects/{p}/sections',json={'title':'规则加载机制演练','kind':'system_design'}).json()['id']
 body='此段仅验证程序流程。写作任务先读取规则包，再检索允许外发的资料。真实论文题目、实验结果和导师审核记录【待补证据】。'
 fake_response={'text':db.dumps({'paragraphs':[{'text':body,'source_ids':[],'reference_ids':[],'fact_ids':[]}],'missing_materials':['实际题目','真实实验结果','学校正式模板'],'notes':'自动化固定样本，不是实际论文'}),'usage':{},'cost':0,'remote_id':'local-fixture-no-network','request_hash':'local-fixture'}
 original_generate=provider.generate;provider.generate=lambda *a,**k:fake_response
 saved_config=provider.config();db.set_setting('provider',{**saved_config,'model':'local-fixture-no-network'})
 try:
  response=client.post(f'/api/v1/sections/{s}/writing-tasks',json={'base_revision_id':None,'operation':'generate'});assert response.status_code==200,response.text
  task=response.json();tasks.run_one();row=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],));assert row['status']=='succeeded',row['error']
  run=db.one('SELECT * FROM generation_runs WHERE task_id=?',(task['id'],));manifest=db.loads(run['manifest']);assert manifest['required_rule_ids']==manifest['loaded_rule_ids']
  accepted=client.post(f'/api/v1/sections/{s}/accept',json={'task_id':task['id'],'base_revision_id':None});assert accepted.status_code==200,accepted.text
  old=accepted.json()['id'];edit=client.put(f'/api/v1/sections/{s}/content',json={'base_revision_id':old,'document':documents.text_document(body+'\n人工修改流程由自动化操作模拟；此状态不代表用户完成验收。')});assert edit.status_code==200,edit.text
  stale=client.put(f'/api/v1/sections/{s}/content',json={'base_revision_id':old,'document':documents.text_document('旧版本不得覆盖当前修改')});assert stale.status_code==409
  export=exports.export_project(p,{'mode':'draft','pdf':True});folder=clone/'exports'/export['export_id'];report=json.loads((folder/'检查报告.json').read_text(encoding='utf-8'))
  archive=exports.backup();restored=exports.restore_backup((clone/'backups'/archive['filename']).read_bytes())
  write('workflow-rehearsal.json',{'passed':True,'scope':'synthetic_fixture_only','network_calls_for_workflow':0,'rule_manifest_complete':True,'candidate_acceptance_automated':True,'manual_review_simulated':True,'human_acceptance':False,'stale_revision_conflict_status':stale.status_code,'export':export,'export_folder':str(folder),'docx_property_checks':report['docx_property_checks'],'pdf_status':report.get('pdf_status'),'word_visual_acceptance':'not_available','backup':archive,'restore':restored,'actual_submission_accepted':False})
 finally:provider.generate=original_generate;db.set_setting('provider',saved_config)
connection=sqlite3.connect(original/'workbench.sqlite3');after=inventory(connection);connection.close();unchanged=before==after;write('original-integrity.json',{'source_records_unchanged':unchanged,'tables_checked':list(before),'credentials_copied':False,'human_confirmations_added_to_original':0});assert unchanged
show('Workflow, backup and restore completed on fixtures; actual source records unchanged.')
write('summary.json',{'output':str(out),'actual_inventory':{k:len(v) for k,v in before.items()},'retrieval_total':len(cases),'retrieval_hits':metrics['hits'],'retrieval_macro_recall_at_10':metrics['macro_recall_at_10'],'human_confirmed_cases':0,'ocr_samples':len(ocr),'rule_gate_blocked':blocked,'live_status':live.get('status','not_requested'),'original_records_unchanged':unchanged,'all_five_formally_accepted':False})
print(str(out),flush=True)
