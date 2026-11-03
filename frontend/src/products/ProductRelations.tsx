import { useEffect, useState } from "react";
import {
  productApi,
  type ProductRelations as Relations,
} from "../api/products";
import {
  navigateWorkspace,
  type SourceRef,
} from "../workspace/WorkspaceContext";
const relations: Record<string, string> = {
  derived_from: "内容来源",
  execution_input: "用于代码执行",
  used_in_current_document: "用于当前正文",
  inserted_in_current_document: "已插入当前正文",
  rendered_from: "由画布生成",
  insertion_pending: "待插入章节",
  insertion_consumed: "已完成插入",
  insertion_dismissed: "已忽略插入",
};
export default function ProductRelations({
  pid,
  source,
  title,
}: {
  pid: string;
  source: SourceRef;
  title: string;
}) {
  const [data, setData] = useState<Relations | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setData(null);
    setError("");
    productApi(pid)
      .relations(source)
      .then((value) => {
        if (active) setData(value);
      })
      .catch((failure) => {
        if (active) setError(failure.message);
      });
    return () => {
      active = false;
    };
  }, [pid, source.type, source.id, source.version]);
  return (
    <section className="product-preview" aria-label="产物来源与用途">
      <h3>{title} · 来源与用途</h3>
      <p>显示现有记录保存的直接关系，点击可进入相应功能。</p>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">正在读取流向…</p>}
      {data && (
        <>
          {!data.selected_version_current && (
            <p className="product-error">
              所选版本已变化；以下展示当前记录关系，请重新核对内容。
            </p>
          )}
          <div className="product-relations-grid">
            {(["upstream", "downstream"] as const).map((direction) => (
              <div key={direction}>
                <h4>
                  {direction === "upstream" ? "内容来自哪里" : "已用于哪些功能"}
                </h4>
                {!data.items.some((item) => item.direction === direction) && (
                  <p className="muted">暂无已记录的直接关系</p>
                )}
                <ul>
                  {data.items
                    .filter((item) => item.direction === direction)
                    .map((item, index) => (
                      <li key={item.type + item.id + item.relation + index}>
                        <strong>{item.title}</strong>
                        <p>
                          {relations[item.relation] || item.relation}
                          {item.state !== "current" ? " · 需复查" : ""}
                        </p>
                        {item.reason && <small>{item.reason}</small>}
                        {item.open_location && (
                          <button
                            className="text-button"
                            onClick={() =>
                              navigateWorkspace(pid, item.open_location!)
                            }
                          >
                            查看关联内容
                          </button>
                        )}
                      </li>
                    ))}
                </ul>
              </div>
            ))}
          </div>
          {data.truncated && (
            <p className="muted">
              关系超过本次显示上限，更多记录可从关联功能中查看。
            </p>
          )}
        </>
      )}
    </section>
  );
}
