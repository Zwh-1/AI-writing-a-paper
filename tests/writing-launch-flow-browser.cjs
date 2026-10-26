const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..'), fixture = JSON.parse(fs.readFileSync(path.join(root, '.runtime/writing-launch-fixture.json'), 'utf8'));
assert.equal(fixture.synthetic_model, true, 'This test must use the isolated synthetic server');
const origin = `http://127.0.0.1:${fixture.port}`, prefix = `/api/v1/projects/${fixture.project_id}`;
const output = path.join(root, '.runtime/writing-launch-flow'); fs.mkdirSync(output, { recursive: true });

(async () => {
  const session = JSON.parse(fs.readFileSync(path.join(root, `.runtime/session-${fixture.port}.json`), 'utf8'));
  const browser = await chromium.launch({ headless: true, executablePath: path.join(root, '.runtime/browsers/chromium-1234/chrome-win64/chrome.exe') });
  let page; const errors = [], failures = [], checks = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'zh-CN' });
    assert.equal((await context.request.post(origin + '/api/v1/session', { data: { token: session.bootstrap_token } })).status(), 200);
    const get = async path => { const response = await context.request.get(origin + path); assert.equal(response.status(), 200); return response.json(); };
    assert.equal((await get(prefix + '/writing-profiles')).length, 0);
    page = await context.newPage(); page.setDefaultTimeout(25000);
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => { if (response.url().includes('/api/') && response.status() >= 400) failures.push(response.status() + ' ' + new URL(response.url()).pathname); });
    await page.addInitScript(({ pid, sid }) => { localStorage.setItem('paper_project', pid); localStorage.setItem('paper_section', sid); }, { pid: fixture.project_id, sid: fixture.section_id });
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    const nav = name => page.getByRole('navigation').getByRole('button', { name, exact: true }).click();
    await nav('有据写作');
    await page.getByText('可以开始组织写作', { exact: true }).waitFor();
    await page.getByRole('button', { name: '进入本节写作', exact: true }).click();
    const start = page.getByRole('button', { name: '开始本节写作（调用模型）', exact: true });
    await start.waitFor();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('button')).some(button => button.textContent === '开始本节写作（调用模型）' && !button.disabled));
    assert(await start.isEnabled());
    assert.equal(await page.getByLabel('章节', { exact: true }).inputValue(), fixture.section_id);
    assert.equal(await page.getByLabel('阶段配置', { exact: true }).inputValue(), '');
    assert.equal(await page.getByLabel('阶段配置', { exact: true }).isVisible(), false);
    const box = await start.boundingBox(); assert(box && box.y + box.height < 1000, 'Writing action should be visible before scrolling');
    await page.screenshot({ path: path.join(output, 'ready-without-stage-profile.png') });
    checks.push('chapter_and_current_model_allow_start_without_manual_stage_profile');
    for (const width of [1024, 768, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Writing page overflow at ' + width);
    }
    await page.setViewportSize({ width: 1600, height: 1000 });
    const launched = page.waitForResponse(response => response.url() === origin + prefix + '/writing-start' && response.request().method() === 'POST');
    await start.click(); const response = await launched; assert.equal(response.status(), 200, await response.text());
    const started = await response.json();
    await page.locator('.wa-draft').getByText(/只有发行方且开关启用时/).waitFor();
    await page.getByRole('heading', { name: '等待审阅', exact: true }).waitFor();
    const result = await get(prefix + '/writing-sessions/' + started.id);
    assert.equal(result.status, 'needs_review');
    assert.deepEqual(result.steps.map(step => step.stage), ['investigate', 'judgment', 'judgment_check', 'draft', 'annotation', 'semantic', 'coherence']);
    assert(result.steps.every(step => step.state === 'completed')); assert.equal(result.candidates.length, 1);
    const profiles = await get(prefix + '/writing-profiles'); assert.equal(profiles.length, 1);
    assert(profiles[0].configuration_current); assert.equal(profiles[0].qualification, 'unassessed');
    assert.equal((await get(`/api/v1/sections/${fixture.section_id}/content`)).section.current_revision_id, null);
    await page.screenshot({ path: path.join(output, 'candidate-and-verification.png'), fullPage: true });
    checks.push('real_http_launch_runs_worker_stages_and_displays_generated_candidate_and_analysis');
    const adopted = page.waitForResponse(response => response.url().endsWith('/writing-candidates/' + result.candidates[0].id + '/accept'));
    await page.getByRole('button', { name: '作为待核验草稿采用', exact: true }).click();
    assert.equal((await adopted).status(), 200);
    await page.locator('.paper-editor').getByText(/只有发行方且开关启用时/).waitFor();
    const saved = await get(`/api/v1/sections/${fixture.section_id}/content`);
    assert(saved.section.current_revision_id); assert.match(saved.revision.text, /只有发行方且开关启用时/);
    assert.notEqual(saved.section.state, 'user_reviewed');
    await page.screenshot({ path: path.join(output, 'adopted-prose-in-editor.png') });
    checks.push('explicit_adoption_creates_saved_prose_and_opens_current_editor_without_claiming_human_review');
    await page.reload({ waitUntil: 'domcontentloaded' }); await nav('有据写作');
    await page.getByRole('button', { name: '本节写作与核验', exact: true }).click();
    await page.waitForFunction(id => document.querySelector('select[aria-label="写作会话"]')?.value === id, started.id);
    await page.locator('.wa-draft').getByText(/只有发行方且开关启用时/).waitFor();
    checks.push('saved_writing_session_and_candidate_restore_after_reload');
    assert.deepEqual(errors, []); assert.deepEqual(failures, []);
    const report = { passed: true, synthetic_model: true, external_model_calls: 0, real_project_changes: 0, checks, errors, failures, fixture_session_id: started.id, fixture_revision_id: saved.section.current_revision_id };
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true });
    console.error(error); console.error({ errors, failures }); process.exitCode = 1;
  } finally { await browser.close(); }
})();
