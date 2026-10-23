const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..');process.env.PLAYWRIGHT_BROWSERS_PATH=path.join(root,'.runtime/browsers');
const {chromium}=require(path.join(root,'.runtime/browser/node_modules/playwright'));
(async()=>{
 const session=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8776.json'),'utf8'));
 const fixture=JSON.parse(fs.readFileSync(path.join(root,'.runtime/parsing-browser-fixture.json'),'utf8'));
 const browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1600,height:1100},locale:'zh-CN'});const page=await context.newPage();
 const errors=[],checks=[],external=[];let paid=0;const origin='http://127.0.0.1:8776';
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/writing-tasks')||r.url().includes('/probe-generation'))paid++;if(/^https?:/.test(r.url())&&new URL(r.url()).origin!==origin)external.push(new URL(r.url()).origin)});
 const auth=await context.request.post(origin+'/api/v1/session',{data:{token:session.bootstrap_token}});assert.equal(auth.status(),200);const csrf=(await auth.json()).csrf;
 async function req(route,body,method='GET'){const r=await context.request.fetch(origin+'/api/v1'+route,{method,headers:{'X-CSRF-Token':csrf},...(body===undefined?{}:{data:body})});assert.equal(r.status(),200,await r.text());return r.json()}
 async function waitTask(id){const end=Date.now()+120000;while(Date.now()<end){const t=await req('/tasks/'+id);if(t.status==='succeeded')return t.result;if(['failed','cancelled','outcome_unknown'].includes(t.status))throw new Error(t.error);await new Promise(r=>setTimeout(r,250))}throw new Error('Local parsing task timed out')}
 async function closeModal(){await page.locator('.modal .icon-button').first().click()}
 const shots=path.join(root,'docs/screenshots/parsing-v031');fs.mkdirSync(shots,{recursive:true});
 try{
 await page.goto(origin,{waitUntil:'networkidle'});await page.getByRole('button',{name:'资料中心',exact:true}).click();await page.getByTestId('material-health').waitFor();checks.push('material_coverage_summary_visible');
 const legacy=page.getByRole('row').filter({hasText:'旧版漏识别样例.pptx'});assert.ok((await legacy.textContent()).includes('待补图片识别'));checks.push('legacy_ready_file_is_not_shown_as_complete');
 const broken=page.getByRole('row').filter({hasText:'图片损坏样例.pptx'});assert.ok((await broken.textContent()).includes('解析有缺项'));checks.push('damaged_image_is_reported_as_partial');
 await page.screenshot({path:path.join(shots,'materials-before-repair.png'),fullPage:true});
 await legacy.getByRole('button').first().click();await page.getByTestId('parse-report').waitFor();assert.ok((await page.getByTestId('parse-report').textContent()).includes('待补图片识别'));checks.push('per_file_and_per_page_diagnostics_visible');
 const pending=page.waitForResponse(r=>r.url().endsWith('/files/'+fixture.legacy_file_id+'/reparse')&&r.request().method()==='POST');await page.getByRole('button',{name:'补解析 / 重试缺项',exact:true}).click();const response=await pending;assert.equal(response.status(),200);const task=await response.json();const result=await waitTask(task.id);assert.equal(result.quality.state,'extracted');checks.push('local_repair_button_runs_real_ocr_task');
 const repaired=await req('/files/'+fixture.legacy_file_id+'/pages');assert.equal(repaired[0].id,fixture.legacy_page_id);assert.ok(repaired[0].text.includes('不得漏读'));assert.equal(repaired[0].review_state,'unreviewed');checks.push('repair_preserves_page_id_and_recovers_embedded_text');
 await page.reload({waitUntil:'networkidle'});await page.getByRole('button',{name:'资料中心',exact:true}).click();await page.getByRole('row').filter({hasText:'旧版漏识别样例.pptx'}).getByRole('button').first().click();await page.locator('.source-content > pre').filter({hasText:'不得漏读'}).waitFor();checks.push('recovered_ocr_candidate_visible_in_source_viewer');await page.screenshot({path:path.join(shots,'source-after-repair.png'),fullPage:true});
 const source=await req('/pages/'+fixture.legacy_page_id);assert.ok(source.sources.some(s=>s.method==='windows_ocr'&&s.verified===0));checks.push('ocr_sources_are_not_automatically_human_verified');
 await closeModal();await page.getByRole('row').filter({hasText:'图片损坏样例.pptx'}).getByRole('button').first().click();await page.locator('.material-parse-errors').waitFor();checks.push('page_failure_reason_is_visible');await closeModal();
 const real=await req('/files/'+fixture.real_file_id+'/pages');assert.equal(real.length,4);const best=real.map((p,i)=>({p,i})).sort((a,b)=>b.p.text.length-a.p.text.length)[0];assert.ok(best.p.text.length>100);await page.getByRole('row').filter({hasText:fixture.real_name}).getByRole('button').first().click();await page.locator('.viewer-toolbar select').selectOption(String(best.i));await page.locator('.source-content > pre').filter({hasText:'本地OCR候选'}).waitFor();checks.push('actual_user_ppt_ocr_text_and_original_page_mapping');
 await closeModal();await page.getByRole('button',{name:'修复解析缺项',exact:true}).waitFor();checks.push('project_repair_action_available');
 await page.setViewportSize({width:1100,height:900});await page.screenshot({path:path.join(shots,'materials-1100.png'),fullPage:true});checks.push('materials_table_at_narrower_desktop_width');
 assert.equal(errors.length,0);assert.equal(paid,0);assert.equal(external.length,0);checks.push('no_page_errors_no_paid_generation_no_external_browser_requests');
 const report={passed:true,version:'0.3.1',check_count:checks.length,checks,page_errors:errors,paid_model_requests:paid,external_browser_requests:external.length,actual_windows_ocr:true,actual_user_ppt:true,original_page_id_preserved:true,human_review_fabricated:false,tested_at:new Date().toISOString()};fs.writeFileSync(path.join(root,'docs/parsing-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close()}
})().catch(e=>{console.error(e.message);process.exit(1)});
