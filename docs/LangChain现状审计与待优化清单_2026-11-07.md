# LangChain / LangGraph 现状审计与待优化清单

审计日期：2026-11-07。对象：当前工作区源码，包括尚未提交的 AI 运行时实现；不是对历史提交或已启动进程的审计。

修复进展：D01—D12 已在源码修正，验证范围与生效条件见 [修复与验收记录](LangChain审计问题修复与验收_2026-11-07.md)。以下保留审计时的问题描述和历史复现结果；修复后的验收入口为 `tests/test_ai_runtime_regressions.py`。

结论：已经接入真实的 Runnable、StructuredTool、BaseRetriever、StateGraph 和 SQLite checkpointer，现有租约、预算、来源权限及人工采用机制仍在使用。不过，目前存在选材类型不一致、内容责任误判、对照评测误配和统计口径不一致等具体问题；框架能力的使用范围也明显小于“统一 AI 工作流”的目标。

本次仅增加审计报告及 `.runtime/langchain-audit-2026-11-07/` 下的复现探针，没有修改业务实现、用户正文、模型设置或当前用户数据库，没有调用收费模型。

## 一、验证方式与结论边界

- 原有 `tests/test_ai_runtime.py`：**41 passed**。
- 新增审计探针：**18 passed**，JUnit 位于 `.runtime/langchain-audit-2026-11-07/reproductions.xml`。
- 探针断言的是“当前缺陷能够重现”，通过不代表缺陷已经修复，也不能直接作为未来修复的验收测试；修复后应改为断言正确行为。
- 数据库探针使用原测试套件的临时目录和隔离 SQLite；模型相关数据均为合成记录。部分探针直接调用契约投影函数，不能据此声称整条真实模型链路必然产生该输入。
- 原有测试通过，说明已有回归边界仍成立，不能排除未覆盖的分类、分歧合并和对照条件。
- P1：优先修复，影响选材、核验可信度或评测有效性。P2：后续修复，影响版本追踪、指标解释或功能完整性。本轮没有确认到需要标成 P0 的数据破坏或绕过人工采用问题。

## 二、已确认的具体问题

### D01 · P1 · 证据要求与选材器使用了两套不兼容的类型

位置：`backend/app/ai_contracts/evidence.py:5`、`:33`；`backend/app/writing_brief.py:99`；`backend/app/writing_argument.py:46`。

任务书使用 `reference`、`test_definition`、`run_result`、`result`、`conclusion` 等要求。新选材器的 `kinds()` 只返回 source/literature、fact/project、evidence/project，另对部分记录补 code。`test_definition` 本身也没有加入返回类型。

复现：需要 run_result 时，真实 `test_run`、`data_result` 记录均得到 `type_eligible=False`；需要 reference 或 test_definition 时也不能正确识别。词面更接近的静态测试定义可以排在实际运行结果之前。类型优先机制因此在实验章节等场景失效。

建议：建立共享的证据分类适配表，把真实证据 kind、来源类型、执行状态和任务要求连接起来；result 与 run_result 的映射须保留实测等级检查，不能只改标签。还应明确 reference、knowledge_unit、document_reading 如何进入候选集合。

### D02 · P1 · 已选中有效代码，论证卡仍报告“缺少代码依据”

位置：`backend/app/writing_argument.py:83`、`:97`。

选材器通过证据 kind 识别了 code，但后续缺口判断只比较候选的 `type`。代码观察的 type 仍是 evidence，无法命中需要 code 的任务点。

复现：单个 code_observation 已进入 `candidate_sources`，同一论证卡的 `gaps` 仍写着缺少 code。

影响：提示词和用户界面收到相互矛盾的信息，可能造成无效补证或降低对已登记源码的使用。

建议：缺口判断复用 D01 的共享分类，不另写一套判断。

### D03 · P1 · 内容责任可以在缺少对应主张核验时显示 verified

位置：`backend/app/ai_contracts/tasks.py:90`，尤其 `:102`—`:109`。

`supported=all(...)` 在相关主张为空时为 True。只要任务点被返回 complete 并提供段落索引，即可将内容责任标成 verified。函数也不检查索引是否落在传入的 paragraphs 内。

