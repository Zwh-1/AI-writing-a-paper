const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),port=process.env.PAPER_TEST_PORT||8765,origin=`http://127.0.0.1:${port}`;
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
let token='';
(async()=>{
 token=JSON.parse(fs.readFileSync(path.join(root,`.runtime/session-${port}.json`),'utf8')).bootstrap_token;
 const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
 try{
  const context=await browser.newContext({viewport:{width:1480,height:1080},locale:'zh-CN'});
  assert.equal((await context.request.post(origin+'/api/v1/session',{data:{token}})).status(),200);
  const pid=(await(await context.request.get(origin+'/api/v1/projects')).json())[0].id;
  const rules=await(await context.request.get(origin+`/api/v1/projects/${pid}/rules`)).json();
  const rate=(total,covered,done)=>({total,ai_covered:covered,automatic_completed:done,ai_percent:covered/total*100,automatic_percent:done/total*100,target_met:covered/total>.95&&done/total>.95,minimum_for_target:Math.floor(total*.95)+1});
  const fixture={policy:{enabled:false,auto_publish:true,target_percent:95},materials:rate(25,25,0),rules:rate(686,0,0),extraction:rate(1068,1068,1068),tasks:[],target_met:false,exceptions:[],exception_count:0,budget:{limit:20,available:8.61},model_ready:true,note:'合成浏览器夹具：按有效AI结果计数，不把人工确认当作AI覆盖。'};
  const page=await context.newPage(),errors=[],submissions=[],unexpected=[],checks=[];page.setDefaultTimeout(20000);
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/v1/**',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(req.method()==='GET'&&url.pathname===`/api/v1/projects/${pid}/rules`)return route.fulfill({json:rules});
   if(url.pathname.endsWith('/ai-autopilot')){
    if(req.method()==='POST'){
     submissions.push(req.postDataJSON());fixture.policy={...fixture.policy,enabled:true,budget_limit:8.61};fixture.tasks=[{id:'mock-autopilot',status:'running',phase:'AI正在逐条判定用途',progress:40,result:{}}];
     return route.fulfill({json:fixture.tasks[0]});
    }
    if(req.method()==='PUT'){fixture.policy.enabled=req.postDataJSON().enabled;return route.fulfill({json:fixture.policy})}
    return route.fulfill({json:fixture});
   }
   if(!['GET','HEAD','OPTIONS'].includes(req.method())&&!url.pathname.endsWith('/session')){unexpected.push(url.pathname);return route.abort()}
   return route.continue();
  });
  await page.addInitScript(id=>localStorage.setItem('paper_project',id),pid);
  await page.goto(origin,{waitUntil:'domcontentloaded'});await page.getByRole('button',{name:'资料中心',exact:true}).click();await page.getByRole('tab',{name:'AI 处理',exact:true}).click();
  let panel=page.getByRole('region',{name:'资料与规则AI托管'});await panel.getByLabel('AI托管预算上限').waitFor();
  assert.equal(await panel.getByLabel('AI托管预算上限').inputValue(),'8.61');
  assert(await panel.getByLabel('新增资料自动接续托管').isChecked());
  assert(await panel.getByLabel('满足发布条件后自动更新规则包').isChecked());
  assert.equal(await page.getByLabel('选择本地OCR文件').isVisible(),false);
  checks.push('materials_defaults_to_budgeted_ai_workflow_and_preserves_advanced_tools');
  await panel.getByRole('button',{name:'AI 接管资料与规则（计费）',exact:true}).click();
  assert.deepEqual(submissions,[{budget_limit:8.61,auto_publish:true,follow_imports:true}]);
  assert(await panel.getByRole('button',{name:'AI 正在托管处理',exact:true}).isDisabled());
  checks.push('one_start_authorizes_the_whole_workflow_with_a_shared_cap');
  fixture.materials=rate(25,25,24);fixture.rules=rate(686,680,670);fixture.extraction=rate(1068,1068,1068);fixture.exception_count=1;fixture.exceptions=[{kind:'rule',id:rules.rules[0].id,label:rules.rules[0].statement,reason:'同级来源的适用范围仍需核实',state:'conflict'}];fixture.tasks[0].status='incomplete';fixture.tasks[0].result={pause_reason:'例外清单已保存'};
  await page.getByRole('button',{name:'论文规则',exact:true}).click();await page.getByRole('tab',{name:'整理与审核',exact:true}).click();
  panel=page.getByRole('region',{name:'资料与规则AI托管'});await panel.getByText('AI 参与 680/686',{exact:false}).waitFor();
  const tools=page.locator('#rules-panel-automation>.autopilot-manual-tools');assert.equal(await tools.getAttribute('open'),null);
  await panel.locator('.autopilot-exceptions>summary').click();await panel.getByText('同级来源的适用范围仍需核实',{exact:true}).waitFor();
  await panel.getByRole('button',{name:'核对条款',exact:false}).click();await page.getByRole('button',{name:'关闭规则编辑',exact:true}).click();
  checks.push('rules_and_materials_share_live_coverage_and_actionable_exceptions');
  const out=path.join(root,'.runtime/ai-autopilot-browser');fs.mkdirSync(out,{recursive:true});await panel.screenshot({path:path.join(out,'desktop.png')});
  await panel.getByRole('button',{name:'关闭自动接续',exact:true}).click();assert.equal(fixture.policy.enabled,false);
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await panel.screenshot({path:path.join(out,'mobile.png')});
  checks.push('follow_imports_can_be_disabled_and_mobile_layout_has_no_overflow');
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
  const report={passed:true,synthetic_fixture:true,checks,page_errors:errors,intercepted_paid_starts:submissions.length,backend_paid_starts:0};fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close()}
})().catch(e=>{console.error(String(e.stack||e).split(token||'NO_TOKEN').join('[REDACTED]'));process.exitCode=1});
