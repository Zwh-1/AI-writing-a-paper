import { API_ROUTES } from "./routes.generated";

export const API_BASE = "/api/v2";
const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const mappings = API_ROUTES.map(([legacy, canonical, methods]) => ({
  pattern: new RegExp("^" + legacy.split(/(\{[^}]+\})/).map(part => part.startsWith("{") ? "([^/]+)" : escape(part)).join("") + "$"),
  canonical,
  methods,
}));

/** One URL builder for JSON, images, file downloads and EventSource. */
export function apiUrl(input: string, method = "GET"): string {
  let path = input.replace(/^\/api\/v[12](?=\/|$)/, "");
  if (!path.startsWith("/") || path.startsWith("//")) throw new Error("接口路径必须是本机相对路径");
  const offset = path.search(/[?#]/);
  const query = offset < 0 ? "" : path.slice(offset);
  path = offset < 0 ? path : path.slice(0, offset);
  for (const mapping of mappings) {
    if (!mapping.methods.includes(method.toUpperCase())) continue;
    const match = mapping.pattern.exec(path);
    if (!match) continue;
    let index = 0;
    return API_BASE + mapping.canonical.replace(/\{[^}]+\}/g, () => match[++index]) + query;
  }
  return API_BASE + path + query;
}

export const pathSegment = (id: string) => encodeURIComponent(id);
