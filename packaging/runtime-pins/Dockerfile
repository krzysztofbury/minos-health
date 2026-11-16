# Never built. Dependabot reads these FROM lines, so an upstream release opens
# a PR; a test requires every pin of these images in the Compose template,
# site.yml, the Kuma bootstrap Compose and CI to equal the digest here (#273).
# Change them together, then release cloudflared with scripts/deploy_tunnel.py
# and Uptime Kuma with scripts/deploy_kuma.py (#274). Kuma pins the multi-arch
# index digest that Dependabot tracks; Compose selects linux/amd64.
FROM docker.io/cloudflare/cloudflared:2026.11.0@sha256:b94b96edf2c9c4360f8879c8580cfaa99077f19c849ec595ca2f711b27d4f344
FROM docker.io/louislam/uptime-kuma:2.5.11-slim-rootless@sha256:ebc6299a1bc8bd4a29e0bddcc04f11440e3690b1c52e19558006eeefb5a8b019
