# Development image: Python 3.12 + Node 22 + git + make + backend test dependencies. No source code inside:
# the repository is mounted at /repo (docker-compose.dev.yml, .devcontainer). Production images: core/ and frontend/.
FROM python:3.12-slim-bookworm
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && apt-get update && apt-get install -y --no-install-recommends git make \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*' \
 && node --version && npm --version
COPY core/requirements.txt core/requirements-dev.txt /tmp/req/
RUN pip install --no-cache-dir -r /tmp/req/requirements-dev.txt
# node_modules is a named volume (Linux binaries, separate from a Mac checkout); writable by any dev user id
RUN mkdir -p /repo/frontend/node_modules && chmod 1777 /repo/frontend/node_modules
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /repo
