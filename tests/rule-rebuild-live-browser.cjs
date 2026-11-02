const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8765';
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN'});
    const token=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8')).bootstrap_token;
    assert.equal((await context.request.post(origin+'/api/v1/session',{data:{token}})).status(),200);
    const job=JSON.parse(fs.readFileSync(path.join(root,'.runtime/rules-document-live-task.json'),'utf8'));
    const page=await context.newPage();page.setDefaultTimeout(30000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(pid=>localStorage.setItem('paper_project',pid),job.project_id);
    await page.goto(origin);await page.getByRole('button',{name:'论文规则',exact:true}).click();
    const panel=page.locator('section[aria-label="按原件重新生成规则"]');await panel.getByRole('heading',{name:'按原件重新生成规则',exact:true}).waitFor();
    const verificationPath=path.join(root,'.runtime/full-rules-refresh-verification.json');
    if(fs.existsSync(verificationPath)){
      const verification=JSON.parse(fs.readFileSync(verificationPath,'utf8'));
      const overview=page.locator('section[aria-label="规则概况与发布状态"]');
      await overview.getByText('规则包 V'+verification.package.version,{exact:true}).waitFor();
      assert.match(await overview.innerText(),new RegExp(String(verification.effective_rules)));
      await overview.screenshot({path:path.join(root,'.runtime/rules-rebuilt-overview.png')});
    }
    await panel.getByRole('button',{name:'刷新重建进度',exact:true}).click();await panel.getByRole('status').waitFor();
    assert.match(await panel.getByRole('status').innerText(),/165/);
    await panel.getByText(/历史规则包（/).click();await panel.getByRole('button',{name:'查看 V32 · 789 条',exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'生效规则快照',exact:true});await dialog.getByRole('heading',{name:'规则包 V32',exact:true}).waitFor();
    await dialog.getByRole('button',{name:'关闭原文依据',exact:true}).click();
    await panel.screenshot({path:path.join(root,'.runtime/rules-rebuild-panel.png')});
    await page.setViewportSize({width:390,height:844});await panel.screenshot({path:path.join(root,'.runtime/rules-rebuild-panel-mobile.png')});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
    console.log(JSON.stringify({checks:['live generation progress','historical V32 snapshot','mobile layout','no automatic paid re-run'],errors},null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
