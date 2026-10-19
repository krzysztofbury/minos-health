# syntax=docker/dockerfile:1.7
# Nginx 1.31.7 / Alpine 3.24.2 linux/amd64, with libexpat 2.9.0-r0, libx11 1.8.14-r0 and tiff 4.7.2-r0.
FROM docker.io/nginxinc/nginx-unprivileged@sha256:c3bcf3d1a53a027bfa515b9fe4fbbe7bf6b791476a5af853de439ad494b145bd
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/krzysztofbury/minos-health" \
      org.opencontainers.image.title="Minos validation reference edge"
COPY --from=edge-artifacts nginx.conf /etc/nginx/nginx.conf
COPY --from=edge-artifacts / /usr/share/nginx/html/
