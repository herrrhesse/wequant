import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// /api goes to `wequant serve` (127.0.0.1 only, no CORS). Its port comes from WQ_API_PORT (environment or .env.local);
// without it there is no proxy and the pages say the backend is not running.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "WQ_");
  const proxy = env.WQ_API_PORT ? { "/api": { target: `http://127.0.0.1:${env.WQ_API_PORT}`, changeOrigin: false } } : undefined;
  return {
    plugins: [react()],
    base: "./",
    server: { host: "127.0.0.1", port: Number(env.WQ_WEB_PORT || 5173), proxy },
    preview: { host: "127.0.0.1", port: Number(env.WQ_WEB_PORT || 4173), proxy },
  };
});
