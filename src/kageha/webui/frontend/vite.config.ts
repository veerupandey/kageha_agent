import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import type { IncomingMessage, ServerResponse } from "node:http";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Cloudspaces exposes the dev server through a generated subdomain.
    allowedHosts: [".cloudspaces.litng.ai"],
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8788",
        changeOrigin: true,
        // Long-running agent turns + SSE — do not time out mid-stream.
        timeout: 0,
        proxyTimeout: 0,
        configure: (proxy) => {
          proxy.on(
            "proxyRes",
            (
              proxyRes: IncomingMessage,
              _req: IncomingMessage,
              res: ServerResponse,
            ) => {
              const ctype = String(proxyRes.headers["content-type"] || "");
              if (!ctype.includes("text/event-stream")) return;
              // Keep Content-Type — do NOT flushHeaders early (that drops it).
              if (!res.getHeader("Content-Type")) {
                res.setHeader(
                  "Content-Type",
                  ctype || "text/event-stream; charset=utf-8",
                );
              }
              res.setHeader("Cache-Control", "no-cache, no-transform");
              res.setHeader("X-Accel-Buffering", "no");
              // Content-Length would force full buffering of the stream.
              delete proxyRes.headers["content-length"];
            },
          );
        },
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
});
