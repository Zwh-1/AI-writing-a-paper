import type { ReactNode } from "react";
import { LoaderCircle, RefreshCw, type LucideIcon } from "lucide-react";
import "./workspace.css";

export function showWorkspacePanel(id: string) {
  requestAnimationFrame(() =>
    document.getElementById(id)?.scrollIntoView({ block: "start" }),
  );
}

export function WorkspaceHeader({
  title,
  section,
  description,
  actions,
}: {
  title: string;
  section: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="workspace-heading">
      <div>
        <span className="workspace-eyebrow">{section}</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="actions">{actions}</div>
    </header>
  );
}
export type WorkspaceTab<K extends string> = {
  key: K;
  label: string;
  icon: LucideIcon;
  count?: number;
};
export function WorkspaceTabs<K extends string>({
  id,
  label,
  tabs,
  value,
  onChange,
}: {
  id: string;
  label: string;
  tabs: readonly WorkspaceTab<K>[];
  value: K;
  onChange: (key: K) => void;
}) {
  return (
    <div className="workspace-tabs" role="tablist" aria-label={label}>
      {tabs.map((tab, index) => (
        <button
          key={tab.key}
          id={`${id}-tab-${tab.key}`}
          role="tab"
          aria-controls={`${id}-panel-${tab.key}`}
          aria-selected={value === tab.key}
          tabIndex={value === tab.key ? 0 : -1}
          onClick={() => onChange(tab.key)}
          onKeyDown={(event) => {
            const next =
              event.key === "ArrowRight"
                ? (index + 1) % tabs.length
                : event.key === "ArrowLeft"
                  ? (index + tabs.length - 1) % tabs.length
                  : event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? tabs.length - 1
                      : null;
            if (next === null) return;
            event.preventDefault();
            onChange(tabs[next].key);
            document.getElementById(`${id}-tab-${tabs[next].key}`)?.focus();
          }}
        >
          <tab.icon size={16} />
          <span>{tab.label}</span>
          {tab.count !== undefined && tab.count > 0 && (
            <small>{tab.count}</small>
          )}
        </button>
      ))}
    </div>
  );
}
export function WorkspaceNotice({
  error,
  loading,
  retry,
  label = "正在加载…",
}: {
  error?: string;
  loading?: boolean;
  retry?: () => void;
  label?: string;
}) {
  if (error)
    return (
      <div className="workspace-notice is-error" role="alert">
        <p>{error}</p>
        {retry && (
          <button className="btn" onClick={retry}>
            <RefreshCw size={14} />
            重新加载
          </button>
        )}
      </div>
    );
  return loading ? (
    <div className="workspace-notice" role="status">
      <LoaderCircle className="spin" size={17} />
      {label}
    </div>
  ) : null;
}
export function WorkspaceEmpty({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="workspace-empty">
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
    </div>
  );
}
