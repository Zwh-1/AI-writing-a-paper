export interface ApiMeta {
  request_id: string;
  api_version: "v2";
  service: string;
  generated_at: string;
  kind?: "collection" | "task";
}
export interface ApiFailure {
  code: string;
  message: string;
  details: unknown;
  retriable: boolean;
  unknown_outcome: boolean;
}
export interface ApiEnvelope<T> {
  data: T;
  meta: ApiMeta;
  error?: ApiFailure;
}
export interface Collection<T> {
  items: T[];
  next_cursor: string | null;
  total: number;
}
