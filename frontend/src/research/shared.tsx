import { useCommand } from "../tasks/useCommand";
export type { Row } from "../workspace/types";
export const base = (pid: string) => `/projects/${pid}/studio`;
export const textError = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
// Compatibility facade; task observation belongs to the public command layer.
export const useStudioTask = useCommand;
export function TaskStatus({ task }: { task: ReturnType<typeof useCommand> }) {
  return (
    <>
      {task.error && (
        <p className="studio-error" role="alert">
          {task.error}
        </p>
      )}
      {task.busy && (
        <p role="status" className="studio-status">
          {task.phase}{" "}
          {task.taskId && (
            <button className="btn" onClick={() => void task.cancel()}>
              停止后续处理
            </button>
          )}
        </p>
      )}
    </>
  );
}
export const safeHtml = (html: string) =>
  `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'">${html}`;
