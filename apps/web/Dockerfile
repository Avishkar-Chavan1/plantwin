# ProcessTwin dashboard image used by docker-compose (demo and self-hosted stacks).
# Runs a production build: `next dev` recompiles on demand and re-renders twice in
# strict mode, which made the demo UI feel slow. The build inlines the public API
# origin; it is never a secret.
FROM node:22-alpine AS builder

WORKDIR /app

# Install pnpm (pinned major) and dependencies from the committed lockfile.
RUN npm install -g pnpm@10
COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml* .npmrc* ./
RUN pnpm install --frozen-lockfile

COPY . .

# NEXT_PUBLIC_* variables are inlined into browser JavaScript at build time, so the
# public API origin must be supplied as a build argument for every image build.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

RUN pnpm run build

FROM node:22-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

# Probe the IPv4 loopback explicitly: the standalone server binds 0.0.0.0, while
# `localhost` resolves to ::1 inside the image and that connection is refused.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:3000 || exit 1

CMD ["node", "server.js"]
