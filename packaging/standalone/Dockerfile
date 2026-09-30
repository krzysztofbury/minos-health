# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 AS uv
FROM debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251

# Bookworm's C++ runtime keeps the frozen analyzer compatible with the smoke OS.
RUN apt-get update && apt-get install --no-install-recommends --yes binutils=2.40-2 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /source
COPY pyproject.toml uv.lock README.md LICENSE-MIT LICENSE-APACHE ./
COPY src ./src
COPY database ./database
COPY packaging/standalone/pyproject.toml packaging/standalone/uv.lock ./packaging/standalone/
COPY packaging/standalone/hooks ./packaging/standalone/hooks
COPY packaging/standalone/minos_entry.py ./packaging/standalone/minos_entry.py
COPY scripts/build_standalone_cli.sh ./scripts/build_standalone_cli.sh
ENV UV_PROJECT_ENVIRONMENT=/tmp/minos-builder-environment
