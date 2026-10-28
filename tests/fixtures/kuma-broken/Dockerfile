# A real Uptime Kuma that writes to its data directory but never reports
# healthy, for scripts/smoke_kuma_deploy.py (#274). BASE is the pinned image
# from packaging/runtime-pins, already pulled by the smoke.
ARG BASE
FROM ${BASE}
USER root
RUN printf '#!/bin/sh\nexit 1\n' > /app/extra/healthcheck \
 && printf '#!/bin/sh\n: > /app/data/candidate-ran\nexec "$@"\n' > /usr/local/bin/mark-data \
 && chmod 0755 /app/extra/healthcheck /usr/local/bin/mark-data
USER node
ENTRYPOINT ["/usr/bin/dumb-init", "--", "/usr/local/bin/mark-data"]
CMD ["node", "server/server.js"]
