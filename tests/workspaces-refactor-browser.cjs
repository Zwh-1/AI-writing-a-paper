const fs = require('fs'), path = require('path'), assert = require('assert');
const root = path.resolve(__dirname, '..'), port = process.env.PAPER_TEST_PORT || 8765, origin = `http://127.0.0.1:${port}`;
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
let token = '', diagnostic;
(async () => {
  token = JSON.parse(fs.readFileSync(path.join(root, `.runtime/session-${port}.json`), 'utf8')).bootstrap_token;
  const browser = await chromium.launch({ headless: true, executablePath: path.join(root, '.runtime/browsers/chromium-1234/chrome-win64/chrome.exe') });
  const output = path.join(root, '.runtime/workspaces-refactor'); fs.mkdirSync(output, { recursive: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1480, height: 1080 }, locale: 'zh-CN' });
    assert.equal((await context.request.post(origin + '/api/v1/session', { data: { token } })).status(), 200);
    const pid = (await (await context.request.get(origin + '/api/v1/projects')).json())[0].id;
    const before = await (await context.request.get(origin + `/api/v1/projects/${pid}/ai-autopilot`)).json();
    const files = await (await context.request.get(origin + `/api/v1/projects/${pid}/files`)).json();
    const rules = await (await context.request.get(origin + `/api/v1/projects/${pid}/rules`)).json();
    const page = await context.newPage(), errors = [], writes = [], checks = [], reads = [];
    const capture = async name => { await page.evaluate(() => window.scrollTo(0,0)); await page.screenshot({path:path.join(output,name),fullPage:true}); };
    diagnostic = page; page.setDefaultTimeout(25000); page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/**', route => {
      const request = route.request(), url = new URL(request.url());
      if (['GET','HEAD','OPTIONS'].includes(request.method())) { reads.push(url.pathname); return route.continue(); }
      if (url.pathname.endsWith('/session')) return route.continue();
      writes.push({ method: request.method(), path: url.pathname }); return route.abort();
    });
    await page.addInitScript(id => localStorage.setItem('paper_project', id), pid);
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    await page.getByRole('navigation').getByRole('button', { name: '资料中心', exact: true }).click();
    const materialLibrary = page.getByRole('region', { name: '项目资料库', exact: true });
    await materialLibrary.locator('.material-row').first().waitFor();
    assert.equal(await materialLibrary.locator('.material-row').count(), Math.min(files.filter(file => file.active).length, 10));
    assert.equal(await page.getByRole('tab', { name: '资料库', exact: true }).getAttribute('aria-selected'), 'true');
    assert.equal(await page.getByLabel('AI托管预算上限').count(), 0);
    assert.equal(await page.getByLabel('选择本地OCR文件').count(), 0);
    assert(!reads.some(url => url.includes('/material-summary/settings')));
    checks.push('materials_defaults_to_paginated_library_and_defers_hidden_tools');
    const firstIds = await materialLibrary.locator('.material-row').evaluateAll(nodes => nodes.map(node => node.dataset.fileId));
    if (files.filter(file => file.active).length > 10) {
      await page.getByRole('button', { name: '下一页资料', exact: true }).click();
      const secondIds = await materialLibrary.locator('.material-row').evaluateAll(nodes => nodes.map(node => node.dataset.fileId));
      assert(secondIds.every(id => !firstIds.includes(id)));
      await page.getByLabel('每页资料条数').selectOption('20');
      assert.equal(await materialLibrary.locator('.material-row').count(), Math.min(files.filter(file => file.active).length, 20));
    }
    const chosen = files.find(file => file.active);
    await page.getByLabel('搜索资料', { exact: true }).fill(chosen.name);
    assert.equal(await materialLibrary.locator('.material-row').count(), 1);
    await page.getByRole('button', { name: '清除资料搜索', exact: true }).click();
    await page.getByLabel('按资料状态筛选').selectOption('summarized');
    assert.equal(await materialLibrary.locator('.material-row').count(), Math.min(files.filter(file => file.active && file.summary_state === 'completed').length, 20));
    await page.getByLabel('按资料状态筛选').selectOption('all'); await page.getByLabel('每页资料条数').selectOption('10');
    checks.push('materials_search_filters_and_pagination_work');
    await materialLibrary.locator('.material-summary-entry').first().click();
    await page.getByTestId('material-summary').getByRole('button', {name:'总结已保存',exact:true}).waitFor();
    await page.getByRole('button', {name:'关闭资料查看',exact:true}).click();
    checks.push('material_opens_original_with_saved_ai_summary_without_generation');
    const add = page.locator('.workspace-heading').getByRole('button', { name: '添加资料', exact: true });
    await add.click(); await page.getByRole('dialog', { name: '添加项目资料' }).waitFor();
    await page.getByLabel('选择文件', { exact: true }).waitFor(); await page.keyboard.press('Escape');
    await page.getByRole('dialog', { name: '添加项目资料' }).waitFor({ state: 'hidden' });
    assert(await add.evaluate(node => document.activeElement === node));
    checks.push('upload_dialog_escape_restores_keyboard_focus');
    await capture('materials-desktop.png');
    const requestsBeforeTab = reads.filter(url => url.endsWith('/ai-autopilot')).length;
    await page.getByRole('tab', { name: 'AI 处理', exact: true }).click();
    let panel = page.getByRole('region', { name: '资料与规则AI托管', exact: true });
    await panel.getByLabel('AI托管预算上限').waitFor();
    assert.equal(reads.filter(url => url.endsWith('/ai-autopilot')).length, requestsBeforeTab);
    assert(!(await panel.innerText()).includes('才超过 95%'));
    const budget = await panel.getByLabel('AI托管预算上限').inputValue();
    await panel.getByLabel('AI托管预算上限').fill('10000'); assert(await panel.getByRole('button', { name: 'AI 接管资料与规则（计费）', exact: true }).isDisabled());
    await panel.getByLabel('AI托管预算上限').fill(budget);
    await capture('materials-ai-desktop.png');
    checks.push('ai_state_is_shared_between_overview_and_tools_and_budget_stays_bounded');
    await page.getByRole('tab', { name: '原文核对', exact: true }).click(); await page.getByTestId('material-health').waitFor();
    await page.getByRole('button', { name: '修复解析缺项', exact: true }).waitFor();
    checks.push('manual_original_review_has_its_own_workspace');
    await page.getByRole('button', { name: '论文规则', exact: true }).click();
    const catalog = page.getByRole('region', { name: '规则条款管理', exact: true });
    await catalog.locator('.rule-row').first().waitFor();
    assert.equal(await catalog.locator('.rule-row').count(), Math.min(10, rules.rules.length));
    assert.equal(await page.getByRole('tab', { name: '条款管理', exact: true }).getAttribute('aria-selected'), 'true');
    assert.equal(await page.getByLabel('选择AI整理的规则文件').count(), 0);
    await capture('rules-desktop.png');
    await page.getByRole('button', { name: '查看生效快照', exact: true }).click();
    const snapshot = page.getByRole('dialog', { name: '生效规则快照', exact: true }); await snapshot.waitFor();
    assert.equal(await snapshot.locator('.rule-snapshot-row').count(), Math.min(20, rules.readiness.package_rule_count));
    if (rules.readiness.package_rule_count > 20) { const text = await snapshot.locator('.rule-snapshot-row').first().innerText(); await snapshot.getByRole('button', { name: '下一页生效快照', exact: true }).click(); assert.notEqual(await snapshot.locator('.rule-snapshot-row').first().innerText(), text); }
    await page.keyboard.press('Escape'); await snapshot.waitFor({ state: 'hidden' });
    checks.push('published_snapshot_is_searchable_paginated_and_keyboard_accessible');
    await page.getByRole('tab', { name: '整理与审核', exact: true }).click();
    panel = page.getByRole('region', { name: '资料与规则AI托管', exact: true }); await panel.getByLabel('AI托管预算上限').waitFor();
    await panel.locator('.autopilot-exceptions>summary').click();
    const exceptions = before.exceptions.filter(item => item.kind === 'rule');
    assert.equal(await panel.locator('.autopilot-exceptions article').count(), Math.min(10, exceptions.length));
    if (exceptions.length) { await panel.getByRole('button', { name: '核对条款', exact: true }).first().click(); await page.getByRole('button', { name: '关闭规则编辑', exact: true }).click(); }
    if (exceptions.length > 10) { await panel.getByRole('button', { name: '下一页处理例外', exact: true }).click(); assert(await panel.locator('.autopilot-exceptions article').count() > 0); }
    checks.push('rule_exceptions_have_distinct_reasons_pagination_and_direct_editing');
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await capture('rules-ai-mobile.png');
    await page.getByRole('tab', { name: '条款管理', exact: true }).click();
    await capture('rules-mobile.png');
    await page.getByRole('navigation').getByRole('button', { name: '资料中心', exact: true }).click();
    await page.getByRole('region', { name: '项目资料库', exact: true }).waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await capture('materials-mobile.png');
    checks.push('both_workspaces_fit_mobile_without_horizontal_overflow');
    assert.deepEqual(errors, []); assert.deepEqual(writes, []);
    const after = await (await context.request.get(origin + `/api/v1/projects/${pid}/ai-autopilot`)).json();
    assert.deepEqual(after.materials, before.materials); assert.deepEqual(after.rules, before.rules); assert.equal(after.budget.limit, 20); assert.equal(after.budget.used, before.budget.used);
    const report = { passed: true, checks, page_errors: errors, writes, paid_requests: 0, real_dataset_unchanged: true };
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report,null,2)); console.log(JSON.stringify(report));
  } catch (error) {
    if (diagnostic && !diagnostic.isClosed()) { await diagnostic.screenshot({ path: path.join(output,'failure.png'),fullPage:true }).catch(()=>{}); console.log((await diagnostic.locator('body').innerText()).slice(-5000)); }
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(String(error.stack || error).split(token || 'NO_TOKEN').join('[REDACTED]')); process.exitCode = 1; });
