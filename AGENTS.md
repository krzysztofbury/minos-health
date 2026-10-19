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
- Update docs, CHANGELOG, README and TODO in the same PR as the change. Never
  open a documentation-only follow-up PR; Actions minutes are limited. Record
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
