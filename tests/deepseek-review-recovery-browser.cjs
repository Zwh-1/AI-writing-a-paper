const fs=require('fs'),path=require('path'),assert=require('assert');
const pw=require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8765';let token='';
(async()=>{
 const session=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8'));token=session.bootstrap_token;
 const audit=JSON.parse(fs.readFileSync(path.join(root,'docs/rule-review-integrity-audit.json'),'utf8')),pid=audit.projects[0].project_id;
 const pause='DeepSeek 连续只返回推理用量，没有可用审核正文；已在本次请求关闭推理，接口仍未返回正文，已暂停以避免继续消耗费用。';
 const task={id:'mock-deepseek-recovery',project_id:pid,kind:'rules_ai',status:'incomplete',created_at:new Date().toISOString(),payload:{automatic:true,review_existing_only:true,provider_profile:'primary'},result:{automatic:true,review_only:true,review:{semantic_checked:10,semantic_total:77,accepted_count:10,reference_count:0,uncertain_count:0,unreviewed_count:67,reused_decisions:0,recovery_requests:2,pause_reason:pause}}};
 const browser=await pw.chromium.launch({headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1480,height:1080},locale:'zh-CN'}),page=await context.newPage(),errors=[],checks=[],paid=[];
  page.on('pageerror',e=>errors.push(e.message));
  const auth=await context.request.post(origin+'/api/v1/session',{data:{token}});assert.equal(auth.status(),200);
  await page.addInitScript(id=>localStorage.setItem('paper_project',id),pid);
  await page.route('**/api/v1/tasks?project_id=*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify([task])}));
  await page.route('**/api/v1/projects/*/rules/ai-extract',async route=>{
   const body=route.request().postDataJSON();paid.push(body);assert.equal(body.provider_profile,'primary');assert.equal(body.allow_gpt,false);assert.equal(body.gpt_budget,0);
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({id:'mock-submission',kind:'rules_ai',status:'queued'})});
  });
  await page.goto(origin+'/#token='+encodeURIComponent(token));await page.getByRole('button',{name:'论文规则',exact:true}).click();
  await page.getByRole('tab',{name:'整理与审核',exact:true}).click();
  await page.getByText('高级整理与独立审核工具',{exact:true}).click();
  const card=page.getByRole('region',{name:'自动规则审核'}),jobs=page.locator('.rule-jobs');
  assert.equal(await page.getByLabel('疑难规则审核模型').inputValue(),'primary');checks.push('primary_model_selected_by_default');
  await jobs.getByText(pause,{exact:true}).waitFor();await jobs.getByText(/缩小批次补齐 2 次/).waitFor();await jobs.getByText(/仍有 67 条没有有效审核结论/).waitFor();
  checks.push('specific_pause_reason','bounded_recovery_count','unreviewed_rules_visible');
  await card.getByRole('button',{name:'复核现有疑难条款（计费）',exact:true}).click();assert.equal(paid.length,1);checks.push('deepseek_payload_intercepted_with_gpt_disabled');
  await jobs.screenshot({path:path.join(root,'docs/screenshots/deepseek-review-recovery-desktop.png')});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await jobs.screenshot({path:path.join(root,'docs/screenshots/deepseek-review-recovery-mobile.png')});checks.push('mobile_layout');
  assert.equal(errors.length,0);const report={passed:true,checks,page_errors:errors,mocked_task_records:1,intercepted_paid_posts:paid.length,backend_paid_submissions:0};
  fs.writeFileSync(path.join(root,'docs/deepseek-review-recovery-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close()}
})().catch(e=>{console.error(String(e.stack||e).split(token||'UNMATCHABLE_TOKEN').join('[REDACTED]'));process.exitCode=1});
