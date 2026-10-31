# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 AS uv
FROM docker.io/library/python:3.14.9-slim-bookworm@sha256:945c2676dc2b18e0c1f16879244122c57c0ffede60006bf3df159c507a31bcae AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY database ./database
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra reference --extra api --no-install-project && \
    uv build --wheel --no-sources --out-dir /dist && \
    uv pip install --python .venv/bin/python --no-deps /dist/*.whl

FROM docker.io/library/python:3.14.9-slim-bookworm@sha256:945c2676dc2b18e0c1f16879244122c57c0ffede60006bf3df159c507a31bcae
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/krzysztofbury/minos-health" \
      org.opencontainers.image.title="Minos validation reference API" \
      org.opencontainers.image.licenses="MIT OR Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/nonexistent OTEL_SDK_DISABLED=true
# The pinned base predates the PCRE2, Perl and OpenSSL (CVE-2026-84782)
# security updates. Keep the Debian fixes pinned until the upstream base is
# refreshed.
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
      "libpcre2-8-0=10.42-1+deb12u2" "perl-base=5.36.0-7+deb12u4" \
      "libssl3=3.0.22-1~deb12u2" "openssl=3.0.22-1~deb12u2" && \
    rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/.venv /build/.venv
# The host processor (#141 B3) judges candidates in this image under these
# owner-approved activation policies; they carry no source data or secrets.
COPY manifests/policy /app/policy
# Criterion 4: reviewed questions a candidate must keep answering (#141 C).
COPY tests/evaluation/search-gold-v1.json tests/golden/grouping-cases.json /app/acceptance/
# A validation deployment mounts one pinned, read-only bundle. No raw sources,
# credentials, source archive, compiler or package manager enter this image.
RUN mkdir -p /app/data && \
    rm -rf /usr/local/bin/pip* /usr/local/lib/python3.14/ensurepip \
    /usr/local/lib/python3.14/site-packages/pip* /usr/bin/apt* /bin/sh /bin/bash
USER 10004:10004
WORKDIR /app
ENTRYPOINT ["/build/.venv/bin/uvicorn", "minos.http.app:application", "--factory", "--host", "0.0.0.0", "--port", "8081", "--workers", "1", "--limit-concurrency", "64", "--timeout-keep-alive", "5", "--no-access-log", "--log-level", "critical"]
