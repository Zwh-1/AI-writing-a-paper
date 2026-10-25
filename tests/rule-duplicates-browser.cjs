const fs = require("fs"),
  path = require("path"),
  assert = require("assert");
const pw = require("C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
(async () => {
  const root = path.resolve(__dirname, ".."),
    port = process.env.PAPER_TEST_PORT || 8782,
    origin = "http://127.0.0.1:" + port;
  const session = JSON.parse(
    fs.readFileSync(
      path.join(root, ".runtime/session-" + port + ".json"),
      "utf8",
    ),
  );
  const browser = await pw.chromium.launch({ headless: true }),
    context = await browser.newContext({
      viewport: { width: 1480, height: 1080 },
      locale: "zh-CN",
    }),
    page = await context.newPage();
  page.setDefaultTimeout(8000);
  const checks = [],
    errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const auth = await context.request.post(origin + "/api/v1/session", {
      data: { token: session.bootstrap_token },
    }),
    csrf = (await auth.json()).csrf;
  async function req(route, data, method = "POST") {
    const r = await context.request.fetch(origin + "/api/v1" + route, {
      method,
      headers: { "X-CSRF-Token": csrf },
      ...(data === undefined ? {} : { data }),
    });
    assert.equal(r.status(), 200, route + " " + (await r.text()));
    return r.json();
  }
  const pass = (name) => {
    checks.push(name);
    console.log("PASS " + name);
  };
  let pid;
  try {
    pid = (await req("/projects", { title: "规则查重验收 " + Date.now() })).id;
    async function material(name) {
      const r = await context.request.post(
        origin + "/api/v1/projects/" + pid + "/files",
        {
          headers: { "X-CSRF-Token": csrf },
          multipart: {
            role: "rules",
            file: {
              name,
              mimeType: "text/plain",
              buffer: Buffer.from(name + "\n参考文献不少于20篇。", "utf8"),
            },
          },
        },
      );
      assert.equal(r.status(), 200, await r.text());
      const f = (await r.json()).file;
      let pages = [];
      for (let i = 0; i < 50; i++) {
        pages = await req("/files/" + f.id + "/pages", undefined, "GET");
        if (pages.length) break;
        await page.waitForTimeout(100);
      }
      assert(pages.length);
      const detail = await req("/pages/" + pages[0].id, undefined, "GET");
      await req("/pages/" + pages[0].id, { review_state: "reviewed" }, "PATCH");
      return detail.sources.find((s) => s.text.includes("参考文献"));
    }
    const sourceA = await material("学校规范甲.txt"),
      sourceB = await material("学校规范乙.txt");
    async function add(extra = {}) {
      return req("/projects/" + pid + "/rules", {
        statement: "参考文献不少于20篇",
        checker: "reference_min",
        value: 20,
        modality: "mandatory",
        ...extra,
      });
    }
    const canonical = await add({ source_id: sourceA.id }),
      candidate = await add({
        state: "candidate",
        modality: "recommended",
        source_id: sourceB.id,
      });
    await add({ state: "candidate" });
    await add({ state: "candidate" });
    const different = await add({
      statement: "参考文献不少于10篇",
      value: 10,
      state: "candidate",
    });
    await page.addInitScript(
      (pid) => localStorage.setItem("paper_project", pid),
      pid,
    );
    await page.goto(
      origin + "/#token=" + encodeURIComponent(session.bootstrap_token),
    );
    await page.getByRole("button", { name: "论文规则", exact: true }).click();
    await page.getByRole("tab", { name: /^重复与冲突/ }).click();
    await page
      .getByRole("heading", { name: "先检查重复条款", exact: true })
      .waitFor();
    const panel = page.getByRole("region", { name: "规则重复检查" });
    assert((await panel.innerText()).includes("共 3 条多余记录"));
    assert.equal(
      await panel.locator("input[type=radio]:checked").inputValue(),
      canonical.id,
    );
    pass("automatic_scan_finds_duplicates_and_prefers_confirmed");
    assert(
      await page.evaluate(
        () =>
          !!(
            document
              .getElementById("rule-duplicates")
              .compareDocumentPosition(
                document.getElementById("rule-conflicts"),
              ) & Node.DOCUMENT_POSITION_FOLLOWING
          ),
      ),
    );
    assert.equal(await page.locator(".rule-conflict-row").count(), 2);
    await page.getByText(/相同条款 4 条，已合并展示/).waitFor();
    pass("duplicate_check_precedes_collapsed_conflict_review");
    await panel
      .getByRole("button", { name: "学校规范乙.txt · 第1页", exact: true })
      .click();
    await page.getByRole("dialog", { name: "规则原文依据" }).waitFor();
    assert(
      (await page.getByRole("dialog").innerText()).includes("学校规范乙.txt"),
    );
    await page.getByRole("button", { name: "关闭原文依据" }).click();
    pass("each_duplicate_keeps_its_original_source");
    await panel
      .getByRole("button", { name: "重新检查重复", exact: true })
      .click();
    let w = await req("/projects/" + pid + "/rules", undefined, "GET");
    assert.equal(w.rules.length, 5);
    assert.equal(w.rules.find((r) => r.id === canonical.id).state, "confirmed");
    pass("rescan_is_read_only");
    await panel
      .getByRole("button", { name: "保留所选，归并其余 3 条", exact: true })
      .click();
    await panel
      .getByText("当前未发现内容与执行配置相同的重复条款。", { exact: true })
      .waitFor();
    w = await req("/projects/" + pid + "/rules", undefined, "GET");
    assert.equal(w.rules.length, 5);
    assert.equal(w.rules.find((r) => r.id === canonical.id).state, "confirmed");
    assert.equal(w.rules.find((r) => r.id === candidate.id).state, "rejected");
    assert.equal(w.rules.find((r) => r.id === different.id).state, "candidate");
    pass("merge_preserves_confirmed_rule_and_different_quantity");
    await page.getByRole("tab", { name: "条款管理", exact: true }).click();
    const primary = page
      .locator(".rule-list .rule-row")
      .filter({
        has: page.locator("summary", { hasText: "已保留 3 条归并来源" }),
      });
    await primary.locator("summary").click();
    await primary
      .getByRole("button", { name: "学校规范乙.txt · 第1页", exact: true })
      .click();
    await page.getByRole("dialog", { name: "规则原文依据" }).waitFor();
    await page.getByRole("button", { name: "关闭原文依据" }).click();
    pass("merged_source_remains_accessible_from_kept_rule");
    await page.getByRole("tab", { name: /^重复与冲突/ }).click();
    await panel.locator("summary").filter({ hasText: "最近归并记录" }).click();
    await panel
      .getByRole("button", { name: "恢复本次归并", exact: true })
      .click();
    await panel.getByText("4 条重复 · 文字完全相同", { exact: true }).waitFor();
    w = await req("/projects/" + pid + "/rules", undefined, "GET");
    assert.equal(w.rules.find((r) => r.id === candidate.id).state, "candidate");
    assert.equal(
      w.rules.find((r) => r.id === candidate.id).modality,
      "recommended",
    );
    assert.equal(w.duplicate_merges[0].restored, true);
    pass("restore_returns_previous_state_and_modality");
    fs.mkdirSync(path.join(root, "docs/screenshots/rule-duplicates"), {
      recursive: true,
    });
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: path.join(root, "docs/screenshots/rule-duplicates/desktop.png"),
    });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    await page.screenshot({
      path: path.join(root, "docs/screenshots/rule-duplicates/mobile.png"),
      fullPage: true,
    });
    pass("mobile_duplicate_review_has_no_horizontal_overflow");
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(root, "docs/rule-duplicates-browser-report.json"),
      JSON.stringify(
        {
          passed: true,
          checks,
          page_errors: errors,
          paid_calls: false,
          tested_at: new Date().toISOString(),
        },
        null,
        2,
      ),
    );
    console.log("All " + checks.length + " duplicate browser checks passed.");
  } catch (e) {
    fs.mkdirSync(path.join(root, "docs/screenshots/rule-duplicates"), {
      recursive: true,
    });
    await page.screenshot({
      path: path.join(root, "docs/screenshots/rule-duplicates/failure.png"),
      fullPage: true,
    });
    console.error(e);
    console.error(errors);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
