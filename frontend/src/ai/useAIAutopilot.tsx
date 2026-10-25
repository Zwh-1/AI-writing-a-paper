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
  budget: { limit: number; available: number; can_call?: boolean };
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
  const [pending, setPending] = useState<string | null>(null),
    [budget, setBudget] = useState(0);
  const [autoPublish, setAutoPublish] = useState(true),
    [followImports, setFollowImports] = useState(true);
  const initialized = useRef(false),
    signature = useRef("");
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
      setError("");
      if (!initialized.current) {
        initialized.current = true;
        const running = value.tasks.some((task) =>
          ["queued", "running"].includes(task.status),
        );
        setBudget(
          running && value.policy.budget_limit != null
            ? value.policy.budget_limit
            : Math.max(0, Math.floor(value.budget.available * 100) / 100),
        );
        setAutoPublish(value.policy.auto_publish ?? true);
        setFollowImports(
          value.policy.budget_limit === undefined ? true : value.policy.enabled,
        );
      }
      const next = `${value.tasks[0]?.id}:${value.tasks[0]?.status}:${value.materials.automatic_completed}:${value.rules.automatic_completed}`;
      if (signature.current && signature.current !== next) updated.current();
      signature.current = next;
    } catch (failure) {
      if (!controller.signal.aborted) setError((failure as Error).message);
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
    if (!active) return;
    let alive = true,
      timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await load();
      if (alive) timer = setTimeout(poll, 4000);
    };
    timer = setTimeout(poll, 4000);
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
    try {
      await work();
      await load();
      updated.current();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      actionLock.current = false;
      setPending(null);
    }
  };
  const available = Math.max(
    0,
    Math.min(data?.budget.limit ?? 0, data?.budget.available ?? 0),
  );
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
    error,
    pending,
    budget,
    setBudget,
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
