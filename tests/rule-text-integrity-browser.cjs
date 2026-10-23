const fs=require('fs'),path=require('path'),assert=require('assert');
const pw=require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8765';let token='';
const folded=value=>value.replace(/\s/g,'');
(async()=>{
 token=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8')).bootstrap_token;
 const pid='bcc2b1c6944946508906ee6e0ecaa80a',browser=await pw.chromium.launch({headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1480,height:1080},locale:'zh-CN'}),page=await context.newPage(),errors=[],checks=[],blocked=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/v1/projects/*/rules/ai-extract',route=>{blocked.push(route.request().url());return route.abort()});
  assert.equal((await context.request.post(origin+'/api/v1/session',{data:{token}})).status(),200);
  const result=await context.request.get(origin+'/api/v1/projects/'+pid+'/rules');assert.equal(result.status(),200);const data=await result.json();
  const keywords=data.rules.filter(r=>r.statement.includes('最能表达主要内容')&&r.statement.includes('中、英文摘要后'));
  assert.equal(keywords.length,5);assert(keywords.every(r=>r.statement.includes('3～5')&&!r.statement.endsWith('同')&&!r.text_integrity.needs_attention));checks.push('all_five_keyword_records_complete_and_range_corrected');
  const exception=data.rules.find(r=>r.id==='f40c4567e2344211897a7acbfe39c54b');assert(exception.statement.includes('除非')&&exception.state==='candidate');checks.push('omitted_exception_restored_without_auto_approval');
  await page.addInitScript(id=>localStorage.setItem('paper_project',id),pid);
  await page.goto(origin+'/#token='+encodeURIComponent(token));await page.getByRole('button',{name:'论文规则',exact:true}).click();
  await page.getByText(/已按原文补全 40 条残缺条款/).waitFor();await page.getByText(/旧规则包含 20 条已撤回/).waitFor();checks.push('batch_repair_and_old_package_warning_visible');
  const group=page.locator('.rule-duplicate-group').filter({hasText:'最能表达主要内容'});await group.first().getByRole('button',{name:'核对条款',exact:true}).first().click();
  const dialog=page.getByRole('dialog',{name:'核对规则条款'}),statement=await dialog.getByLabel('具体要求',{exact:true}).inputValue();
  assert(folded(statement).endsWith('同时有中、英文对照，分别附于中、英文摘要后。'));assert(statement.includes('3～5'));checks.push('editor_contains_full_statement_without_display_truncation');
  await dialog.getByText('完整原文与修复记录',{exact:true}).click();const evidence=await dialog.getByLabel('完整原文上下文',{exact:true}).inputValue();assert.equal(folded(evidence),folded(statement));
  await dialog.getByText(/已按原图校正 OCR/).waitFor();await dialog.getByText(/旧片段：/).waitFor();checks.push('full_evidence_visual_correction_and_original_fragment_visible');
  await dialog.screenshot({path:path.join(root,'docs/screenshots/rule-text-integrity-desktop.png')});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await dialog.screenshot({path:path.join(root,'docs/screenshots/rule-text-integrity-mobile.png')});checks.push('mobile_layout');
  assert.equal(errors.length,0);assert.equal(blocked.length,0);
  const report={passed:true,checks,page_errors:errors,backend_paid_submissions:0};fs.writeFileSync(path.join(root,'docs/rule-text-integrity-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close()}
})().catch(e=>{console.error(String(e.stack||e).split(token||'UNMATCHABLE_TOKEN').join('[REDACTED]'));process.exitCode=1});
