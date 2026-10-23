const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),port=8783;
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const fixture=JSON.parse(fs.readFileSync(path.join(root,'.runtime/source-review-browser-fixture.json'),'utf8'));
 const session=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-'+port+'.json'),'utf8'));
 const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
 const page=await browser.newPage({viewport:{width:1440,height:1000},locale:'zh-CN'});page.setDefaultTimeout(12000);
 await page.addInitScript(pid=>localStorage.setItem('paper_project',pid),fixture.project_id);
 const errors=[],checks=[];let patches=0,paid=0,failNext=false,hold=null,release=null,documentPosts=0,failDocument=false,holdDocument=null,releaseDocument=null;
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&(r.url().includes('/summary')||r.url().includes('probe-generation')))paid++});
 await page.route('**/api/v1/pages/*',async route=>{if(route.request().method()!=='PATCH')return route.continue();patches++;if(failNext){failNext=false;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'模拟保存失败，核对未提交'})})}if(hold){const pending=hold;hold=null;await pending}return route.continue()});
 const confirm=()=>page.getByRole('button',{name:'确认本页原文已核对',exact:true});
 const complete=()=>page.locator('.source-review-complete');const select=()=>page.locator('.viewer-toolbar select');const draft=()=>page.getByLabel('补充原文转录或 OCR 纠错说明',{exact:false});
 async function open(){await page.getByRole('button',{name:'资料中心',exact:true}).click();await page.getByRole('button',{name:'原文核对状态验证.txt'}).click();await select().waitFor()}
 try{
  await page.goto('http://127.0.0.1:'+port+'/#token='+session.bootstrap_token,{waitUntil:'networkidle'});await open();await confirm().waitFor();assert.equal(await select().inputValue(),'0');
  hold=new Promise(resolve=>{release=resolve});await confirm().click();const saving=page.getByRole('button',{name:'正在保存核对…',exact:true});await saving.waitFor();assert(await saving.isDisabled());await saving.evaluate(el=>el.click());assert.equal(patches,1);release();
  await complete().waitFor();assert.equal(await confirm().count(),0);assert.equal(await select().inputValue(),'0');await page.getByTestId('parse-report').getByText('原文提取核对 1/3 页',{exact:false}).waitFor();checks.push('confirmation_replaced_by_completed_status_without_auto_advance','pending_request_disables_duplicate_submission','progress_updates_after_confirmation');
  await complete().scrollIntoViewIfNeeded();await page.screenshot({path:path.join(root,'docs/screenshots/source-review-completed.png')});
  await page.getByRole('button',{name:'核对下一页',exact:true}).click();await confirm().waitFor();assert.equal(await select().inputValue(),'1');checks.push('next_page_is_explicit_and_has_its_own_review_state');
  const text='补充核对记录-'+Date.now();await draft().fill(text);failNext=true;await confirm().click();await page.getByText('模拟保存失败，核对未提交',{exact:true}).waitFor();assert(await confirm().isEnabled());assert.equal(await draft().inputValue(),text);assert.equal(await complete().count(),0);checks.push('failed_save_keeps_pending_state_and_draft_for_retry');
  hold=new Promise(resolve=>{release=resolve});await confirm().click();await saving.waitFor();assert(await saving.isDisabled());await saving.evaluate(el=>el.click());release();await complete().waitFor();assert.equal(await select().inputValue(),'1');assert.equal(await draft().inputValue(),'');
  const response=await page.request.get('http://127.0.0.1:'+port+'/api/v1/pages/'+fixture.pages[1].id);const persisted=await response.json();assert.equal(persisted.review_state,'reviewed');assert.equal(persisted.text.split(text).length-1,1);checks.push('successful_retry_persists_transcript_exactly_once');
  await draft().fill('未提交的另一个补充');await page.getByRole('button',{name:'保存补充并确认核对',exact:true}).waitFor();await select().selectOption('2');await confirm().waitFor();assert.equal(await draft().inputValue(),'');await select().selectOption('1');await complete().waitFor();assert.equal(await draft().inputValue(),'');checks.push('reviewed_page_allows_new_supplement','draft_does_not_leak_across_pages');
  await select().selectOption('2');await confirm().click();await complete().waitFor();assert.equal(await select().inputValue(),'2');assert.equal(await page.getByRole('button',{name:'核对下一页',exact:true}).count(),0);await page.getByTestId('parse-report').getByText('原文提取核对 3/3 页',{exact:false}).waitFor();checks.push('last_page_stays_completed_and_all_pages_counted');
  await page.locator('.modal>header button').click();await page.getByRole('button',{name:'原文核对状态验证.txt'}).click();await complete().waitFor();assert.equal(await confirm().count(),0);checks.push('completed_state_restored_when_reopening_material');
  await page.reload({waitUntil:'networkidle'});await open();await complete().waitFor();assert.equal(await confirm().count(),0);checks.push('completed_state_survives_browser_reload');
  await page.setViewportSize({width:390,height:844});await complete().scrollIntoViewIfNeeded();assert.equal(await page.locator('.modal .actions').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);checks.push('mobile_completed_status_and_next_action_fit');

  const localSession=await page.request.post('http://127.0.0.1:'+port+'/api/v1/session',{data:{}});const headers={'X-CSRF-Token':(await localSession.json()).csrf};
  for(const item of fixture.pages.slice(1)){const reset=await page.request.patch('http://127.0.0.1:'+port+'/api/v1/pages/'+item.id,{headers,data:{review_state:'unreviewed'}});assert.equal(reset.status(),200)}
  await page.reload({waitUntil:'networkidle'});await page.setViewportSize({width:1440,height:1000});await open();
  const documentButton=()=>page.getByRole('button',{name:'确认整份文档原文已核对',exact:true});const documentDone=()=>page.locator('.document-review-complete');
  await documentButton().waitFor();await page.getByTestId('parse-report').getByText('剩余 2 页',{exact:false}).waitFor();
  await draft().fill('整份核对前未保存的补充');assert(await documentButton().isDisabled());await page.getByText('请先保存或清空当前页的补充内容',{exact:false}).waitFor();await draft().fill('');assert(await documentButton().isEnabled());checks.push('document_review_explains_scope_and_requires_saving_current_draft');
  await page.route('**/api/v1/files/'+fixture.file_id+'/review',async route=>{documentPosts++;if(failDocument){failDocument=false;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'模拟整份保存失败'})})}if(holdDocument){const pending=holdDocument;holdDocument=null;await pending}return route.continue()});
  failDocument=true;await documentButton().click();await page.getByText('模拟整份保存失败',{exact:true}).waitFor();assert(await documentButton().isEnabled());assert.equal(await documentDone().count(),0);checks.push('failed_document_review_remains_retryable');
  holdDocument=new Promise(resolve=>{releaseDocument=resolve});await documentButton().click();const savingDocument=page.getByRole('button',{name:'正在保存整份文档核对…',exact:true});await savingDocument.waitFor();assert(await savingDocument.isDisabled());await savingDocument.evaluate(el=>el.click());assert.equal(documentPosts,2);releaseDocument();
  await documentDone().waitFor();assert.equal(await documentButton().count(),0);await page.getByTestId('parse-report').getByText('原文提取核对 3/3 页',{exact:false}).waitFor();assert.equal(await select().inputValue(),'0');checks.push('document_review_updates_all_remaining_pages_and_completion_status','document_review_prevents_duplicate_submission');
  const allPages=await (await page.request.get('http://127.0.0.1:'+port+'/api/v1/files/'+fixture.file_id+'/pages')).json();assert(allPages.every(p=>p.review_state==='reviewed'));await documentDone().scrollIntoViewIfNeeded();await page.screenshot({path:path.join(root,'docs/screenshots/document-review-completed.png')});
  await page.reload({waitUntil:'networkidle'});await open();await documentDone().waitFor();assert.equal(await documentButton().count(),0);checks.push('document_review_completion_survives_reload');
  await page.setViewportSize({width:390,height:844});await documentDone().scrollIntoViewIfNeeded();assert.equal(await page.getByTestId('parse-report').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);checks.push('document_review_status_fits_mobile');
  assert.deepEqual(errors,[]);assert.equal(paid,0);assert.equal(patches,4);
  const report={checks,errors,patch_requests:patches,document_review_requests:documentPosts,upstream_generation_calls:paid,fixture:'isolated three-page synthetic document'};fs.writeFileSync(path.join(root,'docs/source-review-browser-results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