另外，`evidence_requirements`、`measurement_required`、`claim_strength_cap`、`acceptance_checks` 和 `depends_on` 已进入契约，却没有在此评估中执行。探针中要求运行实测、实际仅给文献中的静态设计摘录，仍可获得 complete=True。

影响范围：这里确认的是辅助覆盖投影存在误判；不能据此推断人工采用门禁已经被绕过。上游仍有原有语义核验及本地证据检查，但此投影不能自称执行了这些新增契约字段。

建议：区分不需要来源的结构性责任与需要证据的责任。后者须具备明确映射的核验记录，执行类型及等级要求；索引、必需检查和依赖都应显式验证。

### D04 · P1 · 多批核验的分歧被最后一条任务点结果覆盖

位置：`backend/app/writing_flow.py:1181`；`backend/app/ai_contracts/tasks.py:94`、`:97`。

原语义核验会合并冲突任务点评价，把分歧标成 unknown，并在 `task_coverage.complete` 中保留未完成状态。但责任投影又从原始 `point_reviews` 建立字典，对同一 point_id 采用最后一条，并没有使用合并后的分歧结论。

复现：同一任务点依次出现 missing 和 complete，`task_coverage.complete=False`，责任投影仍返回 complete=True。

建议：语义核验输出统一的合并后任务点列表，所有下游共用；分歧不能通过列表顺序消失。

### D05 · P2 · 普通写作事实版本变化不会改变 TaskSpec 哈希

位置：`backend/app/ai_contracts/tasks.py:58`；普通写作事实版本字段见 `backend/app/tasks.py:1077`、`backend/app/writing_flow.py:122`。

TaskSpec 的版本指纹包含 fact_versions，普通写作 manifest 使用的却是 fact_revisions。探针把事实版本从 1 改成 2，其他输入不变，得到完全相同的 TaskSpec hash。

影响：责任契约的版本追踪不完整；原 manifest 和现有发送前校验仍可能发现变更，不能把此问题扩大成整个来源版本防护失效。

建议：统一事实版本字段，补齐实际使用的知识单元与文档阅读版本；对所有契约依赖逐字段做变更指纹验收。

### D06 · P1 · 对照评测可能把依据不同的任务配成一组

位置：`backend/app/ai_runtime/evaluation.py:22`—`:24`。

配对基准只抽取少数字段。它包含普通写作的 fact_revisions，却遗漏有据写作的 fact_versions，也没有纳入完整的研究核心、章节合同、正文依赖、实际阅读范围和任务质量配置。

复现：两个任务的 fact_versions 分别为 1 和 2，仍得到相同 `input_basis_hash`，并被标为可人工对照。

建议：使用明确的、按任务种类构建的冻结输入快照。作为实验变量的 engine/quality 单独列出，其余输入、参数及阶段职责应保证可比；整篇任务需要汇总逐章输入，不能只读取顶层 manifest。

### D07 · P1 · 实际模型身份未知也会被当成“相同模型”

位置：`backend/app/ai_runtime/evaluation.py:28`—`:31`、`:50`—`:52`。

`provider_identity.effective()` 已能表达 unknown_pool，但 compare 没检查 `actual_known` 或身份字段完整性。多个全空身份会生成相同 hash，非空的 actual_providers 列表又满足就绪判断。

复现：两个旧端点池任务的实际模型都是 None，`ready_for_human_comparison=True`。

建议：身份未知时禁止给出受控对照就绪结论；展示缺失原因，允许用户单独查看结果。已知身份也应核对每个阶段的角色和实际模型配置。

### D08 · P2 · 发送前失败、没有候选的任务也可显示“可人工对照”

位置：`backend/app/ai_runtime/evaluation.py:34`—`:52`。

就绪判断不要求任务成功、存在有效候选或具有完成的模型响应。只需 manifest 含 base_revision_id 等任一字段、模型列表非空、没有未知请求，就可能通过。

复现：两个 failed 任务都只有 failed_before_send 生成记录，没有候选，仍得到 `ready_for_human_comparison=True`。

建议：将“输入可以配对”“执行成本可以比较”“有效候选可以盲评”分为独立状态；失败案例保留在可靠性统计中。

### D09 · P2 · 对照中的请求数没有表达真实 HTTP 尝试数

位置：`backend/app/ai_runtime/evaluation.py:25`、`:38`；实际尝试表见 `backend/migrations/022_request_execution.sql:13`。

