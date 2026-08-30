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
- `minos.http`: FastAPI endpoints and packaged static web assets;
- `minos.grouper` (planned, `v0.0.2`): versioned grouping engine evaluating
  encounter facts against rules-as-data.

One immutable `linux/amd64` container runs locally, on a conventional Docker
host, or on AWS Lambda through Lambda Web Adapter. Offline CLI distributions
bundle the Python runtime and use the same generated database. AWS Lambda is
the primary production target; Mikr.us remains a benchmark-triggered fallback.
Source watchers run as scheduled CI jobs and propose reviewed data updates;
they are not part of the runtime.

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
