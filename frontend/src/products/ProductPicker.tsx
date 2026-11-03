import { useState } from "react";
import { productsPath, type ProductCatalog } from "../api/products";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import type { SourceRef } from "../workspace/WorkspaceContext";
export default function ProductPicker({
  pid,
  value,
  onChange,
  disabled = false,
  purpose = "chat",
}: {
  pid: string;
  value: SourceRef[];
  onChange: (value: SourceRef[]) => void;
  disabled?: boolean;
  purpose?: "chat" | "execute";
}) {
  const [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0);
  const resource = useWorkspaceResource<ProductCatalog>(
    productsPath(pid) +
      "?" +
      new URLSearchParams({ query, limit: "20", offset: String(offset) }),
    0,
  );
  return (
    <details className="studio-sources">
      <summary>
        {purpose === "execute" ? "选择执行输入产物" : "选择项目产物"}（已选{" "}
        {value.length} 项）
      </summary>
      <p className="muted">
        总结、批注、译文、分析、Wiki
        和章节可直接作为讨论材料。发送时核对来源权限与版本。
      </p>
      {value.length > 0 && (
        <div className="studio-toolbar">
          <span>已选材料会随本次问题一起读取</span>
          <button
            className="btn"
            type="button"
            disabled={disabled}
            onClick={() => onChange([])}
          >
            清空产物
          </button>
        </div>
      )}
      <input
        aria-label="搜索对话产物"
        placeholder="搜索项目产物"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setOffset(0);
        }}
      />
      {resource.error && <p role="alert">{resource.error}</p>}
      {resource.data?.items.map((item) => {
        const selected = value.some(
          (ref) => ref.type === item.kind && ref.id === item.id,
        );
        return (
          <label
            key={item.kind + item.id}
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              margin: "8px 0",
              overflowWrap: "anywhere",
            }}
          >
            <input
              type="checkbox"
              checked={selected}
              disabled={
                disabled ||
                !item.source_ref ||
                !item.actions.includes(purpose) ||
                (!selected && value.length >= 20)
              }
              onChange={() =>
                onChange(
                  selected
                    ? value.filter(
                        (ref) => ref.type !== item.kind || ref.id !== item.id,
                      )
                    : [...value, item.source_ref!],
                )
              }
            />
            {item.label} · {item.title}
            {item.state === "stale" ? "（需更新）" : ""}
          </label>
        );
      })}
      <div className="studio-toolbar">
        <button
          className="btn"
          type="button"
          disabled={disabled || offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 20))}
        >
          上一批
        </button>
        <span>
          {resource.data?.total ? offset + 1 : 0} / {resource.data?.total || 0}
        </span>
        <button
          className="btn"
          type="button"
          disabled={disabled || offset + 20 >= (resource.data?.total || 0)}
          onClick={() => setOffset(offset + 20)}
        >
          下一批
        </button>
      </div>
    </details>
  );
}
