import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, send } from "../client";
import type { Row } from "../workspace/types";
import { availableBudget } from "./autopilotBudget";

export type AIDomain = "materials" | "rules";
export type Coverage = {
  total: number;
  ai_covered: number;
  automatic_completed: number;
  ai_percent: number;
  automatic_percent: number;
  target_met: boolean;
};
export type AIException = {
  kind: "material" | "rule";
  id: string;
  label: string;
  reason: string;
  state: string;
};
export type AutopilotReport = {
  materials: Coverage;
  rules: Coverage;
  extraction?: Coverage;
  exceptions: AIException[];
  exception_count: number;
  tasks: Row[];
  policy: Row;
  budget: { limit: number; available: number; can_call?: boolean; currency?: string;
    used?: number; unknown_reserved?: number; pending_bill_reserved?: number };
  pricing?: Row;
  model_ready: boolean;
  note: string;
};
export const taskStatusLabels: Record<string, string> = {
  queued: "等待执行",
  running: "正在自动处理",
  succeeded: "本轮处理已完成",
  incomplete: "处理结果已保存",
  budget_blocked: "等待预算核对",
  outcome_unknown: "远端结果待核实",
  failed: "本轮处理遇到问题",
  cancelled: "后续处理已停止",
};
function useController(pid: string, tick: number, onUpdated: () => void) {
  const [data, setData] = useState<AutopilotReport | null>(null),
    [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [pending, setPending] = useState<string | null>(null),
    [budget, setBudget] = useState(0);
  const [message, setMessage] = useState("");
  const [autoPublish, setAutoPublish] = useState(true),
    [followImports, setFollowImports] = useState(true);
  const initialized = useRef(false),
    signature = useRef("");
  const automaticBudget = useRef(true);
  useEffect(() => {
    initialized.current = false;
    automaticBudget.current = true;
    signature.current = "";
    setData(null);
    setError("");
    setLoadError("");
    setMessage("");
    setBudget(0);
  }, [pid]);
  const actionLock = useRef(false);
  const updated = useRef(onUpdated);
  updated.current = onUpdated;
  const request = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    try {
      const value: AutopilotReport = await api(
        `/projects/${pid}/ai-autopilot`,
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setData(value);
      setLoadError("");
      const running = value.tasks.some((task) =>
        ["queued", "running"].includes(task.status),
      );
      const ceiling = availableBudget(value.budget.limit, value.budget.available);
      if (!initialized.current) {
        initialized.current = true;
        setBudget(
          running && value.policy.budget_limit != null
            ? value.policy.budget_limit
            : ceiling,
        );
        setAutoPublish(value.policy.auto_publish ?? true);
        setFollowImports(
          value.policy.budget_limit === undefined ? true : value.policy.enabled,
        );
      } else if (automaticBudget.current && !running) {
        setBudget(ceiling);
      }
      const next = `${value.tasks[0]?.id}:${value.tasks[0]?.status}:${value.materials.automatic_completed}:${value.rules.automatic_completed}`;
      if (signature.current && signature.current !== next) updated.current();
      signature.current = next;
    } catch (failure) {
      if (!controller.signal.aborted) setLoadError((failure as Error).message);
    }
  }, [pid]);
  useEffect(() => {
    void load();
    return () => request.current?.abort();
  }, [load, tick]);
  const active = data?.tasks.find((task) =>
    ["queued", "running"].includes(task.status),
  );
  useEffect(() => {
    let alive = true,
      timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await load();
      if (alive) timer = setTimeout(poll, active ? 4000 : 10000);
    };
    timer = setTimeout(poll, active ? 4000 : 10000);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [active?.id, load]);
  const perform = async (name: string, work: () => Promise<any>) => {
    if (actionLock.current) return;
    actionLock.current = true;
    setPending(name);
    setError("");
    setMessage("");
    try {
      const result = await work();
      await load();
      if (result?.message) setMessage(result.message);
      updated.current();
    } catch (failure) {
      // Another task may have reserved funds since the last GET.
      await load();
      setError((failure as Error).message);
    } finally {
      actionLock.current = false;
      setPending(null);
    }
  };
  const available = availableBudget(data?.budget.limit ?? 0, data?.budget.available ?? 0);
  const canStart =
    !!data?.model_ready &&
    !pending &&
    !active &&
    data.budget.can_call !== false &&
    Number.isFinite(budget) &&
    budget >= 0.01 &&
    budget <= available;
  return {
    data,
    error: error || loadError,
    message,
    pending,
    budget,
    setBudget: (value: number) => {
      automaticBudget.current = false;
      setError("");
      setBudget(value);
    },
    useAvailableBudget: () => {
      automaticBudget.current = true;
      setError("");
      setBudget(available);
    },
    autoPublish,
    setAutoPublish,
    followImports,
    setFollowImports,
    active,
    available,
    canStart,
    load,
    start: () => {
      if (canStart)
        return perform("start", () =>
          send(`/projects/${pid}/ai-autopilot`, {
            budget_limit: budget,
            auto_publish: autoPublish,
            follow_imports: followImports,
          }),
        );
    },
    reconcileBilling: () =>
      perform("billing", async () => {
        const result = await send("/projects/" + pid + "/tasks/billing-reconcile", {});
        const profiles = result.profiles ?? [result];
        if (!profiles.length) return { message: result.message };
        const failed = profiles.filter((item: Row) => !item.reconciled);
        return { message: failed.length
          ? "账单未完全核对，保留未核实预留：" + failed.map((item: Row) => item.reason).join("；")
          : "账单核对完成，可用预算已刷新。" };
      }),
    cancel: () => {
      if (active)
        return perform("cancel", () => send(`/tasks/${active.id}/cancel`, {}));
    },
    stopFollowing: () =>
      perform("follow", async () => {
        await send(`/projects/${pid}/ai-autopilot`, { enabled: false }, "PUT");
        setFollowImports(false);
      }),
  };
}
type Controller = ReturnType<typeof useController>;
const Context = createContext<Controller | null>(null);
export function AIAutopilotProvider({
  pid,
  tick,
  onUpdated,
  children,
}: {
  pid: string;
  tick: number;
  onUpdated: () => void;
  children: ReactNode;
}) {
  const value = useController(pid, tick, onUpdated);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
export function useAIAutopilot() {
  const value = useContext(Context);
  if (!value) throw new Error("AI 托管上下文未就绪");
  return value;
}
export function useOptionalAIAutopilot() {
  return useContext(Context);
}
