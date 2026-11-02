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
  const shots = path.join(root, '.runtime/source-billing-entry-20261031');
  fs.mkdirSync(shots, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1480, height: 1000 }, locale: 'zh-CN' });
  const page = await context.newPage();
  const errors = [], failures = [], checks = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400) failures.push(r.url() + ':' + r.status()) });
  const note = page.locator('.source-billing-note');
  const dialog = page.getByRole('dialog', { name: '费用依据账本 · 待补依据' });
  try {
    await page.goto(`http://127.0.0.1:${port}/#token=${session.bootstrap_token}`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: '把研究，写成有依据的论文。' }).waitFor();
    await page.locator('aside.sidebar nav').getByRole('button', { name: /^资料与文献/ }).click();
    await page.getByRole('heading', { name: '资料中心' }).first().waitFor({ timeout: 15000 });

    // 1 提示条带着可点的入口，而不只是一个句子
    await note.first().waitFor({ timeout: 15000 });
    const noteText = await note.first().innerText();
    assert.ok(/笔调用的实际费用待核实/.test(noteText), '提示条应说明待核实笔数：' + noteText);
    const openLedger = note.first().getByRole('button', { name: '查看待补依据' });
    assert.equal(await openLedger.count(), 1, '提示条应有唯一的查看入口');
    assert.equal(await openLedger.getAttribute('aria-haspopup'), 'dialog');
    checks.push('billing_note_has_entry');

    // 2 点击后就在原地打开账本，默认停在待补依据
    await openLedger.click();
    await dialog.waitFor({ timeout: 20000 });
    await page.getByRole('heading', { name: '费用依据账本', exact: true }).waitFor();
    const ledger = dialog.locator('.expense-ledger');
    await ledger.waitFor();
    await page.waitForFunction(() => document.querySelectorAll('.expense-ledger tbody tr').length > 0, null, { timeout: 20000 });
    assert.equal(await ledger.getByLabel('费用记录筛选').inputValue(), 'gaps', '打开后应默认筛选待补依据');
    const gapText = await ledger.locator('.section-heading').first().innerText();
    assert.ok(/待补依据/.test(gapText), '应显示待补依据计数：' + gapText);
    assert.ok((await ledger.locator('.expense-runs tbody tr').count()) > 0, '待补依据列表不应为空');
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(shots, 'ledger-dialog.png') });
    checks.push('ledger_opens_on_gaps');

    // 3 弹窗可以关掉，焦点回到入口按钮
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('[role="dialog"]'), null, { timeout: 10000 });
    assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), '查看待补依据');
    checks.push('dialog_closes_and_returns_focus');

    // 4 三个子页面都能看到这条入口，切页不会丢
    for (const [tab, heading] of [['论文规则', '论文规则'], ['文献与知识', '文献与知识工作台']]) {
      await page.locator('.source-destinations button').filter({ hasText: tab }).first().click();
      await page.getByRole('heading', { name: heading }).first().waitFor({ timeout: 20000 });
      await note.first().waitFor({ timeout: 20000 });
      assert.equal(await note.first().getByRole('button', { name: '查看待补依据' }).count(), 1);
    }
    checks.push('entry_kept_on_all_subpages');
    await page.screenshot({ path: path.join(shots, 'note-on-references.png') });

    // 5 390px 窄屏下按钮换行但仍可点
    await page.setViewportSize({ width: 390, height: 860 });
    await page.waitForTimeout(400);
    const button = note.first().getByRole('button', { name: '查看待补依据' });
    assert.ok(await button.isVisible(), '窄屏下入口仍应可见');
    await page.screenshot({ path: path.join(shots, 'note-390.png') });

    const report = { checks, errors, failures, at: new Date().toISOString() };
    fs.writeFileSync(path.join(shots, 'browser-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
    assert.deepEqual(errors, [], '页面不应有 JavaScript 错误');
    assert.deepEqual(failures, [], '不应有业务接口失败');
  } catch (e) {
    fs.writeFileSync(path.join(shots, 'browser-report.json'), JSON.stringify({ checks, errors, failures, error: String(e) }, null, 2));
    await page.screenshot({ path: path.join(shots, 'failure.png') }).catch(() => { });
    throw e;
  } finally {
    await browser.close();
  }
})();
