const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8765',pid='bcc2b1c6944946508906ee6e0ecaa80a';
(async()=>{
 const applied=JSON.parse(fs.readFileSync(path.join(root,'.runtime/scoped-rules-apply.json'),'utf8'));
 const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN'});
  const token=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8')).bootstrap_token;
  assert.equal((await context.request.post(origin+'/api/v1/session',{data:{token}})).status(),200);
  const get=async url=>{const r=await context.request.get(origin+'/api/v1'+url,{timeout:120000});assert.equal(r.status(),200,url);return r.json();};
  const data=await get(`/projects/${pid}/rules`),status=await get(`/projects/${pid}/rules/rebuild`),history=await get(`/projects/${pid}/rules/rebuild/history`);
  assert.equal(data.package.version,34);assert.equal(JSON.parse(data.package.rules).length,241);
  assert.equal(data.readiness.publishable_count,241);assert.equal(data.readiness.dirty,false);assert.equal(data.pending_diagnostics.total,160);
  assert.equal(status.current_package.version,34);assert.equal(status.current_package.rule_count,241);
  assert.equal(status.generation.rule_package.version,33);assert.equal(status.scope_review.added_guidance,142);assert.equal(status.scope_review.removed_from_package,41);
  const old=history.find(p=>p.version===33);assert.equal(old.rule_count,140);
  for(const id of ['41430c25c30345f6b35f5cdbb90a8231','f8942ce435b340f09cf93b5fc3792636','c21a67264fbf4007a448c16944ef3d1e']){
   const r=data.rules.find(r=>r.id===id);assert.equal(r.state,'machine_verified');assert.ok(r.conditions.scope_note);
  }
  for(const id of applied.removed_rule_ids)assert.equal(data.rules.find(r=>r.id===id).state,'reference_only');
  const errors=[],page=await context.newPage();page.setDefaultTimeout(45000);page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(pid=>localStorage.setItem('paper_project',pid),pid);await page.goto(origin);
  await page.getByRole('button',{name:'论文规则',exact:true}).click();
  const panel=page.getByRole('region',{name:'按原件重新生成规则',exact:true});
  await panel.getByText(/当前生效规则包 V34.*241/).waitFor();
  await panel.getByText(/补入建议 142 条/).waitFor();
  await panel.getByText(/该次重建生成规则包 V33/).waitFor();
  await panel.locator('details > summary').click();
  await panel.getByRole('button',{name:'查看 V33 · 140 条',exact:true}).click();
  let dialog=page.getByRole('dialog');await dialog.getByRole('heading',{name:'规则包 V33',exact:true}).waitFor();
  await dialog.getByRole('button',{name:'关闭原文依据',exact:true}).click();
  await panel.getByRole('button',{name:'查看 V34 · 241 条',exact:true}).click();
  dialog=page.getByRole('dialog');await dialog.getByRole('heading',{name:'规则包 V34',exact:true}).waitFor();
  await dialog.getByRole('button',{name:'关闭原文依据',exact:true}).click();
  await panel.evaluate(el=>el.scrollIntoView({block:'start'}));
  await panel.screenshot({path:path.join(root,'.runtime/scoped-rules-current-package.png')});
  await page.getByRole('tab',{name:'条款管理',exact:true}).click();
  const catalog=page.getByRole('region',{name:'规则条款管理',exact:true});
  await catalog.getByRole('textbox',{name:'搜索规则',exact:true}).fill('模块名称全文统一');
  const row=catalog.locator('[data-rule-id="c21a67264fbf4007a448c16944ef3d1e"]');await row.waitFor();
  assert.match(await row.innerText(),/系统设计 \/ 系统实现/);
  assert.match(await row.innerText(),/跨需求、设计、实现核对同一模块名称/);
  await row.locator('.rule-source-link').click();
  dialog=page.getByRole('dialog',{name:'规则原文依据',exact:true});
  await dialog.getByText(/模块名称在第三章、第四章、第五章中必须完全一致/).first().waitFor();
  await dialog.getByRole('button',{name:'关闭原文依据',exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  const layout=await row.evaluate(el=>({content:el.scrollWidth,width:el.clientWidth}));
  assert.ok(layout.content<=layout.width+2,JSON.stringify(layout));
  await row.screenshot({path:path.join(root,'.runtime/scoped-rules-mobile.png')});
  assert.deepEqual(errors,[]);
  const report={packageVersion:34,ruleCount:241,addedGuidance:142,removedFromPackage:41,pendingCandidates:160,
   currentPackageSeparateFromOldGeneration:true,oldV33SnapshotPreserved:true,chapterPredicateVisible:true,originalSourceOpens:true,mobileNoHorizontalOverflow:true,errors};
  fs.writeFileSync(path.join(root,'.runtime/scoped-rules-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
