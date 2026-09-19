# Contributing to Minos

Minos is at an early stage. Please discuss substantial changes in an issue
before implementing them so that terminology, grouping-rule, licensing, and
deployment decisions remain deliberate.

Engineering priorities are security and correctness first, measured
performance second, and developer convenience third. Fast behavior is a product
requirement, but an optimization is accepted only with representative evidence
and without weakening validation, provenance, privacy, or determinism.

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

Prefer the Python standard library when it is sufficient. Approved foundation
tools include uv, Pydantic, HTTPX, pytest, and Loguru. Pydantic belongs at
configuration and untrusted-input boundaries; domain behavior must not depend
on transport or serialization libraries. Add a dependency only with working
code, a clear ownership boundary, and a licence review.

Loguru (MIT) emits allowlisted structured process events without installing
library-level sinks. backoff (MIT) supplies the bounded exponential schedule
and full jitter for idempotent source reads; retry classification, `Retry-After`
handling, and streaming rollback remain explicit in Minos code. APScheduler
(MIT) parses five-field cron expressions and calculates UTC fire times; Minos
retains ownership of durable schedule cursors, claims, and transaction fencing.
Boto3 and Botocore (Apache-2.0) provide direct S3 API calls and modeled AWS error
classification. Minos owns content addressing, single-part conditional-write
policy, bounded conflict handling, and native checksum verification; transfer
manager helpers are not used.

Development-only dependencies are pre-commit (MIT) for reproducible repository
checks, Bandit (Apache-2.0) for Python static security analysis, pip-audit
(Apache-2.0) for advisory checks, pip-licenses (MIT) for licence policy, and
PyYAML (MIT) for workflow policy tests. Zizmor (MIT) audits GitHub Actions for
unsafe triggers, permissions, and expression use; types-PyYAML (Apache-2.0)
supplies static-analysis metadata. Hatchling (MIT) is the pinned build backend.
These tools do not enter production artifacts. CI uses checkout (MIT), setup-uv
(MIT), setup-terraform (MPL-2.0), and Trivy (Apache-2.0); every action reference
is pinned to a full commit SHA.

### Local And Cloud Parity

Docker Compose is the target full local application runtime, but it is not
implemented yet. The production host has a separate maintenance-only edge and
Cloudflare Tunnel definition under `infra/ansible/files/`; it is not a local
Minos application stack. The current watcher foundation provides the RSK
transport, control transactions, and local and S3 archive adapters as tested
libraries; there is no executable watcher, Dockerfile, or root `compose.yaml`.
Development currently uses `uv sync --all-groups` and the checks below. Do not
expect `docker compose up` to work until the watcher entry point is implemented.

When added, Compose must execute the same application artifacts and commands
used on Mikr.us, with persistent ignored local storage for watcher control state.
Tests may use temporary directories for isolated operations.

The local filesystem and production S3 archive adapters must pass the same
contract tests. A local AWS emulator may accelerate development, but it does
not replace a disposable real-AWS integration test for versioning, conditional
writes, and IAM behavior. Mutable watcher state belongs in SQLite, not S3.

`.env.example` currently documents active RSK transport settings and comments
out reserved archive, logging, and S3 settings. Copy it to an ignored `.env`
only for direct Python development. Local application secrets belong only in
`.env`; local cloud credentials stay in an external profile or keychain.
Production secrets are host-managed files restricted to root and the required
service UID or GID; recovery copies stay in the password manager. GitHub uses
AWS OIDC and must not store long-lived AWS access keys. The narrow S3 key
required by the external watcher host never enters GitHub, `.env`, Terraform
state, Compose YAML, or an image.

Install and enable the repository hooks:

```sh
uv run pre-commit install
```

## Before submitting a change

Every behavior change needs unit tests and relevant integration tests. Mutation
testing targets safety-critical decisions such as source validation, archive
state transitions, and ruleset selection. The mutation runner will be selected
only after Python 3.14 compatibility is demonstrated; until then, documented
manual mutation or fault-injection cases are the required evidence.

Run the complete pre-commit suite and the explicit Python checks:

```sh
uv run pre-commit run --all-files
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
uv run mypy src
uv run pytest
```

Keep changes focused, add tests for observable behavior, and update the
documentation and changelog when behavior changes. Accepted `v0.0.1` decisions
are recorded in [docs/v0.0.1/](docs/v0.0.1/README.md); change them explicitly
rather than allowing implementation details to override them silently.

## Commit and pull request guidance

- Work in small, coherent chunks. After each chunk, run the relevant checks,
  perform a `pair-programmer` TigerStyle review, show the diff and evidence,
  and pause for review before continuing.
- Use short, imperative commit subjects.
- Explain the user-visible behavior and important tradeoffs in the pull request.
- Do not commit upstream source data (RSK, NFZ, AOTMiT), generated databases,
  credentials, Terraform state, secret values, machine-local configuration, or
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
