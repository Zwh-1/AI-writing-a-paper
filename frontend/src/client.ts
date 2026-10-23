let csrf = "";
export class ApiError extends Error {
  constructor(message: string, public status: number, public detail: any) { super(message); this.name = "ApiError"; }
}
export function setCsrf(value: string) {
  csrf = value;
}
export async function api(path: string, options: RequestInit = {}) {
  const form = options.body instanceof FormData;
  const r = await fetch("/api/v1" + path, {
    ...options,
    headers: {
      ...(form ? {} : { "Content-Type": "application/json" }),
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      ...options.headers,
    },
  });
  let d: any;
  try {
    d = await r.json();
  } catch {
    throw new Error("服务器未返回有效数据");
  }
  if (!r.ok) {
    let x = d.detail ?? d;
    throw new ApiError(typeof x === "string" ? x : (x.message ?? JSON.stringify(x)), r.status, x);
  }
  return d;
}
export function send(path: string, body: unknown = {}, method = "POST") {
  return api(path, { method, body: JSON.stringify(body) });
}
