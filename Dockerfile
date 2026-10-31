# syntax=docker/dockerfile:1.7

ARG TARGETPLATFORM=linux/amd64
ARG PYTHON_IMAGE=docker.io/library/python:3.14.7-alpine3.23@sha256:976bfed94200c6377b094feb91b7b1bd4849c8a4e3a7b2a364d95ddf1f1c6e01
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff

FROM --platform=$TARGETPLATFORM ${UV_IMAGE} AS uv

FROM --platform=$TARGETPLATFORM ${PYTHON_IMAGE} AS wheel-builder
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY database ./database

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project && \
    uv build --wheel --no-sources --out-dir /dist && \
    uv pip install --python /app/.venv/bin/python --no-deps /dist/*.whl

FROM --platform=$TARGETPLATFORM ${PYTHON_IMAGE} AS watcher

ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Minos watcher" \
      org.opencontainers.image.description="Minos source watcher and scheduler" \
      org.opencontainers.image.licenses="MIT OR Apache-2.0" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/krzysztofbury/minos-health"

ENV HOME=/nonexistent \
    PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=wheel-builder /app/.venv /app/.venv

RUN addgroup -S -g 10001 minos-watcher && \
    adduser -S -D -H -u 10001 -G minos-watcher -h /nonexistent \
      -s /sbin/nologin minos-watcher && \
    mkdir -p \
      /var/lib/minos/control \
      /var/lib/minos/archive \
      /var/lib/minos/public \
      /var/lib/minos/staging && \
    chown 10001:10001 \
      /var/lib/minos/control \
      /var/lib/minos/archive \
      /var/lib/minos/public \
      /var/lib/minos/staging && \
    chmod 0700 \
      /var/lib/minos/control \
      /var/lib/minos/archive \
      /var/lib/minos/staging && \
    chmod 0755 /var/lib/minos/public && \
    rm -rf \
      /bin/ash \
      /bin/busybox \
      /bin/sh \
      /etc/apk \
      /root/.cache \
      /sbin/apk \
      /usr/local/bin/pip* \
      /usr/local/lib/python3.14/ensurepip \
      /usr/local/lib/python3.14/site-packages/pip* \
      /var/cache/apk

USER 10001:10001
WORKDIR /var/lib/minos
STOPSIGNAL SIGTERM
ENTRYPOINT ["/app/.venv/bin/minos"]
CMD ["watcher", "run"]
