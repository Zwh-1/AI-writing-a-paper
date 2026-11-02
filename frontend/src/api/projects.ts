import { api, send } from "./client";
import { pathSegment } from "./paths";

export interface Project { id: string; title: string; archived: number; }
export const projectPath = (id: string) => `/projects/${pathSegment(id)}`;
export const projectsApi = {
  list: (): Promise<Project[]> => api("/projects"),
  get: (id: string): Promise<Project> => api(projectPath(id)),
  create: (title: string): Promise<Project> => send("/projects", { title }),
};
