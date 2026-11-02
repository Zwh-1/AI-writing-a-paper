// Verify the real project's new operations without paid calls or data edits.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),port=process.env.PAPER_TEST_PORT||'8765',origin=`http://127.0.0.1:${port}`;
const {chromium}=require(path.join(root,'.runtime/browser/node_modules/playwright'));
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});const errors=[],writes=[];
 const out=path.join(root,'.runtime/knowledge-completion-20261028');
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1080},locale:'zh-CN'});
  const token=JSON.parse(fs.readFileSync(path.join(root,`.runtime/session-${port}.json`),'utf8')).bootstrap_token;
  assert.equal((await context.request.post(origin+'/api/v1/session',{data:{token}})).status(),200);
  const projects=await (await context.request.get(origin+'/api/v1/projects')).json(),pid=projects[0].id;
  const page=await context.newPage();page.setDefaultTimeout(45000);page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/v1/**',route=>{
   const req=route.request(),url=new URL(req.url());
   if(['GET','HEAD','OPTIONS'].includes(req.method())||url.pathname==='/api/v1/session'||url.pathname.endsWith('/query/route-preview'))return route.continue();
   writes.push(url.pathname);return route.abort();
  });
  await page.addInitScript(value=>localStorage.setItem('paper_project',value),pid);await page.goto(origin);
  await page.getByRole('button',{name:'文献与术语',exact:true}).click();const panel=page.getByRole('region',{name:'图谱与Wiki知识库',exact:true});
  await panel.getByRole('button',{name:'维护与迁移',exact:true}).click();
  await panel.getByLabel('统一查询问题',{exact:true}).fill('探索当前文献中的研究路线');
  await panel.getByRole('button',{name:'预览路由',exact:true}).click();await panel.getByText('推荐 drift',{exact:false}).waitFor();
  assert(await panel.getByLabel('允许本次查询调用收费模型',{exact:false}).isDisabled());
  for(const name of ['完整增量维护队列','Wiki并发编辑合并','论断支持性裁决','完整对照评测与统计','完整知识包与跨项目导入','完整知识云同步']){
    const detail=panel.locator('details').filter({has:page.locator('summary').filter({hasText:name})}).first();
    await detail.locator('summary').first().click();
  }
  await panel.getByRole('button',{name:'刷新队列',exact:true}).click();await panel.getByText('queued 3604',{exact:false}).waitFor();
  assert(await panel.getByRole('button',{name:'运行或续接维护（计费）',exact:true}).isDisabled());
  await panel.getByRole('button',{name:'读取编辑冲突',exact:true}).click();
  const annotation=await (await context.request.get(origin+`/api/v1/projects/${pid}/knowledge/evaluation/annotation-packet`)).json();
  assert.equal(annotation.cases.length,80);assert(annotation.cases.every(c=>!c.human_passed));
  const completion=await (await context.request.get(origin+`/api/v1/projects/${pid}/knowledge/completion`)).json();
  assert(Object.values(completion.software).every(Boolean));assert.equal(completion.all_complete,false);
  await page.screenshot({path:path.join(out,'desktop-operations.png'),fullPage:false});
  await page.setViewportSize({width:390,height:844});await panel.evaluate(el=>el.scrollIntoView({block:'start'}));
  assert(await panel.evaluate(el=>el.scrollWidth<=el.clientWidth+2));await page.screenshot({path:path.join(out,'mobile-operations.png')});
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  const report={passed:true,version:'0.7.1',project_id:pid,checks:['route explanation','blocked paid controls','durable queue visible','merge/adjudication/evaluation controls','bundle and cloud controls','80 unlabelled human materials','honest completion status','desktop and mobile'],errors,writes,paid_model_calls:0};
  fs.writeFileSync(path.join(out,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
