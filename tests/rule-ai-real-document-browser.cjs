const fs=require('fs'),path=require('path'),assert=require('assert');
const pw=require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8765';
 const session=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8'));
 const before=JSON.parse(fs.readFileSync(path.join(root,'.runtime/rule-ai-v2-before.json'),'utf8'));
 const {task_id:tid}=JSON.parse(fs.readFileSync(path.join(root,'.runtime/rule-ai-v2-live-task.json'),'utf8'));
 const browser=await pw.chromium.launch({headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1480,height:1100},locale:'zh-CN'}),page=await context.newPage(),errors=[],checks=[];
  page.on('pageerror',e=>errors.push(e.message));
  const auth=await context.request.post(origin+'/api/v1/session',{data:{token:session.bootstrap_token}});assert.equal(auth.status(),200);
  await page.addInitScript(id=>localStorage.setItem('paper_project',id),before.project_id);
  await page.goto(origin+'/#token='+encodeURIComponent(session.bootstrap_token));
  await page.getByRole('button',{name:'论文规则',exact:true}).click();
  await page.getByRole('tab',{name:'整理与审核',exact:true}).click();
  await page.getByText('高级整理与独立审核工具',{exact:true}).click();
  await page.getByLabel('选择AI整理的规则文件').selectOption('4682795ddce646d4897bbf922298cee1');
  await page.getByText(/共 36 个可整理来源片段/).waitFor();
  const jobs=page.locator('.rule-jobs'),panel=page.locator('.rule-extraction-panel');
  await jobs.getByRole('button',{name:/查看历史整理记录/}).waitFor();
  assert(!(await jobs.innerText()).includes('HTTP 403'));
  assert(!(await jobs.innerText()).includes('HTTP 503'));
  checks.push('current_task_separate_from_past_errors');
  await jobs.getByRole('button',{name:/查看历史整理记录/}).click();
  await jobs.getByText(/历史任务/).first().waitFor();
  assert((await jobs.innerText()).includes('HTTP 403'));
  await jobs.getByRole('button',{name:'收起历史整理记录',exact:true}).click();
  checks.push('history_is_preserved_and_can_be_expanded');
  let task,last='',previous=0;const observations=[],deadline=Date.now()+900000;
  while(Date.now()<deadline){
   const response=await context.request.get(origin+'/api/v1/tasks/'+tid);assert.equal(response.status(),200);task=await response.json();
   assert(task.progress>=previous);previous=task.progress;
   const key=task.status+':'+task.phase;
   if(key!==last){const update={status:task.status,phase:task.phase,progress:task.progress,processed:task.result?.processed_source_units,added:task.result?.added};observations.push(update);console.log(JSON.stringify(update));last=key;}
   if(!['queued','running'].includes(task.status))break;
   await page.waitForTimeout(2000);
  }
  assert.equal(task.status,'succeeded',task.error||task.status);
  assert.equal(task.result.processed_source_units,36);
  assert.equal(task.result.completed_batches,task.result.total_batches);
  assert(task.result.added>0);
  await jobs.getByText(/AI 规则整理\s*·\s*已完成/).waitFor({timeout:10000});
  await jobs.getByText(/已处理 36\/36 个片段/).waitFor();
  assert(!(await jobs.innerText()).includes('HTTP 403')&&!(await jobs.innerText()).includes('HTTP 503'));
  await panel.screenshot({path:path.join(root,'.runtime/rule-ai-v2-completed-desktop.png')});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await panel.screenshot({path:path.join(root,'.runtime/rule-ai-v2-completed-mobile.png')});
  checks.push('real_document_all_36_sources_completed','saved_rule_count_visible','monotonic_progress','desktop_and_mobile_layout');
  assert.equal(errors.length,0);
  const report={passed:true,task_id:tid,result:task.result,checks,page_errors:errors,observations};
  fs.writeFileSync(path.join(root,'docs/rule-ai-v2-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({passed:true,result:task.result,checks}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
