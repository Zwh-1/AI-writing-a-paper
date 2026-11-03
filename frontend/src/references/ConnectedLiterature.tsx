import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import { navigateWorkspace } from "../workspace/WorkspaceContext";
import { LiteratureLibrary, Discover } from "./LiteratureIntake";
import type { Row } from "../workspace/types";
export default function ConnectedLiterature({
  pid,
  tick = 0,
  discover = false,
}: {
  pid: string;
  tick?: number;
  discover?: boolean;
}) {
  const library = useWorkspaceResource<Row>(
    `/projects/${pid}/studio/library`,
    tick,
  );
  if (library.error) return <p role="alert">{library.error}</p>;
  if (!library.data) return <p role="status">正在读取文献…</p>;
  return discover ? (
    <Discover pid={pid} onRefresh={library.load} />
  ) : (
    <LiteratureLibrary
      pid={pid}
      library={library.data}
      onRefresh={library.load}
      onRead={(fileId) =>
        navigateWorkspace(pid, { page: "references", view: "parallel", fileId })
      }
    />
  );
}
