const fs = require('fs'), path = require('path'), assert = require('node:assert/strict'), crypto = require('node:crypto');
const { chromium } = require('C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..'), origin = 'http://127.0.0.1:8765', pid = 'bcc2b1c6944946508906ee6e0ecaa80a';
const sha = text => crypto.createHash('sha256').update(text).digest('hex');
(async () => {
  const repair = JSON.parse(fs.readFileSync(path.join(root, '.runtime/crop-context-apply.json'), 'utf8'));
  const baseline = JSON.parse(fs.readFileSync(path.join(root, '.runtime/crop-repair-baseline.json'), 'utf8'));
  const browser = await chromium.launch({ headless: true, executablePath: path.join(root, '.runtime/browsers/chromium-1234/chrome-win64/chrome.exe') });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN' });
    const token = JSON.parse(fs.readFileSync(path.join(root, '.runtime/session-8765.json'), 'utf8')).bootstrap_token;
    assert.equal((await context.request.post(origin + '/api/v1/session', { data: { token } })).status(), 200);
    const get = async url => { const res = await context.request.get(origin + '/api/v1' + url, { timeout: 120000 }); assert.equal(res.status(), 200, url); return res.json(); };
    const rules = await get(`/projects/${pid}/rules`);
    assert.equal(rules.pending_diagnostics.total, 160);
    assert.equal(rules.readiness.publishable_count, 140);
    assert.equal(rules.package.version, 33);
    assert.equal(rules.readiness.dirty, false);
    let checked = 0;
    for (let offset = 0; offset < repair.candidate_contexts.length; offset += 4) {
      await Promise.all(repair.candidate_contexts.slice(offset, offset + 4).map(async expected => {
        const response = await get(`/rules/${expected.rule_id}/source`), complete = response.reading_context;
        assert.equal(complete.requested_scope_complete, true);
        assert.equal(complete.candidate_verified, false);
        assert.equal(complete.requires_original_comparison, true);
        assert.deepEqual(complete.raw_observations.map(o => sha(o.text)), expected.observation_hashes);
        assert.equal(complete.full_observation_characters, expected.full_observation_characters);
        assert.equal(response.source.id, baseline.items.find(i => i.rule.id === expected.rule_id).source.id);
        checked++;
      }));
    }
    const expanded = repair.candidate_contexts.filter(c => c.full_observation_characters > c.legacy_source_characters);
    const sample = expanded.find(c => c.rule_id === '24bc0e1e0f404e979e933e175fdac3e6') || expanded[0];
    const original = baseline.items.find(i => i.rule.id === sample.rule_id);
    const response = await get(`/rules/${sample.rule_id}/source`);
    const page = await context.newPage(), errors = [];
    page.setDefaultTimeout(45000);
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(pid => localStorage.setItem('paper_project', pid), pid);
    await page.goto(origin);
    await page.getByRole('button', { name: '论文规则', exact: true }).click();
    await page.getByRole('tab', { name: '整理与审核', exact: true }).click();
    const exceptions = page.locator('.autopilot-exceptions');
    await exceptions.locator(':scope > summary').click();
    const query = original.rule.statement.split('\n').find(line => line.trim().length > 8).trim().slice(0, 35);
    await exceptions.getByRole('textbox', { name: '搜索处理例外', exact: true }).fill(query);
    const row = exceptions.locator(`[data-exception-id="${sample.rule_id}"]`);
    await row.waitFor();
    await row.getByRole('button', { name: '查看原图与识别内容', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '规则原文依据' });
    const complete = dialog.getByRole('region', { name: '候选完整来源上下文' });
    await complete.getByText('完整来源上下文', { exact: true }).waitFor();
    assert.match(await dialog.innerText(), /候选原文待核对/);
    const displayed = await complete.locator('details[open] pre').allTextContents();
    assert.deepEqual(displayed, response.reading_context.raw_observations.map(o => o.text));
    assert.ok(sample.full_observation_characters > sample.legacy_source_characters);
    await complete.evaluate(el => el.scrollIntoView({ block: 'start' }));
    await dialog.screenshot({ path: path.join(root, '.runtime/crop-context-source.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    const layout = await dialog.evaluate(el => ({ width: el.clientWidth, content: el.scrollWidth }));
    assert.ok(layout.content <= layout.width + 2, JSON.stringify(layout));
    await complete.evaluate(el => el.scrollIntoView({ block: 'start' }));
    await dialog.screenshot({ path: path.join(root, '.runtime/crop-context-mobile.png') });
    assert.deepEqual(errors, []);
    const report = { apiContextsChecked: checked, expandedContexts: expanded.length, sampleRule: sample.rule_id,
      sampleLegacyCharacters: sample.legacy_source_characters, sampleFullCharacters: sample.full_observation_characters,
      completeTextMatchesAPI: true, mobileNoHorizontalOverflow: true, pending: 160, effectiveRules: 140,
      packageVersion: 33, errors };
    fs.writeFileSync(path.join(root, '.runtime/crop-context-browser-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
