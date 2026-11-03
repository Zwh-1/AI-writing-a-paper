// Run against serve_navigation_fixture.py after building the frontend.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.runtime/sidebar-workspaces-20261102');
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json'), 'utf8'));
const session = JSON.parse(fs.readFileSync(path.join(output, `session-${fixture.port}.json`), 'utf8'));
const { chromium } = require(path.join(root, '.runtime/browser/node_modules/playwright'));
const groups = [
  { label: '文献资料与规则', id: 'sources', pages: [['materials', '资料中心'], ['rules', '论文规则'], ['references', '文献与知识']] },
  { label: '提纲与写作', id: 'writing', pages: [['outline', '提纲与计划'], ['editor', '写作工作台'], ['agent-writing', '有据写作']] },
  { label: '研究与协作', id: 'research', pages: [['research', '研究与成果'], ['studio', '研究助手'], ['products', '产物与互通'], ['advanced', '高级研究与协作']] },
  { label: '检查与交付', id: 'delivery', pages: [['review', '修改与检查'], ['exports', '排版与交付']] },
];
const report = { checks: [], loadedPages: [], errors: [], apiErrors: [], requestsFailed: [], responsive: [] };
const loadedSelectors = {
  materials: '.materials-workspace [role="tabpanel"]',
  rules: '.rules-workspace[aria-busy="false"]',
  references: '.references-main h1',
  outline: '.outline-workspace',
  editor: '.tiptap',
  'agent-writing': '.wa-page .wa-tabs',
  research: '.research-chain',
  studio: '.research-studio .studio-tabs',
  products: '.product-workspace .product-list-heading',
  advanced: '.advanced-tabs',
  review: 'main .page-title',
  exports: 'main .page-title',
};
const escaped = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
async function main() {
  const candidates = fs.readdirSync(path.join(root, '.runtime/browsers'))
    .filter(name => name.startsWith('chromium'))
    .flatMap(name => ['chrome-win64/chrome.exe', 'chrome-headless-shell-win64/chrome-headless-shell.exe']
      .map(tail => path.join(root, '.runtime/browsers', name, tail)));
  const browser = await chromium.launch({ headless: true, executablePath: candidates.find(file => fs.existsSync(file)) });
  let page;
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN' });
    page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('pageerror', error => report.errors.push(error.message));
    page.on('response', response => {
      if (response.url().includes('/api/') && response.status() >= 400)
        report.apiErrors.push({ path: new URL(response.url()).pathname, status: response.status() });
    });
    page.on('requestfailed', request => {
      if (!request.failure()?.errorText?.includes('ERR_ABORTED'))
        report.requestsFailed.push({ url: request.url(), error: request.failure()?.errorText });
    });
    const record = name => { report.checks.push(name); console.log(name); };
    const sidebar = page.locator('aside.sidebar nav');
    const entry = label => sidebar.getByRole('button', { name: new RegExp('^' + escaped(label)) });
    const cards = () => page.locator('main .workspace-navigation-destinations');
    const card = label => cards().getByRole('button', { name: new RegExp('^' + escaped(label)) });
    async function active(group, subpage) {
      await page.waitForFunction(({ group, subpage }) => document.querySelector('.breadcrumb')?.textContent?.includes(`${group} · ${subpage}`), { group: group.label, subpage });
      await card(subpage).waitFor();
      await page.waitForFunction(subpage => [...document.querySelectorAll('main .workspace-navigation-card[aria-current="page"]')].some(button => button.textContent.includes(subpage)), subpage);
      await page.waitForLoadState('networkidle');
      const id = group.pages.find(([, label]) => label === subpage)[0];
      await page.locator(loadedSelectors[id]).first().waitFor();
      if (id === 'agent-writing') await page.getByText('正在读取项目…', { exact: true }).waitFor({ state: 'hidden' });
      if (id === 'advanced') await page.getByText('正在加载功能状态…', { exact: true }).waitFor({ state: 'hidden' });
      if (!report.loadedPages.includes(id)) report.loadedPages.push(id);
      assert.equal(await sidebar.locator('button.active').count(), 1, 'Exactly one sidebar entry must be active');
      assert.equal(await entry(group.label).getAttribute('aria-current'), 'page');
      assert.equal(await cards().count(), 1, 'Each page has one workspace navigation');
      assert.equal(await cards().getByRole('button').count(), group.pages.length);
      assert.equal(await cards().locator('[aria-current="page"]').count(), 1);
    }
    async function overview() {
      await entry('项目概览').click();
      await page.getByRole('heading', { name: '把研究，写成有依据的论文。', exact: true }).waitFor();
      assert.equal(await sidebar.locator('button.active').count(), 1);
      assert.equal(await cards().count(), 0);
    }
    async function project(index) {
      await page.getByLabel('切换论文项目', { exact: true }).selectOption(fixture.projects[index].id);
      await page.waitForFunction(title => document.querySelector('.project-switch select')?.selectedOptions[0]?.textContent === title, fixture.projects[index].title);
    }
    await page.goto(`http://127.0.0.1:${fixture.port}/#token=${encodeURIComponent(session.bootstrap_token)}`);
    await page.getByLabel('切换论文项目', { exact: true }).waitFor();
    await project(0);
    await overview();
    assert.equal(await sidebar.getByRole('button').count(), 5);
    for (const label of ['项目概览', ...groups.map(group => group.label)]) assert.equal(await entry(label).count(), 1);
    record('five_consolidated_sidebar_entries');

    for (const group of groups) {
      await entry(group.label).click();
      await active(group, group.pages[0][1]);
      for (const [, label] of group.pages) {
        await card(label).click();
        await active(group, label);
      }
      const last = group.pages.at(-1)[1];
      await overview();
      await entry(group.label).click();
      await active(group, last);
      await page.screenshot({ path: path.join(output, `${group.id}-desktop.png`) });
      record(`${group.id}_all_subpages_breadcrumb_active_and_return_memory`);
    }

    await overview();
    await project(1);
    for (const group of groups) {
      await entry(group.label).click();
      await active(group, group.pages[0][1]);
      await card(group.pages[1][1]).click();
      await active(group, group.pages[1][1]);
      await overview();
    }
    await project(0);
    for (const group of groups) {
      await entry(group.label).click();
      await active(group, group.pages.at(-1)[1]);
      await overview();
    }
    record('project_memories_are_isolated_for_all_workspaces');

    await entry(groups[1].label).click();
    await active(groups[1], '有据写作');
    await project(1);
    await active(groups[1], '写作工作台');
    await project(0);
    await active(groups[1], '有据写作');
    record('switch_project_inside_workspace_restores_its_own_subpage');

    await page.evaluate(({ pid, fileId }) => {
      const location = { page: 'rules', view: 'sources', fileId, revision: Date.now() };
      sessionStorage.setItem(`paper-source-location:${pid}:rules`, JSON.stringify(location));
      window.dispatchEvent(new CustomEvent('paper-source-workspace-navigation', { detail: { pid, location } }));
    }, { pid: fixture.projects[0].id, fileId: fixture.projects[0].file_id });
    await active(groups[0], '论文规则');
    await page.getByRole('tab', { name: '来源材料', exact: true }).waitFor();
    assert.equal(await page.getByRole('tab', { name: '来源材料', exact: true }).getAttribute('aria-selected'), 'true');
    record('source_navigation_event_preserves_subview_and_source');

    await page.evaluate(({ pid, sectionId }) => {
      const location = { page: 'editor', sectionId };
      sessionStorage.setItem(`paper-workspace-location-v1:${pid}:editor`, JSON.stringify(location));
      window.dispatchEvent(new CustomEvent('paper-workspace-navigation', { detail: { pid, location } }));
    }, { pid: fixture.projects[0].id, sectionId: fixture.projects[0].section_id });
    await active(groups[1], '写作工作台');
    await page.locator('.tiptap').filter({ hasText: fixture.projects[0].text }).first().waitFor();
    record('workspace_navigation_event_opens_target_section');

    await entry(groups[1].label).click();
    await card('提纲与计划').focus();
    await page.keyboard.press('Tab');
    assert.ok(await card('写作工作台').evaluate(button => button === document.activeElement));
    await page.keyboard.press('Tab');
    assert.ok(await card('有据写作').evaluate(button => button === document.activeElement));
    await page.keyboard.press('Enter');
    await active(groups[1], '有据写作');
    assert.ok(await card('有据写作').evaluate(button => button === document.activeElement), 'Keyboard focus stays on the newly active page card');
    await entry('项目概览').focus();
    await page.keyboard.press('Enter');
    await page.getByRole('heading', { name: '把研究，写成有依据的论文。', exact: true }).waitFor();
    record('keyboard_tab_and_enter_switch_workspace_and_sidebar');

    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({ width, height: 950 });
      for (const group of groups) {
        await entry(group.label).click();
        await page.waitForFunction(label => document.querySelector('.breadcrumb')?.textContent?.includes(label + ' · '), group.label);
        await cards().waitFor();
        await page.waitForLoadState('networkidle');
        if (group.id === 'research') await page.locator('.advanced-tabs').waitFor();
        const geometry = await cards().evaluate(element => {
          const rect = element.getBoundingClientRect();
          return { left: rect.left, right: rect.right, width: window.innerWidth, scrollWidth: document.documentElement.scrollWidth,
            cards: [...element.querySelectorAll('button')].map(button => { const box = button.getBoundingClientRect(); return { left: box.left, right: box.right }; }) };
        });
        report.responsive.push({ width, group: group.id, ...geometry });
        assert.ok(geometry.left >= -1 && geometry.right <= width + 1, `${group.label} navigation must fit ${width}px`);
        assert.ok(geometry.cards.every(box => box.left >= -1 && box.right <= width + 1), `${group.label} cards must fit ${width}px`);
        if (width === 390) await page.screenshot({ path: path.join(output, `${group.id}-mobile.png`) });
      }
    }
    record('all_workspace_navigation_fits_390_768_and_1440');
    await page.getByRole('button', { name: '接口与设置', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.breadcrumb')?.textContent.includes('接口与设置'));
    assert.equal(await sidebar.locator('button.active').count(), 0);
    record('settings_remains_available');
    assert.equal(report.loadedPages.length, 12, 'All twelve pages reached their actual content');
    assert.deepEqual(report.errors, [], 'No JavaScript exceptions');
    assert.deepEqual(report.apiErrors, [], 'No failed API responses');
    assert.deepEqual(report.requestsFailed, [], 'No unexpected network failures');
  } catch (error) {
    report.failure = error.stack;
    if (page) await page.screenshot({ path: path.join(output, 'failure.png') }).catch(() => {});
    throw error;
  } finally {
    fs.writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
