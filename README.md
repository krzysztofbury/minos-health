# Minos

Minos is an explainable settlement intelligence platform for Polish hospital
care. It combines versioned medical terminology (Polish ICD-10 and ICD-9 PL)
with the NFZ JGP hospital grouping system: the group definitions, point
catalogs, and grouper algorithm that determine how Polish hospitals are paid.

In Dante's Inferno, Minos hears each case in full and assigns it to its circle.
That is what a DRG grouper does, and this one shows its reasoning.

The repository and command use the short name `minos`. **Minos** is the
provisional public brand pending trademark counsel review; a registry
pre-screen was performed on 2026-08-06 (see
[decisions](docs/v0.0.1/decisions.md)).

## Product Layers

1. **Terminology**: `icd10-pl` and `icd9-pl` with deterministic, fast search
   and full source provenance.
2. **JGP knowledge base**: groups, grouping conditions, directional ICD lists,
   age and length-of-stay rules, point values, and version-to-version diffs,
   ingested from NFZ normative acts.
3. **Grouper engine** (planned): a deterministic encounter-to-group evaluation
   with a complete rule trace explaining why a case grouped where it did and
   which conditions failed.

## Roadmap

| Phase | Ships |
| --- | --- |
| `0` watchers | Daily source watchers, scoped raw snapshot archive, reviewed data-update PRs. Runs before product code because upstream file sets and reused URLs can change without useful validators and prior public bytes may become undiscoverable. |
| `v0.0.1` foundation | Bitemporal store, terminology and JGP knowledge base, search, CLI, read-only HTTP API, anonymous web browser with version diffs. |
| `v0.0.2` grouper | Encounter grouping with points, full rule trace, and near-miss reporting. |
| `v0.0.3` optimizer | What-if simulation framed as coding completeness and rejection prevention. |

Drug products, substitutions, reimbursement contracts in PLN, clinical
recommendations, and international ICD variants are outside these releases.
They may become separately namespaced modules after source and licensing
validation. The data model is designed so additional markets (WHO
classifications, other DRG systems) arrive as new namespaced terminology
systems, grouping schemes, and tariff schedules rather than schema changes.

## Architecture

Minos is one Python 3.14 distribution with strict internal package boundaries:

- `minos.domain`: synchronous domain types, normalization, ranking, and errors;
- `minos.store`: read-only persistence and indexed search through SQLite/FTS5;
- `minos.ingest`: hardened source parsing and reproducible artifact builds;
- `minos.cli`: data build, inspection, and offline lookup commands;
- `minos.http`: bounded FastAPI endpoints and privacy-safe telemetry;
- `minos.grouper` (planned, `v0.0.2`): versioned grouping engine evaluating
  encounter facts against rules-as-data.

The target deployment uses immutable `linux/amd64` API, edge, and watcher
images. Docker Compose runs the complete stack locally and on a dedicated
Mikr.us `3.0` host; Cloudflare Tunnel is its only public ingress. The API image
contains the generated read-only SQLite database, while the edge image contains
the static web artifact. Offline CLI distributions bundle the Python runtime
and use the same generated database.

The watcher container runs APScheduler and a separate mutable control SQLite
database. It stores exact raw bytes and revisioned control-database recovery
images in private versioned S3. GitHub-hosted Actions build and test the images
but do not run production schedules. Collectors in issues #6-#8 emit change
records. Issue #21 will build pinned proposals and automatically activate
policy-qualified routine data updates, retaining the last good release and
sending detailed Telegram failure alerts. That pipeline is not enabled yet.

Offline `minos data diff` compares explicitly pinned terminology, catalog and
conditions releases in verified bundle directories, with bounded Markdown/JSON
reports. See [the CLI contract](docs/v0.0.1/release-diff.md) and
[validation evidence](docs/validation/release-diff.md).

The same Mikr.us host also runs self-hosted Uptime Kuma 2.5.5 at
`status.minoshealth.pl`. Its public page is limited to coarse service state;
detailed watcher state remains private through `minos watcher status`, with
issue #54 reserved for a future private detailed admin portal. This replaces a
paid external monitoring SaaS, not the need for monitoring. Because Kuma shares
the host, it cannot detect or notify for a total Mikr.us host, network, or power
outage. This limitation is accepted for now.

The initial service remains anonymous. The accepted later client direction is
one installable responsive PWA that can synchronize a verified immutable
SQLite reference snapshot for offline use. Future individual calculations are
authorized and metered online; premium offline calculation is licensed
separately to an organization or hospital. Accounts, payments, usage storage,
and exact commercial terms are not part of `v0.0.1`.

See [the v0.0.1 documentation](docs/v0.0.1/README.md) for accepted decisions
and the implementation plan.

## Development

Install the pinned Python runtime and all development tools from a clean
checkout:

```sh
uv sync --all-groups
uv run minos --version
```

Ruff, BasedPyright, mypy, and pytest are pinned in `uv.lock`. Terminal and
Neovim integration is documented in [CONTRIBUTING.md](CONTRIBUTING.md).
Security, correctness, and measured performance are co-equal release
requirements. Evidence-based tradeoffs and representative benchmarks are
required, while developer convenience follows those requirements. Critical
paths receive targeted mutation and integration testing.

