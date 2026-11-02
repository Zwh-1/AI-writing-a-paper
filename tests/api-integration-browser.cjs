const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

(async () => {
  const root = path.resolve(__dirname, '..'), port = Number(process.env.PAPER_TEST_PORT || 18983);
  const session = JSON.parse(fs.readFileSync(path.join(root, `.runtime/session-${port}.json`), 'utf8'));
  const output = path.join(root, '.runtime/api-integration/browser');fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({headless: true});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, locale: 'zh-CN'});
  const page = await context.newPage(), errors = [], failures = [], paths = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.pathname.startsWith('/api/')) { paths.push(url.pathname); if (response.status() >= 400) failures.push([url.pathname, response.status()]); }
  });
  // Any accidental external request is a test failure, including paid providers.
  const external = [];
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.hostname !== '127.0.0.1') {external.push(url.origin);return route.abort();}
    return route.continue();
  });
  try {
    await page.goto(`http://127.0.0.1:${port}/#token=${session.bootstrap_token}`, {waitUntil: 'networkidle'});
    await page.getByRole('button', {name: '接口与设置', exact: true}).click();
    await page.getByText('DeepSeek 接口', {exact: true}).first().waitFor();
    await page.getByRole('button', {name: '项目概览', exact: true}).click();
    await page.getByRole('button', {name: '资料与文献', exact: true}).click();
    await page.getByText('测试原件.txt', {exact: true}).first().waitFor();
    const check = await page.evaluate(async () => {
      const projects = await (await fetch('/api/v2/projects')).json();
      const id = projects.data.items[0].id;
      const files = await (await fetch(`/api/v2/projects/${id}/files`)).json();
      const download = await fetch(`/api/v2/files/${files.data.items[0].id}/download`);
      const missing = await fetch('/api/v2/this-interface-does-not-exist');
      return {download: await download.text(), disposition: download.headers.get('content-disposition'), missingStatus: missing.status, missing: await missing.json()};
    });
    assert.ok(check.download.includes('接口需要统一返回格式'));
    assert.ok(check.disposition.includes('attachment'));
    assert.equal(check.missingStatus, 404);assert.equal(check.missing.error.code, 'NOT_FOUND');
    assert.equal(paths.some(p => p.startsWith('/api/v1/')), false);
    assert.ok(paths.includes('/api/v2/system/session'));
    assert.ok(paths.some(p => p.startsWith('/api/v2/model-providers/deepseek/')));
    assert.ok(paths.some(p => p.includes('/workspace')));
    assert.deepEqual(errors, []);assert.deepEqual(external, []);
    assert.deepEqual(failures.filter(([p]) => !p.endsWith('/this-interface-does-not-exist')), []);
    await page.screenshot({path: path.join(output, 'materials.png'), fullPage: true});
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify({passed: true, unique_paths: [...new Set(paths)], errors, external_requests: external, download_verified: true, json_404_verified: true}, null, 2));
    console.log('Browser integration passed: canonical v2, settings, materials, binary download, JSON 404; no external calls.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e.stack); process.exitCode = 1; });
