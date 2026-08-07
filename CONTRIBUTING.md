# Contributing to Minos

Minos is at an early stage. Please discuss substantial changes in an issue
before implementing them so that terminology, grouping-rule, licensing, and
deployment decisions remain deliberate.

## Development setup

Once the Cargo workspace exists, the project requires a stable Rust toolchain
with `rustfmt` and `clippy`:

```sh
rustup component add rustfmt clippy
```

Install and enable the repository hooks:

```sh
pipx install pre-commit
pre-commit install
```

## Before submitting a change

Run the complete pre-commit suite. The Cargo commands below are its Rust-specific
checks:

```sh
pre-commit run --all-files
cargo fmt --all -- --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-features
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