`requests=len(generation_runs)` 统计逻辑生成记录。网络重试和端点切换记录在 provider_attempts，一次逻辑生成可能对应多次实际请求。当前 compare 没有读取后者，也只汇总生成记录的有效提供方身份。

这是静态代码确认的统计口径缺口，本轮未发起真实故障切换请求。

建议：分别输出逻辑请求、实际发送、发送前拒绝、重试、切换、未知结果及费用占用，避免用一个 requests 字段解释稳定性和成本。

### D10 · P2 · 按任务过滤的运行统计返回了整个项目的费用

位置：`backend/app/ai_runtime/api.py:30`—`:41`。

节点和指标使用 task_id 过滤，但 `budget_occupancy` 始终调用 `charge(pid)`。

复现：目标任务费用为 0，同项目另一任务费用为 0.37；请求目标任务的 overview，返回 budget_occupancy=0.37。

建议：有 task_id 时使用 charge_tasks；若需要同时展示项目费用，应使用含义明确的两个字段。

### D11 · P2 · 恢复复用的零耗时混入执行耗时分位数

位置：`backend/app/ai_graphs/chapter.py:64`；`backend/app/ai_runtime/telemetry.py:45`—`:57`。

复用节点以 duration=0 写入，summary 按 stage/kind 聚合时与真正执行混在一起。失败及恢复也未分别提供分布。

复现：5 次真实执行每次 1000ms，随后 6 次复用；界面 P50 变成 0ms。该数值是全部事件的分布，不能解释为模型或节点实际执行更快。

建议：分别提供执行、失败和复用的样本数与耗时分布。保留端到端任务耗时，清晰区分冷启动、首次执行、恢复执行。

### D12 · P2 · 未核对摘录的“反驳”仍显示为已存在反驳关系

位置：`backend/app/ai_contracts/evidence.py:50`—`:64`；上游处理见 `backend/app/writing_flow.py:1137`—`:1146`。

support_view 仅在 supported 缺少 quote_verified 时降级。contradicted 即使没有摘录、quote_verified=False，仍映射为 contradicts；partial 同样直接映射为 qualifies。

复现：只有模型的 contradicted 标签、原文摘录为空，支持视图仍显示 contradicts。原文核验并没有证明反驳关系已经成立。

建议：把模型判断与摘录核验分成两个字段。对正向、反向和限定关系采用一致的证据展示规则，未经确认的反驳保留待核对标识。

## 三、性能与可维护性优化

| 项目 | 代码证据 | 建议与验收 |
| --- | --- | --- |
| 每次模型请求重新创建 Runnable，每次章节或阶段选择重新构图与编译 | `ai_runtime/gateway.py:17`；`ai_graphs/chapter.py:53`、`:82`；`ai_graphs/agent.py:32`、`:38` | 先测量包装和编译开销；把可复用图结构、执行上下文和数据库连接生命周期分开。不能跨任务缓存捕获旧 task 的闭包 |
| 章节状态虽然只在图中存引用，节点仍反复序列化完整上下文 | `ai_graphs/chapter.py:57`、`:69`、`:80`；`ai_runtime/receipts.py:40` | 引用化大块不变资料，只保存阶段增量；量化长论文的本地序列化、哈希耗时及新增磁盘量 |
| 每次概览读取整个项目全部事件，再在 Python 排序计算分位数 | `ai_runtime/telemetry.py:38`—`:57`；前端 `RuntimeDiagnostics.tsx:54` 每 30 秒刷新 | 增加时间窗口、分页、按代次过滤或可更新聚合；用大量历史事件测量响应时间 |
| 未见新事件表与图检查点的保留期、归档及清理流程 | `023_ai_runtime.sql`；`ai_graphs/chapter.py:79`；`exports.py:573` | 制定保留策略、检查点清理和对象可达性校验。未知请求、业务收据和来源追溯不可随意删除；验证备份恢复后的行为 |

以上是可确认的实现模式及改进空间，本轮没有性能基准，不声称本地包装已经构成主要延迟瓶颈。

## 四、仍待完成或深化的功能

