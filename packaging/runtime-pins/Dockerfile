# Never built. Dependabot reads these FROM lines, so an upstream release opens
# a PR; a test requires every pin of these images in the Compose template,
# site.yml, the Kuma bootstrap Compose and CI to equal the digest here (#273).
# Change them together, then release cloudflared with scripts/deploy_tunnel.py
# and Uptime Kuma with scripts/deploy_kuma.py (#274). Kuma pins the multi-arch
# index digest that Dependabot tracks; Compose selects linux/amd64.
FROM docker.io/cloudflare/cloudflared:2026.11.1@sha256:e613aadbc2d9511785d2e67a86b0c8ac18dd63a3f9af0d06faf04cbfdcb35f2d
FROM docker.io/louislam/uptime-kuma:2.5.12-slim-rootless@sha256:212d2131952eded9b2508620455f15f5c2c85c262aafd23b97a7447c53e1d1bc
