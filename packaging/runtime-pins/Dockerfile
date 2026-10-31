# Never built. Dependabot reads these FROM lines, so an upstream release opens
# a PR; a test requires every pin of these images in the Compose template,
# site.yml, the Kuma bootstrap Compose and CI to equal the digest here (#273).
# Change them together, then release cloudflared with scripts/deploy_tunnel.py
# and Uptime Kuma with scripts/deploy_kuma.py (#274). Kuma pins the multi-arch
# index digest that Dependabot tracks; Compose selects linux/amd64.
FROM docker.io/cloudflare/cloudflared:2026.10.5@sha256:1b1d34d0a9c0bfca9de72db8460d328db3f19edd2b96169114e3e893a97bbe53
FROM docker.io/louislam/uptime-kuma:2.5.8-slim-rootless@sha256:970f01d661de548b88c7e6012d3574666d033b1de6a323fc1bebfc3cb3b4c437
