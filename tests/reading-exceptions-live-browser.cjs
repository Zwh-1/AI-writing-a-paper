const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const { chromium } = require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..'), origin = 'http://127.0.0.1:8765', pid = 'bcc2b1c6944946508906ee6e0ecaa80a';
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: path.join(root, '.runtime/browsers/chromium-1234/chrome-win64/chrome.exe') });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN' });
    const token = JSON.parse(fs.readFileSync(path.join(root, '.runtime/session-8765.json'), 'utf8')).bootstrap_token;
    assert.equal((await context.request.post(origin + '/api/v1/session', { data: { token } })).status(), 200);
    const report = await (await context.request.get(`${origin}/api/v1/projects/${pid}/ai-autopilot`)).json();
    const rules = await (await context.request.get(`${origin}/api/v1/projects/${pid}/rules`)).json();
    assert.equal(report.exception_count, 365);
    assert.equal(report.exceptions.filter(item => item.issue_kind === 'source_text').length, 365);
    assert.equal(report.exceptions.filter(item => item.issue_kind === 'rule_decision').length, 0);
    assert.equal(rules.readiness.publishable_count, 140); assert.equal(rules.package.version, 33); assert.equal(rules.readiness.dirty, false);
    for (const id of ['65e401974aba4952b9edde4fd6b2a528', 'd0223f294314460e8953a2822ca89083']) {
      assert.ok(!report.exceptions.some(item => item.id === id));
      assert.equal(rules.rules.find(item => item.id === id).state, 'reference_only');
    }
    const crop = report.exceptions.find(item => item.id === 'dbe33f71fdd34d7ca76cbdc1c30c077c');
    assert.equal(crop.content_status, 'source_cropped'); assert.equal(crop.purpose_hint, 'counterexample');
    assert.equal(crop.supporting_excerpt.supports_entire_candidate, false);
    assert.match(crop.supporting_excerpt.text, /^4\.2\.1/); assert.match(crop.label, /qzb > 0/);
    const page = await context.newPage(), errors = []; page.setDefaultTimeout(45000); page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(pid => localStorage.setItem('paper_project', pid), pid); await page.goto(origin);
    await page.getByRole('button', { name: '论文规则', exact: true }).click();
    await page.getByRole('tab', { name: '整理与审核', exact: true }).click();
    const exceptions = page.locator('.autopilot-exceptions'); await exceptions.locator(':scope > summary').click();
    await exceptions.getByRole('button', { name: /文字识别\s*365/ }).click();
    assert.equal(await exceptions.locator('article').count(), 10);
    assert.match(await exceptions.getByRole('button', { name: /规则判断/ }).innerText(), /0/);
    const row = exceptions.locator(`[data-exception-id="${crop.id}"]`); await row.waitFor();
    await row.getByText('原件图片裁断', { exact: true }).waitFor();
    await row.getByText('查看本条未核验OCR原文', { exact: true }).click();
    assert.ok((await row.locator('pre').first().textContent()).includes(crop.label));
    await row.getByText('查看并列校对摘录（未覆盖整条候选）', { exact: true }).click();
    assert.ok((await row.textContent()).includes(crop.supporting_excerpt.text));
    await row.screenshot({ path: path.join(root, '.runtime/reading-exceptions-cropped.png') });
    await row.getByRole('button', { name: '查看原图与识别内容', exact: true }).click();
    const dialog = page.getByRole('dialog'); await dialog.waitFor();
    assert.match(await dialog.innerText(), /第 22 页/);
    await dialog.getByText('完整图像观察与识别覆盖', { exact: true }).click();
    await dialog.locator('[data-asset-sha="7080940fbf27c720439be422cdf6cf4d149bc5d66d0a338fa65d68107d84f51a"]').click();
    const reading = dialog.getByRole('region', { name: '完整图像观察', exact: true });
    await reading.getByText(/原件图片自身被裁断/).waitFor();
    await reading.getByRole('img').waitFor();
    assert.match(await reading.innerText(), /4\.2\.1 LMSR/);
    assert.match(await reading.innerText(), /qzb > 0/);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await dialog.getByRole('button', { name: '关闭原文依据', exact: true }).click();
    await row.screenshot({ path: path.join(root, '.runtime/reading-exceptions-mobile.png') });
    await page.setViewportSize({ width: 1440, height: 1100 });
    await exceptions.getByRole('textbox', { name: '搜索处理例外', exact: true }).fill('教学反例');
    assert.ok(await exceptions.locator('article').count() > 0);
    await exceptions.getByRole('button', { name: /规则判断/ }).click();
    await exceptions.getByText('没有匹配的例外', { exact: true }).waitFor();
    assert.deepEqual(errors, []);
    const checks = ['365 unresolved readings remain visible', 'reading and rule decisions have independent counts',
      'two individually corrected candidates moved to reference', 'cropped original remains unresolved',
      'raw OCR and corrected partial text are both accessible', 'source action opens source reader',
      'original image and crop warning displayed', 'purpose hints searchable without claiming verification', 'mobile layout', '140 effective rules and V33 preserved'];
    const result = { checks, errors, pending: 365, corrected: 2, cropped: 1, effective: 140, packageVersion: 33 };
    fs.writeFileSync(path.join(root, '.runtime/reading-exceptions-browser-report.json'), JSON.stringify(result, null, 2)); console.log(JSON.stringify(result, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
