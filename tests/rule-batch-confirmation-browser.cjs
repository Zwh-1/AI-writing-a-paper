const fs = require('fs'), path = require('path'), assert = require('assert');
const root = path.resolve(__dirname, '..'), port = process.env.PAPER_TEST_PORT || 8765, origin = `http://127.0.0.1:${port}`;
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
let token = '';
(async () => {
  token = JSON.parse(fs.readFileSync(path.join(root, `.runtime/session-${port}.json`), 'utf8')).bootstrap_token;
  const browser = await chromium.launch({ headless: true, executablePath: path.join(root, '.runtime/browsers/chromium-1234/chrome-win64/chrome.exe') });
  try {
    const context = await browser.newContext({ viewport: { width: 1480, height: 1080 }, locale: 'zh-CN' });
    assert.equal((await context.request.post(origin + '/api/v1/session', { data: { token } })).status(), 200);
    const pid = (await (await context.request.get(origin + '/api/v1/projects')).json())[0].id;
    const before = await (await context.request.get(origin + `/api/v1/projects/${pid}/rules`)).json();
    const fixture = structuredClone(before);
    fixture.rules = fixture.rules.slice(0, 13).map((rule, index) => ({ ...rule, state: 'candidate', modality: index === 12 ? 'pending' : 'recommended' }));
    const original = structuredClone(fixture.rules), calls = [], errors = [], unexpected = [], checks = [];
    const failed = fixture.rules[1].id;
    let rejectedOnce = false;
    const recount = () => {
      fixture.readiness.counts = Object.fromEntries(['candidate', 'confirmed', 'machine_verified', 'reference_only', 'rejected'].map(state => [state, fixture.rules.filter(rule => rule.state === state).length]));
      fixture.readiness.publishable_count = fixture.rules.filter(rule => rule.state === 'confirmed' && ['mandatory', 'recommended'].includes(rule.modality)).length;
    };
    recount();
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/**', async route => {
      const request = route.request(), url = new URL(request.url());
      if (request.method() === 'GET' && url.pathname === `/api/v1/projects/${pid}/rules`) return route.fulfill({ json: fixture });
      if (request.method() === 'PATCH' && url.pathname.startsWith('/api/v1/rules/')) {
        const id = url.pathname.split('/').at(-1), rule = fixture.rules.find(item => item.id === id);
        assert(rule, 'Only displayed selected rules may be confirmed');
        assert.deepEqual(request.postDataJSON(), { state: 'confirmed' });
        calls.push(id);
        await new Promise(resolve => setTimeout(resolve, 80));
        if (id === failed && !rejectedOnce) {
          rejectedOnce = true;
          return route.fulfill({ status: 503, json: { detail: '测试保存失败，请重试' } });
        }
        rule.state = 'confirmed'; recount();
        return route.fulfill({ json: rule });
      }
      if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method()) && !url.pathname.endsWith('/session')) {
        unexpected.push(url.pathname); return route.abort();
      }
      return route.continue();
    });
    await page.addInitScript(id => localStorage.setItem('paper_project', id), pid);
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '论文规则', exact: true }).click();
    await page.getByRole('tab', { name: /^待确认/ }).click();
    const catalog = page.getByRole('region', { name: '规则条款管理' });
    await catalog.locator('.rule-row').first().waitFor();
    await page.getByLabel('选择本页规则', { exact: true }).check();
    assert(await catalog.getByRole('button', { name: '确认所选', exact: true }).isEnabled());
    assert.equal(await catalog.getByRole('button', { name: '恢复待确认', exact: true }).count(), 0);
    const out = path.join(root, '.runtime/rule-batch-confirmation'); fs.mkdirSync(out, { recursive: true });
    await catalog.screenshot({ path: path.join(out, 'confirm-selected-desktop.png') });
    await catalog.getByRole('button', { name: '确认所选', exact: true }).click();
    assert(await catalog.getByRole('button', { name: '正在确认…', exact: true }).isDisabled());
    await catalog.getByRole('alert').getByText(/已确认 9 条；1 条未能确认/).waitFor();
    assert.deepEqual(calls, original.slice(0, 10).map(rule => rule.id));
    assert.equal(await catalog.locator('.rule-row input:checked').count(), 1);
    assert.equal(await catalog.locator('.rule-row input:checked').getAttribute('aria-label'), `选择规则：${original[1].statement}`);
    checks.push('pending_page_selection_exposes_working_confirm_action', 'partial_failure_keeps_only_failed_selection');
    await catalog.getByRole('button', { name: '确认所选', exact: true }).click();
    await catalog.getByRole('status').getByText(/已确认 1 条/).waitFor();
    assert.deepEqual(calls.slice(10), [failed]);
    assert.equal(fixture.readiness.counts.confirmed, 10);
    assert.equal(fixture.readiness.counts.candidate, 3);
    checks.push('retry_does_not_reconfirm_successful_items');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByLabel('选择本页规则', { exact: true }).check();
    await catalog.getByText(/所选中有 1 条仍为待判断或示例/).waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await catalog.screenshot({ path: path.join(out, 'confirm-selected-mobile.png') });
    await catalog.getByRole('button', { name: '确认所选', exact: true }).click();
    await catalog.getByRole('status').getByText(/已确认 3 条/).waitFor();
    await catalog.getByText(/已确认的条目中仍有待判断或示例/).waitFor();
    assert.equal(fixture.rules[12].modality, 'pending');
    assert.equal(fixture.readiness.publishable_count, 12);
    for (let index = 0; index < fixture.rules.length; index++) {
      const { state: currentState, ...current } = fixture.rules[index], { state: originalState, ...previous } = original[index];
      assert.equal(currentState, 'confirmed'); assert.deepEqual(current, previous);
    }
    await page.getByRole('tab', { name: /^已确认/ }).click();
    assert.equal(await catalog.locator('.rule-row').count(), 10);
    await catalog.getByText(/筛选结果 13 条/).waitFor();
    checks.push('mobile_confirmation_and_confirmed_filter_work', 'confirmation_preserves_modality_statement_source_and_execution_config');
    const after = await (await context.request.get(origin + `/api/v1/projects/${pid}/rules`)).json();
    assert.deepEqual(after.rules, before.rules); assert.deepEqual(after.package, before.package);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    const report = { passed: true, synthetic_candidate_fixture: true, checks, intercepted_confirmation_patches: calls.length, backend_business_writes: 0, model_requests: 0, page_errors: errors };
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => { console.error(String(error.stack || error).split(token || 'NO_TOKEN').join('[REDACTED]')); process.exitCode = 1; });