| 功能 | 当前边界 | 应补齐的结果 |
| --- | --- | --- |
| 有据写作与普通章节的共享内容责任契约 | `writing_agent/context.py:93` 使用 contract/evidence；`compile_spec()` 只读 writing_brief/argument_plan。真实隔离 context 探针确认 attach 后没有 task_spec；旧有章节合同与核验仍存在 | 为原章节合同建立等价责任投影和统一覆盖视图，避免发送重复任务书；明确质量开关在不同任务类型的生效范围 |
| LangChain 原生模型契约 | gateway 当前是 RunnableLambda 包装现有传输，未实现 BaseChatModel、bind_tools、with_structured_output | 若业务需要通用 agent 或模型组合，补齐消息、工具 ID、结构化返回、usage 与结束原因契约；保留当前账本和唯一重试边界。薄适配本身不是缺陷 |
| 真正可观察的工作流分支 | chapter 是 understand→draft→review 三个顺序节点；修订及补证主要仍藏在 writing_flow.finish；agent 图只包装一次后继选择 | 将值得独立恢复、观察或限额的补证、核验、补丁、回归拆为显式阶段，避免仅为使用框架增加节点 |
| 图状态与人工审核的统一进度流 | 新图和 gateway 使用 invoke；原 provider/任务 SSE 继续存在 | 统一暴露阶段、工具执行、候选版本、失败及恢复事件；不能把状态心跳当正文首段，也不能把尚未审阅的候选当已采用正文 |
| 跨版本恢复与运行健康检查 | 策略保存 graph_version；当前只接受常量版本，无旧版本执行器注册/迁移规则；overview 能力字段硬编码为 True | 为支持的历史版本提供恢复策略；检查依赖加载、检查点可写性、协议支持与 worker 实际构建，返回真实能力与不支持原因 |
| 可解释且可复现的实验评测 | compare 为只读报表；人工分数恒为 None，未见其专用前端操作；真实语义质量和成本尚无本轮基准 | 建立代表性冻结样本、配对校验、盲评录入、分歧处理、成本与耗时口径；前端可选择任务并解释不能配对的原因。旧有诊断评分能力可复用 |
| 完整质量策略快照 | engine、quality 版本及并发数冻结，但 writing_flow 的开关、修订轮数等仍按项目当前设置读取（`writing_flow.py:29`、`:1044`、`:1564`） | 明确哪些设置允许实时修改，哪些属于任务可复现输入；对后者保存值或指纹，恢复及对照使用同一快照 |

不建议为了追踪而默认上传论文到 LangSmith。当前显式关闭自动追踪、沿用现有预算和人工采用事务是合理边界，优化应继续保留。

## 五、建议实施顺序

1. **先修选材和责任判定**：D01—D04。统一证据类型，消除“有证据还报缺口”和“有分歧仍显示完成”。将复现探针改写成正确行为的回归测试。
2. **再修信息可比性**：D05—D09。补齐契约版本和冻结输入，身份未知、没有候选时不能显示可盲评，区分逻辑请求和实际尝试。
3. **修正统计与来源关系展示**：D10—D12。任务费用、执行分位数、未核对反驳需要与字段含义一致。
4. **再深化框架和功能**：统一有据写作责任视图、策略快照、分支进度和版本恢复；按真实需要补模型原生接口。
5. **最后用基准决定性能改造**：先获得代表性长论文的数据，再决定图编译缓存、状态增量化和事件聚合的投入顺序。

修复验收不以“测试总数增加”作为效果证据。至少应覆盖上述反例、旧任务恢复、未知请求保持、权限撤回、预算占用、人工采用不变，并在另行授权的真实模型任务中评估语义质量和费用。

## 六、复现入口

在项目根目录使用 PowerShell Core，版本必须大于 7.6.0：

```powershell
if ($PSVersionTable.PSVersion -le [version]'7.6.0' -or $PSVersionTable.PSEdition -ne 'Core') { throw 'Requires PowerShell > 7.6.0 Core' }
& '.runtime/python/python.exe' -I -X utf8 runner.py test tests/test_ai_runtime.py
& '.runtime/python/python.exe' -I -X utf8 runner.py test '.runtime/langchain-audit-2026-11-07/test_audit_findings.py' --junitxml='.runtime/langchain-audit-2026-11-07/reproductions.xml'
```

探针与 JUnit 位于忽略目录，不会默认进入版本控制；这份报告位于 docs。后续正式修复时，应把有价值的正确行为回归用例移入 tests，并分别验证函数层与实际工作流集成。
