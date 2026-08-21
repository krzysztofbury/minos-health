# Contributing to Minos

Minos is at an early stage. Please discuss substantial changes in an issue
before implementing them so that terminology, grouping-rule, licensing, and
deployment decisions remain deliberate.

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

Install and enable the repository hooks:

```sh
pipx install pre-commit
pre-commit install
```

## Before submitting a change

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

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
