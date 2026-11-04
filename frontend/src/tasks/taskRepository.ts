import { api, send } from "../api/client";

export type TaskHandle = {
  resource_type: "task";
  id: string;
  project_id: string;
  kind: string;
  status: string;
  progress: number;
  phase?: string;
  observation_error?: string;
  error?: string;
  result?: unknown;
};
export type CommandResult<T> =
  { type: "completed"; data: T } | { type: "task"; task_id: string };
const states = new Set([
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "incomplete",
  "budget_blocked",
  "outcome_unknown",
]);
export function isTask(value: unknown): value is TaskHandle {
  if (!value || typeof value !== "object") return false;
  const row = value as TaskHandle;
  return (
    typeof row.id === "string" &&
    typeof row.status === "string" &&
    (row.resource_type === "task" ||
      (states.has(row.status) &&
        typeof row.progress === "number" &&
        typeof row.kind === "string"))
  );
}
export const taskResult = (task: TaskHandle) =>
  typeof task.result === "string" ? JSON.parse(task.result) : task.result;
type Listener = (value: TaskHandle) => void;
const listeners = new Map<string, Set<Listener>>(),
  current = new Map<string, TaskHandle>(),
  observing = new Map<string, Promise<TaskHandle>>();
const journalKey = "paper-task-observers-v1";
function journal(): Record<string, string> {
  try {
    return JSON.parse(sessionStorage.getItem(journalKey) || "{}");
  } catch {
    return {};
  }
}
export function remember(scope: string, id: string) {
  const values = journal();
  values[scope] = id;
  sessionStorage.setItem(journalKey, JSON.stringify(values));
}
export const remembered = (scope: string) => journal()[scope];
export function forget(scope: string, id: string) {
  const values = journal();
  if (values[scope] === id) {
    delete values[scope];
    sessionStorage.setItem(journalKey, JSON.stringify(values));
  }
}
export function subscribeTask(id: string, fn: Listener) {
  const values = listeners.get(id) || new Set<Listener>();
  listeners.set(id, values);
  values.add(fn);
  if (current.has(id)) fn(current.get(id)!);
  return () => {
    values.delete(fn);
    if (!values.size) listeners.delete(id);
  };
}
export function observeTask(id: string): Promise<TaskHandle> {
  const old = observing.get(id);
  if (old) return old;
  const work = (async () => {
    let failures = 0;
    for (;;) {
      let value: TaskHandle;
      try {
        value = (await api("/tasks/" + encodeURIComponent(id), { timeout: 10000 })) as TaskHandle;
        failures = 0;
      } catch (failure) {
        const status = (failure as { status?: number }).status;
        if (status === 401 || status === 403 || status === 404) throw failure;
        failures += 1;
        const saved = current.get(id);
        if (saved) {
          const observed = { ...saved, observation_error: "进度连接暂时中断，正在重连；任务仍可能继续执行。" };
          listeners.get(id)?.forEach(fn => fn(observed));
        }
        // Retry this read only. Never replay the command that created a task.
        await new Promise(resolve => setTimeout(resolve, Math.min(10000, 900 * 2 ** Math.min(failures, 4))));
        continue;
      }
      if (!isTask(value)) throw new Error("任务接口返回了无效任务");
      current.set(id, value);
      listeners.get(id)?.forEach((fn) => fn(value));
      if (!["queued", "running"].includes(value.status)) {
        window.dispatchEvent(
          new CustomEvent("paper-resource-changed", {
            detail: {
              project_id: value.project_id,
              resources: [
                "tasks",
                "references",
                "files",
                "artifacts",
                "sections",
                "reading",
                "research",
              ],
            },
          }),
        );
        return value;
      }
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
  })();
  observing.set(id, work);
  void work
    .finally(() => {
      observing.delete(id);
      current.delete(id);
    })
    .catch(() => {});
  return work;
}
export const cancelTask = (id: string) =>
  send("/tasks/" + encodeURIComponent(id) + "/cancel");
