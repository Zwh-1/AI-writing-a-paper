const fs = require("fs"),
  path = require("path"),
  assert = require("assert");
const pw = require("C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
(async () => {
  const root = path.resolve(__dirname, ".."),
    port = process.env.PAPER_TEST_PORT || 8781,
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
  const errors = [],
    checks = [];
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
  async function passed(name) {
    checks.push(name);
    console.log("PASS " + name);
  }
  async function state() {
    return req("/projects/" + pid + "/rules", undefined, "GET");
  }
  let pid;
  try {
    const project = await req("/projects", {
      title: "论文规则端到端验收 " + Date.now(),
    });
    pid = project.id;
    const first = await req("/projects/" + pid + "/rules", {
      statement: "摘要应控制在300至500字之间",
      scope: "abstract",
      checker: "word_range",
      value: { min: 300, max: 500 },
      state: "candidate",
      modality: "pending",
    });
    const conflict1 = await req("/projects/" + pid + "/rules", {
      statement: "参考文献至少10篇",
      checker: "reference_min",
      value: 10,
      modality: "mandatory",
    });
    await req("/projects/" + pid + "/rules", {
      statement: "参考文献至少20篇",
      checker: "reference_min",
      value: 20,
      modality: "mandatory",
    });
    await page.addInitScript(
      (id) => localStorage.setItem("paper_project", id),
      pid,
    );
    await page.goto(
      origin + "/#token=" + encodeURIComponent(session.bootstrap_token),
    );
    await page.getByRole("button", { name: "论文规则", exact: true }).click();
    await page.getByRole("tab", { name: "发布与设置", exact: true }).click();
    await page
      .getByRole("heading", { name: "发布前检查", exact: true })
      .waitFor();
    await page.getByRole("tab", { name: "整理与审核", exact: true }).click();
  await page.getByText('高级整理与独立审核工具',{exact:true}).click();
    await page.getByLabel("选择AI整理的规则文件").waitFor();
    assert(
      (await page.locator(".rule-extraction-panel").getAttribute("open")) !==
        null,
    );
    await passed("extraction_controls_visible_without_hidden_discovery");
    await page.getByRole("tab", { name: "条款管理", exact: true }).click();
    await page.getByRole("button", { name: "手工添加", exact: true }).click();
    let dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("具体要求", { exact: true })
      .fill("手工新增但仍需核对的要求");
    await dialog.getByLabel("状态", { exact: true }).selectOption("candidate");
    await dialog.getByRole("button", { name: "保存规则", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    assert.equal(
      (await state()).rules.find(
        (r) => r.statement === "手工新增但仍需核对的要求",
      ).state,
      "candidate",
    );
    await passed("manual_candidate_state_preserved");
    let row = page
      .locator(".rule-list .rule-row")
      .filter({ hasText: first.statement });
    await row.getByRole("button", { name: "核对与编辑", exact: true }).click();
    dialog = page.getByRole("dialog");
    assert.equal(
      await dialog.getByLabel("最少字数", { exact: true }).inputValue(),
      "300",
    );
    await dialog.getByLabel("最少字数", { exact: true }).fill("320");
    await dialog.getByLabel("最多字数", { exact: true }).fill("520");
    await dialog.getByLabel("性质", { exact: true }).selectOption("mandatory");
    await dialog.getByLabel("状态", { exact: true }).selectOption("confirmed");
    await dialog.getByRole("button", { name: "保存规则", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    assert.equal(
      JSON.parse((await state()).rules.find((r) => r.id === first.id).value)
        .min,
      320,
    );
    await passed("structured_range_edit_and_confirmation");
    await page.getByRole("button", { name: "检查并发布", exact: true }).click();
    assert(await page.getByRole("button", { name: "发布规则包", exact: true }).isDisabled());
    assert.equal((await state()).package, null);
    await passed("conflict_blocks_publication_in_ui");
    await page.getByRole("tab", { name: "条款管理", exact: true }).click();
    await page
      .locator(".rule-list .rule-row")
      .filter({ hasText: conflict1.statement })
      .getByRole("button", { name: "排除", exact: true })
      .click();
    await page.getByRole("tab", { name: "发布与设置", exact: true }).click();
    await page
      .getByText("发布条件已满足。待确认条款及正反例不进入规则包。", {
        exact: true,
      })
      .waitFor();
    await page.getByRole("button", { name: "发布规则包", exact: true }).click();
    await page.getByText("当前生效：规则包 V1", { exact: true }).waitFor();
    await passed("exclude_conflict_then_publish");
    await page.locator(".rule-publication")
      .getByRole("button", { name: "查看生效快照", exact: true })
      .click();
    dialog = page.getByRole("dialog", { name: "生效规则快照" });
    await dialog.waitFor();
    const snapshot = await dialog.innerText();
    assert(snapshot.includes(first.statement));
    assert(!snapshot.includes("手工新增但仍需核对的要求"));
    await page.getByRole("button", { name: "关闭原文依据" }).click();
    await passed("published_snapshot_contains_only_eligible_rules");
    await page.getByRole("tab", { name: "条款管理", exact: true }).click();
    await page.getByLabel("搜索规则", { exact: true }).fill("摘要");
    assert.equal(await page.locator(".rule-list .rule-row").count(), 1);
    await page
      .locator(".rule-list .rule-row")
      .getByRole("button", { name: "核对与编辑", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("采用 / 排除依据").fill("核对后的适用依据");
    await dialog.getByRole("button", { name: "保存规则", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    await page.getByRole("tab", { name: "发布与设置", exact: true }).click();
    await page
      .getByText("有尚未发布的修改；当前写作仍加载已发布版本。", {
        exact: true,
      })
      .waitFor();
    await passed("search_and_unpublished_change_detection");
    await page.getByRole("tab", { name: "条款管理", exact: true }).click();
    await page.getByLabel("搜索规则", { exact: true }).fill("");
    await page.getByRole("tab", { name: /已归并/ }).click();
    await page.getByRole("button", { name: "恢复待确认", exact: true }).click();
    await page.getByRole("tab", { name: /待确认/ }).click();
    await page
      .locator(".rule-list .rule-row")
      .filter({ hasText: conflict1.statement })
      .waitFor();
    await passed("rejected_rule_can_be_restored");
    await page.getByLabel("搜索规则").fill("手工新增");
    await page.getByLabel("选择本页规则").check();
    await page.getByRole("button", { name: "排除所选", exact: true }).click();
    await page.getByText("没有匹配的规则", { exact: true }).waitFor();
    assert.equal(
      (await state()).rules.find(
        (r) => r.statement === "手工新增但仍需核对的要求",
      ).state,
      "rejected",
    );
    await passed("batch_exclusion_with_explicit_selection");
    await page.getByLabel("搜索规则").fill("");
    await page.getByRole("tab", { name: /全部记录/ }).click();
    // Import local text to test real parsing, rule extraction, direct source review and publication coverage.
    const upload = await context.request.post(
      origin + "/api/v1/projects/" + pid + "/files",
      {
        headers: { "X-CSRF-Token": csrf },
        multipart: {
          role: "rules",
          file: {
            name: "规则验收材料.txt",
            mimeType: "text/plain",
            buffer: Buffer.from(
              "摘要必须说明研究方法与实际结果。\n参考文献要求具有真实出处。",
              "utf8",
            ),
          },
        },
      },
    );
    assert.equal(upload.status(), 200, await upload.text());
    const imported = await upload.json();
    await page.getByRole("tab", { name: "整理与审核", exact: true }).click();
    await page
      .getByRole("button", { name: "从课件整理候选", exact: true })
      .click();
    await page
      .getByText(/本地候选整理 · 已完成/)
      .first()
      .waitFor({ timeout: 20000 });
    await page
      .getByText(/新增 [1-9].*条候选，请到下方核对/)
      .first()
      .waitFor({ timeout: 20000 });
    await passed("local_extraction_reports_real_task_result");
    const sourced = (await state()).rules.find((r) => r.source_id);
    assert(sourced);
    await page.getByRole("tab", { name: "条款管理", exact: true }).click();
    await page.getByLabel("搜索规则").fill(sourced.statement);
    await page.locator(".rule-list .rule-source-link").first().click();
    dialog = page.getByRole("dialog", { name: "规则原文依据" });
    await dialog.waitFor();
    assert(
      (await dialog.locator("pre").innerText()).includes(sourced.statement),
    );
    await dialog.getByRole("button", { name: "打开原页核对" }).click();
    await page
      .locator(".modal")
      .filter({ hasText: "规则验收材料.txt" })
      .waitFor();
    await page
      .getByRole("button", { name: "确认本页原文已核对", exact: true })
      .click();
    await page.locator(".source-review-complete").waitFor();
    await page.locator(".modal>header .icon-button").click();
    await passed("source_opens_exact_page_and_persists_review");
    await page.getByRole("tab", { name: "整理与审核", exact: true }).click();
    await page.getByLabel("规则整理处理方式").selectOption("manual");
    await page
      .getByLabel("选择AI整理的规则文件")
      .selectOption(sourced.source_file_id);
    await page.getByText(/共 [1-9] 个可整理来源片段/).waitFor();
    assert(
      await page
        .getByRole("button", { name: "整理候选（计费）", exact: true })
        .isEnabled(),
    );
    await passed("ai_source_count_and_scope_ready_without_paid_call");
    // Exercise continuation and task refresh with intercepted AI requests; no provider call.
    const actualJobs = await req('/tasks?project_id='+pid,undefined,'GET');
    const completedAI={id:'browser-mock-completed-ai',project_id:pid,kind:'rules_ai',status:'succeeded',phase:'完成',progress:100,payload:JSON.stringify({file_ids:[sourced.source_file_id],source_offset:0,source_limit:1}),result:JSON.stringify({added:1,processed_source_units:1,next_offset:1,total_source_units:3})};
    let mockJobs=[completedAI,...actualJobs],submittedBatch=null;
    const jobsPattern='**/api/v1/tasks?project_id='+pid;
    const planPattern='**/api/v1/projects/'+pid+'/rules/ai-sources?file_id=*';
    const extractPattern='**/api/v1/projects/'+pid+'/rules/ai-extract';
    await page.route(jobsPattern,route=>route.fulfill({json:mockJobs}));
    await page.route(planPattern,route=>route.fulfill({json:{total:3,local_only:0}}));
    await page.route(extractPattern,async route=>{submittedBatch=route.request().postDataJSON();const job={...completedAI,id:'browser-mock-queued-ai',status:'queued',result:null,progress:0,payload:JSON.stringify(submittedBatch)};mockJobs=[job,...mockJobs];await route.fulfill({json:job});});
    await page.getByLabel('选择AI整理的规则文件').selectOption('');
    await page.getByLabel('选择AI整理的规则文件').selectOption(sourced.source_file_id);
    await page.getByRole('button',{name:/选择下一批/}).click();
    assert.equal(await page.getByLabel('起始来源片段').inputValue(),'2');
    await passed('ai_continuation_selects_next_unprocessed_batch');
    await page.getByRole('button',{name:'整理候选（计费）',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.rule-jobs')?.textContent.includes('AI 规则整理 · 排队中'));
    assert.equal(submittedBatch.source_offset,1);
    assert.equal(await page.getByLabel('起始来源片段').inputValue(),'2');
    await passed('ai_submission_keeps_offset_through_task_refresh_mocked');
    await page.unroute(jobsPattern);await page.unroute(planPattern);await page.unroute(extractPattern);
    await page.waitForFunction(()=>!document.querySelector('.rule-jobs')?.textContent.includes('AI 规则整理 · 排队中'));
    await page.getByRole("tab", { name: "条款管理", exact: true }).click();
    await page.getByLabel("搜索规则").fill("");
    await page.screenshot({
      path: path.join(root, "docs/screenshots/rules-repair/desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: path.join(root, "docs/screenshots/rules-repair/mobile.png"),
      fullPage: true,
    });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    await passed("mobile_layout_has_no_horizontal_overflow");
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(root, "docs/rules-repair-browser-report.json"),
      JSON.stringify(
        {
          passed: true,
          checks,
          page_errors: errors,
          paid_calls: false,
          project_id: pid,
          tested_at: new Date().toISOString(),
        },
        null,
        2,
      ),
    );
    console.log("All " + checks.length + " browser checks passed.");
  } catch (e) {
    await page.screenshot({
      path: path.join(root, "docs/screenshots/rules-repair/failure.png"),
      fullPage: true,
    });
    console.error(e);
    console.error("Page errors:", errors);
    fs.writeFileSync(
      path.join(root, "docs/rules-repair-browser-report.json"),
      JSON.stringify(
        {
          passed: false,
          checks,
          page_errors: errors,
          error: String(e),
          paid_calls: false,
        },
        null,
        2,
      ),
    );
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
