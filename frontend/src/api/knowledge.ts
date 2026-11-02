import { api, send } from "./client";
import { projectPath } from "./projects";

export const knowledgeApi = (projectId: string) => {
  const base = projectPath(projectId) + "/knowledge";
  return {
    status: () => api(base + "/status"),
    sources: () => api(base + "/sources"),
    query: (command: unknown) => send(base + "/query", command),
  };
};
