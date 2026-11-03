const {openPage}=require('./completion-navigation.cjs');
// Verify remote branch history is usable through the actual notes UI.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),fixture=JSON.parse(fs.readFileSync(path.join(root,'.runtime/studio-acceptance-20261102/fixture.json'),'utf8'));
const {chromium}=require(path.join(root,'.runtime/browser/node_modules/playwright'));
const folder=path.join(root,'.runtime/completeness-acceptance');fs.mkdirSync(folder,{recursive:true});
const errors=[],failures=[],checks=[];
(async()=>{
 const origin=`http://127.0.0.1:${fixture.port}`,token=JSON.parse(fs.readFileSync(path.join(root,`.runtime/session-${fixture.port}.json`),'utf8')).bootstrap_token;
 const login=await fetch(origin+'/api/v1/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
 const cookie=login.headers.get('set-cookie').split(';')[0],auth=await login.json();
 const call=async(url,body)=>{const response=await fetch(origin+'/api/v1'+url,{method:body?'POST':'GET',headers:{Cookie:cookie,'X-CSRF-Token':auth.csrf,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});assert.ok(response.ok,await response.clone().text());return response.json();};
 const base=`/projects/${fixture.project_id}`,stamp=Date.now();
 const sharedAsset=await call(base+'/studio/assets',{title:'Shared reading resource '+stamp,text:'A shared asset available while reading.'});
 const original=await call(base+'/studio/records',{kind:'note',data:{title:'Original history '+stamp,text:'Original remote branch content'}});
 const packet=await call(base+'/research-sync/snapshot',{});packet.base=packet.heads;packet.records=packet.records.filter(row=>row.id===original.id);
 const remote=packet.records[0];remote.versions=[{revision:1,data:remote.data,created_at:remote.created_at}];remote.data=JSON.stringify({title:'Remote history '+stamp,text:'Updated remote branch content'});remote.revision=2;
 const merged=await call(base+'/research-sync/apply',packet);assert.deepEqual(merged.merged,[original.id]);
 const executable=fs.readdirSync(path.join(root,'.runtime/browsers')).filter(name=>name.startsWith('chromium')).flatMap(name=>['chrome-win64/chrome.exe','chrome-headless-shell-win64/chrome-headless-shell.exe'].map(file=>path.join(root,'.runtime/browsers',name,file))).find(file=>fs.existsSync(file));
 const browser=await chromium.launch({headless:true,executablePath:executable});
 try{
  const page=await browser.newPage({viewport:{width:1460,height:1050},locale:'zh-CN'});page.setDefaultTimeout(20000);
  page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400)failures.push({url:new URL(r.url()).pathname,status:r.status()});});
  await page.goto(origin+'/#token='+encodeURIComponent(token));await page.waitForFunction(()=>!!document.querySelector('select[aria-label="切换论文项目"]')?.value);await page.getByLabel('切换论文项目',{exact:true}).selectOption(fixture.project_id);assert.equal(await page.getByLabel('切换论文项目',{exact:true}).inputValue(),fixture.project_id);await openPage(page,'研究助手');await page.getByRole('tab',{name:'思维随记',exact:true}).click();
  await page.getByRole('button',{name:'Remote history '+stamp,exact:true}).click();await page.getByRole('button',{name:'查看版本与恢复',exact:true}).click();
  await page.locator('summary').filter({hasText:'远端分支 · 版本 1'}).click();await page.locator('details[open] pre').filter({hasText:'Original remote branch content'}).waitFor();checks.push('remote branch revisions are visible in the shared note history');
  await page.getByRole('button',{name:'从远端历史创建恢复版本',exact:true}).first().click();await page.locator('.studio-note-editor .tiptap').getByText('Original remote branch content',{exact:true}).waitFor();
  const saved=await call(base+'/studio/records/'+original.id);assert.ok(saved.revision>2);assert.equal(saved.source_state,'unknown');checks.push('remote historical content restores as a new unverified revision');
  await page.screenshot({path:path.join(folder,'remote-history.png'),fullPage:true});
  await openPage(page,'文献与知识');await page.getByRole('tab',{name:'对照阅读',exact:true}).click();
  await page.getByLabel('阅读原文',{exact:true}).selectOption(fixture.file_id);await page.locator('.studio-inline-reference').first().waitFor();assert.match(await page.locator('.studio-inline-reference').first().getAttribute('title'),/Example Author/);checks.push('paper reference links expose actual bibliography information on hover');
  await page.locator('.studio-reader').getByRole('tab',{name:'问答',exact:true}).click();await page.locator('.studio-reader details.studio-sources summary').filter({hasText:'选择文献与研究资源'}).click();
  const resources=page.locator('.studio-reader .studio-sources select[multiple]').nth(1);await resources.locator(`option[value="${sharedAsset.id}"]`).waitFor({state:'attached'});await resources.selectOption(sharedAsset.id);assert.equal(await resources.locator(`option[value="${sharedAsset.id}"]`).textContent(),'Shared reading resource '+stamp);checks.push('reader conversations can select the shared project asset library');
  await page.screenshot({path:path.join(folder,'reader-integrations.png'),fullPage:true});assert.deepEqual(errors,[]);assert.deepEqual(failures,[]);
 }catch(error){const pages=browser.contexts().flatMap(context=>context.pages());if(pages[0]){await pages[0].screenshot({path:path.join(folder,'failure.png'),fullPage:true});fs.writeFileSync(path.join(folder,'failure-dom.txt'),await pages[0].locator('body').innerText());}throw error;}finally{await browser.close();fs.writeFileSync(path.join(folder,'browser-results.json'),JSON.stringify({checks,errors,failures},null,2));}
 console.log(JSON.stringify({checks,errors,failures}));
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
