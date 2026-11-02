const fs = require('fs'), path = require('path'), assert = require('assert');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const port = process.env.PAPER_TEST_PORT || '8793';
  const session = JSON.parse(fs.readFileSync(path.join(root, '.runtime', `session-${port}.json`), 'utf8'));
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN' });
  const errors = [], checks = [];
  let paid = 0, failSave = false;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/probe-generation|probe-image|writing-tasks/.test(request.url())) paid++; });
  await page.route('**/api/v*/deepseek/endpoints/*/models', route => route.fulfill({ json: { models: [{ id: 'deepseek-chat' }, { id: 'deepseek-reasoner' }] } }));
  await page.route('**/api/v*/deepseek/endpoints/*/balance', route => route.fulfill({ json: { balance_known: true, remaining_exact: '0.00000000', unit: 'USD', unit_source: 'response', fetched_at: '2026-11-02T04:00:00Z' } }));
  await page.route('**/api/v*/deepseek/endpoints/*', async route => {
    if (failSave && route.request().method() === 'PUT') return route.fulfill({ status: 503, json: { detail: '模拟磁盘写入失败' } });
    await route.fallback();
  });
  const panel = () => page.getByRole('region', { name: 'DeepSeek 接口管理' });
  const dialog = () => page.getByRole('dialog');
  const row = name => panel().getByTestId('deepseek-endpoint').filter({ has: page.getByText(name, { exact: true }) });
  async function fill(name, url, key) {
    await dialog().getByLabel('接口名称', { exact: true }).fill(name);
    await dialog().getByLabel('API 地址', { exact: true }).fill(url);
    await dialog().getByLabel('API key', { exact: true }).fill(key);
  }
  async function save() {
    await dialog().getByRole('button', { name: '保存接口', exact: true }).click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
  }
  try {
    await page.goto(`http://127.0.0.1:${port}/#token=${session.bootstrap_token}`, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '接口与设置', exact: true }).click();
    await panel().getByTestId('deepseek-endpoint').first().waitFor();
    assert.equal(await page.getByText('DeepSeek 主模型接口', { exact: true }).count(), 0);
    assert.equal(await page.getByText('DeepSeek 资料总结设置', { exact: true }).count(), 0);
    checks.push('one_unified_deepseek_settings_entry');

    await page.getByRole('button', { name: '编辑 DeepSeek 主接口', exact: true }).click();
    await fill('SwiftAPI', 'https://swift.example/v1', 'fake-ui-swift-1111');
    await dialog().getByLabel('DeepSeek 模型 ID', { exact: true }).fill('deepseek-chat');
    await save();
    assert.equal(await row('SwiftAPI').getAttribute('data-active'), 'true');
    checks.push('legacy_profile_can_be_edited_and_used');

    await page.getByRole('button', { name: '添加接口', exact: true }).click();
    await fill('DeepSeek 官方', 'https://official.example/v1', 'fake-ui-official-2222');
    await dialog().getByRole('button', { name: '保存并检测模型', exact: true }).click();
    await dialog().getByRole('status').filter({ hasText: '已找到 2 个' }).waitFor();
    assert.equal(await dialog().getByLabel('API key', { exact: true }).inputValue(), '');
    assert.ok((await dialog().getByLabel('API key', { exact: true }).getAttribute('placeholder')).includes('2222'));
    await dialog().getByLabel('DeepSeek 模型 ID', { exact: true }).fill('deepseek-reasoner');
    const preserved = page.waitForRequest(request => request.method() === 'PUT' && /\/deepseek\/endpoints\/[^/]+$/.test(new URL(request.url()).pathname));
    await save();
    assert.equal(Object.hasOwn((await preserved).postDataJSON(), 'api_key'), false);
    checks.push('add_detect_model_and_blank_key_preservation');

    await page.getByRole('button', { name: '使用 DeepSeek 官方', exact: true }).click();
    await panel().getByRole('status').filter({ hasText: '已切换到' }).waitFor();
    assert.equal(await row('DeepSeek 官方').getAttribute('data-active'), 'true');
    assert.equal(await row('SwiftAPI').getAttribute('data-active'), 'false');
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '接口与设置', exact: true }).click();
    await row('DeepSeek 官方').waitFor();
    assert.equal(await row('DeepSeek 官方').getAttribute('data-active'), 'true');
    checks.push('one_click_switch_survives_reload');

    await page.getByRole('button', { name: 'DeepSeek 官方 更多操作', exact: true }).click();
    assert.equal(await page.getByRole('menuitem', { name: '删除接口（使用中）', exact: true }).isDisabled(), true);
    await page.getByRole('menuitem', { name: '查询余额', exact: true }).click();
    await row('DeepSeek 官方').getByText('0.00000000 USD', { exact: true }).waitFor();
    checks.push('active_delete_guard_and_zero_balance');

    await page.getByRole('button', { name: '编辑 SwiftAPI', exact: true }).click();
    await dialog().getByLabel('API key', { exact: true }).fill('fake-ui-write-failure-3333');
    failSave = true;
    await dialog().getByRole('button', { name: '保存接口', exact: true }).click();
    await dialog().getByRole('alert').filter({ hasText: '模拟磁盘写入失败' }).waitFor();
    assert.equal(await dialog().getByLabel('API key', { exact: true }).inputValue(), 'fake-ui-write-failure-3333');
    failSave = false;
    page.once('dialog', event => event.accept());
    await page.keyboard.press('Escape');
    await dialog().waitFor({ state: 'hidden' });
    checks.push('failed_save_retains_input_and_escape_closes_dialog');

    await page.getByRole('button', { name: '添加接口', exact: true }).click();
    await fill('临时接口', 'https://temporary.example/v1', 'fake-ui-temp-4444');
    await save();
    await page.getByRole('button', { name: '临时接口 更多操作', exact: true }).click();
    page.once('dialog', event => event.accept());
    await page.getByRole('menuitem', { name: '删除接口', exact: true }).click();
    await row('临时接口').waitFor({ state: 'detached' });
    checks.push('delete_inactive_profile');

    const shots = path.join(root, 'docs/screenshots');
    fs.mkdirSync(shots, { recursive: true });
    await panel().screenshot({ path: path.join(shots, 'deepseek-unified-desktop.png') });
    await page.getByRole('button', { name: '编辑 DeepSeek 官方', exact: true }).click();
    await dialog().screenshot({ path: path.join(shots, 'deepseek-unified-editor.png') });
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(shots, 'deepseek-unified-mobile.png') });
    const overflow = await panel().evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
    assert.ok(overflow.scroll <= overflow.width + 1, JSON.stringify(overflow));
    await page.getByRole('button', { name: '编辑 DeepSeek 官方', exact: true }).click();
    const bounds = await dialog().boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 391);
    await page.keyboard.press('Escape');
    checks.push('desktop_and_390px_layouts_and_dialog');

    assert.equal(paid, 0);
    assert.deepEqual(errors, []);
    const report = { passed: true, checks, page_errors: errors, paid_requests: paid, fake_keys_only: true, tested_at: new Date().toISOString() };
    fs.writeFileSync(path.join(root, 'docs/deepseek-settings-browser-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } catch (error) {
    await page.screenshot({ path: path.join(root, '.runtime/deepseek-settings-failure.png'), fullPage: true });
    console.error(error.stack); process.exitCode = 1;
  } finally { await browser.close(); }
})();
