const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8765',pid='bcc2b1c6944946508906ee6e0ecaa80a';
(async()=>{
 const reports=['pending-round2','pending-round2b','pending-round2c'].map(name=>JSON.parse(fs.readFileSync(path.join(root,`.runtime/${name}-apply.json`),'utf8')));
 const fixed=reports.flatMap(r=>r.fixed_rule_ids),unresolved=reports.flatMap(r=>r.pending_rule_ids);
 assert.equal(fixed.length,205);assert.equal(unresolved.length,30);
 const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN'});
  const token=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8')).bootstrap_token;
  assert.equal((await context.request.post(origin+'/api/v1/session',{data:{token}})).status(),200);
  const data=await (await context.request.get(`${origin}/api/v1/projects/${pid}/rules`)).json();
  const autopilot=await (await context.request.get(`${origin}/api/v1/projects/${pid}/ai-autopilot`)).json();
  assert.equal(data.pending_diagnostics.total,160);assert.equal(autopilot.exception_count,160);
  assert.equal(data.readiness.publishable_count,140);assert.equal(data.package.version,33);assert.equal(data.readiness.dirty,false);
  for(const id of fixed){assert.equal(data.rules.find(r=>r.id===id).state,'reference_only');assert.ok(!autopilot.exceptions.some(r=>r.id===id));}
  for(const id of unresolved){assert.equal(data.rules.find(r=>r.id===id).state,'candidate');assert.ok(autopilot.exceptions.some(r=>r.id===id));}
  const checks=['205 individually mapped candidates resolved','30 inspected incomplete candidates remain pending','160 total pending remain visible','140 effective rules and V33 preserved'];
  const errors=[],page=await context.newPage();page.setDefaultTimeout(45000);page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(pid=>localStorage.setItem('paper_project',pid),pid);await page.goto(origin);
  await page.getByRole('button',{name:'论文规则',exact:true}).click();
  await page.getByRole('tab',{name:'整理与审核',exact:true}).click();
  const exceptions=page.locator('.autopilot-exceptions');await exceptions.locator(':scope > summary').click();
  await exceptions.getByRole('button',{name:/文字识别\s*160/}).waitFor();
  const redacted=reports.flatMap(r=>r.results).find(r=>r.n===15).result.pending_rule_ids[0];
  await exceptions.getByRole('textbox',{name:'搜索处理例外',exact:true}).fill('原图姓名、学号');
  const row=exceptions.locator(`[data-exception-id="${redacted}"]`);await row.waitFor();
  assert.match(await row.innerText(),/打码/);await row.getByRole('button',{name:'查看原图与识别内容',exact:true}).click();
  let dialog=page.getByRole('dialog');await dialog.waitFor();
  await dialog.getByText('完整图像观察与识别覆盖',{exact:true}).click();
  await dialog.locator('[data-asset-sha="2e7af19b0949c483631621ae05abbb336135a5734589381d0481909d4f16d0e1"]').click();
  const reading=dialog.getByRole('region',{name:'完整图像观察',exact:true});
  await reading.getByText('完整保存的OCR观察 · 尚未核对',{exact:true}).waitFor();
  const apiRead=await(await context.request.get(`${origin}/api/v1/projects/${pid}/document-reading/documents/${data.rules.find(r=>r.id===redacted).source_file_id}/assets/2e7af19b0949c483631621ae05abbb336135a5734589381d0481909d4f16d0e1/read?page=25&intent=guidance`)).json();
  assert.equal(apiRead.entire_image_compared,false);assert.ok(apiRead.verified_passages.length>=2);
  for(const passage of apiRead.verified_passages)assert.ok((await reading.textContent()).includes(passage.text));
  await reading.screenshot({path:path.join(root,'.runtime/pending-round2-reader.png')});
  checks.push('redacted data blocker visible','old and new partial passages remain side by side','original OCR preserved');
  await dialog.getByRole('button',{name:'关闭原文依据',exact:true}).click();
  await exceptions.screenshot({path:path.join(root,'.runtime/pending-round2-exceptions.png')});
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await exceptions.getByRole('textbox',{name:'搜索处理例外',exact:true}).fill('打码');
  await row.waitFor();await row.screenshot({path:path.join(root,'.runtime/pending-round2-mobile.png')});
  checks.push('specific reasons searchable','mobile layout');
  assert.deepEqual(errors,[]);
  const result={checks,errors,corrected:205,reviewedPending:30,remainingPending:160,effective:140,packageVersion:33};
  fs.writeFileSync(path.join(root,'.runtime/pending-round2-browser-report.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
