# syntax=docker/dockerfile:1.7
FROM docker.io/nginxinc/nginx-unprivileged@sha256:b54ac358b83fc6c965793fd271839b4ea4cdb6e99895bb19618cbc2ca152d972
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/krzysztofbury/minos-health" \
      org.opencontainers.image.title="Minos validation reference edge"
COPY --from=edge-artifacts nginx.conf /etc/nginx/nginx.conf
COPY --from=edge-artifacts / /usr/share/nginx/html/
