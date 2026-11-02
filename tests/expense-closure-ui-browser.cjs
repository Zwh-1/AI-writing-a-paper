const fs = require('fs'), path = require('path'), assert = require('assert');
let playwright;
for (const candidate of [process.env.PLAYWRIGHT_MODULE, path.resolve(__dirname, '../frontend/node_modules/playwright'), path.resolve(__dirname, '../node_modules/playwright'), 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright']) {
  if (!candidate) continue;
  try { playwright = require(candidate); break } catch { }
}
if (!playwright) throw new Error('Install Playwright or set PLAYWRIGHT_MODULE');
const { chromium } = playwright;
const root = path.resolve(__dirname, '..');
const port = process.env.PAPER_TEST_PORT || 8765;
(async () => {
  const session = JSON.parse(fs.readFileSync(path.join(root, '.runtime/session-' + port + '.json'), 'utf8'));
  const shots = path.join(root, '.runtime/expense-closure-ui-20261031');
  fs.mkdirSync(shots, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1480, height: 1000 }, locale: 'zh-CN' });
  const page = await context.newPage();
  const errors = [], failures = [], checks = [], posted = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400) failures.push(r.url() + ':' + r.status()) });
  page.on('request', r => { if (r.url().includes('close-unverifiable')) posted.push(r.url()) });
  const dialog = page.getByRole('dialog', { name: '费用依据账本 · 待补依据' });
  const section = page.locator('.expense-closure');
  const submit = section.getByRole('button', { name: /登记结转/ });
  try {
    await page.goto(`http://127.0.0.1:${port}/#token=${session.bootstrap_token}`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: '把研究，写成有依据的论文。' }).waitFor();
    await page.locator('aside.sidebar nav').getByRole('button', { name: /^资料与文献/ }).click();
    await page.getByRole('heading', { name: '资料中心' }).first().waitFor({ timeout: 15000 });
    await page.locator('.source-billing-note').getByRole('button', { name: '查看待补依据' }).click();
    await dialog.waitFor({ timeout: 20000 });
    await section.waitFor({ timeout: 20000 });

    // 1 结转区把待核实的调用列出来，并给出笔数与预留合计
    const heading = await section.locator('.section-heading').innerText();
    assert.ok(/无法核实的调用结转/.test(heading), heading);
    assert.ok(/\d+ 笔 · 预留/.test(heading), heading);
    const rows = section.locator('.expense-closure-row');
    assert.ok(await rows.count() > 0, '应列出待结转的调用');
    checks.push('closure_section_lists_candidates');

    // 2 全选后显示已选笔数与合计
    await section.getByRole('button', { name: /全选/ }).click();
    const picked = await section.locator('.actions span').first().innerText();
    assert.ok(/已选 \d+ 笔 · 预留合计/.test(picked), picked);
    checks.push('select_all_reports_totals');

    // 3 原因不足 8 字时按钮仍可点，点下去要给出明确原因而不是无反应
    assert.equal(await submit.isDisabled(), false, '按钮不应静默禁用');
    const hint = section.locator('.expense-closure-hint');
    await section.getByLabel('核实结论与原因').fill('中断');
    assert.ok((await hint.innerText()).includes('当前 2 字'), await hint.innerText());
    await submit.click();
    await page.locator('.expense-ledger .error-text').waitFor({ timeout: 5000 });
    const blocked = await page.locator('.expense-ledger .error-text').innerText();
    assert.ok(blocked.includes('至少 8 个字'), blocked);
    assert.deepEqual(posted, [], '原因不足时不应发出请求');
    checks.push('short_reason_explains_instead_of_dead_button');

    await section.getByLabel('核实结论与原因').fill('批量知识维护中断，服务商不提供逐请求账单，用量与响应标识均无法取得');
    assert.ok((await hint.innerText()).includes('可登记'), await hint.innerText());
    checks.push('long_reason_unlocks_submit');

    // 4 两种记账口径都在，且默认按预留上界
    const basis = section.getByLabel('结转记账口径');
    assert.equal(await basis.inputValue(), 'reserved');
    assert.equal(await basis.locator('option').count(), 2);
    checks.push('amount_basis_selectable');

    await page.screenshot({ path: path.join(shots, 'closure-panel.png') });

    // 5 窄屏仍可操作（不提交，避免改动真实账目）
    await page.setViewportSize({ width: 390, height: 860 });
    await page.waitForTimeout(400);
    assert.ok(await submit.isVisible());
    await page.screenshot({ path: path.join(shots, 'closure-390.png') });

    const report = { checks, errors, failures, submitted: false, at: new Date().toISOString() };
    fs.writeFileSync(path.join(shots, 'browser-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
    assert.deepEqual(errors, []);
    assert.deepEqual(failures, []);
  } catch (e) {
    fs.writeFileSync(path.join(shots, 'browser-report.json'), JSON.stringify({ checks, errors, failures, error: String(e) }, null, 2));
    await page.screenshot({ path: path.join(shots, 'failure.png') }).catch(() => { });
    throw e;
  } finally {
    await browser.close();
  }
})();
