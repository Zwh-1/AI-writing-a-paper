// Complete local workflows on an isolated synthetic project; no paid requests.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const out = path.join(root, '.runtime/reference-workflows-20261031');
const fixture = JSON.parse(fs.readFileSync(path.join(out, 'fixture.json'), 'utf8'));
const origin = `http://127.0.0.1:${fixture.port}`;
const { chromium } = require(path.join(root, '.runtime/browser/node_modules/playwright'));

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: path.join(root, '.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
  const checks = [], errors = [], failures = [];
  const record = (name) => { checks.push(name); console.log(name); };
  let page;
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}, locale: 'zh-CN'});
    const token = JSON.parse(fs.readFileSync(path.join(root, `.runtime/session-${fixture.port}.json`), 'utf8')).bootstrap_token;
    const session = await context.request.post(origin + '/api/v1/session', {data: {token}});
    assert.equal(session.status(), 200);
    const base = `/api/v1/projects/${fixture.project_id}`;
    const get = async (url) => {
      const response = await context.request.get(origin + url);
      assert.equal(response.status(), 200, url);
      return response.json();
    };
    page = await context.newPage();
    page.setDefaultTimeout(45000);
    page.on('pageerror', e => errors.push(e.message));
    page.on('response', r => { if (r.url().includes('/api/v1/') && r.status() >= 400) failures.push({path: new URL(r.url()).pathname, status: r.status()}); });
    await page.addInitScript(id => localStorage.setItem('paper_project', id), fixture.project_id);
    await page.goto(origin);
    await page.getByRole('button', {name: '文献与术语', exact: true}).click();
    await page.getByRole('heading', {name: '文献与知识工作台', exact: true}).waitFor();
    const tab = async name => page.getByRole('tab', {name, exact: true}).click();

    // A saved term must survive a reload, edit and delete.
    await page.getByRole('button', {name: '新增术语', exact: true}).click();
    let dialog = page.getByRole('dialog', {name: '新增术语', exact: true});
    await dialog.getByLabel('中文术语', {exact: true}).fill('凭证');
    await dialog.getByLabel('对应英文', {exact: true}).fill('credential');
    await dialog.getByRole('button', {name: '保存术语', exact: true}).click();
    await dialog.waitFor({state: 'detached'});
    await page.locator('.reference-term-list').getByText('credential', {exact: true}).waitFor();
    await page.reload();
    await page.getByRole('button', {name: '文献与术语', exact: true}).click();
    await page.locator('.reference-term-list').getByText('credential', {exact: true}).waitFor();
    dialog = page.getByRole('region', {name: '中英文术语表', exact: true});
    await dialog.getByLabel('对应英文', {exact: true}).fill('verifiable credential');
    await dialog.getByRole('button', {name: '保存术语', exact: true}).click();
    await page.locator('.reference-term-list').getByText('verifiable credential', {exact: true}).waitFor();
    record('术语新增、编辑、刷新后持久保存');

    // Bibliography edits keep the original file association.
    await tab('文献书目');
    await page.locator('.reference-row').first().getByRole('button').click();
    await page.getByRole('button', {name: '核对书目', exact: true}).click();
    dialog = page.getByRole('dialog', {name: '参考文献信息与原文', exact: true});
    await dialog.getByLabel('作者（原文顺序）', {exact: true}).fill('合成验收作者');
    await dialog.getByRole('button', {name: '保存书目', exact: true}).click();
    await dialog.waitFor({state: 'detached'});
    assert((await get(base + '/references')).some(r => JSON.parse(r.metadata).authors.includes('合成验收作者') && r.file_id));
    record('书目编辑与原文关联保存');

    // Read a range, save an exact finding, and resume the same durable record.
    await tab('问题阅读');
    await page.locator('.dr-documents article').filter({hasText: '甲方法研究'}).getByRole('button').click();
    await page.getByRole('button', {name: '按问题阅读', exact: true}).click();
    dialog = page.getByRole('region', {name: '围绕问题阅读原文', exact: true});
    await dialog.getByLabel('本次阅读问题', {exact: true}).fill('单用户验证的适用条件与局限是什么？');
    await dialog.getByLabel('阅读关联章节', {exact: true}).selectOption(fixture.section_id);
    await dialog.getByRole('button', {name: '建立阅读记录与目录', exact: true}).click();
    await dialog.getByRole('button', {name: '读取所选原文范围', exact: true}).waitFor();
    await dialog.getByLabel('结束阅读单元', {exact: true}).fill('4');
    await dialog.getByRole('button', {name: '读取所选原文范围', exact: true}).click();
    await dialog.locator('.dr-passages article').nth(3).waitFor();
    assert((await dialog.locator('.dr-passages article strong').nth(1).innerText()).includes('单元 2'));
    await dialog.getByRole('button', {name: '继续核对与记录', exact: true}).click();
    await dialog.getByLabel('阅读核对主题', {exact: true}).selectOption('conditions');
    await dialog.getByLabel('阅读引句来源', {exact: true}).selectOption({index: 2});
    await dialog.getByLabel('阅读核对引句', {exact: true}).fill('适用条件仅为单用户环境。');
    await dialog.getByLabel('阅读核对人', {exact: true}).fill('合成数据验收');
    await dialog.getByLabel('阅读核对说明', {exact: true}).fill('仅记录合成原文中的单用户限定，不视为本项目测试结果。');
    await dialog.getByRole('button', {name: '保存当前主题的核对记录', exact: true}).click();
    await dialog.locator('.dr-findings blockquote').waitFor();
    await dialog.getByRole('button', {name: '关闭当前详情', exact: true}).click();
    await page.getByRole('button', {name: '继续阅读与核对', exact: true}).first().click();
    dialog = page.getByRole('region', {name: '围绕问题阅读原文', exact: true});
    await dialog.locator('.dr-passages article').nth(3).waitFor();
    assert.equal(await dialog.locator('.dr-passages article').count(), 4);
    await dialog.getByRole('button', {name: '2. 核对与记录', exact: true}).click();
    await dialog.locator('.dr-findings blockquote').waitFor();
    await dialog.getByRole('button', {name: '关闭当前详情', exact: true}).click();
    record('按问题建档、范围阅读、逐字引句核对、关闭后继续阅读');

    // The original filter must apply to full-text/vector retrieval as well as listings.
    await tab('原文与证据');
    await page.getByLabel('证据原件筛选', {exact: true}).selectOption(fixture.first_file);
    await page.getByLabel('检索证据知识库', {exact: true}).fill('蓝鲸');
    await page.getByRole('button', {name: '检索原文', exact: true}).click();
    await page.getByText('检索完成；结果仅用于定位原文', {exact: true}).waitFor();
    assert.equal(await page.locator('.ek-unit').count(), 0);
    await page.getByLabel('证据原件筛选', {exact: true}).selectOption(fixture.second_file);
    await page.getByRole('button', {name: '检索原文', exact: true}).click();
    await page.locator('.ek-unit').first().waitFor();
    assert((await page.locator('.ek-unit').allTextContents()).every(text => text.includes('乙并发研究')));
    await page.getByRole('button', {name: '清除检索', exact: true}).click();
    await page.getByLabel('证据原件筛选', {exact: true}).selectOption('');
    await page.locator('.ek-unit').first().getByRole('button').click();
    dialog = page.getByRole('region', {name: '原文与证据核对', exact: true});
    await dialog.locator('.ek-context pre').first().waitFor();
    await dialog.getByRole('button', {name: '继续核对与修订', exact: true}).click();
    await dialog.getByLabel('核对人', {exact: true}).fill('合成数据验收');
    await dialog.getByLabel('核对说明', {exact: true}).fill('对照合成资料的完整文本和上下文。');
    await dialog.getByLabel('已对照原始文件及必要上下文', {exact: true}).check();
    for (const checkbox of await dialog.locator('fieldset input[type=checkbox]').all()) await checkbox.check();
    await dialog.getByRole('button', {name: '确认原文已核对', exact: true}).click();
    await dialog.getByRole('button', {name: '关闭当前详情', exact: true}).click();
    assert((await get(base + '/evidence-knowledge')).reviewed >= 1);
    record('证据检索严格限定原件、上下文读取、核对状态持久保存');

    // Chapter evidence selection must round-trip through the API.
    await page.locator('.ek-selection summary').click();
    await page.route('**/evidence-knowledge/sections/*/selection', async route => {
      if (route.request().method() === 'GET') await new Promise(resolve => setTimeout(resolve, 400));
      await route.continue();
    });
    await page.locator('.ek-selection select').selectOption(fixture.section_id);
    assert(await page.locator('.ek-unit').first().getByLabel('关联当前章节', {exact: true}).isDisabled());
    await page.waitForFunction(() => !document.querySelector('.ek-directory-selection input')?.disabled);
    await page.locator('.ek-unit').first().getByLabel('关联当前章节', {exact: true}).check();
    await page.getByRole('button', {name: '保存章节证据', exact: true}).click();
    await page.getByText('章节证据已保存', {exact: true}).waitFor();
    assert.equal((await get(base + `/evidence-knowledge/sections/${fixture.section_id}/selection`)).unit_ids.length, 1);
    await page.screenshot({path: path.join(out, 'evidence-desktop.png')});
    record('章节证据加载期间禁用勾选，读回后选择与写作输入关联保存');

    await page.getByRole('button', {name: '全文与向量检索', exact: true}).click();
    await page.getByLabel('检索文献知识库', {exact: true}).fill('凭证验证');
    await page.getByRole('button', {name: '检索知识库', exact: true}).click();
    await page.locator('.knowledge-hit').first().waitFor();
    await page.locator('.knowledge-hit').first().getByRole('button', {name: '查看原文位置', exact: true}).click();
    await page.getByRole('dialog').first().waitFor();
    await page.getByRole('button', {name: '关闭资料查看', exact: true}).click();
    record('全文检索返回可回查原件的位置');

    // Search must stay filtered while opening and saving Wiki revisions.
    await tab('图谱与Wiki');
    const graph = page.getByRole('region', {name: '图谱与Wiki知识库', exact: true});
    await graph.locator('.knowledge-page-item').first().waitFor();
    await graph.getByLabel('问题或关键词', {exact: true}).fill('zzzznohit987654321');
    await graph.getByRole('button', {name: '检索知识', exact: true}).click();
    await graph.getByText('检索完成', {exact: true}).waitFor();
    assert.equal(await graph.locator('.knowledge-page-item').count(), 0);
    await graph.getByLabel('问题或关键词', {exact: true}).fill('甲方法研究');
    await graph.getByRole('button', {name: '检索知识', exact: true}).click();
    await graph.locator('.knowledge-page-item').first().waitFor();
    assert.equal(await graph.locator('.knowledge-page-item').count(), 1);
    await graph.locator('.knowledge-page-item').click();
    await graph.locator('.knowledge-block').first().waitFor();
    assert.equal(await graph.locator('.knowledge-page-item').count(), 1);
    await graph.getByRole('button', {name: '查看出处 1', exact: true}).first().click();
    dialog = page.getByRole('dialog', {name: '论断与原文核对', exact: true});
    await dialog.waitFor();
    await dialog.getByRole('button', {name: '关闭窗口', exact: true}).click();
    await graph.getByRole('button', {name: '编辑与备注', exact: true}).click();
    await graph.getByRole('button', {name: '新增备注', exact: true}).click();
    const wikiNote = '合成数据验收备注：保留单用户条件。' + Date.now();
    await graph.locator('.knowledge-block textarea').last().fill(wikiNote);
    await graph.getByRole('button', {name: '保存修订候选', exact: true}).click();
    await graph.getByRole('button', {name: '发布当前修订', exact: true}).click();
    await graph.locator('p.knowledge-text').filter({hasText: wikiNote}).waitFor();
    assert.equal(await graph.locator('.knowledge-page-item').count(), 1);
    await page.screenshot({path: path.join(out, 'wiki-desktop.png')});
    record('Wiki 搜索不被刷新覆盖、出处回查、编辑发布及筛选保持');

    await graph.getByRole('button', {name: '关系探索', exact: true}).click();
    await graph.getByLabel('问题或关键词', {exact: true}).fill('单用户');
    await graph.getByRole('button', {name: '检索知识', exact: true}).click();
    await graph.getByRole('button', {name: '原文与支持性核对', exact: true}).first().waitFor();
    assert.equal(await graph.locator('svg.knowledge-graph').count(), 1);
    await graph.getByRole('button', {name: '章节准备', exact: true}).click();
    await graph.getByLabel('补充原文检索', {exact: false}).uncheck();
    await graph.getByRole('button', {name: '检索知识', exact: true}).click();
    await graph.getByLabel('目标章节', {exact: true}).selectOption(fixture.section_id);
    await graph.getByRole('button', {name: '执行章节阅读计划', exact: true}).click();
    await graph.getByText('原文阅读范围已保存；方法、条件与局限仍需核对', {exact: true}).waitFor();
    record('关系图谱与章节阅读计划执行保存');

    await tab('处理与维护');
    await page.getByLabel('统一查询问题', {exact: true}).fill('单用户验证有什么局限？');
    await page.getByRole('button', {name: '预览路由', exact: true}).click();
    await page.getByText('推荐 native', {exact: false}).waitFor();
    await page.getByRole('button', {name: '执行统一查询', exact: true}).click();
    await page.getByText('任务状态：succeeded', {exact: true}).waitFor();
    await page.getByRole('button', {name: '构建与更新', exact: true}).click();
    await page.getByRole('button', {name: '预览构建范围', exact: true}).click();
    await page.getByRole('button', {name: '开始构建', exact: true}).waitFor();
    await page.getByRole('button', {name: '检查与版本', exact: true}).click();
    await page.getByRole('button', {name: '对照评测', exact: true}).first().click();
    await page.getByRole('button', {name: '建立 80 个任务草稿', exact: true}).waitFor();
    record('维护统一查询任务完成、构建预览、版本检查与评测入口可操作');

    await page.setViewportSize({width: 390, height: 844});
    for (const name of ['问题阅读', '原文与证据', '图谱与Wiki', '统一术语', '处理与维护']) {
      await tab(name);
      await page.getByRole('tabpanel').waitFor();
      assert(await page.locator('main').evaluate(el => el.scrollWidth <= el.clientWidth + 2), `${name} overflows at 390px`);
    }
    await page.screenshot({path: path.join(out, 'maintenance-mobile.png')});
    record('五个页面在390px宽度无横向溢出');
    await tab('统一术语');
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', {name: '删除术语：凭证', exact: true}).click();
    await page.getByRole('button', {name: '删除术语：凭证', exact: true}).waitFor({state: 'detached'});
    assert.deepEqual(await get(base + '/terms'), []);
    record('术语删除同步到持久数据');
    assert.deepEqual(errors, []);
    assert.deepEqual(failures, []);
    const report = {passed: true, synthetic: true, checks, errors, failures, paid_model_calls: 0};
    fs.writeFileSync(path.join(out, 'browser-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  } catch (e) {
    if (page) await page.screenshot({path: path.join(out, 'failure.png')});
    fs.writeFileSync(path.join(out, 'browser-report.json'), JSON.stringify({passed: false, checks, errors, failures, error: e.message}, null, 2));
    throw e;
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
