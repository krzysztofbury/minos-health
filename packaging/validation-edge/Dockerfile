# syntax=docker/dockerfile:1.7
# Nginx 1.31.7 / Alpine 3.24.2 linux/amd64, with libexpat 2.9.0-r0, libx11 1.8.14-r0 and tiff 4.7.2-r0.
FROM docker.io/nginxinc/nginx-unprivileged@sha256:c3bcf3d1a53a027bfa515b9fe4fbbe7bf6b791476a5af853de439ad494b145bd
# CVE-2026-111497: the newest upstream digest still ships brotli-libs 1.2.0-r1.
# Pin the fixed Alpine package until upstream rebuilds, then drop this layer.
USER root
RUN apk add --no-cache "brotli-libs=1.2.1-r0"
USER 101
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/krzysztofbury/minos-health" \
      org.opencontainers.image.title="Minos validation reference edge"
COPY --from=edge-artifacts nginx.conf /etc/nginx/nginx.conf
COPY --from=edge-artifacts / /usr/share/nginx/html/
