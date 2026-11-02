// Read-only checks against the running project. No model calls or main DB writes.
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..'), port = process.env.PAPER_TEST_PORT || '8765';
const origin = `http://127.0.0.1:${port}`;
const { chromium } = require(path.join(root, '.runtime/browser/node_modules/playwright'));
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
  const errors=[],writes=[];const out=path.join(root,'.runtime/expense-evidence-20261028');
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1100},locale:'zh-CN'});
    const token=JSON.parse(fs.readFileSync(path.join(root,`.runtime/session-${port}.json`),'utf8')).bootstrap_token;
    const login=await context.request.post(origin+'/api/v1/session',{data:{token}});assert.equal(login.status(),200);
    const projects=await (await context.request.get(origin+'/api/v1/projects')).json();const pid=projects[0].id;
    const audit=await (await context.request.get(origin+`/api/v1/projects/${pid}/expenses`)).json();
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/api/v1/**',route=>{
      if(new URL(route.request().url()).pathname==='/api/v1/session')return route.continue();
      if(!['GET','HEAD','OPTIONS'].includes(route.request().method())){writes.push(route.request().url());return route.abort();}
      return route.continue();
    });
    await page.addInitScript(value=>localStorage.setItem('paper_project',value),pid);
    await page.goto(origin,{waitUntil:'domcontentloaded'});
    await page.getByRole('button',{name:'接口与设置',exact:true}).click();
    const ledger=page.getByRole('region',{name:'费用依据账本',exact:true});await ledger.waitFor();
    await ledger.getByText('要求完整计价依据',{exact:false}).waitFor();
    assert((await ledger.textContent()).includes(`${audit.gap_count} 笔待补依据`));
    assert.equal(await ledger.getByText('登记账单',{exact:true}).count(),0);
    assert.equal(await ledger.getByText('导入请求账单',{exact:true}).count(),audit.runs.filter(r=>r.gaps.length&&!r.group_id&&!['excluded','imported_receipt'].includes(r.kind)&&!['pending','sent'].includes(r.status)).slice(0,40).length);
    await ledger.getByLabel('费用记录筛选').selectOption('supported_estimate');
    await ledger.locator('tbody tr').filter({hasText:'有依据的计价估算'}).first().waitFor();
    const download=page.waitForEvent('download');await ledger.getByRole('link',{name:'导出全部费用与依据',exact:true}).click();
    const saved=await download;const downloadPath=await saved.path();const data=JSON.parse(fs.readFileSync(downloadPath,'utf8'));
    assert.equal(data.runs.length,audit.runs.length);assert.equal(data.gap_count,audit.gap_count);
    await ledger.getByLabel('费用记录筛选').selectOption('gaps');
    await ledger.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'expense-ledger-desktop.png')});
    await page.setViewportSize({width:390,height:844});await ledger.evaluate(el=>el.scrollIntoView({block:'start'}));
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
    assert.equal(overflow,false,'Page must not overflow on mobile');
    await page.screenshot({path:path.join(out,'expense-ledger-mobile.png')});
    assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
    const report={passed:true,project_id:pid,runs:audit.runs.length,gaps:audit.gap_count,checks:['strict policy visible','evidence types separated','receipt upload available','manual numeric settlement removed','download contains all runs and evidence','desktop and mobile render','no paid API calls','no main database mutation'],errors,writes};
    fs.writeFileSync(path.join(out,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
