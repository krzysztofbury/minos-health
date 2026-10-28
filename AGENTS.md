# Minos Agent Instructions

## Authority

Read `SPEC.md`, `docs/v0.0.1/decisions.md`, and the active GitHub issue before
changing code. Do not let an implementation shortcut silently override an
accepted decision.

## Priorities

Security, correctness, and measured performance are co-equal release
requirements. A release must satisfy all three. Tradeoffs among them require
representative evidence, documented limits, and explicit review. Developer
convenience follows these requirements and does not override them.

Minos must be faster than incumbent workflows, but performance claims require
representative benchmarks. Never weaken validation, provenance, privacy, or
determinism for an unmeasured optimization, and do not dismiss a measured
performance regression for developer convenience.

## Work Rhythm

- Use the single checkout at `~/Work/minos-health` and switch branches there.
  Do not create Git worktrees for this repository.
- Work in small, coherent chunks with explicit acceptance criteria.
- After each chunk, run checks relevant to its changed boundary and show the
  diff and evidence. Run the full repository checks before submitting code.
  Reserve fault injection, container, cloud, and host checks for changes to
  those boundaries; verify real behavior there before claiming success.
- Use the `pair-programmer` skill for TigerStyle review of substantial or
  safety-sensitive changes, not every small edit. Pause for user review at
  decisions and risk boundaries.
- Keep one reviewable purpose per commit and use short imperative subjects.
- Update docs, CHANGELOG and README in the same PR as the change. Never open a
  documentation-only follow-up PR; a change and its documentation are reviewed
  together. Open work lives
  in GitHub issues, not in a TODO file: add, update or close the owning issue
  with the change. Write `Closes #N` only when the merge itself finishes the
  issue; when an owner step follows the merge (a publish, a deploy, external
  evidence), write `Refs #N` and close the issue after that step. Record
  post-deployment evidence in issue comments and fold it into the next PR.
- Never modify or discard user changes outside the agreed chunk.

## Implementation

- Use the Python standard library when it is the clearest adequate choice. Use
  maintained libraries when they materially improve correctness, security,
  interoperability, performance, or maintainability. Approved foundation tools
  include uv, Pydantic, HTTPX, pytest, and Loguru. Add a dependency only when
  code uses it, its boundary is defined, and its purpose and licence are
  reviewed.
- Use Pydantic at untrusted input and configuration boundaries. Keep domain
  behavior independent of transport, persistence, and serialization libraries.
- Keep control flow explicit and bounded. Handle every error path; never advance
  watcher state after an incomplete, ambiguous, or failed operation.
- Comments explain non-obvious intent, invariants, or source constraints. Do not
  narrate syntax.
- Production code must be warning-clean under Ruff, BasedPyright, and mypy.

## Tests

- Unit tests isolate deterministic behavior and failure cases without network
  or cloud access.
- Mutation tests target safety-critical decisions and invariants, not trivial
  accessors or generated models. No test-count or coverage-percentage target
  substitutes for evidence at the changed boundary.
- Integration tests exercise real component boundaries with bounded fixtures.
- Docker Compose exercises the same application artifacts and commands used on
  the Mikr.us production host. R2 object-store semantics (create-once writes,
  SHA-256 checksums, bucket locks) need evidence against a real R2 bucket.

## Operator Checks (Agents)

Production health, without credentials:

- `uv run python -m scripts.check_monitoring` reads the public Uptime Kuma
  status page and `/readyz`. Kuma sends every down state of the public edge,
  the RSK, BAW and NFZ watchers and the data update and activation runs to the
  owner's Telegram alert; this command is an agent's view of the same monitors.
  Exit 0 is healthy, 1 names each problem, including a Kuma that stopped
  checking (then no alert is sent), and 2 means the status page is unreachable:
  Kuma, the tunnel or the host is down, which Kuma cannot report itself.
- Deeper state needs the host. `uv run python -m scripts.deploy_validation
  inspect` is read-only (identities, Compose SHA-256, memory, disk, container
  health and restart counts). Watcher status and bounded logs
  ([operations](docs/v0.0.1/operations.md#logging)) need SSH, so the owner
  approves that exact command first.
- Unit tests, fakes and the Docker smokes (`scripts/smoke_*_deploy.py`) prove
  the tools, never production health. An empty log or query through a filter is
  evidence only after the filter is shown to match running containers.

Releases ([validation deployment guide](docs/validation/validation-deploy.md)):

- `scripts/deploy_validation.py` releases the API, edge and reference bundle.
  Inputs: the reviewed remote-`main` revision, its successful publish run ID and
  the local bundle; `plan` records the image digests, the bundle database
  SHA-256 and the live Compose SHA-256 in
  `test-output/validation-deploy/<revision>/plan.json`. `stage`, `activate` and
  `verify` follow (`deploy` runs all four). Activation starts the other API
  colour beside the live one, then retires the old colour; `rollback` restores
  the retained `validation-rollback/<revision>/compose.yaml`.
- `scripts/deploy_watcher.py`, `scripts/deploy_tunnel.py` and
  `scripts/deploy_kuma.py` release one service each: `plan`, `stage`,
  `activate`, then `rollback --plan` if needed.
- Every host-changing command holds `/opt/minos/.validation-deploy.lock`.
  Read-only: `inspect`, each tool's `plan`, and `prune` without `--apply`.
  Everything else changes the host, including `verify`, which writes a receipt.

Human gates:

- An agent runs a host-changing command only after the owner approves that
  exact command text in `/permissions`; a chat approval does not lift a
  permission denial. Ask for every command of a release in one message, with
  revisions and digests taken from command output.
- Ansible playbooks run only when the owner authorises that exact run;
  `site.yml` never runs over the live validation deployment. Secret files,
  1Password items, the R2 key and Cloudflare or Tailscale settings change only
  at the owner's explicit request.

## Security And Privacy

- Never commit credentials, `.env` files, raw source data, or licensed
  generated artifacts.
- Local application secrets come from ignored `.env` files. Local cloud
  credentials stay in external profiles or keychains. Production secrets are
  host-managed files restricted to root and the required service UID or GID;
  recovery copies stay in the password manager. The bucket-scoped R2 watcher
  key never enters GitHub, `.env`, Git, or an image.
- Logs and error reports must not include raw medical queries, encounter data,
  credentials, signed URLs, headers, cookies, or local paths.
- Issue #6 downloads only explicitly allowlisted RSK bytes, initially the
  `00CD10` XML, while monitoring metadata for every returned HCD.
