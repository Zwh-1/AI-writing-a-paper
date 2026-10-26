const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..'), port = process.env.PAPER_TEST_PORT || 8765;
const origin = `http://127.0.0.1:${port}`, output = path.join(root, '.runtime/writing-entry');
fs.mkdirSync(output, { recursive: true });

(async () => {
  const token = JSON.parse(fs.readFileSync(path.join(root, `.runtime/session-${port}.json`), 'utf8')).bootstrap_token;
  const browser = await chromium.launch({ headless: true, executablePath: path.join(root, '.runtime/browsers/chromium-1234/chrome-win64/chrome.exe') });
  let page;
  const errors = [], forbidden = [], checks = [], submissions = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'zh-CN' });
    assert.equal((await context.request.post(origin + '/api/v1/session', { data: { token } })).status(), 200);
    const projects = await (await context.request.get(origin + '/api/v1/projects')).json();
    const pid = projects.find(p => p.title === '我的毕业论文')?.id || projects[0].id;
    const prefix = `/api/v1/projects/${pid}`;
    const sections = await (await context.request.get(origin + prefix + '/sections')).json();
    const section = sections.find(s => s.title.startsWith('第二章')) || sections.find(s => s.kind !== 'abstract_en');
    const originalContent = await (await context.request.get(origin + `/api/v1/sections/${section.id}/content`)).json();
    const link = await (await context.request.get(origin + prefix + '/writing-link')).json();
    const realProfiles = await (await context.request.get(origin + prefix + '/writing-profiles')).json();
    assert(section && realProfiles.length, 'This regression needs an existing chapter and model profile');
    const stale = { ...realProfiles[0], configuration_current: false, qualification: 'needs_review' };
    let profiles = [stale], profileError = false, blockPreflight = false, manuscriptPreview = null, polishFixture = false;
    page = await context.newPage(); page.setDefaultTimeout(20000);
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(({ pid, sid }) => {
      localStorage.setItem('paper_project', pid); localStorage.setItem('paper_section', sid);
    }, { pid, sid: section.id });
    await page.route('**/api/v1/**', async route => {
      const req = route.request(), url = new URL(req.url());
      if (req.method() === 'GET' && url.pathname === `/api/v1/sections/${section.id}/content` && polishFixture) return route.fulfill({status:200,json:{...originalContent,document:{type:'doc',content:[{type:'paragraph',attrs:{nodeId:'ui-polish-paragraph'},content:[{type:'text',text:'这个系统尚未部署。'}]}]}}});
      if (req.method() === 'GET' && url.pathname === `/api/v1/sections/${section.id}/writing-context` && manuscriptPreview) return route.fulfill({status:200,json:manuscriptPreview});
      if (req.method() === 'GET' && url.pathname === prefix + '/writing-sessions') return route.fulfill({ status: 200, json: [] });
      if (req.method() === 'GET' && url.pathname === prefix + '/writing-link') {
        return route.fulfill({ status: 200, json: { ...link, model: { name: stale.actual_config.model, configured: true }, readiness: { ...link.readiness, blockers: [] } } });
      }
      if (req.method() === 'GET' && url.pathname === prefix + '/writing-preflight') {
        return route.fulfill({ status: 200, json: { can_start: !blockPreflight, default_budget: 20, warnings: [], context: null, budget: { limit: 20, used: 0, available: 20, currency: 'USD' }, blockers: blockPreflight ? [{ code: 'CONTEXT_BUDGET_EXCEEDED', message: '测试：本节必要原文超过上下文上限' }, { code: 'PROJECT_BUDGET_INSUFFICIENT', message: '测试：项目剩余预算不足请求预留' }] : [] } });
      }
      if (url.pathname === prefix + '/writing-profiles') {
        if (req.method() === 'GET') {
          return route.fulfill({ status: profileError ? 422 : 200, json: profileError ? { detail: '配置读取测试失败' } : profiles });
        }
        if (req.method() === 'POST') {
          assert.deepEqual(req.postDataJSON(), { role: 'draft', configuration: { route: 'primary' } });
          const current = { ...stale, id: 'browser-current-profile', configuration_current: true, qualification: 'unassessed' };
          profiles = [current, stale]; submissions.push({ kind: 'local_profile', body: req.postDataJSON() });
          return route.fulfill({ status: 200, json: current });
        }
      }
      if (req.method() === 'POST' && url.pathname === prefix + '/writing-start') {
        submissions.push({ kind: 'session', body: req.postDataJSON() });
        return route.fulfill({ status: 422, json: { detail: '测试请求已截获，未调用模型' } });
      }
      if (req.method() === 'POST' && url.pathname === `/api/v1/sections/${section.id}/writing-tasks`) {
        submissions.push({ kind: 'ordinary_write', body: req.postDataJSON() });
        return route.fulfill({ status: 200, json: { id: 'browser-writing-task', status: 'queued' } });
      }
      if (['GET', 'HEAD', 'OPTIONS'].includes(req.method()) || url.pathname.endsWith('/session')) return route.continue();
      forbidden.push(req.method() + ' ' + url.pathname); return route.abort();
    });
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    const nav = name => page.getByRole('navigation').getByRole('button', { name, exact: true }).click();
    await nav('写作工作台');
    await page.locator('.editor-heading h2').filter({ hasText: section.title }).waitFor();
    const composer = page.getByRole('region', { name: '本节辅助写作', exact: true });
    await composer.waitFor();
    const generate = composer.getByRole('button', { name: '生成候选稿', exact: true });
    const position = await generate.boundingBox();
    assert(position && position.y >= 0 && position.y + position.height < 1000, 'Generate button should be visible before scrolling');
    assert(await composer.evaluate(node => Boolean(node.compareDocumentPosition(document.querySelector('.paper-canvas')) & Node.DOCUMENT_POSITION_FOLLOWING)));
    await page.locator('.writing-link-card').getByText('待写作', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(output, 'editor-desktop.png') });
    await generate.click();
    await page.getByText('写作任务已创建；完成后在任务中心审阅候选稿', { exact: true }).waitFor();
    const ordinary = submissions.find(s => s.kind === 'ordinary_write').body;
    assert.equal(ordinary.operation, 'generate'); assert.equal(ordinary.base_revision_id, section.current_revision_id);
    checks.push('ordinary_generation_visible_above_paper_and_submits_current_chapter_without_facts');
    for (const width of [1024, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Editor overflow at ' + width);
    }
    await page.setViewportSize({ width: 390, height: 900 });
    await generate.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'editor-mobile.png') });
    checks.push('writing_controls_fit_tablet_and_mobile');
    await page.setViewportSize({ width: 1600, height: 1000 });
    for (const operation of ['revise','expand','compress']) {
      const before = submissions.filter(s => s.kind === 'ordinary_write').length;
      await composer.getByLabel('写作操作').selectOption(operation);
      const written = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/sections/${section.id}/writing-tasks` && response.request().method() === 'POST');
      await generate.click();
      await written;
      assert.equal(submissions.filter(s => s.kind === 'ordinary_write').length, before+1);
      assert.equal(submissions.filter(s => s.kind === 'ordinary_write').at(-1).body.operation, operation);
    }
    checks.push('generation_revision_expansion_and_compression_recheck_full_manuscript');
    if (!originalContent.document.content?.some(node => node.type === 'paragraph' && node.content?.some(child => child.type === 'text' && child.text.trim()))) {
      await composer.getByLabel('写作操作').selectOption('polish');
      assert(await composer.getByRole('button',{name:'生成润写候选',exact:true}).isDisabled());
      assert(await composer.getByRole('note').filter({hasText:'请先保存需要润写的正文。'}).isVisible());
      checks.push('polish_requires_original_text_before_submission');
    }
    polishFixture=true;
    await page.reload({waitUntil:'domcontentloaded'});await nav('写作工作台');
    await composer.getByLabel('写作操作').selectOption('polish');
    await composer.getByRole('note').filter({hasText:'润写按独立规范'}).waitFor();
    assert(await composer.getByPlaceholder('例如：改善句式与衔接，保持原有事实、数值和结论。').isVisible());
    const polishSubmission=page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/sections/${section.id}/writing-tasks` && response.request().method()==='POST');
    await composer.getByRole('button',{name:'生成润写候选',exact:true}).click();await polishSubmission;
    assert.equal(submissions.filter(s=>s.kind==='ordinary_write').at(-1).body.operation,'polish');
    await page.screenshot({path:path.join(output,'independent-polish.png')});
    for (const width of [768,390]) {
      await page.setViewportSize({width,height:900});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Independent polish overflow at '+width);
    }
    await page.screenshot({path:path.join(output,'independent-polish-mobile.png')});
    await page.setViewportSize({width:1600,height:1000});
    checks.push('polish_has_independent_instructions_and_candidate_action');
    polishFixture=false;
    manuscriptPreview={section_id:section.id,scope:'full_saved_manuscript',can_load:true,complete:true,total_sections:sections.length,saved_sections:2,characters:2345,sections:sections.map((s,i)=>({section_id:s.id,title:s.title,revision_id:i<2?'ui-fixture-'+i:null,characters:i===0?1200:i===1?1145:0,has_content:i<2}))};
    await page.reload({waitUntil:'domcontentloaded'});await nav('写作工作台');
    await composer.getByText(`全文上下文：2/${sections.length} 章已有正文 · 2,345 字`,{exact:true}).click();
    assert.equal(await composer.locator('.writing-context li').count(), sections.length);
    checks.push('full_manuscript_coverage_is_visible_and_expandable');
    manuscriptPreview={...manuscriptPreview,can_load:false,message:'测试：完整论文超过上下文预算，未退回局部上下文。'};
    await page.reload({waitUntil:'domcontentloaded'});await nav('写作工作台');
    await composer.getByText(manuscriptPreview.message,{exact:true}).waitFor();assert(await generate.isDisabled());
    checks.push('full_manuscript_budget_failure_blocks_generation_visibly');
    manuscriptPreview=null;
    await nav('论文规则');await page.getByRole('tab',{name:'条款管理',exact:true}).click();
    await page.getByRole('button',{name:'核对与编辑',exact:true}).first().click();
    const ruleDialog=page.getByRole('dialog');
    const reviseCheckbox=ruleDialog.getByRole('checkbox',{name:'改写',exact:true});
    const reviseBefore=await reviseCheckbox.isChecked();
    await ruleDialog.getByRole('checkbox',{name:'润写',exact:true}).check();
    assert.equal(await reviseCheckbox.isChecked(),reviseBefore);
    checks.push('polish_rule_scope_can_be_selected_independently_of_revision');
    await page.reload({waitUntil:'domcontentloaded'});
    await nav('有据写作');
    await page.getByText('可以开始组织写作', { exact: true }).waitFor();
    await page.getByRole('button', { name: '本节写作与核验', exact: true }).click();
    const sessionButton = page.getByRole('button', { name: '开始本节写作（调用模型）', exact: true });
    await sessionButton.waitFor();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('button')).some(button => button.textContent === '开始本节写作（调用模型）' && !button.disabled));
    await page.getByLabel('章节', { exact: true }).selectOption(section.id);
    const profileSelect = page.getByLabel('阶段配置', { exact: true });
    assert.equal(await profileSelect.inputValue(), '');
    assert.equal(await profileSelect.isVisible(), false);
    await sessionButton.click();
    await page.getByRole('alert').filter({ hasText: '测试请求已截获，未调用模型' }).waitFor();
    const automatic = submissions.find(s => s.kind === 'session').body;
    assert.equal(automatic.section_id, section.id);
    assert.equal(automatic.base_revision_id, section.current_revision_id);
    assert.equal(automatic.expected_project_state_revision, link.state.revision);
    assert.equal(automatic.profile_id, undefined);
    await page.getByRole('button', { name: '关闭错误', exact: true }).click();
    checks.push('stale_stage_profiles_do_not_disable_start_with_current_interface_settings');
    await page.getByText('高级设置：阶段配置与预算', { exact: true }).click();
    assert.equal(await profileSelect.locator(`option[value="${stale.id}"]`).evaluate(option => option.disabled), true);
    await page.getByRole('button', { name: '使用当前接口设置建立写作配置', exact: true }).click();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('select')).some(s => s.value === 'browser-current-profile'));
    assert.equal(submissions.filter(s => s.kind === 'local_profile').length, 1);
    assert.equal(await profileSelect.inputValue(), 'browser-current-profile');
    assert.match(await profileSelect.locator('option:checked').innerText(), /未评估/);
    await sessionButton.click();
    await page.getByRole('alert').filter({ hasText: '测试请求已截获，未调用模型' }).waitFor();
    const explicit = submissions.filter(s => s.kind === 'session').at(-1).body;
    assert.equal(explicit.profile_id, 'browser-current-profile');
    await page.getByRole('button', { name: '关闭错误', exact: true }).click();
    checks.push('advanced_configuration_remains_optional_and_explicit_overrides_are_submitted');
    await page.screenshot({ path: path.join(output, 'writing-launch-desktop.png') });
    profileError = true;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await nav('有据写作');
    await page.getByText('可以开始组织写作', { exact: true }).waitFor();
    await page.getByRole('button', { name: '本节写作与核验', exact: true }).click();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('button')).some(button => button.textContent === '开始本节写作（调用模型）' && !button.disabled));
    checks.push('advanced_profile_read_failure_keeps_current_interface_launch_available');
    profileError = false; profiles = [];
    await page.reload({ waitUntil: 'domcontentloaded' });
    await nav('有据写作');
    await page.getByText('可以开始组织写作', { exact: true }).waitFor();
    await page.getByRole('button', { name: '本节写作与核验', exact: true }).click();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('button')).some(button => button.textContent === '开始本节写作（调用模型）' && !button.disabled));
    assert.equal(await profileSelect.inputValue(), '');
    checks.push('first_use_without_profiles_can_start_writing_directly');
    blockPreflight = true;
    await page.reload({ waitUntil: 'domcontentloaded' }); await nav('有据写作');
    await page.getByRole('button', { name: '本节写作与核验', exact: true }).click();
    await page.getByText('当前章节尚不能启动写作', { exact: true }).waitFor();
    assert(await sessionButton.isDisabled());
    assert.equal(await page.getByText('测试：本节必要原文超过上下文上限', { exact: true }).count(), 1);
    assert.equal(await page.getByText('测试：项目剩余预算不足请求预留', { exact: true }).count(), 1);
    checks.push('context_and_budget_blockers_are_visible_before_submission');
    assert.deepEqual(errors, []); assert.deepEqual(forbidden, []);
    const report = { passed: true, checks, errors, forbidden, model_calls: 0, real_project_mutations: 0, intercepted_submissions: submissions.map(s => s.kind) };
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true });
    console.error(error); console.error({ errors, forbidden }); process.exitCode = 1;
  } finally { await browser.close(); }
})();
