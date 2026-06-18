# Remotion Studio image for the Greffon catalog.
#
# There is no upstream Studio image, so we build our own: a Node base with the
# Chrome Headless Shell system dependencies Remotion needs for preview and
# rendering, plus a minimal starter project. Published to a registry and
# referenced by tag from docker-compose.yml (the greffer never builds images).
#
# Remotion is source-available under the Remotion License, NOT an OSI open
# source license. Free for individuals, non-profits, and for-profit orgs with
# up to 3 employees; larger for-profit orgs need a company license from
# remotion.pro. Deployers are responsible for their own compliance.
FROM node:22-bookworm-slim

# Chrome Headless Shell dependencies (per Remotion's official Docker guide).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libdbus-1-3 \
    libatk1.0-0 \
    libgbm-dev \
    libasound2 \
    libxrandr2 \
    libxkbcommon-dev \
    libxfixes3 \
    libxcomposite1 \
    libxdamage1 \
    libatk-bridge2.0-0 \
    libpango-1.0-0 \
    libcairo2 \
    libcups2 \
  && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=production
WORKDIR /home/remotion/app

# Install dependencies first for layer caching.
COPY package.json ./
RUN npm install --no-audit --no-fund

# Pull in the project sources.
COPY tsconfig.json ./
COPY src ./src

# Pre-download the Chrome Headless Shell so the first render/preview is fast and
# works offline. Cached under the project dir; the named volume preserves it.
RUN npx remotion browser ensure

EXPOSE 3000

# --host 0.0.0.0 so the greffon_nginx proxy can reach Studio across the
# internal network. Disable the open-browser behavior (headless container).
CMD ["npx", "remotion", "studio", "src/index.ts", "--host", "0.0.0.0", "--port", "3000", "--no-open"]
