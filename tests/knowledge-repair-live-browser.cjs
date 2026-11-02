// Real project: local read operations only; never enqueue paid work or edit content.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),port=process.env.PAPER_TEST_PORT||'8765',origin=`http://127.0.0.1:${port}`;
const {chromium}=require(path.join(root,'.runtime/browser/node_modules/playwright'));
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
 const out=path.join(root,'.runtime/knowledge-repair-20261028');const errors=[],writes=[],checks=[];
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1050},locale:'zh-CN'});
  const token=JSON.parse(fs.readFileSync(path.join(root,`.runtime/session-${port}.json`),'utf8')).bootstrap_token;
  assert.equal((await context.request.post(origin+'/api/v1/session',{data:{token}})).status(),200);
  const projects=await (await context.request.get(origin+'/api/v1/projects')).json();const pid=projects[0].id;
  const status=await (await context.request.get(origin+`/api/v1/projects/${pid}/knowledge/status`)).json();
  assert.equal(status.billing_gate.paid_requests_allowed,false);
  const page=await context.newPage();page.setDefaultTimeout(45000);page.on('pageerror',e=>errors.push(e.message));
  const waitEnabled=async locator=>{await locator.waitFor();await page.waitForFunction(el=>!el.disabled,await locator.elementHandle());};
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),url=new URL(request.url());
   if(['GET','HEAD','OPTIONS'].includes(request.method())||url.pathname==='/api/v1/session')return route.continue();
   if(url.pathname.endsWith('/knowledge/query')||url.pathname.endsWith('/knowledge/builds/preview'))return route.continue();
   writes.push(url.pathname);return route.abort();
  });
  await page.addInitScript(value=>localStorage.setItem('paper_project',value),pid);
  await page.goto(origin,{waitUntil:'domcontentloaded'});
  await page.getByRole('button',{name:'文献与术语',exact:true}).click();
  const panel=page.getByRole('region',{name:'图谱与Wiki知识库',exact:true});await panel.waitFor();
  assert.equal(await panel.locator('.knowledge-tabs').evaluate(el=>getComputedStyle(el).flexDirection),'row');
  await panel.getByText('收费功能暂不可用：',{exact:false}).waitFor();checks.push('billing gate explains unresolved fees before any paid submission');
  await panel.getByRole('button',{name:'章节准备',exact:true}).click();
  await panel.getByLabel('问题或关键词',{exact:true}).fill('资料有哪些明确的适用条件与限制？');
  await panel.getByLabel('补充原文检索',{exact:false}).uncheck();
  const queryResponse=page.waitForResponse(r=>r.url().endsWith('/knowledge/query')&&r.request().method()==='POST');
  await panel.getByRole('button',{name:'检索知识',exact:true}).click();
  const response=await queryResponse;assert.equal(response.status(),200);const query=await response.json();
  assert.equal(query.reading_plan.length,16);assert(new Set(query.reading_plan.map(r=>r.file_id)).size>1);
  assert(query.timings_ms.total<10000,JSON.stringify(query.timings_ms));
  await panel.getByText('任务路线：',{exact:false}).waitFor();
  assert(await panel.getByRole('button',{name:'生成有据回答（计费）',exact:true}).isDisabled());
  checks.push('fast navigation returns original reading locations with timings and multiple documents');
  await panel.screenshot({path:path.join(out,'desktop-query.png')});
  await panel.getByRole('button',{name:'构建与更新',exact:true}).click();
  await waitEnabled(panel.getByRole('button',{name:'构建本地社区索引（不调用模型）',exact:true}));
  assert(await panel.getByLabel('生成社区报告',{exact:false}).isDisabled());
  await panel.getByRole('button',{name:'预览构建范围',exact:true}).click();
  await waitEnabled(panel.getByRole('button',{name:'开始构建',exact:true}));
  await panel.getByLabel('整理方式',{exact:false}).selectOption('llm');
  await panel.getByRole('button',{name:'预览构建范围',exact:true}).click();
  assert(await panel.getByRole('button',{name:'开始构建',exact:true}).isDisabled());
  checks.push('local build and community computation remain available; paid compilation and reports are blocked');
  await page.setViewportSize({width:390,height:844});await panel.evaluate(el=>el.scrollIntoView({block:'start'}));
  assert(await panel.evaluate(el=>el.scrollWidth<=el.clientWidth+2));
  assert(await panel.locator('.knowledge-tabs button').evaluateAll(els=>els.every(el=>el.getBoundingClientRect().height<65)));
  await page.screenshot({path:path.join(out,'mobile-build.png')});checks.push('desktop and 390px render without panel overflow');
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  const report={passed:true,project_id:pid,checks,errors,writes,query_seconds:query.timings_ms.total/1000,reading_documents:new Set(query.reading_plan.map(r=>r.file_id)).size,paid_model_calls:0,scope:'native graph/wiki navigation without independent original neural retrieval'};
  fs.writeFileSync(path.join(out,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
