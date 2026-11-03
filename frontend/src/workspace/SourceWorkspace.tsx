import {
  GitBranch,
  X,
} from "lucide-react";
import { useWorkspaceResource } from "./useWorkspaceResource";
import { navigateSources, type SourcePage } from "./sourceNavigation";
import type { Row } from "./types";
import type { ReactNode } from "react";
import { createContext, useContext, useEffect, useState } from "react";
import WorkspaceDialog from "./WorkspaceDialog";
import ExpenseLedger from "../ExpenseLedger";
import WorkspaceNavigation from "./WorkspaceNavigation";
import { workspaceForPage } from "./projectNavigation";
import "./sourceWorkspace.css";

const sourceWorkspace = workspaceForPage("materials")!;
const sourceCounts: Record<string, { key: string; unit: string }> = {
  materials: { key: "active_files", unit: "份原件" },
  rules: { key: "eligible_rules", unit: "条可纳入" },
  references: { key: "references", unit: "篇书目" },
};

type SourceResource = ReturnType<typeof useWorkspaceResource<Row>>;
const SourceData = createContext<SourceResource | null>(null);
export function SourceWorkspaceData({
  pid,
  tick,
  children,
}: {
  pid: string;
  tick: number;
  children: ReactNode;
}) {
  const resource = useWorkspaceResource<Row>(
    `/projects/${pid}/source-workspace`,
    tick,
  );
  const ready = resource.data?.project_id === pid;
  return (
    <SourceData.Provider
      value={{
        ...resource,
        data: ready ? resource.data : null,
        loading: resource.loading || (!ready && !resource.error),
      }}
    >
      {children}
    </SourceData.Provider>
  );
}
function useSources(pid: string, tick: number) {
  const shared = useContext(SourceData);
  const local = useWorkspaceResource<Row>(
    shared ? "" : `/projects/${pid}/source-workspace`,
    tick,
  );
  return shared || local;
}
export function useSourceBillingGate() {
  const shared = useContext(SourceData);
  return {
    blocked: !!shared && (!shared.data || !!shared.data.billing?.paid_blocked),
    reason:
      shared?.data?.billing?.reason ||
      (shared && !shared.data ? "正在检查费用依据…" : ""),
  };
}
export function useSourceMetadata() {
  return useContext(SourceData)?.data;
}
export function useSourceWorkspaceResource() {
  return useContext(SourceData);
}

export function SourceWorkspaceNavigation({
  pid,
  tick,
  current,
}: {
  pid: string;
  tick: number;
  current: SourcePage;
}) {
  const resource = useSources(pid, tick);
  const [ledger, setLedger] = useState(false);
  const billing = resource.data?.billing;
  const blocked = !!billing?.paid_blocked;
  return (
    <WorkspaceNavigation
      className="source-workspace-nav"
      label="资料规则文献联动"
      current={current}
      introduction={
        <>
          <GitBranch size={14} /> {sourceWorkspace.description}
        </>
      }
      note={sourceWorkspace.note}
      destinations={sourceWorkspace.pages.map((destination) => ({
        ...destination,
        metadata: (
          <>
            {resource.data ? resource.data.counts[sourceCounts[destination.id].key] : "—"}
            <small>{sourceCounts[destination.id].unit}</small>
          </>
        ),
      }))}
      onNavigate={(page) =>
        navigateSources(pid, page as SourcePage, {
          view: page === "rules" ? "catalog" : "library",
        })
      }
    >
      {blocked && (
        <div className="source-billing-note">
          <p>
            <span>本地整理与核对可用</span> · {billing?.reason}
            。收费任务暂停，原件与已有结果保留。
          </p>
          <div className="actions">
            <button
              className="btn"
              aria-haspopup="dialog"
              onClick={() => setLedger(true)}
            >
              查看待补依据
            </button>
          </div>
        </div>
      )}
      {ledger && (
        <WorkspaceDialog
          title="费用依据账本 · 待补依据"
          wide
          onClose={() => setLedger(false)}
        >
          <p className="muted mb">
            账本按调用逐笔列出缺口。补齐服务商凭证或计价依据后，收费任务才会解除暂停；本地整理、核对与已有结果不受影响。
          </p>
          <ExpenseLedger
            pid={pid}
            tick={tick}
            onChanged={() => void resource.load()}
          />
        </WorkspaceDialog>
      )}
      {resource.error && (
        <p role="alert">
          关联状态暂不可用：{resource.error}
          <button className="text-button" onClick={() => void resource.load()}>
            重新读取
          </button>
        </p>
      )}
    </WorkspaceNavigation>
  );
}

export function SourceFocus({
  pid,
  fileId,
  tick,
  onClear,
  children,
}: {
  pid: string;
  fileId?: string;
  tick: number;
  onClear: () => void;
  children?: ReactNode;
}) {
  const resource = useSources(pid, tick);
  if (!fileId) return null;
  const file = resource.data?.files.find((r: Row) => r.id === fileId);
  return (
    <div className="source-focus" role="status">
      <div>
        <small>当前来源范围</small>
        <strong>
          {file?.name ||
            (resource.loading ? "正在读取原件…" : "原件不在当前项目中")}
        </strong>
        <span>所有关联入口使用同一原件编号</span>
      </div>
      <div className="actions">
        {children}
        <button className="btn" onClick={onClear}>
          <X size={13} />
          查看全部来源
        </button>
      </div>
    </div>
  );
}

