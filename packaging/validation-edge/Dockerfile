# syntax=docker/dockerfile:1.7
# Nginx 1.31.6 / Alpine 3.24.2, upstream rebuild with pcre2 10.49-r0 and libexpat 2.8.5-r0.
FROM docker.io/nginxinc/nginx-unprivileged@sha256:b9241c6e7b8e9a862f129d8d4199ab64b10390949a78bdd5603379b32c844083
# CVE-2026-4775: the newest upstream digest still ships tiff 4.7.1-r0.
# Pin the fixed Alpine package until upstream rebuilds, then drop this layer.
USER root
RUN apk add --no-cache "tiff=4.7.2-r0"
USER 101
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/krzysztofbury/minos-health" \
      org.opencontainers.image.title="Minos validation reference edge"
COPY --from=edge-artifacts nginx.conf /etc/nginx/nginx.conf
COPY --from=edge-artifacts / /usr/share/nginx/html/
