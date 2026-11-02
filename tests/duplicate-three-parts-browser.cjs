const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
 const errors=[],checks=[];
 async function connect(port){
  const context=await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN'}),origin=`http://127.0.0.1:${port}`;
  const token=JSON.parse(fs.readFileSync(path.join(root,`.runtime/session-${port}.json`),'utf8')).bootstrap_token;
  const auth=await context.request.post(origin+'/api/v1/session',{data:{token}});assert.equal(auth.status(),200);const csrf=(await auth.json()).csrf;
  async function req(route,data,method=data===undefined?'GET':'POST'){
   const response=await context.request.fetch(origin+'/api/v1'+route,{method,headers:{'X-CSRF-Token':csrf},...(data===undefined?{}:{data})});
   return response;
  }
  async function open(pid){const page=await context.newPage();page.setDefaultTimeout(45000);page.on('pageerror',error=>errors.push(error.message));
   await page.addInitScript(pid=>localStorage.setItem('paper_project',pid),pid);await page.goto(origin);await page.getByRole('button',{name:'论文规则',exact:true}).click();
   await page.locator('.rules-overview').getByRole('button',{name:/重复与冲突/}).click();return page;
  }
  return {context,origin,req,open};
 }
 try{
  const test=await connect(8794);
  const created=await test.req('/projects',{title:'重复资料三部分流程隔离验证',budget:0});assert.equal(created.status(),200);const pid=(await created.json()).id;
  const a=await (await test.req(`/projects/${pid}/rules`,{statement:'参考文献不少于20篇。',state:'reference_only',modality:'pending'})).json();
  const b=await (await test.req(`/projects/${pid}/rules`,{statement:'参考文献不少于20篇。',state:'reference_only',modality:'pending'})).json();
  const page=await test.open(pid);let group=page.locator('.duplicate-three-parts').first();await group.waitFor();
  for(const name of ['重复资料来源','重复资料审核','重复资料采用'])await group.getByRole('region',{name,exact:true}).waitFor();
  assert.equal(await group.getByRole('button',{name:'采用复核结果并归并',exact:true}).isEnabled(),false);
  let proofResponse=page.waitForResponse(r=>r.url().endsWith('/rules/duplicates/review')&&r.request().method()==='POST');
  await group.getByRole('button',{name:'复核当前来源与条款',exact:true}).click();let proof=await (await proofResponse).json();assert.equal(proof.ready,true);
  await group.getByText('程序复核通过',{exact:true}).waitFor();assert.equal(await group.getByRole('button',{name:'采用复核结果并归并',exact:true}).isEnabled(),true);
  assert.match(await group.getByRole('region',{name:'重复资料采用',exact:true}).innerText(),/仅供参考/);
  await group.screenshot({path:path.join(root,'.runtime/duplicate-three-parts-ready.png')});
  const adoptionResponse=page.waitForResponse(r=>r.url().endsWith('/rules/duplicates/adopt')&&r.request().method()==='POST');
  await group.getByRole('button',{name:'采用复核结果并归并',exact:true}).click();const adoption=await (await adoptionResponse).json();await group.waitFor({state:'detached'});
  let data=await (await test.req(`/projects/${pid}/rules`)).json();assert.equal(data.rules.filter(r=>r.state==='reference_only').length,1);assert.equal(data.rules.filter(r=>r.state==='rejected').length,1);
  assert.equal(data.rules.find(r=>r.id===proof.canonical_id).modality,'pending');
  await page.getByText(/最近采用与恢复记录（/).click();await page.getByText('查看本次来源、审核与采用依据',{exact:true}).click();
  await page.getByText(/程序复核 · 已采用/).waitFor();checks.push('three visible stages','adoption disabled before current review','reference state preserved','saved source and review receipt');
  const restoreResponse=page.waitForResponse(r=>r.url().endsWith(`/duplicates/${adoption.merge_id}/restore`));
  await page.getByRole('button',{name:'恢复本次归并',exact:true}).click();assert.equal((await restoreResponse).status(),200);
  const revoked=await test.req(`/projects/${pid}/rules/duplicates/adopt`,{review_id:proof.id,expected_fingerprint:proof.fingerprint});assert.equal(revoked.status(),409);
  group=page.locator('.duplicate-three-parts').first();await group.waitFor();
  proofResponse=page.waitForResponse(r=>r.url().endsWith('/rules/duplicates/review')&&r.request().method()==='POST');
  await group.getByRole('button',{name:'复核当前来源与条款',exact:true}).click();const fresh=await (await proofResponse).json();assert.notEqual(fresh.id,proof.id);assert.equal(fresh.ready,true);
  const changed=await test.req(`/rules/${b.id}`,{decision_note:'审核后发生的修改'},'PATCH');assert.equal(changed.status(),200);
  const staleResponse=page.waitForResponse(r=>r.url().endsWith('/rules/duplicates/adopt')&&r.request().method()==='POST');
  await group.getByRole('button',{name:'采用复核结果并归并',exact:true}).click();assert.equal((await staleResponse).status(),409);
  data=await (await test.req(`/projects/${pid}/rules`)).json();assert.equal(data.rules.filter(r=>r.state==='reference_only').length,2);
  checks.push('restored receipt cannot be replayed','stale review cannot be adopted');
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await group.screenshot({path:path.join(root,'.runtime/duplicate-three-parts-mobile.png')});checks.push('mobile layout');
  const actual=await connect(8765),actualPid='bcc2b1c6944946508906ee6e0ecaa80a';
  const audit=JSON.parse(fs.readFileSync(path.join(root,'.runtime/duplicate-three-parts-apply.json'),'utf8'));
  const live=await (await actual.req(`/projects/${actualPid}/rules`)).json();
  assert.equal(live.pending_diagnostics.total,367);assert.equal(live.readiness.publishable_count,140);assert.equal(live.package.version,33);
  assert.equal(live.readiness.dirty,false);assert.equal(live.duplicates.length,audit.duplicate_groups_remaining);
  for(const adoption of audit.adoptions){
   const receipt=await (await actual.req(`/projects/${actualPid}/rules/duplicates/reviews/${adoption.review_id}`)).json();
   assert.equal(receipt.status,'applied');assert.equal(receipt.human_approval,false);assert.equal(receipt.adoption_preview.canonical_state_after,'reference_only');
   assert.ok(receipt.sources.length>=2);assert.ok(receipt.adoption_preview.distinct_source_occurrences<=receipt.adoption_preview.retained_rule_occurrences);
  }
  const livePage=await actual.open(actualPid);const liveGroup=livePage.locator('.duplicate-three-parts').first();await liveGroup.waitFor();
  await liveGroup.getByRole('button',{name:'复核当前来源与条款',exact:true}).click();await liveGroup.getByText('复核未通过，继续保留独立记录',{exact:true}).waitFor();
  assert.equal(await liveGroup.getByRole('button',{name:'采用复核结果并归并',exact:true}).isEnabled(),false);
  await liveGroup.screenshot({path:path.join(root,'.runtime/duplicate-three-parts-live-blocked.png')});
  checks.push('live adopted source receipts intact','unresolved live sources stay blocked','140 effective requirements and V33 preserved');
  assert.deepEqual(errors,[]);
  const report={checks,errors,fixtureProject:pid,live:{reviewed:audit.source_review_count,adoptedGroups:audit.adopted_groups,mergedRecords:audit.merged_records,pending:live.pending_diagnostics.total,remainingDuplicateCandidates:live.duplicates.length,overlapGroups:live.text_overlaps.length}};
  fs.writeFileSync(path.join(root,'.runtime/duplicate-three-parts-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
