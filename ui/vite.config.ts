import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into ../ui-host/dist so the SPX-deployed FastAPI host can serve it.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../ui-host/dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      // Dev proxy: forward API calls to the local server on :8080
      "/rooms": "http://localhost:8080",
      "/llm": "http://localhost:8080",
      "/healthz": "http://localhost:8080",
      "/sse-test": "http://localhost:8080",
    },
  },
});
