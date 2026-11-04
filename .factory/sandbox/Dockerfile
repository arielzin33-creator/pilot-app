# Full-stack sandbox image (Phase B deliverable 1; spec §10.2, PRAC-07, PRAC-08).
# Node 22, Playwright Chromium with its system libraries, bubblewrap for sandbox-exec.sh, and
# the repository's dependencies warmed into the image so a run starts in seconds.
#
# Pin the base by digest before relying on it (supply-chain rule 5, spec §6.5):
#   docker buildx imagetools inspect mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-noble
# and replace the tag below with  @sha256:<digest>.  PLAYWRIGHT_VERSION must equal the
# repository's @playwright/test version so the bundled browser matches.
ARG PLAYWRIGHT_VERSION=1.63.0
FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-noble

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends bubblewrap socat git python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root user; the factory never runs agent code as root.
RUN useradd --create-home --uid 10001 factory
USER factory
WORKDIR /home/factory/app

# Warm dependency cache from the lockfile only (no install scripts, no source code).
COPY --chown=factory:factory package.json package-lock.json ./
RUN npm ci --ignore-scripts && npm cache verify

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright CI=true
