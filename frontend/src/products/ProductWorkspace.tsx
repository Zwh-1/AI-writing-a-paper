import { useEffect, useState, type ReactNode } from "react";
import { api, apiUrl, send } from "../api/client";
import {
  productApi,
  productKey,
  productsPath,
  type Product,
  type ProductCatalog,
  type ProductPreview,
} from "../api/products";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import {
  navigateWorkspace,
  useWorkspaceLocation,
  type SourceRef,
} from "../workspace/WorkspaceContext";
import SourceCard from "../workspace/SourceCard";
import ProductRelations from "./ProductRelations";
import "./products.css";

type Section = {
  id: string;
  title: string;
  current_revision_id: string | null;
  state: string;
};
const plain = (node: any): string =>
  node.type === "text"
    ? node.text
    : (node.content || []).map(plain).join(node.type === "doc" ? "\n" : "");
export default function ProductWorkspace({
  pid,
  navigation,
}: {
  pid: string;
  navigation?: ReactNode;
}) {
  const workspace = useWorkspaceLocation(pid, "products");
  const [related, setRelated] = useState<Product | null>(null);
  const [query, setQuery] = useState(""),
    [search, setSearch] = useState(""),
    [kind, setKind] = useState(workspace.location.productType || ""),
    [offset, setOffset] = useState(0),
    [sectionId, setSectionId] = useState(workspace.location.sectionId || ""),
    [sectionOnly, setSectionOnly] = useState(false),
    [selected, setSelected] = useState<Product[]>(() =>
      (workspace.location.productSelection || []).map((row) => ({
        ...row,
        stamp: "",
        section_id: "",
        state: "current",
        reason: "",
        actions: row.actions || [],
      })),
    ),
    [preview, setPreview] = useState<ProductPreview | null>(null),
    [proposal, setProposal] = useState<any>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const parameters = new URLSearchParams({
    query: search,
    kind,
    offset: String(offset),
    limit: "30",
    section_id: sectionOnly ? sectionId : "",
  });
  const catalog = useWorkspaceResource<ProductCatalog>(
    productsPath(pid) + "?" + parameters,
    0,
  );
  const sections = useWorkspaceResource<Section[]>(
    `/projects/${pid}/sections`,
    0,
  );
  const targetSection = sections.data?.find((row) => row.id === sectionId);
  const sourceRefs = selected
    .map((row) => row.source_ref)
    .filter((ref): ref is SourceRef => !!ref);
  const canUse = (target: string) =>
    selected.length > 0 &&
    selected.every((row) => row.actions.includes(target));
  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (!workspace.location.proposalId) return;
    let active = true;
    api(`/projects/${pid}/studio/records/${workspace.location.proposalId}`)
      .then((value) => {
        if (active && !value.data.adopted_revision_id) setProposal(value);
      })
      .catch((failure) => {
        if (active) setError(failure.message);
      });
    return () => {
      active = false;
    };
  }, [pid, workspace.location.proposalId]);
  useEffect(() => {
    if (workspace.location.productType && workspace.location.productId) {
      setKind(workspace.location.productType);
      setOffset(0);
    }
  }, [workspace.location.productType, workspace.location.productId]);
  useEffect(() => {
    const id = workspace.location.productId;
    if (!id) return;
    void run(async () => {
      const item = await productApi(pid).item(
        workspace.location.productType!,
        id,
      );
      setSelected([item]);
      workspace.update({ productId: undefined, productSelection: [item] });
      if (item.source_ref)
        setPreview(await productApi(pid).preview(item.source_ref));
      else setError(item.reason);
    });
  }, [workspace.location.productId]);
  const toggle = (item: Product) => {
    setProposal(null);
    const rows = selected.some((row) => productKey(row) === productKey(item))
      ? selected.filter((row) => productKey(row) !== productKey(item))
      : [...selected, item];
    setSelected(rows);
    workspace.update({ productSelection: rows, proposalId: undefined });
  };
  const reuse = (target: string) =>
    void run(async () => {
      const result = await productApi(pid).reuse({
        sources: sourceRefs,
        target,
        section_id: sectionId || undefined,
        revision_id: targetSection?.current_revision_id || null,
      });
      if (target === "draft") {
        setProposal(result.record);
        workspace.update({ proposalId: result.record.id });
        setNotice("草稿已生成，请核对后采用。");
      }
      if (target === "note") {
        setNotice("已保存到思维随记，保留所选产物的来源。");
      }
      if (target === "asset") {
        setNotice("已保存到共享资产库，可在研究与成果、学术对话中使用。");
      }
      if (target === "figure")
        navigateWorkspace(pid, { page: "editor", sectionId });
      if (target === "material")
        setNotice("已关联到本章写作材料，后续写作请求会读取实际内容与来源。");
      if (target === "plan")
        setNotice("已补充章节目标并关联写作材料，可在提纲与计划中查看。");
      if (target === "review")
        setNotice("已加入修改与检查的待办，保留来源随记。");
    });
  return (
    <section className="product-workspace" aria-label="产物与互通">
      <header className="studio-heading workspace-heading">
        <div>
          <h1>产物与互通</h1>
          <p>把各功能生成的内容用于阅读、讨论与写作，保留来源和版本。</p>
        </div>
        <button
          className="btn"
          disabled={catalog.loading || busy}
          onClick={() => void catalog.load()}
        >
          刷新产物
        </button>
      </header>
      {navigation}
      <form
        className="product-filters"
        onSubmit={(event) => {
          event.preventDefault();
          setSearch(query);
          setOffset(0);
        }}
      >
        <label>
          搜索产物
          <input
            aria-label="搜索项目产物"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索标题、正文或分析结果"
          />
        </label>
        <label>
          产物类型
          <select
            aria-label="产物类型"
            value={kind}
            onChange={(event) => {
              setKind(event.target.value);
              workspace.update({ productType: event.target.value });
              setOffset(0);
            }}
          >
            <option value="">全部类型</option>
            {catalog.data?.kinds.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <button className="btn" type="submit">
          搜索
        </button>
      </form>
      {(error || catalog.error || sections.error) && (
        <p role="alert" className="product-error">
          {error || catalog.error || sections.error}
        </p>
      )}
      {notice && (
        <p role="status" className="product-notice">
          {notice}
        </p>
      )}
      <div className="product-layout">
        <div>
          <div className="product-list-heading">
            <span>
              共 {catalog.data?.total ?? 0} 项 · 已选 {selected.length} 项
            </span>
            <button
              className="text-button"
              disabled={!selected.length || busy}
              onClick={() => {
                setSelected([]);
                setProposal(null);
                workspace.update({
                  productSelection: [],
                  proposalId: undefined,
                });
              }}
            >
              清空选择
            </button>
          </div>
          {catalog.loading && <p role="status">正在读取项目产物…</p>}
          {!catalog.loading && !catalog.data?.items.length && (
            <div className="studio-empty">
              <h3>还没有符合条件的产物</h3>
              <p>
                导入资料、保存批注或完成分析后，产物会出现在这里。可调整搜索与分类。
              </p>
            </div>
          )}
          <div className="product-list">
            {catalog.data?.items.map((item) => (
              <article
                key={productKey(item)}
                className={
                  "product-item " +
                  (selected.some((row) => productKey(row) === productKey(item))
                    ? "selected"
                    : "")
                }
              >
                <input
                  type="checkbox"
                  aria-label={"选择产物：" + item.title}
                  checked={selected.some(
                    (row) => productKey(row) === productKey(item),
                  )}
                  disabled={
                    busy ||
                    !item.source_ref ||
                    (!selected.some(
                      (row) => productKey(row) === productKey(item),
                    ) &&
                      selected.length >= 20)
                  }
                  onChange={() => toggle(item)}
                />
                <div className="product-item-content">
                  <small>{item.label}</small>
                  <h3>{item.title}</h3>
                  {item.state === "stale" ? (
                    <p className="product-error">需更新：{item.reason}</p>
                  ) : (
                    <small>来源版本可用 · 内容仍需核对</small>
                  )}
                </div>
                <button
                  className="btn"
                  disabled={busy || !item.source_ref || !item.actions.length}
                  onClick={() =>
                    void run(async () =>
                      setPreview(
                        await productApi(pid).preview(item.source_ref!),
                      ),
                    )
                  }
                >
                  预览
                </button>
                <button
                  className="text-button"
                  disabled={busy || !item.source_ref}
                  onClick={() => setRelated(item)}
                >
                  查看流向
                </button>
                {item.open_location && (
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => navigateWorkspace(pid, item.open_location!)}
                  >
                    打开原功能
                  </button>
                )}
              </article>
            ))}
          </div>
          <div className="product-pagination">
            <button
              className="btn"
              disabled={offset === 0 || busy || catalog.loading}
              onClick={() => setOffset(Math.max(0, offset - 30))}
            >
              上一页
            </button>
            <span>第 {Math.floor(offset / 30) + 1} 页</span>
            <button
              className="btn"
              disabled={
                offset + 30 >= (catalog.data?.total || 0) ||
                busy ||
                catalog.loading
              }
              onClick={() => setOffset(offset + 30)}
            >
              下一页
            </button>
          </div>
        </div>
        <aside className="product-actions" aria-label="产物用途">
          <h3>选择下一步用途</h3>
          <p>选中的产物会带上来源与版本，供目标功能实际读取。</p>
          {selected.length > 0 && (
            <ul className="product-selected">
              {selected.map((row) => (
                <li key={productKey(row)}>
                  {row.title}
                  <button
                    className="text-button"
                    aria-label={"移除产物：" + row.title}
                    disabled={busy}
                    onClick={() => toggle(row)}
                  >
                    移除
                  </button>
                </li>
              ))}
            </ul>
          )}
          <label>
            目标章节
            <select
              aria-label="产物目标章节"
              value={sectionId}
              disabled={busy}
              onChange={(event) => {
                setSectionId(event.target.value);
                workspace.update({
                  sectionId: event.target.value,
                  proposalId: undefined,
                });
                setProposal(null);
                setOffset(0);
              }}
            >
              <option value="">请选择章节</option>
              {sections.data
                ?.filter((row) => row.state !== "archived")
                .map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.title}
                  </option>
                ))}
            </select>
          </label>
          <label className="product-check">
            <input
              type="checkbox"
              checked={sectionOnly}
              onChange={(event) => {
                setSectionOnly(event.target.checked);
                setOffset(0);
              }}
              disabled={!sectionId || busy}
            />
            只看已关联此章节的产物
          </label>
          <div className="product-action-buttons">
            <button
              className="btn"
              disabled={busy || !canUse("note")}
              onClick={() => reuse("note")}
            >
              转为思维随记
            </button>
            <button
              className="btn"
              disabled={busy || !canUse("asset")}
              onClick={() => reuse("asset")}
            >
              存入共享资产库
            </button>
            <button
              className="btn"
              disabled={busy || !canUse("chat")}
              onClick={() =>
                navigateWorkspace(pid, {
                  page: "studio",
                  view: "chat",
                  sectionId,
                  productRefs: sourceRefs,
                })
              }
            >
              交给学术对话
            </button>
            <button
              className="btn primary"
              disabled={busy || !canUse("draft") || !targetSection}
              onClick={() => reuse("draft")}
            >
              生成章节草稿
            </button>
            <button
              className="btn"
              disabled={
                busy || selected.length !== 1 || !canUse("figure") || !sectionId
              }
              onClick={() => reuse("figure")}
            >
              插入图表到章节
            </button>
            <button
              className="btn"
              disabled={busy || !canUse("material") || !targetSection}
              onClick={() => reuse("material")}
            >
              关联本章写作材料
            </button>
            <button
              className="btn"
              disabled={busy || !canUse("plan") || !targetSection}
              onClick={() => reuse("plan")}
            >
              补充章节计划
            </button>
            <button
              className="btn"
              disabled={busy || !canUse("review")}
              onClick={() => reuse("review")}
            >
              加入修改检查待办
            </button>
          </div>
          {sectionId && !targetSection?.current_revision_id && (
            <small>此章节尚无正文，可以直接从所选产物建立第一份草稿。</small>
          )}
          <small>
            转换不调用模型；草稿经核对采用后进入正文。学术对话在点击发送时计费。
          </small>
          {selected.length > 0 && (
            <button
              className="text-button"
              onClick={() =>
                navigateWorkspace(pid, { page: "studio", view: "notes" })
              }
            >
              查看思维随记
            </button>
          )}
        </aside>
      </div>
      {related?.source_ref && (
        <ProductRelations
          pid={pid}
          source={related.source_ref}
          title={related.title}
        />
      )}
      {preview && (
        <section className="product-preview">
          <h3>{preview.title}</h3>
          <SourceCard pid={pid} source={preview.source_ref} />
          {preview.visual && (
            <img
              className="product-image"
              src={apiUrl(
                "/projects/" +
                  encodeURIComponent(pid) +
                  "/studio/assets/" +
                  encodeURIComponent(preview.source_ref.id),
              )}
              alt={preview.title}
            />
          )}
          <pre>{preview.text}</pre>
        </section>
      )}
      {proposal && (
        <section className="product-preview" aria-label="互通草稿预览">
          <h3>章节草稿预览</h3>
          <p>原正文与新增产物如下，采用会创建新版本。</p>
          <pre>{plain(proposal.data.document)}</pre>
          <button
            className="btn primary"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await send(
                  `/projects/${pid}/studio/proposals/${proposal.id}/adopt`,
                  { revision: proposal.revision },
                );
                setProposal(null);
                workspace.update({ proposalId: undefined });
                navigateWorkspace(pid, {
                  page: "editor",
                  sectionId: proposal.owner_id,
                });
              })
            }
          >
            核对并采用草稿
          </button>
        </section>
      )}
    </section>
  );
}
