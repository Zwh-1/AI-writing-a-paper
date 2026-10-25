import { ArrowUpRight } from "lucide-react";
import { WorkspaceEmpty } from "../workspace/WorkspaceChrome";
import { materialRoles, sourceLocation, type Row } from "../workspace/types";

export default function MaterialSearchResults({
  results,
  query,
  onClear,
  onOpen,
}: {
  results: Row[];
  query: string;
  onClear: () => void;
  onOpen: (id: string, page?: number) => void;
}) {
  return (
    <section className="material-search-results" aria-label="资料原文检索结果">
      <div className="material-search-results-heading">
        <div>
          <h3>
            原文检索结果 <span>{results.length}</span>
          </h3>
          <p>“{query}” · 点击结果回到对应资料与原页。</p>
        </div>
        <button className="btn" onClick={onClear}>
          返回资料列表
        </button>
      </div>
      {results.length ? (
        results.map((result) => (
          <article className="search-result" key={result.id}>
            <div>
              <span className="badge">
                {materialRoles[result.role] ?? result.role}
              </span>
              <span>
                {result.name} · {sourceLocation(result)}
              </span>
            </div>
            <p>{result.text}</p>
            <button
              className="text-button"
              onClick={() => onOpen(result.file_id, result.page_number)}
            >
              打开资料
              <ArrowUpRight size={14} />
            </button>
          </article>
        ))
      ) : (
        <WorkspaceEmpty
          title="未找到对应原文"
          description="试试同义词或更短的关键词，或继续补充资料。"
        />
      )}
    </section>
  );
}
