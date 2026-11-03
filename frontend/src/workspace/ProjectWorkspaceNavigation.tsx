import WorkspaceNavigation from "./WorkspaceNavigation";
import { workspaceForPage } from "./projectNavigation";

export default function ProjectWorkspaceNavigation({
  page,
  onNavigate,
}: {
  page: string;
  onNavigate: (page: string) => void;
}) {
  const group = workspaceForPage(page);
  if (!group) return null;
  return (
    <WorkspaceNavigation
      label={`${group.label}工作区`}
      introduction={<><group.icon size={14} />{group.description}</>}
      note={group.note}
      destinations={group.pages}
      current={page}
      onNavigate={onNavigate}
    />
  );
}
