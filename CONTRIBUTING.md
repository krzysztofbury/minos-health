# Contributing to Minos

Minos is at an early stage. Please discuss substantial changes in an issue
before implementing them so that terminology, grouping-rule, licensing, and
deployment decisions remain deliberate.

Security, correctness, and measured performance are co-equal release
requirements. A release must satisfy all three, and tradeoffs require
representative evidence, documented limits, and explicit review. Developer
convenience follows these requirements. An optimization is accepted only with
representative evidence and without weakening validation, provenance, privacy,
or determinism.

## Development setup

The project uses the Python version pinned in `.python-version`. From a clean
checkout, one command installs Python when needed and synchronizes every
dependency group:

```sh
uv sync --all-groups
```

Verify the installed command:

```sh
uv run minos --version
```

### Terminal and Neovim

The repository provides editor tooling inside `.venv`; it does not own personal
Neovim configuration. Point the existing Neovim LSP client at:

| Capability | Executable |
| --- | --- |
| Python navigation and type diagnostics | `.venv/bin/basedpyright-langserver --stdio` |
| Ruff diagnostics and formatting | `.venv/bin/ruff server` |

Tests run with `uv run pytest`. File search remains the responsibility of the
existing terminal and Neovim setup rather than project-specific dotfiles.

### Dependencies

Use the Python standard library when it is the clearest adequate choice. Use a
maintained library when it materially improves correctness, security,
interoperability, performance, or maintainability. Approved foundation tools
include uv, Pydantic, HTTPX, pytest, and Loguru. Pydantic belongs at
configuration and untrusted-input boundaries; domain behavior must remain
independent of transport, persistence, and serialization libraries. Add a
dependency only with working code, a clear ownership boundary, and a licence
review.

Loguru (MIT) emits allowlisted structured process events without installing
library-level sinks. backoff (MIT) supplies the bounded exponential schedule
and full jitter for idempotent source reads; retry classification, `Retry-After`
handling, and streaming rollback remain explicit in Minos code. APScheduler
(MIT) parses five-field cron expressions and calculates UTC fire times; Minos
retains ownership of durable schedule cursors, claims, and transaction fencing.
Boto3 and Botocore (Apache-2.0) provide direct S3 API calls to Cloudflare R2 and
modeled error classification. Minos owns content addressing, single-part conditional-write
policy, bounded conflict handling, and native checksum verification; transfer
manager helpers are not used.

RapidFuzz (MIT, locked to 3.14.6) owns bounded Levenshtein scoring for offline
spelling suggestions. Minos supplies a release/language-scoped vocabulary derived
from source text, explicit score cutoffs, stable ordering and resource limits.
Suggestions never silently correct codes or trigger a replacement query. No
classification-specific fuzzy algorithm or manually maintained spelling corpus
is used.

Development-only dependencies are pre-commit (MIT) for reproducible repository
checks, Bandit (Apache-2.0) for Python static security analysis, pip-audit
(Apache-2.0) for advisory checks, pip-licenses (MIT) for licence policy, and
PyYAML (MIT) for workflow policy tests. Zizmor (MIT) audits GitHub Actions for
unsafe triggers, permissions, and expression use; types-PyYAML (Apache-2.0)
supplies static-analysis metadata. Hatchling (MIT) is the pinned build backend.
These tools do not enter production artifacts. CI uses checkout (MIT), setup-uv
(MIT), and Trivy (Apache-2.0); every action reference
is pinned to a full commit SHA.

### Local And Cloud Parity

Root Docker and Compose definitions now execute the same watcher entry point used
by the Mikr.us candidate stack. Local Compose uses persistent control, archive,
and disk-backed staging volumes with the filesystem archive backend. Production
uses the R2 backend and service-restricted secret files. Source-specific
collectors remain separate and are registered by their own issues; a runtime
without a collector fails closed before claiming a configured source.

Tests may use temporary directories for isolated operations. Ordinary CI must
never touch cloud resources. Unit fakes model R2's observed behavior (no
versions, no `ChecksumType` for single-part objects, `501` for bucket-owner
headers); a behavior change there needs fresh evidence against a real bucket
under `probes/`, recorded in the issue.

The local filesystem and production R2 archive adapters must pass the same
contract tests. Mutable watcher state belongs in SQLite, not the object store.

`.env.example` documents active RSK and local watcher settings plus commented
production identifiers. Copy it to an ignored `.env` only for direct Python
development. Local application secrets belong only in `.env`; local cloud
credentials stay in an external profile or keychain.
Production secrets are host-managed files restricted to root and the required
service UID or GID; recovery copies stay in the password manager. GitHub holds
no object-store credential. The bucket-scoped R2 key on the watcher host never
enters GitHub, `.env`, Compose YAML, or an image.

Install and enable the repository hooks:

```sh
uv run pre-commit install
```

## Before submitting a change

Test observable behavior at the boundary changed. Use unit tests for decisions
and failure paths, integration tests when component interactions change, and
targeted fault injection for safety-critical transitions such as source
validation, archive commits, and ruleset selection. The mutation runner will
be selected only after Python 3.14 compatibility is demonstrated; until then,
use documented manual mutation or fault-injection cases where those invariants
change. Do not optimize for a test-count or coverage percentage; a passing
suite is not evidence for an untested production boundary.

Before submitting code, run the complete pre-commit suite and explicit Python
checks:

```sh
uv run pre-commit run --all-files
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
uv run mypy src tests scripts
uv run pytest
```

Keep changes focused, add tests for observable behavior, and update the
documentation and changelog when behavior changes. Accepted `v0.0.1` decisions
are recorded in [docs/v0.0.1/](docs/v0.0.1/README.md); change them explicitly
rather than allowing implementation details to override them silently.

## Commit and pull request guidance

- Work in small, coherent chunks. After each chunk, run the relevant checks,
  show the diff and evidence, and pause where a decision or risk boundary needs
  review. Run the full checks at submission; use a `pair-programmer` TigerStyle
  review for substantial or safety-sensitive changes rather than every small
  edit. Test cloud, container, and host behavior at the matching boundary
  before claiming it works there.
- Use short, imperative commit subjects.
- Explain the user-visible behavior and important tradeoffs in the pull request.
- Do not commit upstream source data (RSK, NFZ, AOTMiT), generated databases,
  credentials, secret values, machine-local configuration, or
  personal data.
- Reviewed declarative production infrastructure belongs in Git; its secrets and
  state do not.
- Small synthetic or legally redistributable test fixtures belong outside the
  ignored `/data` directory and must identify their origin.
- Treat terminology parsing, hierarchy, normalization, ranking, grouping
  conditions, and the grouper engine as safety-sensitive. Include
  representative edge cases and malformed inputs.
- Never add raw medical search queries or encounter payloads to production
  logs, analytics, traces, or error reports.
- Keep code and dataset licensing separate. A dependency or data-source change
  must include a licensing review.
- Write comments only for non-obvious intent, invariants, source constraints,
  or safety decisions. Do not narrate syntax.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
