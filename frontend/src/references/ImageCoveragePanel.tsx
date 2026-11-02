import { useEffect, useState } from "react";
import { api } from "../client";
import type { Row } from "../workspace/types";

const labels: Record<string, string> = {
  full_transcript_compared: "整图文字已对照",
  partial_transcript_compared: "仅部分文字已对照",
  ocr_unreviewed: "完整OCR观察待核对",
  image_unread: "原图尚无文字观察",
};

export default function ImageCoveragePanel({
  pid,
  fid,
  page,
  intent = "guidance",
}: {
  pid: string;
  fid: string;
  page?: number;
  intent?: string;
}) {
  const [open, setOpen] = useState(false),
    [coverage, setCoverage] = useState<Row | null>(null);
  const [reading, setReading] = useState<Row | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [query, setQuery] = useState(""),
    [locations, setLocations] = useState<Row | null>(null);
  const base = `/projects/${pid}/document-reading/documents/${fid}`;
  useEffect(() => {
    setCoverage(null);
    setReading(null);
    setError("");
  }, [fid, pid]);
  useEffect(() => {
    if (!open) return;
    let active = true;
    void api(base + "/coverage")
      .then((value) => {
        if (active) setCoverage(value);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [open, base]);
  const read = async (asset: Row) => {
    setBusy(true);
    setError("");
    try {
      setReading(
        await api(
          `${base}/assets/${asset.sha256}/read?page=${asset.page}&intent=${intent}`,
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const assets = (coverage?.assets ?? []).filter(
    (asset: Row) => !page || asset.page === page,
  );
  return (
    <details
      className="dr-image-coverage"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>完整图像观察与识别覆盖</summary>
      <p>
        整图观察、校对摘录和规则用途分别记录。未核对OCR保留全文，不能作为确定结论。
      </p>
      {error && <p role="alert">{error}</p>}
      {coverage && (
        <p>
          整图对照 {coverage.counts.full_transcript_compared} · 部分对照{" "}
          {coverage.counts.partial_transcript_compared} · OCR待核对{" "}
          {coverage.counts.ocr_unreviewed} · 尚无观察{" "}
          {coverage.counts.image_unread}
        </p>
      )}
      <div className="actions">
        <input
          aria-label="在完整图像观察中定位"
          placeholder="搜索完整观察中的文字"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button
          className="btn"
          disabled={busy || !query.trim()}
          onClick={() => {
            setBusy(true);
            setError("");
            void api(
              `${base}/locate-images?query=${encodeURIComponent(query)}&intent=${intent}`,
            )
              .then(setLocations)
              .catch((e) => setError(e.message))
              .finally(() => setBusy(false));
          }}
        >
          定位原图
        </button>
      </div>
      {locations && (
        <div>
          <p>
            找到 {locations.items.length} 处原图位置；命中本身不能证明结论。
          </p>
          {locations.items.map((location: Row) => (
            <button
              className="btn"
              key={location.unit_id}
              disabled={busy}
              onClick={() => {
                const asset = coverage?.assets.find(
                  (item: Row) =>
                    item.page === location.page &&
                    item.sha256 === location.asset_sha256,
                );
                if (asset) void read(asset);
              }}
            >
              第{location.page}页 · 打开完整观察
            </button>
          ))}
        </div>
      )}
      <div className="actions">
        {assets.map((asset: Row) => (
          <button
            className="btn"
            key={`${asset.page}:${asset.sha256}`}
            data-asset-sha={asset.sha256}
            disabled={busy}
            onClick={() => void read(asset)}
          >
            第{asset.page}页 · {labels[asset.state]} · OCR{" "}
            {asset.raw_characters}字 / 校对 {asset.verified_characters}字
          </button>
        ))}
      </div>
      {coverage && !assets.length && (
        <p>该范围未登记独立图片；请结合原页预览核对。</p>
      )}
      {reading && (
        <section aria-label="完整图像观察">
          <h4>
            第{reading.asset.page}页 · {labels[reading.asset.state]}
          </h4>
          {reading.asset.source_issues?.includes("cropped_original") && (
            <p role="status">
              原件图片自身被裁断。缺失内容须补充未裁断原件，不能根据OCR或上下文猜补。
            </p>
          )}
          {reading.asset.image_url && (
            <a href={reading.asset.image_url} target="_blank" rel="noreferrer">
              <img
                src={reading.asset.image_url}
                alt={`第${reading.asset.page}页完整原始图片`}
                style={{
                  maxWidth: "100%",
                  height: "auto",
                  objectFit: "contain",
                }}
              />
            </a>
          )}
          <p>{reading.scope_note}</p>
          {reading.status !== "read" && <p>{reading.action}</p>}
          {reading.raw_observations?.map((observation: Row, index: number) => (
            <div key={index}>
              <h4>完整保存的OCR观察 · 尚未核对</h4>
              {observation.issues?.length > 0 && (
                <p>{observation.issues.join("、")}</p>
              )}
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  maxHeight: 420,
                  overflow: "auto",
                }}
              >
                {observation.text}
              </pre>
            </div>
          ))}
          {reading.verified_passages?.map((passage: Row) => (
            <div key={passage.source_id}>
              <h4>
                {passage.coverage === "complete_image_text"
                  ? "整图校对文字"
                  : "独立校对摘录"}
              </h4>
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  maxHeight: 420,
                  overflow: "auto",
                }}
              >
                {passage.text}
              </pre>
            </div>
          ))}
        </section>
      )}
    </details>
  );
}
