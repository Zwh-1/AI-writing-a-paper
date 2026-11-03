import { useState } from "react";
import { researchApi } from "../api/research";
import { navigateWorkspace, type SourceRef } from "./WorkspaceContext";
export default function SourceCard({
  pid,
  source,
  label,
}: {
  pid: string;
  source: SourceRef;
  label?: string;
}) {
  const [error, setError] = useState("");
  return (
    <span className="source-card">
      <button
        className="text-button"
        onClick={() =>
          void researchApi(pid)
            .resolve(source)
            .then((value) => {
              if (!value.current) {
                setError("此来源版本已过期，请重新选择原文");
                return;
              }
              if (!value.location.file_id) {
                if (value.location.section_id)
                  navigateWorkspace(pid, {
                    page: "editor",
                    sectionId: value.location.section_id,
                  });
                else navigateWorkspace(pid,{page:'products',productType:source.type,productId:source.id});
                return;
              }
              navigateWorkspace(pid, {
                page: "references",
                view: "parallel",
                fileId: value.location.file_id,
                unitId: value.location.unit_id,
                parseId: value.location.parse_id,
              });
            })
            .catch((failure) => setError(failure.message))
        }
      >
        {label || "查看来源"} · {source.type}
      </button>
      {error && <small role="alert">{error}</small>}
    </span>
  );
}
