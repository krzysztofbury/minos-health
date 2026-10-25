# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv
FROM debian@sha256:4c25275446ca371766b8b093c696782eabce06263fccacb8d5d813b3dc44b308

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
