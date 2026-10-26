// Inspect the installed configuration and UI. Never invokes a paid image request.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8765',output=path.join(root,'.runtime/image-generation-qa');
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
 try{
  const context=await browser.newContext({viewport:{width:1536,height:1000},locale:'zh-CN'});
  const token=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8')).bootstrap_token;
  assert.equal((await context.request.post(origin+'/api/v1/session',{data:{token}})).status(),200);
  const state=await(await context.request.get(origin+'/api/v1/image-generation/settings')).json();
  assert.equal(state.provider.model,'gpt-image-2');assert.equal(state.provider.base_url,'https://api.hanhegufei.online');
  const page=await context.newPage(),errors=[],paid=[];page.setDefaultTimeout(20000);
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/paper-visuals/image-tasks'))paid.push(r.url());});
  await page.goto(origin);await page.getByRole('button',{name:'接口与设置',exact:true}).click();
  const panel=page.getByRole('region',{name:'GPT 生图专用设置',exact:true});
  await panel.getByLabel('生图模型',{exact:true}).waitFor();
  assert.equal(await panel.getByLabel('生图模型',{exact:true}).inputValue(),'gpt-image-2');
  assert.equal(await panel.getByLabel('生图 API 地址',{exact:true}).inputValue(),'https://api.hanhegufei.online');
  assert.equal(await panel.getByLabel('独立生图 API key',{exact:true}).getAttribute('type'),'password');
  await panel.screenshot({path:path.join(output,'live-desktop-settings.png')});
  await page.setViewportSize({width:390,height:844});
  assert(await panel.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await panel.screenshot({path:path.join(output,'live-mobile-settings.png')});
  assert.deepEqual(errors,[]);assert.deepEqual(paid,[]);
  const report={passed:true,model:state.provider.model,base_url:state.provider.base_url,key_configured:state.credential.configured,errors,paid_requests:0};
  fs.writeFileSync(path.join(output,'live-report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
