// web/next.config.ts
//
// PURPOSE:
//   Next.js configuration for the InsightX frontend.
//
// API + AUTH PROXY (DEVELOPMENT):
//   In dev, Next.js forwards /api/* requests to the FastAPI backend.
//   This keeps all cookies first-party on localhost:5500 — the BFF auth flow
//   relies on this: /api/auth/callback sets HttpOnly cookies via the proxy,
//   so subsequent /api/* calls carry them automatically.
//
//   The backend port is read from NEXT_PUBLIC_BASE_URL so the proxy stays in
//   sync with the URL that the browser uses for direct env-var-based calls.
//   Default: http://localhost:8091
//
//   Production: remove rewrites() — NGINX handles routing instead.
//
// allowedDevOrigins:
//   Permits the dev server to accept connections from a local network IP
//   (useful when developing on a VM or accessing from another device on LAN).

import type { NextConfig } from "next";

// In Docker: BACKEND_URL=http://backend:8091 (internal service name, set via docker-compose)
// In local dev: falls back to NEXT_PUBLIC_BASE_URL (e.g. http://localhost:8091)
// BACKEND_URL must NOT be NEXT_PUBLIC_ — it is server-side only and must never
// reach the browser bundle.
const backendOrigin = (
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_BASE_URL ??
  "http://localhost:8091"
).replace(/\/+$/, "");

const nextConfig: NextConfig = {
  // Required by the multi-stage Dockerfile — produces .next/standalone which
  // is a self-contained server bundle (no node_modules copy needed).
  output: "standalone",

  // LLM pipelines (Ollama SQL generation + target DB execution) can take
  // 60-120 s. The default Next.js rewrite proxy timeout is ~30 s, which
  // causes ECONNRESET before the backend finishes. 180 s gives enough headroom.
  experimental: {
    proxyTimeout: 180_000,
  },

  // Allow local-network access to the dev server (e.g. from a VM or phone)
  allowedDevOrigins: ["10.11.200.109", "localhost", "10.11.200.99"],

  // Forward /api/* → FastAPI backend
  // In Docker: proxies to the internal backend service via BACKEND_URL.
  // In local dev: proxies to NEXT_PUBLIC_BASE_URL (same effect, different address).
  // Production with NGINX: remove this block — NGINX handles routing instead.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
