const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..'), port = process.env.PAPER_TEST_PORT || 8803;
const origin = `http://127.0.0.1:${port}`, output = path.join(root, '.runtime/writing-materials-browser');
fs.mkdirSync(output, { recursive: true });

(async () => {
  const token = JSON.parse(fs.readFileSync(path.join(root, `.runtime/session-${port}.json`), 'utf8')).bootstrap_token;
  const browser = await chromium.launch({ headless: true, executablePath: path.join(root, '.runtime/browsers/chromium-1234/chrome-win64/chrome.exe') });
  const errors = [], forbidden = [], checks = [];
  let page;
  try {
    const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'zh-CN' });
    assert.equal((await context.request.post(origin + '/api/v1/session', { data: { token } })).status(), 200);
    const projects = await (await context.request.get(origin + '/api/v1/projects')).json();
    const pid = projects.find(p => p.title === '我的毕业论文')?.id || projects[0].id;
    const sections = await (await context.request.get(origin + `/api/v1/projects/${pid}/sections`)).json();
    const section = sections.find(s => s.kind === 'abstract') || sections[0];
    const endpoint = `/api/v1/projects/${pid}/sections/${section.id}/materials`;
    const response = await context.request.get(origin + endpoint);
    assert.equal(response.status(), 200);
    const originals = await response.json();
    assert.equal(originals.mode, 'linked'); assert(originals.items.length > 0);
    page = await context.newPage(); page.setDefaultTimeout(20000);
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(({ pid, sid }) => {
      localStorage.setItem('paper_project', pid); localStorage.setItem('paper_section', sid);
      window.addEventListener('paper-attach-source', event => {
        event.stopImmediatePropagation(); window.testAttachedSource = event.detail;
      }, true);
    }, { pid, sid: section.id });
    let fail = false, slow = false, releaseSlow, startedSlow;
    await page.route('**/api/v1/**', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname === endpoint && request.method() === 'GET') {
        if (fail) return route.fulfill({ status: 503, json: { detail: '测试资料暂不可用' } });
        if (slow && url.searchParams.get('q') === '延迟查询') {
          startedSlow();
          await new Promise(resolve => { releaseSlow = resolve; });
          return route.fulfill({ status: 200, json: { mode: 'search', items: [{ ...originals.items[0], name: '过期请求不应出现' }] } }).catch(() => {});
        }
      }
      if (['GET', 'HEAD', 'OPTIONS'].includes(request.method()) || url.pathname.endsWith('/session')) return route.continue();
      forbidden.push(request.method() + ' ' + url.pathname); return route.abort();
    });
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    await page.getByRole('navigation').getByRole('button', { name: '写作工作台', exact: true }).click();
    const panel = page.getByRole('region', { name: '本节资料依据', exact: true });
    await panel.getByRole('status').filter({ hasText: `本节已关联原文 · ${originals.items.length} 项` }).waitFor();
    assert.equal(await panel.locator('.evidence-card').count(), originals.items.length);
    await panel.locator('summary').first().click();
    assert((await panel.locator('.evidence-card p').first().innerText()).trim());
    await panel.getByRole('button', { name: '关联当前段落', exact: true }).first().click();
    assert.deepEqual(await page.evaluate(() => window.testAttachedSource), { sectionId: section.id, sourceId: originals.items[0].id });
    checks.push('real_task_originals_load_automatically_and_attach_uses_source_unit_id');
    await panel.getByRole('button', { name: '查看原文', exact: true }).first().click();
    await page.getByRole('button', { name: '关闭资料查看', exact: true }).waitFor();
    await page.locator('.source-content').waitFor();
    await page.getByRole('button', { name: '关闭资料查看', exact: true }).click();
    checks.push('original_viewer_opens_the_corresponding_material');
    await page.screenshot({ path: path.join(output, 'loaded-desktop.png') });
    await page.locator('.evidence-panel').getByRole('button', { name: '文献与资源', exact: true }).click();
    await page.getByText('可用参考文献', { exact: true }).waitFor();
    await page.locator('.evidence-panel').getByRole('button', { name: '资料依据', exact: true }).click();
    await panel.locator('.evidence-card').first().waitFor();
    const input = panel.getByRole('textbox', { name: '资料关键词', exact: true });
    await input.fill('zzqxjvkrpmntshdgfw'); await input.press('Enter');
    await panel.getByText('未找到匹配原文，请换用关键词检索。', { exact: true }).waitFor();
    await panel.getByRole('button', { name: '返回本节资料', exact: true }).click();
    await panel.locator('.evidence-card').first().waitFor();
    checks.push('references_still_load_and_enter_search_has_distinct_empty_state');
    fail = true; await input.fill('加载失败'); await input.press('Enter');
    await panel.getByRole('alert').filter({ hasText: '测试资料暂不可用' }).waitFor();
    fail = false; await panel.getByRole('button', { name: '重新加载资料', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[aria-label="本节资料依据"]')?.getAttribute('aria-busy') === 'false');
    assert.equal(await panel.getByRole('alert').count(), 0);
    await panel.getByRole('button', { name: '返回本节资料', exact: true }).click();
    await panel.locator('.evidence-card').first().waitFor();
    checks.push('request_failures_are_visible_and_retry_recovers');
    slow = true;
    const slowStarted = new Promise(resolve => { startedSlow = resolve; });
    await input.fill('延迟查询'); await input.press('Enter'); await slowStarted;
    await panel.getByText('正在加载资料依据…', { exact: true }).waitFor();
    const next = sections.find(s => s.id !== section.id);
    await page.locator('.chapter-list button').filter({ hasText: next.title }).click();
    await panel.locator('.evidence-card').first().waitFor();
    releaseSlow();
    assert.equal(await input.inputValue(), '');
    assert.equal(await page.getByText('过期请求不应出现', { exact: true }).count(), 0);
    checks.push('chapter_switch_clears_query_and_discards_previous_pending_response');
    for (const width of [1024, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Overflow at width ' + width);
    }
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'loaded-mobile.png') });
    checks.push('material_panel_fits_tablet_and_mobile');
    assert.deepEqual(errors, []); assert.deepEqual(forbidden, []);
    const report = { passed: true, loaded_originals: originals.items.length, checks, errors, forbidden, model_calls: 0, project_mutations: 0 };
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true });
    console.error(error); console.error({ errors, forbidden }); process.exitCode = 1;
  } finally { await browser.close(); }
})();
