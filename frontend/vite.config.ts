import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The API is served from the app's own origin: in development this server forwards API
// paths to the backend on port 8000, and deployed environments put both behind one proxy.
const apiTarget = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  // Only PF_WEB_* variables reach browser code (import.meta.env).
  envPrefix: "PF_WEB_",
  server: {
    // Must match PF_WEB_ORIGIN, which the identity provider's client allows as a redirect origin.
    port: 5173,
    strictPort: true,
    proxy: {
      "/v1": apiTarget,
      "/health": apiTarget,
    },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
