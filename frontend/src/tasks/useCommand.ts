import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelTask,
  forget,
  isTask,
  observeTask,
  remember,
  remembered,
  subscribeTask,
  taskResult,
  type TaskHandle,
} from "./taskRepository";

const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
export function useCommand(onDone?: () => void | Promise<void>, scope?: string) {
  const [busy, setBusy] = useState(false),
    [phase, setPhase] = useState(""),
    [error, setError] = useState(""),
    [taskId, setTaskId] = useState("");
  const mounted = useRef(true),
    running = useRef(false),
    done = useRef(onDone);
  done.current = onDone;
  const wait = useCallback(
    async (id: string) => {
      setTaskId(id);
      if (scope) remember(scope, id);
      const remove = subscribeTask(id, (task) => {
        if (mounted.current) setPhase(task.observation_error || task.phase || "任务处理中");
      });
      let task: TaskHandle;
      try {
        task = await observeTask(id);
      } finally {
        remove();
      }
      if (scope) forget(scope, id);
      if (task.status === "incomplete") {
        if (mounted.current)
          setError(task.error || "部分结果已保存，请在任务中心查看未完成项");
        return taskResult(task);
      }
      if (task.status !== "succeeded")
        throw new Error(
          task.error ||
            (
              {
                cancelled: "任务已取消",
                budget_blocked: "预算不足，请在任务中心处理",
                outcome_unknown: "远端结果待确认，请在任务中心核对",
              } as Record<string, string>
            )[task.status] ||
            "任务未完成，请在任务中心查看",
        );
      return taskResult(task);
    },
    [scope],
  );
  const run = useCallback(
    async (action: () => Promise<any>) => {
      if (running.current) return null;
      running.current = true;
      setBusy(true);
      setError("");
      setPhase("正在处理…");
      try {
        let value = await action();
        if (isTask(value)) value = await wait(value.id);
        else if (value?.type === "task" && typeof value.task_id === "string")
          value = await wait(value.task_id);
        else if (value?.type === "completed") value = value.data;
        if (mounted.current) {
          setPhase("已完成");
          await done.current?.();
        }
        return value;
      } catch (failure) {
        if (mounted.current) setError(errorText(failure));
        return null;
      } finally {
        running.current = false;
        if (mounted.current) {
          setBusy(false);
          setTaskId("");
        }
      }
    },
    [wait],
  );
  useEffect(() => {
    mounted.current = true;
    const id = scope && remembered(scope);
    if (id) void run(() => Promise.resolve({ type: "task", task_id: id }));
    return () => {
      mounted.current = false;
    };
  }, [scope, run]);
  return {
    busy,
    phase,
    error,
    run,
    taskId,
    cancel: () => taskId && cancelTask(taskId),
  };
}
export function useTask(taskId: string) {
  const [task, setTask] = useState<TaskHandle | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    setTask(null);
    setError("");
    if (!taskId) return;
    let active = true;
    const remove = subscribeTask(taskId, (value) => {
      if (active) setTask(value);
    });
    void observeTask(taskId).catch((failure) => {
      if (active) setError(errorText(failure));
    });
    return () => {
      active = false;
      remove();
    };
  }, [taskId]);
  return { task, error, cancel: () => cancelTask(taskId) };
}
