# Stand-in cloudflared for scripts/smoke_tunnel_deploy.py (#273). The base is
# the watcher's pinned Python image, already present in CI.
FROM docker.io/library/python:3.14.7-alpine3.23@sha256:976bfed94200c6377b094feb91b7b1bd4849c8a4e3a7b2a364d95ddf1f1c6e01
ARG STANDIN_VARIANT
ENV STANDIN_VARIANT=${STANDIN_VARIANT} STANDIN_VERSION=2026.10.4
COPY --chmod=0755 cloudflared.py /usr/local/bin/cloudflared
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/cloudflared"]
