# web/Dockerfile
#
# Multi-stage build using Next.js standalone output.
# Standalone mode bundles only the files needed to run the server,
# making the final image ~80% smaller than a full node_modules copy.
#
# Uses node:22-slim (Debian/glibc) NOT node:22-alpine.
# Reason: lightningcss (used by Tailwind CSS 4) ships native .node binaries
# per platform. npm on Alpine (musl) fails to detect the libc variant reliably,
# so the linux-x64-musl binary is never installed. On Debian (glibc) both npm
# and Node.js agree on linux-x64-gnu and everything resolves correctly.
#
# NEXT_PUBLIC_* vars are embedded at build time — pass them as build args.
# BACKEND_URL is a server-side runtime var read by next.config.ts.

# ── Stage 1: install deps ───────────────────────────────────────────────────
FROM node:22-slim AS deps

WORKDIR /app

# Copy only package.json — NOT the lockfile.
# The lockfile is generated on Windows and records only win32 optional deps
# (lightningcss-win32-x64-msvc). If we copy it here, npm on Linux follows it
# and never installs lightningcss-linux-x64-gnu, causing the next build to fail.
# Without a lockfile, npm resolves optional deps fresh for the Linux target.
COPY package.json ./
RUN npm install


# ── Stage 2: build ──────────────────────────────────────────────────────────
FROM node:22-slim AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* vars must be present at build time so Next.js can embed them
# into the client bundle. Pass via docker-compose build.args or --build-arg.
ARG NEXT_PUBLIC_BASE_URL
ARG NEXT_PUBLIC_KEYCLOAK_URL
ARG NEXT_PUBLIC_KEYCLOAK_REALM
ARG NEXT_PUBLIC_KEYCLOAK_CLIENT_ID
# BACKEND_URL must be a build arg, not a runtime env var.
# next.config.ts rewrites() is evaluated at build time and written into
# routes-manifest.json. A runtime env var has no effect on it.
ARG BACKEND_URL=http://backend:8091

ENV NEXT_PUBLIC_BASE_URL=$NEXT_PUBLIC_BASE_URL
ENV NEXT_PUBLIC_KEYCLOAK_URL=$NEXT_PUBLIC_KEYCLOAK_URL
ENV NEXT_PUBLIC_KEYCLOAK_REALM=$NEXT_PUBLIC_KEYCLOAK_REALM
ENV NEXT_PUBLIC_KEYCLOAK_CLIENT_ID=$NEXT_PUBLIC_KEYCLOAK_CLIENT_ID
ENV BACKEND_URL=$BACKEND_URL

RUN npm run build


# ── Stage 3: runtime ────────────────────────────────────────────────────────
FROM node:22-slim AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV PORT=5500
ENV HOSTNAME="0.0.0.0"

RUN groupadd --gid 1001 --system nodejs \
    && useradd  --uid 1001 --system --gid nodejs nextjs

# Standalone output: .next/standalone is self-contained (no node_modules needed)
COPY --from=builder /app/public                          ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static     ./.next/static

USER nextjs

EXPOSE 5500

# BACKEND_URL is a server-side env var consumed by next.config.ts rewrites.
# Set it at runtime via docker-compose environment: or -e flag.
CMD ["node", "server.js"]
