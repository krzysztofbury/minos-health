# syntax=docker/dockerfile:1.7
# Nginx 1.31.6 / Alpine, refreshed with libexpat 2.8.5-r0.
FROM docker.io/nginxinc/nginx-unprivileged@sha256:c18d735d33a1c3ccb5ef201d504e5b4aa4d003e81f034b329011267f4c4d0f58
# CVE-2026-103111: the newest upstream digest still ships pcre2 10.48-r0.
# Pin the fixed Alpine package until upstream rebuilds, then drop this layer.
USER root
RUN apk add --no-cache "pcre2=10.49-r0"
USER 101
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/krzysztofbury/minos-health" \
      org.opencontainers.image.title="Minos validation reference edge"
COPY --from=edge-artifacts nginx.conf /etc/nginx/nginx.conf
COPY --from=edge-artifacts / /usr/share/nginx/html/
