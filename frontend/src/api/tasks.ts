import { api, apiUrl, send } from "./client";
import { pathSegment } from "./paths";

export interface Task { id: string; kind: string; status: string; progress: number; }
export const tasksApi = {
  get: (id: string): Promise<Task> => api(`/tasks/${pathSegment(id)}`),
  cancel: (id: string): Promise<Task> => send(`/tasks/${pathSegment(id)}/cancel`),
  eventsUrl: (id: string) => apiUrl(`/tasks/${pathSegment(id)}/events`),
};
