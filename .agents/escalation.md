# Escalation policy

Read by the `approver` role. A pull request that matches any item below goes
to the owner instead of merging on its own. The `retro` role proposes changes
to this file as pull requests; add what should have reached the owner, relax
what the owner keeps waving through, and give the reason in the PR.

## Always escalate

- Agent rules: `.agents/**`, `AGENTS.md`, `CLAUDE.md` and `.github/**`. Agent
  rules and the policy that gates merges do not change without the owner.
- Privacy and medical data boundaries: anything that could log, store, put in
  a URL or send to analytics a raw search query or encounter payload, the
  Plausible setup (`scripts/build_site.py`, `scripts/build_validation_edge.py`),
  and request logging in `src/minos/http/app.py`. SPEC.md
  "Privacy And Safety" changes only with an accepted decision.
- Accepted decisions and the specification: `SPEC.md`, `docs/v0.0.1/**`.
- Public contracts: the MCP tool catalog and server metadata
  (`src/minos/http/mcp.py`, `src/minos/http/mcp_docs.py`, `server.json`,
  `tests/golden/mcp-tools.json`), HTTP API routes and response shapes in
  `src/minos/http/**`, and the CLI output formats in `src/minos/cli/**`.
- Clinical correctness of coding and grouping: grouper rules and the JGP
  ruleset (`src/minos/domain/grouper*.py`, `src/minos/domain/jgp_*.py`,
  `src/minos/store/grouper_*.py`, `src/minos/store/jgp_*.py`), golden
  results in `tests/golden/**`, and search safety thresholds.
- Source data, licences and provenance: `manifests/**` (source, policy and
  redistribution manifests), `src/minos/licenses/**`, `NOTICE.md`, the RSK
  download allowlist, and any new source a watcher fetches.
- Watchers and their state: `src/minos/ingest/watchers/**` (state may only
  advance after a complete operation), recovery and inventory signing
  (`src/minos/ingest/watchers/recovery/**`), object storage
  (`src/minos/ingest/object_storage.py`), and the control database
  (`database/control/**`).
- Database migrations: `database/control/sqlite/migrations/**`,
  `database/reference/sqlite/migrations/**`. Migrations are one-way.
- Deploy, release, CI and hosting: `infra/**` (Ansible, Tailscale policy),
  `packaging/**`, `Dockerfile`, `compose.yaml`, `scripts/deploy_*.py`,
  `scripts/*_host.py`, `scripts/materialize_ansible_host.py`,
  `scripts/render_validation_compose.py`, `scripts/validation_images.py`,
  the publish workflow and image tags or digests.
- Alerts and monitoring: Uptime Kuma setup and its notifications
  (`scripts/deploy_kuma.py`, `scripts/kuma_host.py`,
  `infra/ansible/uptime-kuma-bootstrap.compose.yml`) and anything that sends
  a message outside the host.
- Secrets and credentials handling: R2 keys, Cloudflare tunnel tokens,
  `.env` handling, and secret-scanning config in `.pre-commit-config.yaml`.
- Dependencies: `pyproject.toml`, `uv.lock`, `packaging/standalone/uv.lock`,
  `packaging/runtime-pins/**`, `apps/web/vendor/**`,
  `.github/dependabot.yml`.
- Legal and public copy: `apps/web/site/pages/privacy.html`,
  `apps/web/site/pages/terms.html`, `infra/ansible/files/privacy-policy.html`,
  `infra/ansible/files/terms.html`, the reference-only disclaimer, and
  `SECURITY.md`.
- A measured performance regression, or a change that trades validation,
  provenance or privacy for speed.
- Anything that needs a production run (a release, `deploy_*.py` beyond
  `plan`, Ansible, a host-changing SSH command, R2 or Cloudflare settings);
  the PR merges only after the owner approves that run.

## May merge without the owner

When review passed, CI is green and nothing above applies:

- Web UI (`apps/web/**` except `vendor/` and legal pages) and page templates
  in `src/minos/http/*.html` with a browser check recorded in the PR.
- Features and fixes in `src/minos/**` outside the paths above, with tests.
- Read-only tooling (`scripts/check_*.py`, `scripts/evaluate_*.py`,
  `scripts/measure_*.py`, `scripts/smoke_*.py`).
- Tests (except `tests/golden/**`) and docs (except `SPEC.md` and
  `docs/v0.0.1/**`).
