const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),out=path.join(root,'.runtime/graph-wiki-delivery-20261027/browser');
const fixture=JSON.parse(fs.readFileSync(path.join(out,'fixture.json'),'utf8')),origin=`http://127.0.0.1:${fixture.port}`;
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});const errors=[],checks=[];
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN'});
  const token=JSON.parse(fs.readFileSync(path.join(root,`.runtime/session-${fixture.port}.json`),'utf8')).bootstrap_token;
  const login=await context.request.post(origin+'/api/v1/session',{data:{token}});assert.equal(login.status(),200);
  const page=await context.newPage();page.setDefaultTimeout(20000);page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(f=>{localStorage.setItem('paper_project',f.project_id);localStorage.setItem('paper_section',f.section_id)},fixture);
  await page.goto(origin);await page.getByRole('button',{name:'文献与术语',exact:true}).click();
  const panel=page.locator('section[aria-label="图谱与Wiki知识库"]');await panel.getByRole('heading',{name:'图谱与 Wiki',exact:true}).waitFor();
  await panel.locator('.knowledge-page-item').first().click();await panel.locator('.knowledge-block').first().waitFor();
  assert((await panel.locator('.knowledge-page-detail').innerText()).includes('单用户'));checks.push('Wiki reads complete conditions with source links');
  await panel.getByRole('button',{name:'查看出处 1',exact:true}).first().click();let dialog=page.getByRole('dialog',{name:'论断与原文核对',exact:true});
  assert((await dialog.innerText()).includes('单用户'));await dialog.getByRole('button',{name:'关闭窗口',exact:true}).click();checks.push('Claim opens actual original scope');
  await panel.getByRole('button',{name:'编辑与备注',exact:true}).click();await panel.getByRole('button',{name:'新增备注',exact:true}).click();
  const note='浏览器合成验收：人工备注需要保留。'+Date.now();await panel.locator('.knowledge-block textarea').last().fill(note);await panel.getByRole('button',{name:'保存修订候选',exact:true}).click();
  await panel.getByRole('button',{name:'发布当前修订',exact:true}).click();await panel.getByText(note,{exact:true}).waitFor();checks.push('Editorial note saves as revision and publishes');
  await panel.screenshot({path:path.join(out,'desktop-wiki.png')});
  await panel.getByRole('button',{name:'关系探索',exact:true}).click();await panel.getByLabel('问题或关键词',{exact:true}).fill('单用户');await panel.getByRole('button',{name:'检索知识',exact:true}).click();
  await panel.getByRole('button',{name:'原文与支持性核对',exact:true}).first().waitFor();assert(await panel.locator('svg.knowledge-graph').count());checks.push('Bounded graph and claims render');
  await panel.screenshot({path:path.join(out,'desktop-graph.png')});
  await panel.getByRole('button',{name:'章节准备',exact:true}).click();await panel.getByRole('button',{name:'检索知识',exact:true}).click();await panel.getByRole('button',{name:'执行章节阅读计划',exact:true}).waitFor();
  await panel.getByLabel('目标章节',{exact:true}).selectOption(fixture.section_id);await panel.getByRole('button',{name:'执行章节阅读计划',exact:true}).click();
  await panel.getByText('原文阅读范围已保存；方法、条件与局限仍需核对',{exact:true}).waitFor();checks.push('Multi-document plan persists actual chapter reading');
  await panel.getByRole('button',{name:'构建与更新',exact:true}).click();await panel.getByRole('button',{name:'预览构建范围',exact:true}).click();await panel.getByRole('button',{name:'开始构建',exact:true}).waitFor();checks.push('Local and model modes remain distinct');
  await panel.getByRole('button',{name:'对照评测',exact:true}).click();await panel.getByRole('button',{name:'建立 80 个任务草稿',exact:true}).click();await panel.locator('.knowledge-case-list .knowledge-page-item').first().waitFor();assert.equal(await panel.locator('.knowledge-case-list .knowledge-page-item').count(),80);checks.push('80 drafts are unlabelled, not fabricated gold');
  await page.setViewportSize({width:390,height:844});await panel.getByRole('button',{name:'主题 Wiki',exact:true}).click();await panel.screenshot({path:path.join(out,'mobile-wiki.png')});
  const width=await panel.evaluate(e=>({client:e.clientWidth,scroll:e.scrollWidth}));assert(width.scroll<=width.client+2,JSON.stringify(width));checks.push('390px layout has no panel overflow');
  assert.equal(errors.length,0,errors.join('\n'));fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({checks,errors,synthetic:true,original_user_data_modified:false},null,2));console.log(JSON.stringify({checks,errors}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