Offline reference commands and their bounded JSON/exit-code contract are in
[offline-search.md](docs/v0.0.1/offline-search.md). Local full-source and
standalone evidence is in [the search validation](docs/validation/offline-reference-search.md).
The website does not expose this search yet; source redistribution and signed
product promotion retain their separate review gates.

A production-only maintenance Compose stack currently serves an explicit holding
page through Cloudflare Tunnel. A separate digest-pinned Uptime Kuma bootstrap
container runs privately on the same host; its public status hostname is not
routed yet. The watcher foundation now includes an executable
UTC scheduler, fenced SQLite control transactions, local and S3 archives,
revisioned recovery images, signed trusted-recovery inventories, a read-only
watcher status command, preflight commands, a hardened watcher image, bounded
local Docker logs, and local Compose. The RSK collector is registered in the
runtime, but a fresh control volume must be initialized explicitly before the
watcher starts.

Local runtime commands are:

```sh
docker compose build watcher
docker compose --profile bootstrap run --rm --no-deps watcher-bootstrap-rsk
docker compose --profile preflight run --rm --no-deps watcher-preflight
docker compose up --detach watcher
docker compose exec --no-TTY watcher /app/.venv/bin/minos watcher status --json
docker compose logs --since 24h --tail 500 --timestamps watcher
```

Use `minos watcher status [SOURCE_ID] --json` to inspect one configured source.
The status path opens control SQLite read-only. These host-local status and log
commands do not detect a complete Mikr.us host, network, or power outage from
outside its fault domain.

Uptime Kuma setup and the two-stage public-route bootstrap are documented in
[the Ansible runbook](infra/ansible/README.md). The operator created its admin
account and 2FA, configured a private status page and paused watcher push
monitor, tested a Telegram alert from Kuma, and stored a stopped
integrity-checked off-host backup in 1Password on 2026-09-23. An isolated
Docker restore of that backup passed against the same pinned image without
network or host ports. The watcher stack, DNS status route, watcher push
delivery, full host-loss recovery, representative load, and public browser
verification remain pending.

The real-AWS acceptance harness under `infra/terraform/watcher-acceptance` is
opt-in and creates only randomized, tagged disposable resources in the built-in
repository account. It requires a dedicated non-default profile, exact
same-account and production confirmations, reviewed plan digests, and separate
apply, writer-revocation, and destroy approvals. The operator must be an exact
preconfigured least-privilege IAM principal. Production Terraform apply,
Mikr.us activation, and restore drills remain explicit operator approval gates.

## Data

All sources are public. Details, endpoints, and polling procedures live in
[data sources](docs/v0.0.1/data-sources.md); the legal analysis lives in
[data and licensing](docs/v0.0.1/data-and-licensing.md).

| Dataset | Source |
| --- | --- |
| JGP group catalog, characteristics, coefficients | Zarządzenia Prezesa NFZ (leczenie szpitalne), via <https://baw.nfz.gov.pl> |
| JGP grouper algorithm and procedure ranks | NFZ komunikaty JGP |
| ICD-9 PL | NFZ dictionary downloads and <https://slowniki.nfz.gov.pl> |
| ICD-10 PL | RSK resource HCD `00CD10`, <https://rsk3.ezdrowie.gov.pl> |
| JGP utilization statistics | <https://api.nfz.gov.pl> (open data, attribution required) |

Source snapshots and generated databases are artifacts, not repository
materials, and must not be committed to Git. Every generated dataset carries
source identity, timestamps, checksums, transformation version, attribution,
and required notices.

## Safety, Compliance, And Privacy

Minos is a reference and settlement-analysis tool, not medical advice,
diagnosis, treatment, or a substitute for professional judgement.

The compliance boundary is explicit: Minos supports **complete and correct
coding of what was actually performed and documented**, and prevention of
avoidable claim rejections. It must not recommend coding beyond documentation.
Grouping results are expressed in points and product codes; contractual PLN
amounts depend on payer contracts and are out of scope.

The public service is anonymous. Logs must not contain raw medical search
queries, encounter payloads, patient data, prescriptions, credentials, or
other personal data. Grouping (from `v0.0.2`) is stateless: encounter inputs
are evaluated and discarded, never stored.

## Licensing

Repository software, infrastructure, tests, and documentation are licensed
under either Apache-2.0 or MIT, at your option. Dataset artifacts remain under
their source terms. NFZ normative acts and their appendices are treated as
copyright-exempt official materials under Art. 4 of Polish copyright law
(formal legal opinion pending); the RSK Polish ICD-10 resource identifies
`CC BY-NC-SA 3.0 IGO`. See [NOTICE.md](NOTICE.md) and
[data and licensing](docs/v0.0.1/data-and-licensing.md).

## Status

Minos is in specification and initial development. No release has been
published. See [SPEC.md](SPEC.md) for the implementation baseline and
[TODO.md](TODO.md) for the current work queue.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue or pull request.
