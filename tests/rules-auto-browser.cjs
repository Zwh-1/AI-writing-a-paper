const fs=require('fs'),path=require('path'),assert=require('assert');
const pw=require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8765';
let token='';
(async()=>{
 const session=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8'));token=session.bootstrap_token;
 const migration=JSON.parse(fs.readFileSync(path.join(root,'docs/rules-auto-migration-report.json'),'utf8'));
 const pid=migration.projects[0].project_id;
 const browser=await pw.chromium.launch({headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1480,height:1050},locale:'zh-CN'}),page=await context.newPage(),errors=[],checks=[],paid=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()==='POST'&&/ai-extract|\/retry|provider.*probe/.test(r.url()))paid.push(r.url())});
  const auth=await context.request.post(origin+'/api/v1/session',{data:{token}});assert.equal(auth.status(),200);
  await page.addInitScript(id=>localStorage.setItem('paper_project',id),pid);
  await page.goto(origin+'/#token='+encodeURIComponent(token));
  await page.getByRole('button',{name:'论文规则',exact:true}).click();
  const mode=page.getByLabel('规则整理处理方式');await mode.waitFor();assert.equal(await mode.inputValue(),'automatic');
  assert.equal(await page.getByLabel('选择AI整理的规则文件').inputValue(),'__all__');
  const panel=page.locator('.rule-extraction-panel');await panel.getByText(/共 1068 个可整理来源片段/).waitFor();
  assert((await panel.innerText()).includes('已核对')&&(await panel.innerText()).includes('待对账占用'));
  assert(await panel.getByRole('button',{name:'自动整理全部材料',exact:true}).isEnabled());
  assert.equal(await page.getByRole('tab',{name:/机器审核通过/}).count(),1);
  checks.push('all_materials_selected','complete_scope_1068_visible','budget_components_visible','automatic_start_enabled','machine_review_filter_present');
  await mode.selectOption('manual');assert(await page.getByLabel('本次来源片段上限').isVisible());
  await mode.selectOption('automatic');assert.equal(await page.getByLabel('本次来源片段上限').count(),0);checks.push('advanced_range_is_optional');
  await panel.screenshot({path:path.join(root,'docs/screenshots/rules-auto-desktop.png')});
  await page.setViewportSize({width:390,height:844});
  await panel.screenshot({path:path.join(root,'docs/screenshots/rules-auto-mobile.png')});checks.push('desktop_and_mobile_layout');
  const overflow=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,nodes:[...document.querySelectorAll('body *')].map(e=>({tag:e.tagName,cls:String(e.className),right:Math.round(e.getBoundingClientRect().right),width:Math.round(e.getBoundingClientRect().width)})).filter(e=>e.right>innerWidth+1).sort((a,b)=>b.right-a.right).slice(0,12)}));
  if(overflow.scroll>overflow.width)console.log(JSON.stringify({overflow}));
  assert(overflow.scroll<=overflow.width);
  const jobText=await panel.locator('.rule-jobs > div > .grow').first().boundingBox();assert(!jobText||jobText.width>=200,'mobile task text must have a readable column');
  const review=page.getByRole('region',{name:'自动规则审核'});assert(await review.getByRole('button',{name:'自动审核并应用可用规则'}).count());
  assert.equal(paid.length,0);assert.equal(errors.length,0);
  const report={passed:true,project_id:pid,checks,page_errors:errors,paid_requests:paid};
  fs.writeFileSync(path.join(root,'docs/rules-auto-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close()}
})().catch(e=>{console.error(String(e.stack||e).split(token||'UNMATCHABLE_TOKEN').join('[REDACTED]'));process.exitCode=1});
