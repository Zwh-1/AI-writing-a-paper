const fs = require('fs'), path = require('path'), assert = require('assert');
const root = path.resolve(__dirname, '..');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin = 'http://127.0.0.1:' + (process.env.PAPER_TEST_PORT || 8765);
let token = '';
(async () => {
  const session = JSON.parse(fs.readFileSync(path.join(root, '.runtime', `session-${process.env.PAPER_TEST_PORT || 8765}.json`), 'utf8'));
  token = session.bootstrap_token;
  const browser = await chromium.launch({ headless: true, executablePath: path.join(root, '.runtime/browsers/chromium-1234/chrome-win64/chrome.exe') });
  try {
    const context = await browser.newContext({ viewport: { width: 1480, height: 1080 }, locale: 'zh-CN' });
    assert.equal((await context.request.post(origin + '/api/v1/session', { data: { token } })).status(), 200);
    const projects = await (await context.request.get(origin + '/api/v1/projects')).json();
    const pid = projects[0].id;
    const tasks = await (await context.request.get(origin + '/api/v1/tasks?project_id=' + pid)).json();
    const task = tasks.find(t => t.kind === 'rules_ai' && JSON.parse(t.result || '{}').review?.unreviewed_count > 0);
    assert(task, 'A saved incomplete review is required for this read-only check');
    const review = JSON.parse(task.result).review;
    const response = await context.request.get(origin + `/api/v1/tasks/${task.id}/events`);
    assert.equal(response.status(), 200);
    const events = (await response.text()).split(/\r?\n/).filter(line => line.startsWith('data: ')).map(line => JSON.parse(line.slice(6)));
    const scoped = [...new Set(events.flatMap(event => event.rule_ids || []))];
    const decisions = new Map(events.filter(event => event.rule_id && event.state).map(event => [event.rule_id, event]));
    const workspace = await (await context.request.get(origin + `/api/v1/projects/${pid}/rules`)).json();
    const currentRules = new Map(workspace.rules.map(rule => [rule.id, rule]));
    const buckets = { machine_verified: 'accepted', knowledge: 'reference', example: 'reference', conditional_reference: 'reference', needs_resolution: 'uncertain' };
    const details = {
      counts: { accepted: review.accepted_count, reference: review.reference_count, uncertain: review.uncertain_count, unreviewed: review.unreviewed_count },
      items: scoped.map(id => ({ rule: currentRules.get(id), bucket: buckets[decisions.get(id)?.state] || 'unreviewed', reason: decisions.get(id)?.error })),
    };
    const errors = [], writes = [], checks = [];
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => {
      if (r.url().includes('/api/') && !r.url().endsWith('/api/v1/session') && !['GET', 'HEAD', 'OPTIONS'].includes(r.method())) writes.push(r.url());
    });
    await page.addInitScript(id => localStorage.setItem('paper_project', id), pid);
    await page.goto(origin, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '论文规则', exact: true }).click();
    await page.getByRole('tab', { name: '整理与审核', exact: true }).click();
    await page.getByText('高级整理与独立审核工具', { exact: true }).click();
    const jobs = page.locator('.rule-jobs');
    if (!(await jobs.locator(`[data-task-id="${task.id}"]`).count())) await jobs.getByRole('button', { name: /查看历史整理记录/ }).click();
    const card = jobs.locator(`[data-task-id="${task.id}"]`);
    await card.getByRole('button', { name: `未审核 ${review.unreviewed_count} 条`, exact: true }).click();
    const panel = card.getByRole('region', { name: '审核条款详情' });
    await panel.locator('.rule-review-item').first().waitFor();
    assert.equal(await panel.locator('.rule-review-item').count(), details.counts.unreviewed);
    const unresolved = details.items.find(item => item.bucket === 'unreviewed');
    await panel.getByText(unresolved.reason, { exact: true }).first().waitFor();
    await panel.getByText(unresolved.rule.statement, { exact: false }).first().waitFor();
    checks.push('unreviewed_count_opens_exact_task_items_and_failure_reason');
    const original = await (await context.request.get(origin + `/api/v1/rules/${unresolved.rule.id}/source`)).json();
    await panel.getByRole('button', { name: '打开原文核对', exact: true }).first().click();
    const source = page.getByRole('dialog', { name: '规则原文依据' });
    await source.waitFor();
    assert.equal(await source.locator('.rule-source-text').innerText(), original.source.text);
    assert(await source.getByRole('button', { name: '打开原页核对', exact: true }).isVisible());
    await source.getByRole('button', { name: '关闭原文依据', exact: true }).click();
    checks.push('saved_source_text_and_original_page_entry_accessible');
    for (const [key, label] of [['accepted', '本次通过'], ['reference', '参考'], ['uncertain', '不确定']]) {
      await panel.getByRole('button', { name: `${label}（${details.counts[key]}）`, exact: true }).click();
      assert.equal(await panel.locator('.rule-review-item').count(), details.counts[key]);
      if (!details.counts[key]) await panel.getByText('本次任务没有此类条款。', { exact: true }).waitFor();
    }
    await panel.getByRole('button', { name: `全部（${details.items.length}）`, exact: true }).click();
    assert.equal(await panel.locator('.rule-review-item').count(), details.items.length);
    checks.push('all_categories_and_empty_states_work');
    await card.getByRole('button', { name: '收起审核详情', exact: true }).click();
    const history = jobs.getByRole('button', { name: /查看历史整理记录/ });
    if (await history.count()) await history.click();
    assert(await jobs.getByText(/历史任务/).count() > 0);
    checks.push('history_expands_and_remains_accessible');
    await page.route(`**/api/v1/tasks/${task.id}/events`, route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '详情暂时无法加载' }) }));
    await card.getByRole('button', { name: '查看审核详情', exact: true }).click();
    await panel.getByRole('alert').getByText('审核记录暂时无法加载，请重试', { exact: true }).waitFor();
    await page.unroute(`**/api/v1/tasks/${task.id}/events`);
    await panel.getByRole('button', { name: '重新加载详情', exact: true }).click();
    await panel.locator('.rule-review-item').first().waitFor();
    checks.push('failed_details_can_be_reloaded');
    const out = path.join(root, '.runtime/review-details-browser');
    fs.mkdirSync(out, { recursive: true });
    await card.screenshot({ path: path.join(out, 'desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    await card.getByRole('button', { name: `未审核 ${review.unreviewed_count} 条`, exact: true }).focus();
    await page.keyboard.press('Enter');
    assert.equal(await panel.locator('.rule-review-item').count(), details.counts.unreviewed);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await card.screenshot({ path: path.join(out, 'mobile.png') });
    checks.push('keyboard_activation_and_mobile_layout');
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, []);
    const report = { passed: true, checks, page_errors: errors, mutating_business_requests: writes.length };
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => { console.error(String(error.stack || error).split(token || 'NO_TOKEN').join('[REDACTED]')); process.exitCode = 1; });
