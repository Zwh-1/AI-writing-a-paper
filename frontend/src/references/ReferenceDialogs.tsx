import { useRef, useState, type FormEvent } from "react";
import { LoaderCircle, Upload } from "lucide-react";
import { api, send } from "../client";
import WorkspaceDialog from "../workspace/WorkspaceDialog";
import type { Row, WorkspaceAction } from "../workspace/types";

export const metadataOf = (reference: Row): Row => {
  try {
    return typeof reference.metadata === "string"
      ? JSON.parse(reference.metadata)
      : (reference.metadata ?? {});
  } catch {
    return {};
  }
};

export function ReferenceEditor({
  pid,
  reference,
  files,
  act,
  onClose,
}: {
  pid: string;
  reference: Row;
  files: Row[];
  act: WorkspaceAction;
  onClose: () => void;
}) {
  const meta = metadataOf(reference),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const lock = useRef(false);
  const [selectedFile, setSelectedFile] = useState(reference.file_id ?? "");
  const sourceFiles = files.filter((file) =>
    ["research", "example"].includes(file.role),
  );
  return (
    <WorkspaceDialog
      title="参考文献信息与原文"
      wide
      busy={busy}
      onClose={onClose}
      closeLabel="关闭文献编辑"
    >
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (lock.current) return;
          lock.current = true;
          setBusy(true);
          setError("");
          try {
            const fields = Object.fromEntries(
              new FormData(event.currentTarget).entries(),
            );
            const { file_id, verification, ...changed } = fields;
            const next: Row = { ...meta, ...changed };
            const originalAuthors = Array.isArray(meta.authors)
              ? meta.authors.join("，")
              : (meta.authors ?? "");
            if (
              meta.citation_raw &&
              [
                "title",
                "authors",
                "year",
                "journal",
                "publisher",
                "type",
                "doi",
                "volume",
                "pages",
              ].some(
                (key) =>
                  String(changed[key] ?? "") !==
                  String(
                    key === "authors" ? originalAuthors : (meta[key] ?? ""),
                  ),
              )
            ) {
              next.source_citation = meta.citation_raw;
              delete next.citation_raw;
            }
            const result = await act(
              () =>
                send(
                  reference.id
                    ? "/references/" + reference.id
                    : "/projects/" + pid + "/references",
                  {
                    metadata: next,
                    file_id: selectedFile || null,
                    verification,
                  },
                  reference.id ? "PATCH" : "POST",
                ),
              "文献已保存",
            );
            if (result) onClose();
            else setError("书目尚未保存，请核对提示后重试。");
          } finally {
            lock.current = false;
            setBusy(false);
          }
        }}
        className="reference-edit-form"
      >
        <fieldset disabled={busy}>
          <label className="field">
            <span>文献题名</span>
            <input
              name="title"
              required
              maxLength={1000}
              defaultValue={meta.title ?? ""}
            />
          </label>
          <div className="form-grid">
            {[
              ["作者（原文顺序）", "authors"],
              ["年份", "year"],
              ["期刊/会议", "journal"],
              ["出版单位/学校", "publisher"],
              ["DOI", "doi"],
              ["文献类型（J/C/D等）", "type"],
              ["卷期", "volume"],
              ["页码", "pages"],
            ].map(([label, key]) => (
              <label className="field" key={key}>
                <span>{label}</span>
                <input
                  name={key}
                  defaultValue={
                    key === "authors" && Array.isArray(meta.authors)
                      ? meta.authors.join("，")
                      : (meta[key] ?? "")
                  }
                />
              </label>
            ))}
          </div>
          <label className="field">
            <span>原文文件</span>
            <select
              name="file_id"
              aria-label="原文文件"
              value={selectedFile}
              onChange={(event) => setSelectedFile(event.target.value)}
            >
              <option value="">暂不关联</option>
              {selectedFile &&
                !sourceFiles.some((file) => file.id === selectedFile) && (
                  <option value={selectedFile}>
                    已关联原文（文件信息加载中）
                  </option>
                )}
              {sourceFiles.map((file) => (
                <option key={file.id} value={file.id}>
                  {file.name}
                </option>
              ))}
            </select>
          </label>
          <div className="form-grid">
            <label className="field">
              <span>书目核查</span>
              <select
                name="verification"
                aria-label="书目核查"
                defaultValue={reference.verification ?? "unverified"}
              >
                <option value="unverified">待核实</option>
                <option value="human_confirmed">已人工核对</option>
                <option value="metadata_matched">已与元数据匹配</option>
              </select>
            </label>
            <label className="field">
              <span>文献语种</span>
              <select
                name="language"
                aria-label="文献语种"
                defaultValue={meta.language ?? ""}
              >
                <option value="">待确认</option>
                <option value="zh">中文</option>
                <option value="en">英文</option>
                <option value="other">其他外文</option>
              </select>
            </label>
            <label className="field">
              <span>资料范围</span>
              <select
                name="scope"
                aria-label="资料范围"
                defaultValue={meta.scope ?? "metadata_only"}
              >
                <option value="full_text">全文原件</option>
                <option value="abstract_only">只有摘要</option>
                <option value="metadata_only">仅书目信息</option>
              </select>
            </label>
          </div>
          {(meta.citation_raw || meta.source_citation) && (
            <details className="reference-original-citation">
              <summary>查看导入的原始著录</summary>
              <p>{meta.citation_raw || meta.source_citation}</p>
              <small>{meta.archive_member}</small>
            </details>
          )}
        </fieldset>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className="reference-dialog-actions">
          <button
            className="btn"
            type="button"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </button>
          <button className="btn primary" type="submit" disabled={busy}>
            {busy && <LoaderCircle className="spin" size={15} />}保存书目
          </button>
        </div>
      </form>
    </WorkspaceDialog>
  );
}

