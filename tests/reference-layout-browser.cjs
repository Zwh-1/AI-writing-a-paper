// Read-only layout checks against the live project or the isolated workflow fixture.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const out = path.join(root, '.runtime/reference-redesign-20261031');
const live = process.env.PAPER_LAYOUT_LIVE === '1';
const fixture = live ? { port: 8765 } : JSON.parse(fs.readFileSync(path.join(root, '.runtime/reference-workflows-20261031/fixture.json'), 'utf8'));
const origin = `http://127.0.0.1:${fixture.port}`;
const { chromium } = require(path.join(root, '.runtime/browser/node_modules/playwright'));
const tabs = [['library', '文献书目'], ['reading', '问题阅读'], ['evidence', '原文与证据'], ['wiki', '图谱与Wiki'], ['terms', '统一术语'], ['manage', '处理与维护']];

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: path.join(root, '.runtime/browsers/chromium-1234/chrome-win64/chrome.exe') });
  const errors = [], failures = [], checks = [], writes = [];
  let page;
  try {
    const context = await browser.newContext({ viewport: { width: 1600, height: 1060 }, locale: 'zh-CN' });
    const token = JSON.parse(fs.readFileSync(path.join(root, `.runtime/session-${fixture.port}.json`), 'utf8')).bootstrap_token;
    assert.equal((await context.request.post(origin + '/api/v1/session', { data: { token } })).status(), 200);
    if (!fixture.project_id) fixture.project_id = (await (await context.request.get(origin + '/api/v1/projects')).json())[0].id;
    page = await context.newPage();
    page.setDefaultTimeout(30000);
    page.on('pageerror', e => errors.push(e.message));
    page.on('response', r => { if (r.url().includes('/api/v1/') && r.status() >= 400) failures.push({ path: new URL(r.url()).pathname, status: r.status() }); });
    await page.route('**/api/v1/**', route => {
      const request = route.request(), url = new URL(request.url());
      if (['GET', 'HEAD', 'OPTIONS'].includes(request.method()) || url.pathname === '/api/v1/session' || /\/evidence-knowledge\/units\/[^/]+\/read$/.test(url.pathname)) return route.continue();
      writes.push(url.pathname);
      return route.abort();
    });
    await page.addInitScript(id => localStorage.setItem('paper_project', id), fixture.project_id);
    await page.goto(origin);
    await page.getByRole('button', { name: '文献与术语', exact: true }).click();
    await page.getByRole('heading', {name: '文献与知识工作台', exact: true}).waitFor();
    await page.waitForFunction(() => {
      const summary = document.querySelector('.reference-project-context summary');
      return summary && !summary.textContent.includes('—');
    });
    const selectTab = async key => {
      const label = tabs.find(item => item[0] === key)[1];
      await page.getByRole('tab', { name: label, exact: true }).click();
      await page.locator('.references-workspace > [role=tabpanel] .research-columns').waitFor();
      if (key === 'library') await page.locator('.reference-row').first().waitFor();
      if (key === 'reading') await page.locator('.dr-documents article').first().waitFor();
      if (key === 'evidence') await page.locator('.ek-unit').first().waitFor();
      if (key === 'wiki') {
        await page.locator('.knowledge-page-item').first().waitFor();
        await page.locator('.knowledge-page-item').first().click();
        await page.locator('.knowledge-block').first().waitFor();
      }
      if (key === 'manage') await page.getByLabel('统一查询问题', { exact: true }).waitFor();
      if (key === 'wiki' || key === 'manage') await page.waitForFunction(() => {
        const summary = document.querySelector('.knowledge-system-status summary');
        return summary && !summary.textContent.includes('—');
      });
      await page.locator('main').evaluate(el => el.scrollTop = 0);
      await page.evaluate(() => window.scrollTo(0, 0));
    };
    for (const [key] of tabs) {
      await selectTab(key);
      assert.equal(await page.locator('.research-columns > .research-directory').count(), 1, key + ' directory');
      assert.equal(await page.locator('.research-columns > .research-inspector').count(), 1, key + ' inspector');
      await page.screenshot({ path: path.join(out, `${live ? 'live' : 'synthetic'}-${key}-desktop.png`) });
      checks.push(`${key}: desktop directory, content and inspector`);
    }
    await selectTab('manage');
    const tools = page.locator('.research-tools > .research-directory .research-directory-item');
    await page.getByLabel('统一查询问题', {exact: true}).fill('切换工具后保留的问题');
    for (let index = 0; index < await tools.count(); index++) {
      await tools.nth(index).click();
      assert.equal(await page.locator('.research-tool-section:visible').count(), 1);
      assert((await page.locator('.research-tool-section:visible h3').innerText()).length > 0);
    }
    await tools.first().click();
    assert.equal(await page.getByLabel('统一查询问题', {exact: true}).inputValue(), '切换工具后保留的问题');
    checks.push('8 maintenance tools stay mounted and display one task at a time');
    await selectTab('library');
    const firstTitle = await page.locator('.reference-document-heading h2').innerText();
    await page.locator('.reference-row').nth(1).getByRole('button').click();
    assert.notEqual(await page.locator('.reference-document-heading h2').innerText(), firstTitle);
    await page.locator('.research-filters summary').click();
    await page.getByLabel('按文献语种筛选', { exact: true }).selectOption('zh');
    assert((await page.locator('.reference-row').allTextContents()).every(value => value.includes('中文')));
    await page.getByLabel('按文献语种筛选', { exact: true }).selectOption('all');
    checks.push('library selection updates full details and language filtering remains available');
    await selectTab('evidence');
    await page.locator('.ek-unit').first().getByRole('button').click();
    await page.locator('.ek-inline-review .ek-context pre').first().waitFor();
    await page.getByRole('button', { name: '继续核对与修订', exact: true }).click();
    await page.getByLabel('核对说明', { exact: true }).waitFor();
    assert.equal(await page.getByRole('dialog').count(), 0);
    await page.screenshot({ path: path.join(out, `${live ? 'live' : 'synthetic'}-evidence-selected.png`) });
    checks.push('evidence original and review steps open inline');
    for (const width of [1280, 1024, 768, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      for (const [key] of tabs) {
        await selectTab(key);
        assert(await page.locator('main').evaluate(el => el.scrollWidth <= el.clientWidth + 2), `${key}: main overflow at ${width}`);
        const columns = page.locator('.references-workspace > [role=tabpanel] .research-columns').first();
        assert(await columns.evaluate(el => el.scrollWidth <= el.clientWidth + 2), `${key}: workbench overflow at ${width}`);
        if (width === 390) await page.screenshot({ path: path.join(out, `${live ? 'live' : 'synthetic'}-${key}-mobile.png`) });
      }
      checks.push(`all 6 pages fit ${width}px without horizontal overflow`);
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(failures, []);
    assert.deepEqual(writes, []);
    const report = { passed: true, live, checks, errors, failures, writes, paid_model_calls: 0 };
    fs.writeFileSync(path.join(out, `${live ? 'live' : 'synthetic'}-layout-report.json`), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(out, 'layout-failure.png') });
    fs.writeFileSync(path.join(out, `${live ? 'live' : 'synthetic'}-layout-report.json`), JSON.stringify({ passed: false, checks, errors, failures, writes, error: error.message }, null, 2));
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
