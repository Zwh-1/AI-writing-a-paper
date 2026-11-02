import { api, send } from "./client";
export type ModelProfileId = "primary" | "material-summary" | "gpt-fallback" | "image-generation";
export const providersApi = {
  get: (id: ModelProfileId) => api(`/model-profiles/${id}`),
  save: (id: ModelProfileId, configuration: unknown) => send(`/model-profiles/${id}`, configuration, "PUT"),
};
