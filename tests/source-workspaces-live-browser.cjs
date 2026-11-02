// Read-only verification against the user's current project; no paid calls.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'.runtime/source-workspace-refactor-20261028'),origin='http://127.0.0.1:8765';
const {chromium}=require(path.join(root,'.runtime/browser/node_modules/playwright'));
(async()=>{const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});const errors=[],writes=[],requests=[];try{
 const context=await browser.newContext({viewport:{width:1440,height:1050},locale:'zh-CN'});const token=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8')).bootstrap_token;
 assert.equal((await context.request.post(origin+'/api/v1/session',{data:{token}})).status(),200);
 const projects=await(await context.request.get(origin+'/api/v1/projects')).json(),pid=projects[0].id;
 const state=await(await context.request.get(origin+`/api/v1/projects/${pid}/source-workspace`)).json();assert(state.files.length>=1);
 const page=await context.newPage();page.setDefaultTimeout(45000);page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/v1/**',route=>{const r=route.request(),u=new URL(r.url());requests.push(u.pathname);if(['GET','HEAD','OPTIONS'].includes(r.method())||u.pathname==='/api/v1/session')return route.continue();writes.push(u.pathname);return route.abort()});
 await page.addInitScript(value=>localStorage.setItem('paper_project',value),pid);await page.goto(origin);
 for(const [key,name,heading] of [['materials','资料中心','资料中心'],['rules','论文规则','论文规则工作台'],['references','文献与术语','文献与知识工作台']]){
  await page.getByRole('button',{name,exact:true}).first().click();await page.getByRole('heading',{name:heading,exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelector('.source-destination-count')?.textContent.includes('份原件')&&!document.querySelector('.source-destination-count')?.textContent.includes('—'));
  if(key==='materials')await page.locator('.material-row').first().waitFor();
  if(key==='rules')await page.getByLabel('按规则出处筛选').locator('option').nth(2).waitFor({state:'attached'});
  if(key==='references')await page.locator('.reference-row').first().waitFor();
  await page.locator('main').evaluate(el=>el.scrollTop=0);await page.screenshot({path:path.join(out,'live-'+key+'-desktop.png')});
  assert.equal(await page.locator('.source-workspace-nav').count(),1);
 }
 assert.equal(await page.getByRole('region',{name:'图谱与Wiki知识库',exact:true}).count(),0);
 assert.equal(await page.getByRole('region',{name:'证据知识库',exact:true}).count(),0);
 const referenceDefaultDidNotFetchGraph=!requests.some(p=>p.endsWith('/knowledge/status'));assert(referenceDefaultDidNotFetchGraph);
 await page.getByRole('tab',{name:'问题阅读',exact:true}).click();await page.locator('.dr-documents article').first().waitFor();
 await page.getByRole('tab',{name:'统一术语',exact:true}).click();await page.getByRole('heading',{name:'中英文术语表',exact:false}).waitFor();
 await page.getByRole('tab',{name:'图谱与Wiki',exact:true}).click();await page.getByRole('heading',{name:'图谱与 Wiki',exact:true}).waitFor();await page.locator('.knowledge-page-item').first().waitFor();
 await page.setViewportSize({width:390,height:844});
 for(const [key,name] of [['materials','资料中心'],['rules','论文规则'],['references','文献与术语']]){
  await page.getByRole('button',{name,exact:true}).first().click();await page.locator('.source-workspace-nav').waitFor();
  await page.waitForFunction(()=>!document.querySelector('.source-destination-count')?.textContent.includes('—'));
  if(key==='references')await page.locator('.knowledge-page-item').first().waitFor();
  await page.locator('main').evaluate(el=>el.scrollTop=0);await page.waitForTimeout(200);
  assert(await page.locator('main').evaluate(el=>el.scrollWidth<=el.clientWidth+2),key+' overflow');
  await page.screenshot({path:path.join(out,'live-'+key+'-mobile.png')});
 }
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 const report={passed:true,project_id:pid,counts:state.counts,checks:['shared original identities and actual counts','three focused pages render','reference library is default','unused graph/evidence APIs not fetched on default view','question reading and terms remain reachable','graph is loaded on demand','390px pages fit'],errors,writes,paid_model_calls:0};fs.writeFileSync(path.join(out,'live-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
