import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../client";

export function useWorkspaceResource<T>(path: string, tick: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const request = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    request.current?.abort();
    if (!path) { setData(null); setError(""); setLoading(false); return; }
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    try {
      const value = await api(path, { signal: controller.signal });
      if (!controller.signal.aborted) {
        setData(value);
        setError("");
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError((failure as Error).message);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    setData(null);
    setError("");
  }, [path]);
  useEffect(() => {
    void load();
    return () => request.current?.abort();
  }, [load, tick]);
  return { data, error, loading, load };
}
