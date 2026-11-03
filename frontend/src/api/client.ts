import { apiUrl } from "./paths";
import type { ApiEnvelope } from "./types";

let csrf = "";
export { apiUrl, API_BASE } from "./paths";
export class ApiError extends Error {
  constructor(message: string, public status: number, public detail: any, public requestId?: string) {
    super(message); this.name = "ApiError";
  }
  get code(): string | undefined { return this.detail?.code; }
  get unknownOutcome(): boolean { return !!this.detail?.unknown_outcome; }
}
export const setCsrf = (value: string) => { csrf = value; };
export type ApiOptions = RequestInit & { timeout?: number };

/** Complete contract for callers that need collection/task metadata. No automatic retries. */
export async function request<T = unknown>(path: string, options: ApiOptions = {}): Promise<ApiEnvelope<T>> {
  const { timeout, ...rest } = options;
  const headers = new Headers(rest.headers);
  if (!(rest.body instanceof FormData) && rest.body != null && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (csrf && !headers.has("X-CSRF-Token")) headers.set("X-CSRF-Token", csrf);
  if (!headers.has("X-Request-Id")) headers.set("X-Request-Id", "req_" + crypto.randomUUID());
  const controller = timeout ? new AbortController() : null;
  const abort = () => controller?.abort(rest.signal?.reason);
  if (rest.signal?.aborted) abort();
  else rest.signal?.addEventListener("abort", abort, { once: true });
  let timedOut = false;
  const timer = controller ? setTimeout(() => { timedOut = true; controller.abort(); }, timeout) : null;
  try {
    const response = await fetch(apiUrl(path, rest.method ?? "GET"), { ...rest, headers, signal: controller?.signal ?? rest.signal, credentials: "same-origin" });
    const rid = response.headers.get("X-Request-Id") ?? undefined;
    if (response.status === 204) return { data: undefined as T, meta: { api_version: "v2", request_id: rid ?? "", service: "workbench", generated_at: "" } };
    let value: any;
    try { value = await response.json(); }
    catch (error) {
      if (controller?.signal.aborted || rest.signal?.aborted) throw error;
      throw new ApiError("服务器未返回有效数据", response.status, null, rid);
    }
    if (!response.ok) {
      const error = value.error ?? value.detail ?? value;
      throw new ApiError(typeof error === "string" ? error : error?.message ?? JSON.stringify(error), response.status, error, rid);
    }
    // Accepts legacy/mock replies during the migration period as well.
    return value?.meta?.api_version === "v2" && "data" in value ? value : { data: value, meta: { api_version: "v2", request_id: rid ?? "", service: "workbench", generated_at: "" } };
  } catch (error) {
    if (timedOut) throw new ApiError("请求超时，执行结果请在任务中心核对", 0, { code: "CLIENT_TIMEOUT", unknown_outcome: !["GET", "HEAD"].includes(rest.method ?? "GET") });
    throw error;
  } finally {
    if (timer) clearTimeout(timer);
    rest.signal?.removeEventListener("abort", abort);
  }
}

/** Existing components retain their original data shape. */
export async function api(path: string, options: ApiOptions = {}) {
  const envelope = await request<any>(path, options);
  if (envelope.meta.kind === "collection") return envelope.data.items;
  if (envelope.meta.kind === "task") return { ...envelope.data.task, resource_type: "task" };
  return envelope.data;
}
export function send(path: string, body: unknown = {}, method = "POST") {
  return api(path, { method, body: JSON.stringify(body) }).then(value=>{
    const pid=path.match(/\/projects\/([^/]+)/)?.[1];
    window.dispatchEvent(new CustomEvent('paper-resource-changed',{detail:{project_id:pid,path}}));
    return value;
  });
}
