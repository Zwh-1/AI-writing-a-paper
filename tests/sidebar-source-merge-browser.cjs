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
  const shots = path.join(root, '.runtime/sidebar-source-merge-20261031');
  fs.mkdirSync(shots, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1480, height: 1000 }, locale: 'zh-CN' });
  const page = await context.newPage();
  const errors = [], failures = [], checks = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400) failures.push(r.url() + ':' + r.status()) });
  const sidebar = page.locator('aside.sidebar nav');
  const entry = sidebar.getByRole('button', { name: /^资料与文献/ });
  const breadcrumb = page.locator('.breadcrumb');
  const tabs = page.locator('.source-destinations button');
  try {
    await page.goto(`http://127.0.0.1:${port}/#token=${session.bootstrap_token}`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: '把研究，写成有依据的论文。' }).waitFor();

    // 1 侧栏只保留一个入口，旧的三个入口不再单独出现
    assert.equal(await sidebar.getByRole('button', { name: /^资料中心/ }).count(), 0, '资料中心不应再作为独立主导航');
    assert.equal(await sidebar.getByRole('button', { name: /^论文规则/ }).count(), 0, '论文规则不应再作为独立主导航');
    assert.equal(await sidebar.getByRole('button', { name: /^文献与术语/ }).count(), 0, '文献不应再作为独立主导航');
    assert.equal(await entry.count(), 1, '资料与文献入口应当唯一');
    checks.push('sidebar_single_source_entry');

    // 2 点击后进入资料中心，侧栏保持一个高亮并标出子页面
    await entry.click();
    await page.getByRole('heading', { name: '资料中心' }).first().waitFor({ timeout: 15000 });
    assert.ok((await entry.getAttribute('class') || '').includes('active'));
    assert.ok((await entry.innerText()).includes('资料中心'), '侧栏应标注当前子页面');
    assert.ok((await breadcrumb.innerText()).includes('资料与文献 · 资料中心'));
    assert.equal(await sidebar.locator('button.active').count(), 1, '同一时刻只应有一个高亮入口');
    checks.push('entry_opens_materials');

    // 3 页面内部切换到论文规则，侧栏不变
    await tabs.filter({ hasText: '论文规则' }).first().click();
    await page.getByRole('heading', { name: '论文规则' }).first().waitFor({ timeout: 15000 });
    assert.ok((await entry.getAttribute('class') || '').includes('active'));
    assert.ok((await entry.innerText()).includes('论文规则'));
    assert.ok((await breadcrumb.innerText()).includes('资料与文献 · 论文规则'));
    checks.push('internal_switch_rules');

    // 4 页面内部切换到文献与知识
    await tabs.filter({ hasText: '文献与知识' }).first().click();
    await page.waitForFunction(() => document.querySelector('.breadcrumb')?.textContent?.includes('文献与知识'), null, { timeout: 15000 });
    await page.getByRole('heading', { name: '文献与知识工作台' }).waitFor({ timeout: 20000 });
    await page.locator('.source-destinations').first().waitFor({ timeout: 20000 });
    assert.ok((await entry.getAttribute('class') || '').includes('active'));
    checks.push('internal_switch_references');
    await page.screenshot({ path: path.join(shots, 'sidebar-references.png') });

    // 5 离开后再次点击入口，回到上次的子页面
    await sidebar.getByRole('button', { name: /^项目概览/ }).click();
    await page.getByRole('heading', { name: '把研究，写成有依据的论文。' }).waitFor();
    assert.equal(await sidebar.locator('button.active').count(), 1);
    assert.equal(await entry.getAttribute('class'), '');
    await entry.click();
    await page.waitForFunction(() => document.querySelector('.breadcrumb')?.textContent?.includes('文献与知识'), null, { timeout: 15000 });
    checks.push('entry_restores_last_subpage');
    await page.screenshot({ path: path.join(shots, 'sidebar-materials-entry.png') });

    // 6 窄屏只显示图标，不出现文字错位
    await page.setViewportSize({ width: 390, height: 860 });
    await page.waitForTimeout(400);
    assert.equal(await sidebar.locator('button.active').count(), 1);
    await page.screenshot({ path: path.join(shots, 'sidebar-390.png') });

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
