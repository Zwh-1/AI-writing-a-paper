const fs = require('fs'), path = require('path'), assert = require('assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..'), port = process.env.PAPER_TEST_PORT || '8797';
const origin = 'http://127.0.0.1:' + port;
const out = path.join(root, '.runtime/autopilot-budget-browser');
let token = '';

(async () => {
  token = JSON.parse(fs.readFileSync(path.join(root, '.runtime/session-' + port + '.json'), 'utf8')).bootstrap_token;
  const browser = await chromium.launch({headless: true, executablePath: path.join(root, '.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}, locale: 'zh-CN'});
    const auth = await context.request.post(origin + '/api/v1/session', {data: {token}});
    assert.equal(auth.status(), 200);
    const csrf = (await auth.json()).csrf;
    const ids = [];
    for (const title of ['预算同步回归 A', '预算同步回归 B']) {
      const res = await context.request.post(origin + '/api/v1/projects', {headers: {'X-CSRF-Token': csrf}, data: {title}});
      assert.equal(res.status(), 200); ids.push((await res.json()).id);
    }
    const rate = {total: 1, ai_covered: 0, automatic_completed: 0, ai_percent: 0, automatic_percent: 0, target_met: false};
    function fixture(available) {
      return {policy: {enabled: false, auto_publish: true}, materials: rate, rules: rate, exceptions: [],
        exception_count: 1, tasks: [], budget: {limit: 20, available, currency: 'USD', unknown_reserved: 12.25, can_call: true},
        pricing: {currency: 'USD'}, model_ready: true, note: '隔离测试'};
    }
    const fixtures = {[ids[0]]: fixture(1.1), [ids[1]]: fixture(.65)};
    const page = await context.newPage(), errors = [], submissions = [], unexpected = [], checks = [];
    page.on('pageerror', e => errors.push(e.message)); page.setDefaultTimeout(25000);
    let rejectNext = false;
    await page.route('**/api/v1/**', async route => {
      const request = route.request(), url = new URL(request.url());
      const id = ids.find(id => url.pathname.includes('/projects/' + id + '/'));
      if (id && url.pathname.endsWith('/ai-autopilot')) {
        if (request.method() === 'POST') {
          const body = request.postDataJSON(); submissions.push(body);
          if (rejectNext) {
            rejectNext = false; fixtures[id].budget.available = .3;
            return route.fulfill({status: 409, json: {detail: {code: 'AUTOPILOT_BUDGET_CHANGED', message: '可用预算已更新为 0.3000', budget: fixtures[id].budget}}});
          }
          await new Promise(resolve => setTimeout(resolve, 200));
          fixtures[id].tasks = [{id: 'synthetic-round', status: 'running', phase: '隔离测试处理中', progress: 10}];
          fixtures[id].policy.budget_limit = body.budget_limit;
          return route.fulfill({json: fixtures[id].tasks[0]});
        }
        return route.fulfill({json: fixtures[id]});
      }
      if (id && url.pathname.endsWith('/tasks/billing-reconcile')) {
        return route.fulfill({json: {profiles: [{profile: 'gpt-fallback', reconciled: false, reason: '账单查询 HTTP 403'}], budget: fixtures[id].budget}});
      }
      if (url.pathname === '/api/v1/tasks') return route.fulfill({json: []});
      if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method()) && !url.pathname.endsWith('/session')) {
        unexpected.push(url.pathname); return route.abort();
      }
      return route.continue();
    });
    await page.addInitScript(id => localStorage.setItem('paper_project', id), ids[0]);
    await page.goto(origin, {waitUntil: 'domcontentloaded'});
    await page.getByRole('button', {name: '资料中心', exact: true}).click();
    await page.getByRole('tab', {name: 'AI 处理', exact: true}).click();
    const panel = page.getByRole('region', {name: '资料与规则AI托管'});
    const budget = panel.getByLabel('AI托管预算上限');
    const start = panel.getByRole('button', {name: 'AI 接管资料与规则（计费）', exact: true});
    const waitBudget = value => page.waitForFunction(value => document.querySelector('[aria-label="AI托管预算上限"]')?.value === value, String(value));
    await waitBudget(1.1);
    fixtures[ids[0]].budget.available = 1.0549;
    await waitBudget(1.05);
    assert.equal(await start.isEnabled(), true);
    checks.push('untouched_budget_tracks_new_available_amount_and_rounds_down');
    await budget.fill('1.1');
    assert.equal(await start.isDisabled(), true);
    await panel.getByRole('button', {name: '使用当前可用预算 1.05 USD', exact: true}).click();
    await waitBudget(1.05); assert.equal(submissions.length, 0);
    await budget.fill('.5'); fixtures[ids[0]].budget.available = .4;
    await panel.getByText('项目可用 0.40 USD', {exact: true}).waitFor();
    assert.equal(Number(await budget.inputValue()), .5);
    assert.equal(await start.isDisabled(), true);
    await panel.getByRole('button', {name: '使用当前可用预算 0.40 USD', exact: true}).click();
    checks.push('manual_cap_is_preserved_and_over_limit_input_has_one_click_repair');
    await panel.getByRole('button', {name: '核对账单并刷新预算', exact: true}).click();
    await panel.getByText('账单未完全核对，保留未核实预留：账单查询 HTTP 403', {exact: true}).waitFor();
    assert.equal(fixtures[ids[0]].budget.unknown_reserved, 12.25);
    assert.equal(submissions.length, 0);
    checks.push('billing_403_is_visible_without_auto_retry_or_clearing_reservations');
    rejectNext = true; await start.click();
    await panel.getByText('可用预算已更新为 0.3000', {exact: true}).waitFor();
    await waitBudget(.3);
    assert.equal(submissions.length, 1);
    checks.push('server_budget_race_refreshes_input_and_preserves_error');
    await start.evaluate(button => { button.click(); button.click(); });
    await panel.getByRole('button', {name: 'AI 正在托管处理', exact: true}).waitFor();
    assert.equal(submissions.length, 2);
    assert.equal(submissions[1].budget_limit, .3);
    fixtures[ids[0]].budget.available = .2;
    await panel.getByText('项目可用 0.20 USD', {exact: true}).waitFor();
    assert.equal(await budget.inputValue(), '0.3');
    fixtures[ids[0]].budget.available = .18;
    fixtures[ids[0]].tasks[0].status = 'incomplete';
    await waitBudget(.18);
    checks.push('duplicate_start_is_locked_and_running_authorized_cap_is_not_rewritten');
    await page.getByLabel('切换论文项目').selectOption(ids[1]);
    await page.getByRole('tab', {name: 'AI 处理', exact: true}).click();
    await waitBudget(.65);
    fixtures[ids[1]].budget.available = .009;
    await waitBudget(0); assert.equal(await start.isDisabled(), true);
    checks.push('project_switch_resets_budget_and_subcent_funds_do_not_enable_start');
    fixtures[ids[1]].budget.available = 1.0588556;
    await waitBudget(1.05);
    fs.mkdirSync(out, {recursive: true});
    await page.screenshot({path: path.join(out, 'desktop.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({path: path.join(out, 'mobile.png'), fullPage: true});
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    const report = {passed: true, synthetic_fixture: true, checks, page_errors: errors, intercepted_starts: submissions.length, paid_requests: 0};
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => {console.error(String(error.stack || error).split(token || 'NO_TOKEN').join('[REDACTED]')); process.exitCode = 1;});
