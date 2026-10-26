// Read-only smoke check of the installed app. Previewing creates no project assets.
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..'), origin = 'http://127.0.0.1:8765';
(async () => {
  const browser = await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
  try {
    const context = await browser.newContext({viewport:{width:1500,height:1000},locale:'zh-CN'});
    const token = JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8')).bootstrap_token;
    const session = await context.request.post(origin+'/api/v1/session',{data:{token}});
    assert.equal(session.status(),200);
    const csrf = (await session.json()).csrf;
    const projects = await (await context.request.get(origin+'/api/v1/projects')).json();
    assert(projects.length,'A saved project is needed for the installed-app smoke check');
    const projectId = projects[0].id;
    const standards = await (await context.request.get(origin+`/api/v1/projects/${projectId}/figure-standards`)).json();
    const index = await (await context.request.get(origin+`/api/v1/projects/${projectId}/figure-index`)).json();
    assert.equal(Object.keys(standards.diagram_types).length,12);
    assert(Array.isArray(index.figures));
    let freeBriefVerified = false;
    if (standards.policy.ai_image_policy !== 'forbidden') {
      const before = await (await context.request.get(origin+'/api/v1/tasks')).json();
      const preview = await context.request.post(origin+`/api/v1/projects/${projectId}/paper-visuals/image-brief`,{
        headers:{'X-CSRF-Token':csrf},data:{kind:'architecture',title:'本机提示词编译验收',instruction:'仅检查本机编译流程：一个用户界面调用一个业务服务，不保存图片。',explanation:'本机编译验收用说明，不插入论文。',required_labels:['用户界面','业务服务']}
      });
      assert.equal(preview.status(),200);
      const compiled = await preview.json();
      assert.equal(compiled.paid_request,false);assert.equal(compiled.brief.version,'thesis-image-brief-v2');
      assert(compiled.prompt.includes('本机编译验收用说明'));
      const after = await (await context.request.get(origin+'/api/v1/tasks')).json();
      assert.equal(after.length,before.length);
      freeBriefVerified = true;
    }
    const page = await context.newPage(), errors = [], paid = [];
    await page.addInitScript(id=>localStorage.setItem('paper_project',id),projectId);
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/paper-visuals/image-tasks'))paid.push(request.url());});
    await page.goto(origin);
    await page.getByRole('button',{name:'研究与成果',exact:true}).click();
    await page.getByRole('button',{name:'论文图表',exact:true}).click();
    const dialog = page.getByRole('dialog',{name:'论文图表',exact:true});
    await dialog.getByRole('tab',{name:'架构图',exact:true}).waitFor();
    assert.equal(await dialog.getByRole('tab').count(),12);
    await dialog.getByRole('tab',{name:'架构图',exact:true}).click();
    await dialog.getByRole('button',{name:'生成预览',exact:true}).click();
    await dialog.locator('.pv-preview img').waitFor();
    await dialog.getByRole('tab',{name:'ER 图',exact:true}).click();
    await dialog.getByRole('button',{name:'生成预览',exact:true}).click();
    await dialog.locator('.pv-preview img').waitFor();
    await dialog.getByRole('tab',{name:'三线表',exact:true}).click();
    await dialog.getByRole('button',{name:'生成预览',exact:true}).click();
    await dialog.locator('.pv-table-preview').waitFor();
    assert(await dialog.locator('.pv-grid-editor').evaluate(el=>el.scrollWidth<=el.clientWidth+2),'The default four-column table editor should fit on desktop');
    await dialog.getByRole('tab',{name:'架构图',exact:true}).click();
    await dialog.locator('.pv-standards > summary').click();
    await dialog.getByLabel('图表编号方式',{exact:true}).waitFor();
    await dialog.getByLabel('AI 图片使用政策',{exact:true}).waitFor();
    await dialog.getByLabel('生图必需文字',{exact:true}).waitFor();
    await dialog.getByRole('button',{name:'预览生成提示词（免费）',exact:true}).waitFor();
    await dialog.locator('.pv-standards').screenshot({path:path.join(root,'.runtime/figure-standards-qa/live-standards.png')});
    await page.setViewportSize({width:390,height:844});
    assert(await dialog.getByRole('tab').evaluateAll(tabs=>tabs.every(tab=>tab.getBoundingClientRect().height<=52)));
    assert(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
    assert.deepEqual(errors,[]);
    assert.deepEqual(paid,[]);
    fs.writeFileSync(path.join(root,'.runtime/paper-visuals-qa/live-smoke-report.json'),JSON.stringify({passed:true,entry:'研究与成果 / 论文图表',types:12,previews:['architecture','er','three_line_table'],standards_panel:true,mobile_labels:true,free_brief_verified:freeBriefVerified,errors,project_writes:false,model_calls:0},null,2));
    console.log('Installed workbench: diagram/table previews, free image-brief compiler, prompt controls and mobile layout passed; no project writes or model calls.');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
