# syntax=docker/dockerfile:1.7
# Nginx 1.31.6 / Alpine, refreshed with libexpat 2.8.5-r0.
FROM docker.io/nginxinc/nginx-unprivileged@sha256:c18d735d33a1c3ccb5ef201d504e5b4aa4d003e81f034b329011267f4c4d0f58
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/krzysztofbury/minos-health" \
      org.opencontainers.image.title="Minos validation reference edge"
COPY --from=edge-artifacts nginx.conf /etc/nginx/nginx.conf
COPY --from=edge-artifacts / /usr/share/nginx/html/
