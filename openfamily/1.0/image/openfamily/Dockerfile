# Combined OpenFamily image (client + server, single origin) for the greffon
# catalog. Pure packaging of unmodified upstream source at the pinned tag; only
# flags upstream already supports are flipped (empty VITE_* -> same-origin
# client, SERVE_CLIENT_DIR -> server serves the SPA). No source fork.
#
# Self-contained on purpose: the build context is just this directory, so both
# the PR smoke CI (`docker build <dir>`) and the publish workflow build it the
# same way. Bumping upstream = update OPENFAMILY_TAG + OPENFAMILY_SHA256 here,
# in ../openfamily-db/Dockerfile, the ../../docker-compose.yml image tags, and
# the publish workflow's tags (see ../README.md).
ARG OPENFAMILY_TAG=v1.2.0
# sha256 of the release tarball: fail the build loudly if the tag is ever
# force-moved (or the tarball otherwise changes) instead of silently building
# different source under the same pinned name.
ARG OPENFAMILY_SHA256=99c0ec418b8d7b589a13aa007d6c79a4168626f2a7b427aae8afbb8acfad7b52

# ---- upstream source at the pinned release tag ----
FROM alpine:3.20 AS src
ARG OPENFAMILY_TAG
ARG OPENFAMILY_SHA256
ADD --checksum=sha256:${OPENFAMILY_SHA256} https://github.com/NexaFlowFrance/OpenFamily/archive/refs/tags/${OPENFAMILY_TAG}.tar.gz /tmp/src.tar.gz
RUN mkdir -p /src && tar -xzf /tmp/src.tar.gz -C /src --strip-components=1

# Upstream is an npm-workspaces monorepo (client/server/shared) with a root
# lockfile; `npm ci` against it makes every build (PR smoke, publish, local)
# install the exact dependency tree upstream released, instead of re-resolving
# on each build.

# ---- client build (host-agnostic: empty VITE_* -> same-origin) ----
FROM node:20-alpine AS client
WORKDIR /app
COPY --from=src /src/package.json /src/package-lock.json ./
COPY --from=src /src/client/package.json ./client/
COPY --from=src /src/server/package.json ./server/
COPY --from=src /src/shared/package.json ./shared/
RUN npm ci --legacy-peer-deps
COPY --from=src /src/shared ./shared
COPY --from=src /src/client ./client
COPY --from=src /src/tsconfig.json ./
RUN cd shared && npm run build
ENV VITE_API_URL=
ENV VITE_WS_URL=
RUN cd client && npm run build

# ---- server build ----
FROM node:20-alpine AS server
WORKDIR /app
COPY --from=src /src/package.json /src/package-lock.json ./
COPY --from=src /src/client/package.json ./client/
COPY --from=src /src/server/package.json ./server/
COPY --from=src /src/shared/package.json ./shared/
RUN npm ci --legacy-peer-deps
COPY --from=src /src/shared ./shared
COPY --from=src /src/server ./server
COPY --from=src /src/tsconfig.json ./
RUN cd shared && npm run build && cd ../server && npm run build

# ---- runtime ----
FROM node:20-alpine
WORKDIR /app
COPY --from=src /src/package.json /src/package-lock.json ./
COPY --from=src /src/client/package.json ./client/
COPY --from=src /src/server/package.json ./server/
COPY --from=src /src/shared/package.json ./shared/
RUN npm ci --omit=dev --legacy-peer-deps
COPY --from=server /app/shared/dist ./shared/dist
COPY --from=server /app/server/dist ./server/dist
COPY --from=client /app/client/dist ./client
# Run unprivileged: node:20-alpine ships a 'node' user. uploads/ must be writable.
RUN mkdir -p /app/uploads && chown -R node:node /app/uploads
ENV SERVE_CLIENT_DIR=/app/client
USER node
EXPOSE 3001
CMD ["node", "server/dist/index.js"]
