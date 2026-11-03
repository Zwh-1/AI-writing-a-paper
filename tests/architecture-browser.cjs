const {openPage}=require('./completion-navigation.cjs');
// UI acceptance in an isolated local fixture; no real accounts or paid model calls.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'.runtime/architecture-acceptance');fs.mkdirSync(out,{recursive:true});
const fixture=JSON.parse(fs.readFileSync(path.join(root,'.runtime/studio-acceptance-20261102/fixture.json'),'utf8'));
const {chromium}=require(path.join(root,'.runtime/browser/node_modules/playwright'));
const checks=[],errors=[],responses=[];const canvasTitle='Integrated canvas '+Date.now();
async function main(){
 const candidates=fs.readdirSync(path.join(root,'.runtime/browsers')).filter(name=>name.startsWith('chromium')).flatMap(name=>['chrome-win64/chrome.exe','chrome-headless-shell-win64/chrome-headless-shell.exe'].map(tail=>path.join(root,'.runtime/browsers',name,tail)));
 const browser=await chromium.launch({headless:true,executablePath:candidates.find(file=>fs.existsSync(file))});
 try{
  const context=await browser.newContext({viewport:{width:1460,height:1050},locale:'zh-CN'}),page=await context.newPage();page.setDefaultTimeout(20000);
  page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400)responses.push({path:new URL(r.url()).pathname,status:r.status()});});
  const token=JSON.parse(fs.readFileSync(path.join(root,`.runtime/session-${fixture.port}.json`),'utf8')).bootstrap_token;
  await page.goto(`http://127.0.0.1:${fixture.port}/#token=`+encodeURIComponent(token));await page.getByLabel('切换论文项目',{exact:true}).selectOption(fixture.project_id);
  const record=name=>{checks.push(name);console.log(name);};
  await openPage(page,'文献与知识');

  await page.getByRole('heading',{name:'文献与知识工作台',exact:true}).waitFor();
  await page.getByRole('tab',{name:'文献接入',exact:true}).click();await page.getByText('合成研究论文',{exact:true}).waitFor();record('intake is available in existing literature workspace');
  await page.getByRole('tab',{name:'对照阅读',exact:true}).click();await page.locator('[data-original-unit]').first().waitFor();
  await page.getByRole('button',{name:'翻译全文（计费）',exact:true}).click();
  await page.getByRole('tab',{name:'思维随记',exact:true}).click();
  await page.getByRole('tab',{name:'对照阅读',exact:true}).click();await page.locator('.studio-translation').filter({hasText:'测试译文'}).first().waitFor();
  assert.equal(await page.getByRole('alert').count(),0);record('translation task survives changing workspace views');
  await openPage(page,'研究助手');await page.getByRole('tab',{name:'自由画布',exact:true}).click();
  await page.getByLabel('画布名称',{exact:true}).fill(canvasTitle);
  await page.getByRole('button',{name:'保存可编辑画布',exact:true}).click();
  await page.getByText('已保存场景，可再次编辑',{exact:true}).waitFor();assert.equal(await page.getByRole('alert').count(),0);record('canvas save is not misclassified as a failed task');
  await page.getByRole('button',{name:'生成图片并用于正文',exact:true}).click();
  await page.getByText('待插入：'+canvasTitle,{exact:false}).waitFor();
  await page.getByRole('button',{name:'插入到当前光标',exact:true}).click();
  await page.locator('.tiptap figure[data-artifact]').waitFor();
  await page.waitForFunction(title=>!document.body.innerText.includes('待插入：'+title),canvasTitle);
  assert.equal(await page.getByRole('alert').count(),0);record('durable asset intent inserts into the targeted editor and is consumed');
  await openPage(page,'研究助手');await page.getByRole('tab',{name:'资产库',exact:true}).click();
  await page.getByRole('button',{name:canvasTitle,exact:true}).click();await page.getByText('类型：figure',{exact:false}).waitFor();record('published canvas is available in the shared asset catalog');
  await openPage(page,'排版与交付');await page.getByLabel('导出格式',{exact:true}).selectOption('latex');
  await page.getByRole('button',{name:'生成导出文件',exact:true}).click();await page.getByRole('link',{name:'latex-project.zip',exact:true}).first().waitFor();
  await openPage(page,'研究助手');await openPage(page,'排版与交付');
  const history=page.locator('.export-row').first();await history.getByRole('link',{name:'latex-project.zip',exact:true}).waitFor();assert.equal(await history.getByRole('link',{name:'DOCX',exact:true}).count(),0);record('TeX history exposes actual artifacts in the existing delivery workspace');
  await page.screenshot({path:path.join(out,'delivery.png'),fullPage:true});
  await page.getByRole('button',{name:'接口与设置',exact:true}).click();await page.getByRole('heading',{name:'研究记录协作',exact:true}).waitFor();record('parsing execution and record collaboration are exposed in shared settings');
  await page.setViewportSize({width:430,height:932});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2));await page.screenshot({path:path.join(out,'settings-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(responses,[]);
 }finally{await browser.close();fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({checks,errors,responses},null,2));}
}
main().catch(error=>{console.error(error.stack);process.exitCode=1;});
