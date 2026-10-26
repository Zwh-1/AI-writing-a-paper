import { useEffect, useMemo, useRef, useState } from "react";
import RuleListRow from "./rules/RuleListRow";
import {
  ChevronLeft,
  ChevronRight,
  CheckCheck,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import type { RuleConfirmationResult } from "./ruleBatchConfirmation";
import { checkerLabels } from "./RuleEditor";
import { matchesRuleFilter } from "./rules/ruleRecordSummary";

type Row = Record<string, any>;
type Props = {
  rules: Row[];
  loaded: boolean;
  filter: string;
  onFilter: (filter: string) => void;
  busy: boolean;
  sourceBusy: string;
  onEdit: (rule: Row) => void;
  onSource: (rule: Row) => void;
  onAutomation?: () => void;
  onBatch: (ids: string[], state: string) => Promise<any>;
  run: (key: string, fn: () => Promise<any>, message: string) => Promise<any>;
};
const states = [
  ["all", "全部记录"],
  ["publishable", "可纳入规则包"],
  ["candidate", "待确认 / 分类"],
  ["confirmed", "已确认"],
  ["machine_verified", "机器审核通过"],
  ["reference_only", "仅供参考"],
  ["rejected", "已归并 / 排除"],
];
const filterDescriptions: Record<string, string> = {
  all: "包含可纳入的要求、参考内容、归并或排除记录及待核验条目。",
  publishable:
    "仅含人工确认或机器审核通过的强制要求与建议；发布前仍需通过来源与冲突检查。",
  candidate: "尚待核验，或已确认但用途仍待判断的记录；暂不纳入规则包。",
  confirmed:
    "人工确认是审核状态；只有性质为强制要求或建议的记录才会进入规则包。",
  machine_verified:
    "机器审核通过是审核状态；是否纳入规则包还取决于要求性质与发布检查。",
  reference_only:
    "示例、反例、知识说明及带适用条件的参考内容，保留供查阅，不作为当前写作要求。",
  rejected: "已归并的重复条款及已排除记录保留在此，原文与出处仍可追溯。",
};

export default function RuleCatalog({
  rules,
  loaded,
  filter,
  onFilter,
  busy,
  sourceBusy,
  onEdit,
  onSource,
  onAutomation,
  onBatch,
  run,
}: Props) {
  const [query, setQuery] = useState("");
  const [file, setFile] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [batching, setBatching] = useState<string | null>(null);
  const [confirmation, setConfirmation] =
    useState<RuleConfirmationResult | null>(null);
  const batchBusy = busy || batching !== null;
  const list = useRef<HTMLDivElement>(null);
  const selectPage = useRef<HTMLInputElement>(null);
  const current = useMemo(
    () =>
      rules.filter(
        (rule) =>
          matchesRuleFilter(rule, filter) &&
          (!file ||
            (file === "manual"
              ? !rule.source_id
              : rule.source_file_id === file)) &&
          (!query.trim() ||
            [
              rule.statement,
              rule.source_file,
              rule.decision_note,
              checkerLabels[rule.checker],
            ]
              .join(" ")
              .toLowerCase()
              .includes(query.trim().toLowerCase())),
      ),
    [rules, filter, file, query],
  );
  const pages = Math.max(1, Math.ceil(current.length / pageSize));
  const currentPage = Math.min(page, pages);
  const start = (currentPage - 1) * pageSize;
  const visible = current.slice(start, start + pageSize);
  const visibleIds = visible.map((rule) => rule.id);
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const currentIds = useMemo(
    () => new Set(current.map((rule) => rule.id)),
    [current],
  );
  const chosen = selected.filter((id) => currentIds.has(id));
  const pageSelected = visibleIds.filter((id) => selectedSet.has(id)).length;
  const chosenRules = current.filter((rule) => selectedSet.has(rule.id));
  const unresolvedModality = chosenRules.filter(
    (rule) => !["mandatory", "recommended"].includes(rule.modality),
  ).length;
  useEffect(() => {
    setPage(1);
    setSelected([]);
    setConfirmation(null);
  }, [query, file, filter, pageSize]);
  useEffect(() => {
    if (selectPage.current)
      selectPage.current.indeterminate =
        pageSelected > 0 && pageSelected < visible.length;
  }, [pageSelected, visible.length]);
  useEffect(() => {
    list.current?.scrollTo({ top: 0 });
  }, [currentPage, filter, file, query]);
  const files = [
    ...new Map(
      rules
        .filter((rule) => rule.source_file_id)
        .map((rule) => [rule.source_file_id, rule.source_file]),
    ).entries(),
  ];
  const clear = () => {
    setQuery("");
    setFile("");
    onFilter("all");
  };
  const batch = async (state: string) => {
    if (batchBusy || chosen.length === 0) return;
    setBatching(state);
    setConfirmation(null);
    try {
      const result = await onBatch([...chosen], state);
      if (!result) return;
      if (state === "confirmed") {
        setConfirmation(result);
        setSelected((ids) =>
          ids.filter((id) => !result.confirmed_ids.includes(id)),
        );
      } else setSelected([]);
    } finally {
      setBatching(null);
    }
  };

  return (
    <section
      className="rules-catalog panel no-padding"
      aria-label="规则条款管理"
    >
      <div className="rules-catalog-toolbar">
        <div>
          <h2>
            提取记录 <span>{rules.length}</span>
          </h2>
          <p>按状态或出处筛选，点击条款查看原文与执行方式。</p>
        </div>
        <div className="rule-filters">
          <label className="rule-search">
            <Search size={16} />
            <input
              aria-label="搜索规则"
              placeholder="搜索条款、出处或依据"
              value={query}
              disabled={batchBusy}
              onChange={(event) => setQuery(event.target.value)}
            />
            {query && (
              <button
                type="button"
                aria-label="清除规则搜索"
                disabled={batchBusy}
                onClick={() => setQuery("")}
              >
                <X size={14} />
              </button>
            )}
          </label>
          <select
            aria-label="按规则出处筛选"
            value={file}
            disabled={batchBusy}
            onChange={(event) => setFile(event.target.value)}
          >
            <option value="">全部出处</option>
            <option value="manual">用户新增规则</option>
            {files.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div
        className="tabs rule-state-tabs"
        role="tablist"
        aria-label="规则状态"
      >
        {states.map(([key, label]) => (
          <button
            role="tab"
            aria-selected={filter === key}
            key={key}
            className={filter === key ? "active" : ""}
            disabled={batchBusy}
            onClick={() => onFilter(key)}
          >
            {label}
            <small>
              {key === "all"
                ? rules.length
                : rules.filter((rule) => matchesRuleFilter(rule, key)).length}
            </small>
          </button>
        ))}
      </div>
      <p className="rules-filter-description">{filterDescriptions[filter]}</p>
      <div className="rule-batch-bar">
        <label className="checkbox">
          <input
            ref={selectPage}
            type="checkbox"
            aria-label="选择本页规则"
            checked={visible.length > 0 && pageSelected === visible.length}
            disabled={!visible.length || batchBusy}
            onChange={(event) =>
              setSelected((ids) =>
                event.target.checked
                  ? [...new Set([...ids, ...visibleIds])]
                  : ids.filter((id) => !visibleIds.includes(id)),
              )
            }
          />
          选择本页
        </label>
        <span aria-live="polite">
          {chosen.length
            ? `已选择 ${chosen.length} 条`
            : `筛选结果 ${current.length} 条`}
        </span>
        {chosen.length > 0 && (
          <div className="actions">
            {chosenRules.some((rule) => rule.state !== "confirmed") && (
              <button
                className="btn primary"
                disabled={batchBusy}
                onClick={() => batch("confirmed")}
              >
                <CheckCheck size={14} />
                {batching === "confirmed" ? "正在确认…" : "确认所选"}
              </button>
            )}
            <button
              className="btn"
              disabled={batchBusy}
              onClick={() => batch("rejected")}
            >
              排除所选
            </button>
            {chosenRules.some((rule) => rule.state !== "candidate") && (
              <button
                className="btn"
                disabled={batchBusy}
                onClick={() => batch("candidate")}
              >
                恢复待确认
              </button>
            )}
            <button
              className="text-button"
              disabled={batchBusy}
              onClick={() => setSelected([])}
            >
              取消选择
            </button>
          </div>
        )}
        {(query || file || filter !== "all") && (
          <button
            className="text-button rules-clear-filters"
            disabled={batchBusy}
            onClick={clear}
          >
            清除筛选
          </button>
        )}
      </div>
      {chosen.length > 0 && unresolvedModality > 0 && (
        <p className="rules-batch-hint">
          所选中有 {unresolvedModality}{" "}
          条仍为待判断或示例；这些条目确认后仍需核对性质，才会进入规则包。
        </p>
      )}
      {confirmation && (
        <div
          className={`rules-batch-feedback ${confirmation.failed_ids.length ? "has-failures" : ""}`}
          role={confirmation.failed_ids.length ? "alert" : "status"}
        >
          <p>
            已确认 {confirmation.updated} 条
            {confirmation.failed_ids.length
              ? `；${confirmation.failed_ids.length} 条未能确认，已保留勾选。`
              : "，可在“已确认”中查看；发布后用于写作。"}
          </p>
          {rules.some(
            (rule) =>
              confirmation.confirmed_ids.includes(rule.id) &&
              !["mandatory", "recommended"].includes(rule.modality),
          ) && (
            <p>
              已确认的条目中仍有待判断或示例，它们不会进入规则包；请在“核对与编辑”中核对性质。
            </p>
          )}
          {confirmation.failures.length > 0 && (
            <ul>
              {confirmation.failures.map((failure) => (
                <li key={failure.id}>
                  {rules.find((rule) => rule.id === failure.id)?.statement ??
                    failure.id}
                  ：{failure.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div
        className="rule-list rules-catalog-list"
        ref={list}
        aria-label="当前页规则"
      >
        {visible.map((rule) => (
          <RuleListRow
            key={rule.id}
            rule={rule}
            selected={selectedSet.has(rule.id)}
            busy={batchBusy}
            sourceBusy={sourceBusy}
            onSelect={(checked) =>
              setSelected((ids) =>
                checked
                  ? [...new Set([...ids, rule.id])]
                  : ids.filter((id) => id !== rule.id),
              )
            }
            onEdit={onEdit}
            onSource={onSource}
            onAutomation={onAutomation}
            run={run}
          />
        ))}
        {!current.length && loaded && (
          <div className="empty">
            <ShieldCheck size={30} />
            <h3>{rules.length ? "没有匹配的规则" : "从已有要求开始整理"}</h3>
            <p>
              {rules.length
                ? "调整筛选条件，或搜索其他关键词。"
                : "导入规则材料后整理候选，也可以手工录入导师提出的要求。"}
            </p>
            {rules.length > 0 && (
              <button className="btn" onClick={clear}>
                清除筛选
              </button>
            )}
          </div>
        )}
      </div>
      <div className="rules-pagination" aria-label="规则分页">
        <span aria-live="polite">
          {current.length
            ? `${start + 1}–${Math.min(start + pageSize, current.length)} / ${current.length} 条`
            : "0 条规则"}
        </span>
        <label>
          <span>每页</span>
          <select
            aria-label="每页规则条数"
            value={pageSize}
            disabled={batchBusy}
            onChange={(event) => setPageSize(Number(event.target.value))}
          >
            {[10, 20, 50].map((size) => (
              <option key={size} value={size}>
                {size} 条
              </option>
            ))}
          </select>
        </label>
        <div className="rules-page-controls">
          <button
            className="btn"
            aria-label="上一页规则"
            disabled={currentPage === 1 || batchBusy}
            onClick={() => setPage(currentPage - 1)}
          >
            <ChevronLeft size={15} />
          </button>
          <span>
            第 {currentPage} / {pages} 页
          </span>
          <button
            className="btn"
            aria-label="下一页规则"
            disabled={currentPage === pages || batchBusy}
            onClick={() => setPage(currentPage + 1)}
          >
            <ChevronRight size={15} />
          </button>
        </div>
      </div>
    </section>
  );
}
