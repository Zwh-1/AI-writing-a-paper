import { useState } from "react";
import { api, send } from "../client";
import type { Row } from "../workspace/types";

export default function EntitySplitControl({
  base,
  entity,
  busy,
  run,
  onResult,
}: {
  base: string;
  entity: Row;
  busy: boolean;
  run: (fn: () => Promise<any>, success?: string) => Promise<any>;
  onResult: (value: Row) => void;
}) {
  const [claims, setClaims] = useState<Row[]>([]),
    [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState(""),
    [reviewer, setReviewer] = useState(""),
    [note, setNote] = useState("");
  if (entity.kind === "document" || entity.redirect_id) return null;
  return (
    <details>
      <summary>拆分被混在一起的实体</summary>
      <p className="muted">
        选择应属于另一实体的论断，填写新名称及核对依据。拆分会保留原版本，并要求重新核对相关知识。
      </p>
      <button
        className="btn"
        disabled={busy}
        onClick={() =>
          void run(async () => {
            const graph = await send(base + "/graph/search", {
              query: entity.name,
              namespace: entity.namespace,
              depth: 1,
              node_limit: 100,
              edge_limit: 200,
            });
            setClaims(
              (graph.assertions ?? []).filter(
                (a: Row) =>
                  a.subject_id === entity.id || a.object_id === entity.id,
              ),
            );
            setSelected([]);
          }, "已读取当前实体的关联论断")
        }
      >
        读取关联论断
      </button>
      {claims.map((claim) => (
        <label className="checkbox" key={claim.claim_version_id}>
          <input
            type="checkbox"
            checked={selected.includes(claim.claim_version_id)}
            onChange={(e) =>
              setSelected((v) =>
                e.target.checked
                  ? [...v, claim.claim_version_id]
                  : v.filter((id) => id !== claim.claim_version_id),
              )
            }
          />
          {claim.proposition?.statement || claim.predicate || "待核对论断"}
        </label>
      ))}
      <label>
        新实体名称
        <input
          value={name}
          maxLength={500}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label>
        核对人
        <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
      </label>
      <label>
        拆分理由
        <textarea value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <button
        className="btn"
        disabled={
          busy ||
          !name.trim() ||
          !reviewer.trim() ||
          !note.trim() ||
          !selected.length ||
          selected.length > 60
        }
        onClick={() =>
          void run(async () => {
            const result = await send(
              base + "/entities/" + entity.id + "/split",
              { name, reviewer, note, claim_ids: selected },
            );
            onResult(await api(base + "/entities/" + result.entity.id));
          }, "已拆分实体并保留来源与历史版本")
        }
      >
        保存拆分
      </button>
    </details>
  );
}
