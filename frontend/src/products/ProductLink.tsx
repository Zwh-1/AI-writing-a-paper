import { navigateWorkspace } from "../workspace/WorkspaceContext";
export default function ProductLink({
  pid,
  sectionId,
  kind,
  identity,
  label = "复用项目产物",
}: {
  pid: string;
  sectionId?: string;
  kind?: string;
  identity?: string;
  label?: string;
}) {
  return (
    <button
      className="btn"
      onClick={() =>
        navigateWorkspace(pid, {
          page: "products",
          sectionId,
          productType: kind,
          productId: identity,
        })
      }
    >
      {label}
    </button>
  );
}
