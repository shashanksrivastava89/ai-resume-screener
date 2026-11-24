import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: Vite serves the SPA on :5173 and proxies /api to the FastAPI app on :8000.
// Build: static bundle in web/dist, which FastAPI serves in production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      // Local Entra SSO (LOCAL_SSO=1) serves /.auth/login/aad, its callback and
      // /.auth/me from FastAPI — the same paths Easy Auth serves hosted. Without
      // this, sign-in from the dev server 404s in Vite instead of reaching the app.
      //
      // changeOrigin MUST stay false here. It rewrites the Host header to the proxy
      // target, so FastAPI would build the OAuth redirect_uri from 127.0.0.1:8000 —
      // which is not a registered redirect URI (AADSTS50011) and would also dump you
      // on :8000 after sign-in instead of back on the dev server. Host must stay
      // localhost:5173, which IS registered.
      "/.auth": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
