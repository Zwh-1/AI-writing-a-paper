import { useCallback, useEffect, useState } from "react";

export type SourcePage = "materials" | "rules" | "references";
export type SourceLocation = {
  page: SourcePage;
  view?: string;
  fileId?: string;
  pageNumber?: number;
  ruleId?: string;
  sourceFilter?: string;
  namespace?: string;
  query?: string;
  revision: number;
};
export const SOURCE_NAVIGATION = "paper-source-workspace-navigation";
const key = (pid: string, page: SourcePage) =>
  `paper-source-location:${pid}:${page}`;
export function sourceLocation(
  pid: string,
  page: SourcePage,
  view = "",
): SourceLocation {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key(pid, page)) || "null");
    if (saved?.page === page) return saved;
  } catch {}
  return { page, view, revision: 0 };
}
export function navigateSources(
  pid: string,
  page: SourcePage,
  location: Partial<SourceLocation> = {},
) {
  const value = { page, ...location, revision: Date.now() } as SourceLocation;
  sessionStorage.setItem(key(pid, page), JSON.stringify(value));
  window.dispatchEvent(
    new CustomEvent(SOURCE_NAVIGATION, { detail: { pid, location: value } }),
  );
}
export function useSourceLocation(
  pid: string,
  page: SourcePage,
  defaultView: string,
) {
  const [location, set] = useState(() =>
    sourceLocation(pid, page, defaultView),
  );
  useEffect(() => {
    set(sourceLocation(pid, page, defaultView));
    const listener = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (detail?.pid === pid && detail.location?.page === page)
        set(detail.location);
    };
    window.addEventListener(SOURCE_NAVIGATION, listener);
    return () => window.removeEventListener(SOURCE_NAVIGATION, listener);
  }, [pid, page, defaultView]);
  const update = useCallback(
    (patch: Partial<SourceLocation>) => {
      set((previous) => {
        const next = { ...previous, ...patch, revision: Date.now() };
        sessionStorage.setItem(key(pid, page), JSON.stringify(next));
        return next;
      });
    },
    [pid, page],
  );
  return { location, update };
}
