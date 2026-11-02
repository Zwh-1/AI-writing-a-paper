const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8765';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN'});
  const token=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8')).bootstrap_token;
  assert.equal((await context.request.post(origin+'/api/v1/session',{data:{token}})).status(),200);
  const job=JSON.parse(fs.readFileSync(path.join(root,'.runtime/literature-content-live-task.json'),'utf8'));
  const result=await (await context.request.get(`${origin}/api/v1/projects/${job.project_id}/literature-refresh`)).json();
  assert.equal(result.updated,31);assert.equal(result.task.status,'succeeded');
  const page=await context.newPage();page.setDefaultTimeout(40000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(pid=>localStorage.setItem('paper_project',pid),job.project_id);await page.goto(origin);
  await page.getByRole('button',{name:'文献与术语',exact:true}).click();
  const panel=page.locator('section[aria-label="文献内容更新"]');await panel.getByRole('status').filter({hasText:'31/31'}).waitFor();
  await panel.getByText('逐篇内容与更新时间',{exact:true}).click();
  const rows=panel.locator('.literature-refresh-files article');assert.equal(await rows.count(),31);
  assert.equal(await rows.filter({hasText:'本轮内容已更新'}).count(),31);
  await panel.screenshot({path:path.join(root,'.runtime/literature-content-updated.png')});
  await rows.first().getByRole('button',{name:'查看文献总结',exact:true}).click();
  let dialog=page.getByRole('dialog',{name:'文献内容总结',exact:true});
  await dialog.getByText(/文献原文重新阅读/).waitFor();await dialog.getByText('查看对应原文引句',{exact:true}).first().click();
  await dialog.locator('blockquote').first().waitFor();
  await page.screenshot({path:path.join(root,'.runtime/literature-new-summary.png')});
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.ok(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await dialog.screenshot({path:path.join(root,'.runtime/literature-new-summary-mobile.png')});
  await dialog.getByRole('button',{name:/查看第\d+页原文/}).first().click();
  await page.getByRole('dialog',{name:'文献内容总结',exact:true}).waitFor({state:'hidden'});
  await page.getByRole('dialog').last().waitFor();
  assert.deepEqual(errors,[]);
  const report={updated:result.updated,total:result.total,checks:['31 new content versions','per-paper new timestamps','research summary and exact quotes','original page navigation','mobile dialog'],errors};
  fs.writeFileSync(path.join(root,'.runtime/literature-refresh-browser-results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
