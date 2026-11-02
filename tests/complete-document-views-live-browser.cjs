const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8765',pid='bcc2b1c6944946508906ee6e0ecaa80a';
(async()=>{
 const verification=JSON.parse(fs.readFileSync(path.join(root,'.runtime/complete-views-apply.json'),'utf8'));
 const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN'});
  const token=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8')).bootstrap_token;
  const auth=await context.request.post(origin+'/api/v1/session',{data:{token}});assert.equal(auth.status(),200);const csrf=(await auth.json()).csrf;
  const response=await context.request.get(`${origin}/api/v1/projects/${pid}/rules`);assert.equal(response.status(),200);const data=await response.json();
  assert.equal(data.pending_diagnostics.total,verification.pending.total);assert.ok(data.pending_diagnostics.total>0);
  assert.equal(data.duplicates.length,verification.duplicate_groups);assert.ok(data.text_overlaps.length>0);
  assert.ok(data.text_overlaps.every(g=>!g.mergeable));
  const overlap=data.text_overlaps[0];
  const merged=await context.request.post(`${origin}/api/v1/projects/${pid}/rules/duplicates/merge`,{headers:{'X-CSRF-Token':csrf},data:{canonical_id:overlap.rules[0].id,duplicate_ids:[overlap.rules[1].id]}});
  assert.equal(merged.status(),409,'An overlap cannot be discarded as a verified duplicate');
  const rule=data.rules.find(r=>r.state==='candidate'&&r.supporting_excerpt&&r.statement.length>500&&r.supporting_excerpt.text.length<300);
  assert.ok(rule);const sha=rule.supporting_excerpt.asset_sha256;
  const readResponse=await context.request.get(`${origin}/api/v1/projects/${pid}/document-reading/documents/${rule.source_file_id}/assets/${sha}/read?page=${rule.page_number}&intent=guidance`);
  assert.equal(readResponse.status(),200);const read=await readResponse.json();assert.equal(read.status,'read');
  assert.ok(read.raw_observations.length);const raw=read.raw_observations.map(o=>o.text).join('\n\n');
  assert.ok(raw.length>rule.supporting_excerpt.text.length);assert.ok(read.raw_observations.every(o=>o.cannot_support_claims));
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(45000);
  await page.addInitScript(pid=>localStorage.setItem('paper_project',pid),pid);await page.goto(origin);
  await page.getByRole('button',{name:'论文规则',exact:true}).click();
  const overview=page.locator('section[aria-label="规则概况与发布状态"]');await overview.getByText('规则包 V33',{exact:true}).waitFor();
  assert.match(await overview.innerText(),/资料识别（按原图统计）/);assert.match(await overview.innerText(),/部分对照/);
  await overview.screenshot({path:path.join(root,'.runtime/complete-views-overview.png')});
  await overview.getByRole('button',{name:/待确认 \/ 分类/}).click();
  const search=rule.statement.split(/\r?\n/).filter(s=>s.length>20).sort((a,b)=>b.length-a.length)[0].slice(0,30);
  await page.getByRole('textbox',{name:'搜索规则',exact:true}).fill(search);
  const row=page.locator(`[data-rule-id="${rule.id}"]`);await row.waitFor();await row.locator('.rule-source-link').first().click();
  const dialog=page.getByRole('dialog');await dialog.waitFor();
  await dialog.getByText('完整图像观察与识别覆盖',{exact:true}).click();
  await dialog.locator(`[data-asset-sha="${sha}"]`).click();
  const reading=dialog.locator('section[aria-label="完整图像观察"]');await reading.getByText('完整保存的OCR观察 · 尚未核对',{exact:true}).first().waitFor();
  const rendered=await reading.textContent();assert.ok(rendered.includes(read.raw_observations[0].text.slice(-80)),'The original tail is displayed');
  assert.ok(rendered.includes(rule.supporting_excerpt.text),'The checked excerpt is displayed in parallel');
  await reading.screenshot({path:path.join(root,'.runtime/complete-views-reader.png')});
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await dialog.getByRole('button',{name:'关闭原文依据',exact:true}).click();await overview.screenshot({path:path.join(root,'.runtime/complete-views-mobile.png')});
  assert.deepEqual(errors,[]);
  const report={pending:data.pending_diagnostics.total,safeDuplicateGroups:data.duplicates.length,overlapGroups:data.text_overlaps.length,
   rawCharacters:raw.length,parallelExcerptCharacters:rule.supporting_excerpt.text.length,checks:['Full source tail retained','Checked excerpts are parallel','Explicit recognition coverage','Overlap merge rejected','Distinct subjects retained','Mobile source reader'],errors};
  fs.writeFileSync(path.join(root,'.runtime/complete-views-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