export function ArchiveImportDialog({
  pid,
  act,
  onClose,
  onImported,
}: {
  pid: string;
  act: WorkspaceAction;
  onClose: () => void;
  onImported: (result: Row) => void;
}) {
  const [file, setFile] = useState<File | null>(null),
    [preview, setPreview] = useState<Row | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const lock = useRef(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    const form = new FormData();
    form.set("file", file);
    try {
      if (!preview)
        setPreview(
          await api(`/projects/${pid}/references/archive-preview`, {
            method: "POST",
            body: form,
          }),
        );
      else {
        form.set("expected_sha256", preview.sha256);
        const result = await act(
          () =>
            api(`/projects/${pid}/references/archive-import`, {
              method: "POST",
              body: form,
            }),
          "文献包已导入，原文正在本地提取文字",
        );
        if (result) {
          onImported(result);
          onClose();
        } else setError("导入未完成，请核对提示后重试。");
      }
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };
  return (
    <WorkspaceDialog
      title="批量导入文献包"
      wide
      busy={busy}
      onClose={onClose}
      closeLabel="关闭文献包导入"
    >
      <form onSubmit={submit} className="reference-archive-form">
        <label className="reference-dropzone">
          <Upload size={27} />
          <strong>选择 ZIP 文献包</strong>
          <span>包含 PDF 原件及“文献索引.csv”，支持分类目录。</span>
          <input
            type="file"
            aria-label="选择ZIP文献包"
            accept=".zip"
            required
            disabled={busy}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setPreview(null);
              setError("");
            }}
          />
        </label>
        <p className="muted">
          先预览书目与原件关联，再导入当前项目。重复文献自动识别；书目信息保留待核对状态。
        </p>
        {preview && (
          <section className="reference-import-preview" aria-label="文献包预览">
            <h3>
              {preview.pdf_count} 篇文献 · {preview.pages} 页原件
            </h3>
            <p>{preview.warnings?.join(" ")}</p>
            <div>
              {preview.records.map((record: Row) => (
                <article key={record.member}>
                  <strong>{record.metadata.title}</strong>
                  <small>
                    {record.metadata.year} ·{" "}
                    {record.metadata.language === "zh" ? "中文" : "外文"} ·{" "}
                    {record.pages} 页
                  </small>
                  {record.metadata.import_warnings?.map((message: string) => (
                    <p key={message}>{message}</p>
                  ))}
                </article>
              ))}
            </div>
          </section>
        )}
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className="reference-dialog-actions">
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </button>
          <button
            type="submit"
            className="btn primary"
            disabled={busy || !file}
          >
            {busy ? (
              <LoaderCircle className="spin" size={15} />
            ) : (
              <Upload size={15} />
            )}
            {busy ? "正在处理…" : preview ? "确认导入当前项目" : "预览文献包"}
          </button>
        </div>
      </form>
    </WorkspaceDialog>
  );
}

