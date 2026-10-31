# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv
FROM docker.io/library/python:3.14.10-slim-bookworm@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527 AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY database ./database
# The same uv-managed Python as development and CI, for the same SQLite: query
# plans differ between versions, and Debian's 3.40 failed search in production
# while every test passed on 3.53 (#294). scripts/smoke_reference_web.py compares.
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed
RUN --mount=type=cache,target=/root/.cache/uv \
    uv python install 3.14.9 && \
    uv sync --frozen --no-dev --extra reference --extra api --no-install-project --python 3.14.9 && \
    uv build --wheel --no-sources --out-dir /dist && \
    uv pip install --python .venv/bin/python --no-deps /dist/*.whl && \
    python_home=$(echo /opt/python/cpython-3.14.9-*) && \
    rm -rf "$python_home/lib/python3.14/test" "$python_home/lib/python3.14/idlelib" \
    "$python_home/lib/python3.14/tkinter" "$python_home/lib/python3.14/turtledemo" \
    "$python_home/lib/python3.14/ensurepip" "$python_home/lib/python3.14/site-packages/pip"* \
    "$python_home/lib/libpython3.14.a" "$python_home/lib/tcl"* "$python_home/lib/tk"* \
    "$python_home/lib/itcl"* "$python_home/lib/thread"* "$python_home/lib/libtcl"* \
    "$python_home/lib/libtk"* "$python_home/include" && \
    find "$python_home" -name '*.a' -delete && \
    find "$python_home" -name __pycache__ -prune -exec rm -rf {} +

FROM docker.io/library/python:3.14.10-slim-bookworm@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/krzysztofbury/minos-health" \
      org.opencontainers.image.title="Minos validation reference API" \
      org.opencontainers.image.licenses="MIT OR Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/nonexistent OTEL_SDK_DISABLED=true
# The pinned base predates the PCRE2, Perl and OpenSSL (CVE-2026-84782)
# security updates. Keep the Debian fixes pinned until the upstream base is
# refreshed. The runtime does not load Debian's libssl: the managed Python links
# its own OpenSSL and SQLite, which the image scan does not see (#301).
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
      "libpcre2-8-0=10.42-1+deb12u2" "perl-base=5.36.0-7+deb12u4" \
      "libssl3=3.0.22-1~deb12u2" "openssl=3.0.22-1~deb12u2" && \
    rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/python /opt/python
COPY --from=builder /build/.venv /build/.venv
# The host processor (#141 B3) judges candidates in this image under these
# owner-approved activation policies; they carry no source data or secrets.
COPY manifests/policy /app/policy
# Criterion 4: reviewed questions a candidate must keep answering (#141 C).
COPY tests/evaluation/search-gold-v1.json tests/golden/grouping-cases.json /app/acceptance/
# A validation deployment mounts one pinned, read-only bundle. No raw sources,
# credentials, source archive, compiler or package manager enter this image.
RUN mkdir -p /app/data && \
    rm -rf /usr/local/bin/* /usr/local/lib/python3.14 /usr/local/lib/libpython* \
    /usr/local/include /usr/bin/apt* /bin/sh /bin/bash
USER 10004:10004
WORKDIR /app
ENTRYPOINT ["/build/.venv/bin/uvicorn", "minos.http.app:application", "--factory", "--host", "0.0.0.0", "--port", "8081", "--workers", "1", "--limit-concurrency", "64", "--timeout-keep-alive", "5", "--no-access-log", "--log-level", "critical"]
