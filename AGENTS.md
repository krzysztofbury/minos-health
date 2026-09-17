# Minos Agent Instructions

## Authority

Read `SPEC.md`, `docs/v0.0.1/decisions.md`, and the active GitHub issue before
changing code. Do not let an implementation shortcut silently override an
accepted decision.

## Priorities

Apply this order when tradeoffs conflict:

1. Security and correctness.
2. Measured performance.
3. Developer convenience.

Minos must be faster than incumbent workflows, but performance claims require
representative benchmarks. Never weaken validation, provenance, privacy, or
determinism for an unmeasured optimization.

## Work Rhythm

- Work in small, coherent chunks with explicit acceptance criteria.
- After each chunk, run the relevant unit, documented mutation or fault-
  injection, integration, static, and container checks; show the diff and
  evidence before continuing.
- Use the `pair-programmer` skill for a TigerStyle review of every chunk before
  presenting it for user review.
- Keep one reviewable purpose per commit and use short imperative subjects.
- Never modify or discard user changes outside the agreed chunk.

## Implementation

- Prefer the Python standard library when it is sufficient. Approved foundation
  tools include uv, Pydantic, HTTPX, pytest, and Loguru. Add a dependency only
  when code uses it and record its purpose and licence.
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
  accessors or generated models.
- Integration tests exercise real component boundaries with bounded fixtures.
- Docker Compose exercises the same application artifacts and commands used by
  cloud workflows. Cloud-specific semantics still require a disposable AWS
  integration test.

## Security And Privacy

- Never commit credentials, `.env` files, Terraform state, raw source data, or
  licensed generated artifacts.
- Local application secrets come from ignored `.env` files. Local cloud
  credentials stay in external profiles or keychains. Production workload
  secrets come from AWS Secrets Manager; operator and recovery credentials stay
  in the password manager. GitHub authenticates to AWS through OIDC, never
  stored AWS access keys.
- Logs and error reports must not include raw medical queries, encounter data,
  credentials, signed URLs, headers, cookies, or local paths.
- Issue #6 downloads only explicitly allowlisted RSK bytes, initially the
  `00CD10` XML, while monitoring metadata for every returned HCD.