export function BibliographyImportDialog({
  pid,
  act,
  onClose,
}: {
  pid: string;
  act: WorkspaceAction;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const lock = useRef(false);
  return (
    <WorkspaceDialog title="导入 BibTeX / RIS" busy={busy} onClose={onClose}>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (lock.current) return;
          lock.current = true;
          setBusy(true);
          setError("");
          const body = new FormData(event.currentTarget);
          try {
            const result = await act(
              () =>
                api(`/projects/${pid}/references/import`, {
                  method: "POST",
                  body,
                }),
              "书目已导入，重复条目自动跳过",
            );
            if (result) onClose();
            else setError("导入未完成，请检查文件格式。");
          } finally {
            lock.current = false;
            setBusy(false);
          }
        }}
      >
        <label className="field">
          <span>书目文件</span>
          <input
            type="file"
            name="file"
            accept=".bib,.ris"
            required
            disabled={busy}
          />
        </label>
        <p className="muted">导入的是书目信息，全文可在“核对书目”中关联。</p>
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        <div className="reference-dialog-actions">
          <button type="submit" className="btn primary" disabled={busy}>
            {busy && <LoaderCircle className="spin" size={15} />}导入书目
          </button>
        </div>
      </form>
    </WorkspaceDialog>
  );
}

export function ReferenceLookupDialog({
  pid,
  act,
  onClose,
}: {
  pid: string;
  act: WorkspaceAction;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [results, setResults] = useState<Row[]>([]),
    [error, setError] = useState("");
  return (
    <WorkspaceDialog
      title="查询 DOI / 文献元数据"
      wide
      busy={busy}
      onClose={onClose}
    >
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy) return;
          const query = new FormData(event.currentTarget).get("query");
          setBusy(true);
          setError("");
          setResults([]);
          try {
            setResults(
              (await send(`/projects/${pid}/references/lookup`, { query }))
                .candidates,
            );
          } catch (failure) {
            setError((failure as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label className="field">
          <span>DOI 或文献题名</span>
          <input name="query" required disabled={busy} />
        </label>
        <button className="btn" type="submit" disabled={busy}>
          {busy && <LoaderCircle className="spin" size={15} />}查询 Crossref
        </button>
      </form>
      <p className="muted mt">
        匹配元数据不代表已阅读全文；查询无结果也不能证明文献不存在。
      </p>
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {results.map((result, index) => (
        <article className="reference-lookup-result" key={index}>
          <h3>{result.title}</h3>
          <p>
            {result.authors?.join("，")} · {result.year} · {result.journal}
          </p>
          <small>{result.doi}</small>
          <button
            className="btn"
            disabled={busy}
            onClick={async () => {
              if (busy) return;
              setBusy(true);
              try {
                await act(
                  () =>
                    send(`/projects/${pid}/references`, {
                      metadata: result,
                      verification: "metadata_matched",
                    }),
                  "已登记元数据，全文仍需关联核对",
                );
              } finally {
                setBusy(false);
              }
            }}
          >
            登记此候选
          </button>
        </article>
      ))}
    </WorkspaceDialog>
  );
}

export function TermEditor({
  pid,
  term,
  act,
  onClose,
}: {
  pid: string;
  term: Row;
  act: WorkspaceAction;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const lock = useRef(false);
  return (
    <WorkspaceDialog
      title={term.id ? "编辑术语" : "新增术语"}
      busy={busy}
      onClose={onClose}
    >
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (lock.current) return;
          lock.current = true;
          setBusy(true);
          setError("");
          const values = Object.fromEntries(
            new FormData(event.currentTarget).entries(),
          );
          try {
            const result = await act(
              () =>
                send(
                  `/projects/${pid}/terms` + (term.id ? "/" + term.id : ""),
                  values,
                  term.id ? "PATCH" : "POST",
                ),
              "术语已保存",
            );
            if (result) onClose();
            else setError("术语尚未保存，请核对提示后重试。");
          } finally {
            lock.current = false;
            setBusy(false);
          }
        }}
      >
        <label className="field">
          <span>中文术语</span>
          <input
            name="zh"
            required
            maxLength={300}
            defaultValue={term.zh ?? ""}
            disabled={busy}
          />
        </label>
        <label className="field">
          <span>对应英文</span>
          <input
            name="en"
            required
            maxLength={600}
            defaultValue={term.en ?? ""}
            disabled={busy}
          />
        </label>
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        <div className="reference-dialog-actions">
          <button type="submit" className="btn primary" disabled={busy}>
            {busy && <LoaderCircle className="spin" size={15} />}保存术语
          </button>
        </div>
      </form>
    </WorkspaceDialog>
  );
}
