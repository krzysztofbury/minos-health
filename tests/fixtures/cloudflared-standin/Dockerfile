# Stand-in cloudflared for scripts/smoke_tunnel_deploy.py (#273). The base is
# the watcher's pinned Python image, already present in CI.
FROM docker.io/library/python:3.14.10-alpine3.23@sha256:373c4d777c20616b78ca24018e710e3fc8c97eac8d4584ef92a57006303e9936
ARG STANDIN_VARIANT
ENV STANDIN_VARIANT=${STANDIN_VARIANT} STANDIN_VERSION=2026.10.4
COPY --chmod=0755 cloudflared.py /usr/local/bin/cloudflared
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/cloudflared"]
