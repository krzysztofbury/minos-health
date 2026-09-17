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

### Local And Cloud Parity

Docker Compose is the supported full local runtime. It must execute the same
application artifacts and commands used by cloud workflows, with persistent
ignored local storage instead of ephemeral `/tmp` for watcher archives. Tests
may use temporary directories for isolated operations.

The local filesystem and production S3 archive adapters must pass the same
contract tests. A local AWS emulator may accelerate development, but it does
not replace a disposable real-AWS integration test for versioning, conditional
writes, IAM, and state compare-and-swap behavior.

Copy `.env.example` to an ignored `.env` for local configuration. Local secrets
for the application belong only in `.env`; local cloud credentials stay in an
external profile or keychain. Production workload secrets belong in AWS Secrets
Manager, while operator and recovery credentials stay in the password manager.
GitHub uses AWS OIDC and must not store long-lived AWS access keys. A narrowly
scoped third-party deployment token that cannot use workload identity may use a
protected GitHub secret after review.

Install and enable the repository hooks:

```sh
pipx install pre-commit
pre-commit install
```

## Before submitting a change

Every behavior change needs unit tests and relevant integration tests. Mutation
testing targets safety-critical decisions such as source validation, archive
state transitions, and ruleset selection. The mutation runner will be selected
only after Python 3.14 compatibility is demonstrated; until then, documented
manual mutation or fault-injection cases are the required evidence.

Run the complete pre-commit suite and the explicit Python checks:

```sh
pre-commit run --all-files
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
