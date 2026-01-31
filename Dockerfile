# syntax=docker/dockerfile:1.7
ARG OPENHANDS_BUILD_VERSION=railway
ARG NODE_VERSION=22.12.0
ARG PYTHON_VERSION=3.12.12

FROM node:${NODE_VERSION}-bookworm-slim AS frontend-builder

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend ./
RUN npm run build

FROM python:${PYTHON_VERSION}-slim-bookworm AS base
FROM base AS backend-builder

WORKDIR /app
ENV PYTHONPATH="/app" \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

RUN apt-get update -y \
    && apt-get install -y --no-install-recommends \
        curl \
        make \
        git \
        build-essential \
        jq \
        gettext \
        tmux \
    && python -m pip install --no-cache-dir poetry \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock ./
RUN touch README.md
RUN poetry install --no-root && rm -rf $POETRY_CACHE_DIR

FROM base AS openhands-app

WORKDIR /app

ARG OPENHANDS_BUILD_VERSION

ENV RUN_AS_OPENHANDS=true \
    OPENHANDS_USER_ID=42420 \
    SANDBOX_LOCAL_RUNTIME_URL=http://host.docker.internal \
    USE_HOST_NETWORK=false \
    WORKSPACE_BASE=/opt/workspace_base \
    OPENHANDS_BUILD_VERSION=$OPENHANDS_BUILD_VERSION \
    SANDBOX_USER_ID=0 \
    FILE_STORE=local \
    FILE_STORE_PATH=/.openhands \
    OH_PERSISTENCE_DIR=/.openhands \
    INIT_GIT_IN_EMPTY_WORKSPACE=1 \
    RUNTIME=local \
    ENABLE_BROWSER=false \
    DISABLE_VSCODE_PLUGIN=true \
    SKIP_DEPENDENCY_CHECK=1 \
    PROCESS_SANDBOX_STARTUP_TIMEOUT=120 \
    PROCESS_SANDBOX_INHERIT_IO=1 \
    OH_AGENT_SERVER_ENV='{"OH_PRELOAD_TOOLS":"false","OH_ENABLE_VSCODE":"false","OH_ENABLE_VNC":"false"}' \
    PYTHONUNBUFFERED=1 \
    PORT=3000 \
    SERVE_FRONTEND=true

RUN mkdir -p $FILE_STORE_PATH
RUN mkdir -p $WORKSPACE_BASE

RUN apt-get update -y \
    && apt-get install -y --no-install-recommends curl ssh sudo bash git tmux \
    && rm -rf /var/lib/apt/lists/*

RUN sed -i 's/^UID_MIN.*/UID_MIN 499/' /etc/login.defs \
    && sed -i 's/^UID_MAX.*/UID_MAX 1000000/' /etc/login.defs

RUN groupadd --gid $OPENHANDS_USER_ID openhands
RUN useradd -l -m -u $OPENHANDS_USER_ID --gid $OPENHANDS_USER_ID -s /bin/bash openhands && \
    usermod -aG openhands openhands && \
    usermod -aG sudo openhands && \
    echo '%sudo ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
RUN chown -R openhands:openhands /app && chmod -R 770 /app
RUN chown -R openhands:openhands $WORKSPACE_BASE && chmod -R 770 $WORKSPACE_BASE
USER openhands

ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app"

COPY --chown=openhands:openhands --chmod=770 --from=backend-builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}

COPY --chown=openhands:openhands --chmod=770 ./skills ./skills
COPY --chown=openhands:openhands --chmod=770 ./openhands ./openhands
COPY --chown=openhands:openhands --chmod=777 ./openhands/runtime/plugins ./openhands/runtime/plugins
COPY --chown=openhands:openhands pyproject.toml poetry.lock README.md MANIFEST.in LICENSE ./

# This is run as "openhands" user, and will create __pycache__ with openhands:openhands ownership
RUN python openhands/core/download.py
# Ensure group ownership on any stray files
RUN find /app \! -group openhands -exec chgrp openhands {} +

COPY --chown=openhands:openhands --chmod=770 --from=frontend-builder /app/build ./frontend/build
COPY --chown=openhands:openhands --chmod=770 ./containers/app/entrypoint.sh /app/entrypoint.sh

USER root

WORKDIR /app

EXPOSE 3000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["bash","-lc","/app/.venv/bin/python -m uvicorn openhands.server.listen:app --host 0.0.0.0 --port ${PORT:-3000}"]
