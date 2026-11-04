# Never built. Dependabot reads these FROM lines, so an upstream release opens
# a PR; a test requires every pin of these images in the Compose template,
# site.yml, the Kuma bootstrap Compose and CI to equal the digest here (#273).
# Change them together, then release cloudflared with scripts/deploy_tunnel.py
# and Uptime Kuma with scripts/deploy_kuma.py (#274). Kuma pins the multi-arch
# index digest that Dependabot tracks; Compose selects linux/amd64.
FROM docker.io/cloudflare/cloudflared:2026.10.7@sha256:4955524aeaa379e154023d5a027c250f7b35041a2e7e950cf13f144bc9b9c5ba
FROM docker.io/louislam/uptime-kuma:2.5.9-slim-rootless@sha256:053a48927ee714b1da78c6810041a44a266bd8c06dc30ead5b3f459d0f219fb0
