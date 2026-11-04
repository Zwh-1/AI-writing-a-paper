import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
let runtimeBuild: Record<string, unknown> | null = null;
try { runtimeBuild = JSON.parse(readFileSync(new URL("./public/build-info.json", import.meta.url), "utf8")); } catch {}
export default defineConfig({
  plugins: [react()],
  define: { __RUNTIME_FRONTEND_BUILD__: JSON.stringify(runtimeBuild) },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  build: {
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules")) {
            if (id.includes("@tiptap") || id.includes("prosemirror"))
              return "editor";
            if (id.includes("react")) return "react";
            if (id.includes("lucide")) return "icons";
          }
        },
      },
    },
  },
});
