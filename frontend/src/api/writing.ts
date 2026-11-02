import { api, send } from "./client";
import { pathSegment } from "./paths";
import { projectPath } from "./projects";

export const writingApi = (projectId: string) => {
  const base = projectPath(projectId) + "/writing";
  return {
    sessions: () => api(base + "/sessions"),
    session: (id: string) => api(base + "/sessions/" + pathSegment(id)),
    start: (command: unknown) => send(base + "/start", command),
    preflight: (sectionId: string) => api(base + "/preflight?section_id=" + encodeURIComponent(sectionId)),
  };
};
