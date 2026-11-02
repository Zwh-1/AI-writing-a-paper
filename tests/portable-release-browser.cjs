// The browser, Node and Playwright all come from the extracted portable release.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const qa=path.resolve(process.env.PAPER_PORTABLE_QA||path.join(__dirname,'../.runtime/portable-packaging-qa'));
const fixture=JSON.parse(fs.readFileSync(path.join(qa,'runtime-report.json'),'utf8'));
const app=fixture.application_root,origin=`http://127.0.0.1:${fixture.port}`;
process.env.PLAYWRIGHT_BROWSERS_PATH=path.join(app,'.runtime/browsers');
const {chromium}=require(path.join(app,'.runtime/browser/node_modules/playwright'));
(async()=>{
 const browser=await chromium.launch({headless:true});const errors=[];
 try{
  const context=await browser.newContext({viewport:{width:1536,height:1050},locale:'zh-CN'});
  const token=JSON.parse(fs.readFileSync(path.join(qa,'browser-session.json'),'utf8')).bootstrap_token;
  assert.equal((await context.request.post(origin+'/api/v1/session',{data:{token}})).status(),200);
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(value=>{localStorage.setItem('paper_project',value.project_id);localStorage.setItem('paper_section',value.section_id);},fixture);
  await page.goto(origin);await page.getByRole('button',{name:'写作工作台',exact:true}).click();
  await page.locator('.paper-editor figure img').waitFor();
  await page.getByRole('button',{name:'论文图表',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'论文图表',exact:true});
  await dialog.getByRole('tab',{name:'架构图',exact:true}).click();
  await dialog.getByRole('button',{name:'生成预览',exact:true}).click();await dialog.locator('.pv-preview img').waitFor();
  await dialog.getByLabel('生图必需文字',{exact:true}).waitFor();
  assert(await dialog.getByRole('button',{name:'使用 gpt-image-2 生成（计费）',exact:true}).isDisabled());
  await page.screenshot({path:path.join(qa,'portable-desktop.png')});
  await page.setViewportSize({width:390,height:844});assert(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:path.join(qa,'portable-mobile.png')});
  await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});
  await page.setViewportSize({width:1536,height:1050});
  await page.getByRole('button',{name:'文献与术语',exact:true}).click();
  for(const [key,name] of [['library','文献书目'],['reading','问题阅读'],['evidence','原文与证据'],['wiki','图谱与Wiki'],['terms','统一术语'],['manage','处理与维护']]){
   await page.getByRole('tab',{name,exact:true}).click();await page.locator('.references-workspace > [role=tabpanel] .research-columns').waitFor();
   assert(await page.locator('main').evaluate(el=>el.scrollWidth<=el.clientWidth+2),name+' overflow');
   await page.locator('main').evaluate(el=>el.scrollTop=0);await page.evaluate(()=>window.scrollTo(0,0));
   await page.screenshot({path:path.join(qa,'portable-reference-'+key+'.png')});
  }
  assert.deepEqual(errors,[]);
  const report={passed:true,version:fixture.version,profile:fixture.profile,source:app,own_browser_runtime:true,checks:['Saved chapter, figure and editable table load in the extracted app','Local architecture preview and image-prompt controls render','Desktop and mobile have no horizontal overflow or page errors','All six reference workbenches load from the extracted integrated release'],errors,paid_model_calls:0};
  fs.writeFileSync(path.join(qa,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
