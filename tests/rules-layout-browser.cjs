const fs = require('fs'), path = require('path'), assert = require('assert');
const root = path.resolve(__dirname, '..');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const port = process.env.PAPER_TEST_PORT || 8765, origin = `http://127.0.0.1:${port}`;
let token = '';
let diagnosticPage;
const diagnosticErrors = [];
(async () => {
  const session = JSON.parse(fs.readFileSync(path.join(root, `.runtime/session-${port}.json`), 'utf8'));
  token = session.bootstrap_token;
  const browser = await chromium.launch({ headless: true, executablePath: path.join(root, '.runtime/browsers/chromium-1234/chrome-win64/chrome.exe') });
  try {
    const context = await browser.newContext({ viewport: { width: 1480, height: 1080 }, locale: 'zh-CN' });
    assert.equal((await context.request.post(origin + '/api/v1/session', { data: { token } })).status(), 200);
    const projects = await (await context.request.get(origin + '/api/v1/projects')).json(), pid = projects[0].id;
    const before = await (await context.request.get(origin + `/api/v1/projects/${pid}/rules`)).json();
    const page = await context.newPage(), errors = [], unexpected = [], intercepted = [], checks = [];
    diagnosticPage = page;
    page.setDefaultTimeout(20000);
    page.on('pageerror', error => { errors.push(error.message); diagnosticErrors.push(error.message); });
    await page.route('**/api/v1/**', async route => {
      const request = route.request();
      if (['GET', 'HEAD', 'OPTIONS'].includes(request.method()) || request.url().endsWith('/session')) return route.continue();
      if (request.url().endsWith('/rules/batch-review')) {
        intercepted.push({ kind: 'batch', body: request.postDataJSON() });
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ updated: request.postDataJSON().rule_ids.length }) });
      }
      if (request.url().endsWith('/rule-packages')) {
        intercepted.push({ kind: 'publish' });
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 'intercepted-layout-preview' }) });
      }
      unexpected.push(request.url());
      return route.abort();
    });
    await page.addInitScript(id => localStorage.setItem('paper_project', id), pid);
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '论文规则', exact: true }).click();
    const catalog = page.getByRole('region', { name: '规则条款管理' });
    await catalog.locator('.rule-row').first().waitFor();
    assert.equal(await page.getByRole('tabpanel').count(), 1);
    assert.equal(await page.getByRole('tab', { name: '条款管理', exact: true }).getAttribute('aria-selected'), 'true');
    assert.equal(await catalog.locator('.rule-row').count(), Math.min(10, before.rules.length));
    assert.equal(await page.getByLabel('选择AI整理的规则文件').isVisible(), false);
    checks.push('catalog_is_default_and_other_workflows_are_separate');
    const firstIds = await catalog.locator('.rule-row').evaluateAll(rows => rows.map(row => row.dataset.ruleId));
    if (before.rules.length > 10) {
      await page.getByRole('button', { name: '下一页规则', exact: true }).click();
      const secondIds = await catalog.locator('.rule-row').evaluateAll(rows => rows.map(row => row.dataset.ruleId));
      assert(secondIds.every(id => !firstIds.includes(id)));
      await page.getByLabel('每页规则条数').selectOption('20');
      assert.equal(await catalog.locator('.rule-row').count(), Math.min(20, before.rules.length));
      await page.getByText(/^第 1 \/ \d+ 页$/).waitFor();
      await page.getByLabel('每页规则条数').selectOption('10');
    }
    checks.push('pagination_has_distinct_rows_and_page_size_resets_position');
    await page.getByLabel('选择本页规则', { exact: true }).check();
    await catalog.getByRole('button', { name: '排除所选', exact: true }).click();
    assert.deepEqual(new Set(intercepted[0].body.rule_ids), new Set(firstIds));
    checks.push('batch_selection_submits_only_checked_page_with_intercepted_post');
    const sourceRule = before.rules.find(rule => rule.source_id);
    await page.getByLabel('搜索规则', { exact: true }).fill(sourceRule.statement);
    await catalog.locator('.rule-row').first().waitFor();
    assert((await catalog.locator('.rule-row').first().innerText()).includes(sourceRule.statement));
    await catalog.locator('.rule-source-link').first().click();
    await page.getByRole('dialog', { name: '规则原文依据' }).waitFor();
    await page.getByRole('button', { name: '关闭原文依据', exact: true }).click();
    await catalog.getByRole('button', { name: '核对与编辑', exact: true }).first().click();
    await page.getByRole('button', { name: '关闭规则编辑', exact: true }).click();
    await catalog.locator('.rules-row-statement').first().click();
    await page.getByRole('button', { name: '关闭规则编辑', exact: true }).click();
    await page.getByLabel('搜索规则', { exact: true }).fill('不存在的条款_layout_20261024');
    await page.getByRole('heading', { name: '没有匹配的规则', exact: true }).waitFor();
    await page.getByRole('button', { name: '清除筛选', exact: true }).first().click();
    await catalog.locator('.rule-row').first().waitFor();
    await page.locator('.rules-metrics > button').nth(1).click();
    assert.equal(await page.getByRole('tab', { name: /^待确认/ }).getAttribute('aria-selected'), 'true');
    assert.equal(await catalog.locator('.rule-row').count(), Math.min(10, before.readiness.counts.candidate));
    await page.getByRole('tab', { name: /^全部规则/ }).click();
    checks.push('search_empty_state_metrics_filters_source_and_editor_work');
    const out = path.join(root, '.runtime/rules-refactor'); fs.mkdirSync(out, { recursive: true });
    await page.screenshot({ path: path.join(out, 'catalog-desktop.png'), fullPage: true });
    const desktopHeight = await page.locator('.rules-workspace').evaluate(node => Math.round(node.getBoundingClientRect().height));
    assert(desktopHeight < 1700, `Desktop workspace is unexpectedly tall: ${desktopHeight}`);
    await page.getByRole('tab', { name: '整理与审核', exact: true }).click();
    await page.getByText('高级整理与独立审核工具', { exact: true }).click();
    await page.getByLabel('选择AI整理的规则文件').waitFor();
    assert(await page.getByRole('region', { name: '自动规则审核' }).isVisible());
    await page.getByText('历史审核与完整性记录', { exact: true }).click();
    await page.screenshot({ path: path.join(out, 'automation-desktop.png'), fullPage: true });
    checks.push('automation_controls_and_history_records_remain_accessible');
    await page.getByRole('tab', { name: /^重复与冲突/ }).click();
    assert(await page.getByRole('region', { name: '规则重复检查' }).isVisible());
    assert(await page.getByRole('region', { name: '规则冲突', exact: true }).isVisible());
    const group = page.locator('.rule-conflict-group').first();
    if (await group.count()) {
      if ((await group.getAttribute('open')) === null) await group.locator('summary').click();
      await group.getByRole('button', { name: '核对此条', exact: true }).first().click();
      await page.getByRole('button', { name: '关闭规则编辑', exact: true }).click();
    }
    checks.push('duplicates_and_conflict_groups_keep_review_actions');
    await page.screenshot({ path: path.join(out, 'issues-desktop.png'), fullPage: true });
    await page.getByRole('button', { name: '检查并发布', exact: true }).click();
    assert(await page.getByRole('region', { name: '发布前检查' }).isVisible());
    assert.equal(intercepted.filter(item => item.kind === 'publish').length, 0);
    if (before.readiness.can_publish) {
      await page.getByRole('button', { name: '发布规则包', exact: true }).click();
      assert.equal(intercepted.filter(item => item.kind === 'publish').length, 1);
    }
    await page.locator('.rule-publication').getByRole('button', { name: '查看生效快照', exact: true }).click();
    await page.getByRole('dialog', { name: '生效规则快照' }).waitFor();
    await page.getByRole('button', { name: '关闭原文依据', exact: true }).click();
    await page.screenshot({ path: path.join(out, 'publication-desktop.png'), fullPage: true });
    checks.push('publication_is_explicit_and_snapshot_remains_accessible');
    await page.getByRole('tab', { name: '条款管理', exact: true }).focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.getByRole('tab', { name: '整理与审核', exact: true }).getAttribute('aria-selected'), 'true');
    await page.keyboard.press('Home');
    assert.equal(await page.getByRole('tab', { name: '条款管理', exact: true }).getAttribute('aria-selected'), 'true');
    await page.setViewportSize({ width: 390, height: 844 });
    for (const name of ['条款管理', '整理与审核', /^重复与冲突/, '发布与设置']) {
      await page.getByRole('tab', { name, exact: typeof name === 'string' }).click();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Horizontal overflow in ${name}`);
      assert.equal(await page.getByRole('tabpanel').count(), 1);
    }
    await page.getByRole('tab', { name: '条款管理', exact: true }).click();
    await page.getByRole('button', { name: '手工添加', exact: true }).click();
    await page.getByRole('button', { name: '关闭规则编辑', exact: true }).click();
    await page.screenshot({ path: path.join(out, 'catalog-mobile.png'), fullPage: true });
    checks.push('keyboard_navigation_and_all_four_mobile_views_have_no_overflow');
    const after = await (await context.request.get(origin + `/api/v1/projects/${pid}/rules`)).json();
    assert.deepEqual(after.rules, before.rules);
    assert.deepEqual(after.package, before.package);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    const report = { passed: true, checks, desktop_workspace_height: desktopHeight, page_errors: errors, intercepted_writes: intercepted.length, backend_business_writes: 0, model_requests: 0 };
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify(report));
  } catch (error) {
    console.error(JSON.stringify({ page_errors: diagnosticErrors, visible_text: diagnosticPage ? (await diagnosticPage.locator('body').innerText()).slice(-2500) : '' }));
    if (diagnosticPage) await diagnosticPage.screenshot({ path: path.join(root, '.runtime/rules-refactor/failure.png'), fullPage: true });
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(String(error.stack || error).split(token || 'NO_TOKEN').join('[REDACTED]')); process.exitCode = 1; });
