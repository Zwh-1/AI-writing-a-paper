import { ArrowRight, type LucideIcon } from "lucide-react";
import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import "./WorkspaceNavigation.css";

export type WorkspaceDestination<K extends string = string> = {
  id: K;
  title: string;
  description: string;
  icon: LucideIcon;
  metadata?: ReactNode;
};

type Props<K extends string> = {
  label: string;
  introduction: ReactNode;
  note?: string;
  destinations: readonly WorkspaceDestination<K>[];
  current: K;
  onNavigate: (id: K) => void;
  children?: ReactNode;
  className?: string;
};

// Carry keyboard focus across pages, including a lazy page's loading boundary.
let keyboardDestination: { label: string; id: string; until: number } | null = null;

export default function WorkspaceNavigation<K extends string>({
  label,
  introduction,
  note,
  destinations,
  current,
  onNavigate,
  children,
  className = "",
}: Props<K>) {
  const navigation = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (
      keyboardDestination?.label === label &&
      keyboardDestination.id === current &&
      keyboardDestination.until >= Date.now() &&
      (document.activeElement === document.body ||
        navigation.current?.contains(document.activeElement))
    ) {
      navigation.current?.querySelector<HTMLButtonElement>('[aria-current="page"]')?.focus();
    }
  }, [current, label]);
  return (
    <div className={`workspace-navigation ${className}`.trim()}>
      <div className="workspace-navigation-intro">
        <span>{introduction}</span>
        {note && <small>{note}</small>}
      </div>
      <div
        ref={navigation}
        className="workspace-navigation-destinations"
        role="navigation"
        aria-label={label}
        style={
          { "--workspace-destinations": destinations.length } as CSSProperties
        }
      >
        {destinations.map(({ id, title, description, icon: Icon, metadata }) => (
          <button
            key={id}
            type="button"
            className="workspace-navigation-card"
            aria-current={current === id ? "page" : undefined}
            onClick={(event) => {
              if (id === current) return;
              keyboardDestination = event.detail === 0 ? { label, id, until: Date.now() + 10000 } : null;
              onNavigate(id);
            }}
          >
            <span className="workspace-navigation-icon">
              <Icon size={19} />
            </span>
            <span className="workspace-navigation-copy">
              <strong>{title}</strong>
              <small>{description}</small>
            </span>
            {metadata !== undefined && (
              <span className="workspace-navigation-metadata">{metadata}</span>
            )}
            {current !== id && (
              <ArrowRight className="workspace-navigation-arrow" size={14} />
            )}
          </button>
        ))}
      </div>
      {children}
    </div>
  );
}
