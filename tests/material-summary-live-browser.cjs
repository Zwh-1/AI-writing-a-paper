const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..');const {chromium}=require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const session=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8'));
 const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
 const page=await browser.newPage({viewport:{width:1600,height:1100},locale:'zh-CN'});page.setDefaultTimeout(12000);
 try {
  await page.goto('http://127.0.0.1:8765/#token='+session.bootstrap_token,{waitUntil:'networkidle'});console.log('Live session opened.');
  await page.getByRole('button',{name:'资料中心',exact:true}).click();
  await page.getByRole('tab',{name:'AI 处理',exact:true}).click();
  await page.getByText('资料总结设置与独立整理工具',{exact:true}).click();
  await page.getByTestId('summary-batch').locator('.summary-counters').filter({hasText:/25\s*\/\s*25/}).waitFor();console.log('Live batch reports 25/25.');
  await page.screenshot({path:path.join(root,'docs/screenshots/deepseek-repair-live-batch.png')});
  await page.getByRole('tab',{name:'资料库',exact:true}).click();await page.getByRole('button',{name:'智评综合等级划分说明.pdf',exact:true}).click();
  const summary=page.getByTestId('material-summary');await summary.getByText('整体概括',{exact:true}).waitFor();
  for(const title of ['核心要点','主题归纳'])assert(await summary.getByText(title,{exact:true}).isVisible());
  assert(await summary.getByRole('button',{name:'总结已保存'}).isDisabled());
  await page.screenshot({path:path.join(root,'docs/screenshots/deepseek-repair-live-pdf-summary.png')});
  const report=JSON.parse(fs.readFileSync(path.join(root,'docs/material-summary-live-results.json'),'utf8'));const file=report.files.find(f=>f.name==='智评综合等级划分说明.pdf');
  const download=await page.request.get('http://127.0.0.1:8765/api/v1/files/'+file.file_id+'/summary/markdown');assert.equal(download.status(),200);assert((await download.text()).includes('## 核心要点'));
  const nums=await summary.locator('.summary-page-link').allTextContents();assert(nums.length>0);
  console.log(JSON.stringify({checks:['real_batch_25_of_25','real_pdf_overview_points_topics','saved_summary_reused','real_markdown_download'],referenced_page_links:nums.length,upstream_generation_calls:0}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
