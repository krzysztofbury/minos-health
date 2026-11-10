# syntax=docker/dockerfile:1.7
# Nginx 1.31.7 / Alpine 3.24.2 linux/amd64, with libexpat 2.9.0-r0, libx11 1.8.14-r0, libxslt 1.1.43-r3 and tiff 4.7.2-r0.
FROM docker.io/nginxinc/nginx-unprivileged:1.31.7-alpine@sha256:b5047b9b76845eac124358373c6852377c52e4b05834a47647e955dd91cb8f1e
# CVE-2026-111497, CVE-2026-92392, CVE-2026-117610, CVE-2026-118370,
# CVE-2026-107891, CVE-2026-85091, CVE-2026-87636 and CVE-2026-9947: the newest
# upstream digest still ships brotli-libs 1.2.0-r1, curl 8.22.0-r0, libexpat
# 2.9.0-r0, libxslt 1.1.43-r3, musl 1.2.6-r2 and zlib 1.3.2-r0. Pin the fixed
# Alpine packages until upstream rebuilds, then drop this layer. Alpine 3.24 carries the zlib fix as 1.4-r0.
USER root
RUN apk add --no-cache "brotli-libs=1.2.1-r0" "curl=8.23.0-r0" "libcurl=8.23.0-r0" \
    "libexpat=2.10.0-r0" "libxslt=1.1.43-r4" "musl=1.2.6-r3" "musl-utils=1.2.6-r3" \
    "zlib=1.4-r0"
USER 101
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/krzysztofbury/minos-health" \
      org.opencontainers.image.title="Minos validation reference edge"
COPY --from=edge-artifacts nginx.conf /etc/nginx/nginx.conf
COPY --from=edge-artifacts / /usr/share/nginx/html/