export function SourceFlowPanel({
  pid,
  tick,
  scope = "all",
  onOpen,
  onChoose,
  fileId,
}: {
  pid: string;
  tick: number;
  scope?: string;
  onOpen: (id: string, page?: number) => void;
  onChoose?: (fileId: string) => void;
  fileId?: string;
}) {
  const resource = useSources(pid, tick);
  const [query, setQuery] = useState(""),
    [page, setPage] = useState(0);
  useEffect(() => setPage(0), [pid, scope, fileId, query]);
  const files = (resource.data?.files || []).filter(
    (f: Row) =>
      f.active &&
      (!fileId || f.id === fileId) &&
      (!query.trim() ||
        f.name
          .toLocaleLowerCase()
          .includes(query.trim().toLocaleLowerCase())) &&
      (scope === "all" ||
        (scope === "rules" &&
          (f.role === "rules" || f.rules.total || f.guidance_eligible)) ||
        (scope === "research" && (f.research_eligible || f.references.length))),
  );
  const current = Math.min(page, Math.max(0, Math.ceil(files.length / 10) - 1));
  return (
    <section className="panel source-flow-panel" aria-label="原件与下游关联">
      <div className="section-heading">
        <div>
          <h2>原件与使用流向</h2>
          <p>调整用途、处理规则或阅读文献，始终回到同一份原件。</p>
        </div>
        <span>{resource.data ? files.length : "—"} 份</span>
      </div>
      {resource.error && (
        <p role="alert">
          {resource.error}
          <button className="btn" onClick={() => void resource.load()}>
            重试
          </button>
        </p>
      )}
      {resource.loading && !resource.data && (
        <p role="status">正在读取原件关联…</p>
      )}
      <label className="source-flow-search">
        查找来源关联
        <input
          aria-label="查找来源关联"
          placeholder="输入原件名称"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      <div className="source-flow-list">
        {files.slice(current * 10, (current + 1) * 10).map((f: Row) => (
          <article key={f.id} data-source-flow={f.id}>
            <div className="source-flow-title">
              <strong>{f.name}</strong>
              <span>
                {f.pages} 页 / 片段 ·{" "}
                {f.egress === "local_only" ? "仅限本地" : "允许按任务读取"} ·{" "}
                {f.active ? "在用原件" : "已归档"}
              </span>
            </div>
            <dl>
              <div>
                <dt>规则记录</dt>
                <dd>
                  {f.rules.total || 0}
                  <small>待判断 {f.rules.pending || 0}</small>
                </dd>
              </div>
              <div>
                <dt>书目关联</dt>
                <dd>
                  {f.references.length}
                  <small>
                    {f.research_eligible ? "研究用途" : "保留原用途"}
                  </small>
                </dd>
              </div>
              <div>
                <dt>知识与阅读</dt>
                <dd>
                  {f.knowledge_units}
                  <small>{f.readings} 个阅读任务</small>
                </dd>
              </div>
            </dl>
            <div className="actions">
              <button className="btn" onClick={() => onOpen(f.id)}>
                查看原件
              </button>
              {onChoose && (
                <button className="btn" onClick={() => onChoose(f.id)}>
                  查看该来源规则
                </button>
              )}
              {(f.role === "rules" || f.rules.total > 0) && !onChoose && (
                <button
                  className="btn"
                  onClick={() =>
                    navigateSources(pid, "rules", {
                      view: "catalog",
                      fileId: f.id,
                    })
                  }
                >
                  关联规则
                </button>
              )}
              {f.research_eligible && (
                <button
                  className="btn"
                  onClick={() =>
                    navigateSources(pid, "references", {
                      view: "reading",
                      fileId: f.id,
                    })
                  }
                >
                  进入文献阅读
                </button>
              )}
              {f.guidance_eligible && (
                <button
                  className="btn"
                  onClick={() =>
                    navigateSources(pid, "rules", {
                      view: "knowledge",
                      fileId: f.id,
                      namespace: "guidance",
                    })
                  }
                >
                  查看指导知识
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
      {!resource.loading && !resource.error && !files.length && (
        <p className="muted">
          当前范围没有关联原件。可在资料中心添加资料或调整用途。
        </p>
      )}
      {files.length > 10 && (
        <div className="workspace-pagination">
          <span>
            共 {files.length} 份 · 第 {current + 1}/
            {Math.ceil(files.length / 10)} 页
          </span>
          <div className="actions">
            <button
              className="btn"
              disabled={!current}
              onClick={() => setPage(current - 1)}
            >
              上一页来源
            </button>
            <button
              className="btn"
              disabled={(current + 1) * 10 >= files.length}
              onClick={() => setPage(current + 1)}
            >
              下一页来源
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
