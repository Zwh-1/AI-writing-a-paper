const fs=require('fs'),path=require('path'),assert=require('assert');
const pw=require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8765';let token='';
(async()=>{
 const session=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8'));token=session.bootstrap_token;
 const audit=JSON.parse(fs.readFileSync(path.join(root,'docs/rule-review-integrity-audit.json'),'utf8'));const pid=audit.projects[0].project_id;
 const browser=await pw.chromium.launch({headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1480,height:1080},locale:'zh-CN'}),page=await context.newPage(),errors=[],checks=[],intercepted=[];
  page.on('pageerror',e=>errors.push(e.message));
  const auth=await context.request.post(origin+'/api/v1/session',{data:{token}});assert.equal(auth.status(),200);
  await page.addInitScript(id=>localStorage.setItem('paper_project',id),pid);
  await page.goto(origin+'/#token='+encodeURIComponent(token));await page.getByRole('button',{name:'论文规则',exact:true}).click();
  const card=page.getByRole('region',{name:'自动规则审核'});await card.getByText(/历史响应回放发现/).waitFor();
  await card.getByText(/26 条历史正面结论/).waitFor();
  const selector=page.getByLabel('疑难规则审核模型');await selector.selectOption('gpt');assert.equal(await selector.inputValue(),'gpt');
  await page.route('**/api/v1/projects/*/rules/ai-extract',async route=>{
   const body=route.request().postDataJSON();intercepted.push(body);assert.equal(body.provider_profile,'gpt');assert.equal(body.allow_gpt,true);assert.equal(body.gpt_budget,body.budget_limit);
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({id:'intercepted-ui-test',kind:'rules_ai',status:'queued'})});
  });
  await card.getByRole('button',{name:'复核现有疑难条款（计费）',exact:true}).click();assert.equal(intercepted.length,1);
  await page.unroute('**/api/v1/projects/*/rules/ai-extract');await selector.selectOption('primary');
  checks.push('explicit_gpt_payload_intercepted_without_backend_submission','unsupported_exclusion_warning_visible','legacy_positive_evidence_staged_without_activation');
  const jobs=page.locator('.rule-jobs');await jobs.getByText(/本次请求模型/).first().waitFor();
  const history=jobs.getByRole('button',{name:/查看历史整理记录/});if(await history.count())await history.click();
  await jobs.getByText(/0.032175/).first().waitFor();assert((await jobs.innerText()).includes('deepseek-v4.1-flash-1017'));
  await jobs.getByText(/未通过事后证据校验|未通过证据回放校验/).first().waitFor();
  checks.push('actual_model_separate_from_reused_model','closed_task_billing_displayed','legacy_failed_evidence_marked');
  await card.screenshot({path:path.join(root,'docs/screenshots/review-integrity-desktop.png')});
  const close=jobs.getByRole('button',{name:'收起历史整理记录',exact:true});if(await close.count())await close.click();
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await card.screenshot({path:path.join(root,'docs/screenshots/review-integrity-mobile.png')});checks.push('mobile_layout');
  assert.equal(errors.length,0);const report={passed:true,checks,page_errors:errors,intercepted_paid_posts:intercepted.length,backend_paid_submissions:0};
  fs.writeFileSync(path.join(root,'docs/review-integrity-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close()}
})().catch(e=>{console.error(String(e.stack||e).split(token||'UNMATCHABLE_TOKEN').join('[REDACTED]'));process.exitCode=1});
