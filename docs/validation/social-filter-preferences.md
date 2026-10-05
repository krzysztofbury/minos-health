# Social metadata and browser-local search filters

Scope: owner-requested follow-up to #80, 2026-10-05, on a separate branch.
This changes presentation and browser preferences, not ranking, source selection,
database contents or API budgets. The previous dictionary and embedding
experiments stay unchanged.

## Accepted behavior

- The live homepage title, description and WebSite JSON-LD include ICD-10 PL,
  ICD-9 PL and JGP reference search, conditions and base points. No encounter
  grouping or final settlement claim is added.
- Validation-mode static and server-rendered pages share a 1200 x 630 PNG,
  absolute Open Graph/Twitter image URLs, dimensions, type and alt text, while
  retaining their own titles, dates and source descriptions. The API-free
  maintenance mode keeps text-only cards and explicitly unavailable search;
  it does not reference an image absent from its host inventory.
- A new browser starts with all available search sources selected. ICD-11
  stays unavailable. Missing metadata disables the affected source only.
- Explicit filter changes automatically save only an ordered allowlisted
  source selection, schema version and absolute 90-day expiry in
  `minos.search.filters.v1`. No initial write, per-keystroke storage, cookies,
  medical queries, codes, identifiers or analytics fields are added.
- Expiry is not renewed by reading or searching. Invalid or expired records
  fall back to all available sources and deletion is attempted on access.
  Storage failures do not invalidate the current in-memory choice.
- User intent is separate from current availability. Unavailable sources do
  not enter requests but remain part of the saved preference. A deliberately
  empty selection stays empty and produces validation, not an implicit default.
- Reset removes only this key and restores available sources, without creating
  another record. It and filter changes cancel stale suggestions. A visible
  inline notice links to the revised privacy policy.

## Privacy decision and limits

The owner explicitly selected automatic localStorage without an opt-in, after
being told that browser-storage rules still apply. The policy describes this
actual behavior and removes the former blanket no-banner-required assertion.
This is not legal clearance. The adequacy of persistent automatic storage
without consent has not been established. A notice or policy update is not
equivalent to consent, and the storage mechanism is not itself an exemption.

Relevant sources:

- [Polish Electronic Communications Law, Article 399](https://api.sejm.gov.pl/eli/acts/DU/2024/1221/text.html).
- [WP29 Opinion 04/2012, section 3.6 on UI customization](https://ec.europa.eu/justice/article-29/documentation/opinion-recommendation/files/2012/wp194_en.pdf).

The existing transient sessionStorage dictionary handoff stays documented and
is not added to this persistent setting. No analytics or provider boundary is
changed. Expiry means rejection and attempted deletion on next access, not
physical erasure from a closed or storage-blocked browser.

## Before-change pair-programmer review

Independent read-only review identified these acceptance boundaries:

1. Separate empty user selection from unavailable service and preserve latent
   preferences for unavailable sources (TigerStyle #6, #13).
2. Bound and validate storage on read/write, catch all storage failures, delete
   only the feature key, and never renew expiry on reads (TigerStyle #2, #4, #12).
3. Cancel stale suggestions on filter changes/reset (TigerStyle #3, #12).
4. Amend the narrow scope exception and privacy text without claiming legal
   exemption (TigerStyle #6, #13, #19).
5. Deliver image bytes through the exact edge allowlist and regenerate both
   API shells, keeping page-specific metadata (TigerStyle #4, #13).
6. Measure all-filter typing/submission fan-out against the full bundle and
   unchanged runtime budgets (TigerStyle #14, #15).
7. Replace old single-default/empty-storage browser assumptions with exact
   preference checks and continued query/identifier/cookie exclusion.

## Verification

The first baseline run used the harness's restrictive inherited umask and had
three failures before code changes: two exact-mode filesystem fixture failures
and one browser fixture that could not read its public projection. All three
passed with the expected `0022` umask; no production checks were weakened.
The failed baseline scratch was cleaned after restoring owner permissions.

Changed-boundary checks: 51 static metadata, dictionary/JGP HTML and sources
tests passed. The independent after-review ran 62 targeted tests successfully.
Full repository pre-commit checks passed, including the unit/integration suite,
Ruff, mypy, BasedPyright, Bandit, secrets, source assets, rendered pages,
lockfile, licence policy, Terraform formatting and workflow security checks.

The real Chromium preference suite passed against the packaged full production
bundle and the synthetic smoke fixture: desktop/mobile defaults, ordinary
three-source typing/submission, checkbox changes, reload and actual browser
restart, fixed expiry, empty selection, keyboard reset, unrelated-key
preservation, invalid/oversized records, blocked storage, preservation after
failed reads, missing availability and stale suggestion/search cancellation.
Wire checks confirmed PNG bytes, dimensions, GET/HEAD, content type, revalidation
and rejection of query/path aliases. Static, dynamic and error pages carried
the expected metadata. Desktop/mobile, 200% text zoom and the social image
were visually inspected. The existing packaged browser suites, query/log
privacy and stopped-API recovery passed too. Fault injection was local only.

### Measured all-filter tradeoff

The unchanged full production bundle ran in the packaged API/edge with the
existing 1 CPU/512 MiB API limits. Each simulated user entered three prefixes
at 300 ms intervals and submitted a search, repeated for 12 cycles. Single
source means ICD-10 only; all sources means ICD-10, ICD-9 and JGP. Suggestions
did not retry; submitted requests used the existing one-retry behavior.

| Sessions | Sources | Requests | p95 ms | Maximum ms | Busy 429 | Failed final source submissions |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Single | 48 | 22.20 | 54.68 | 0 | 0/12 |
| 1 | All | 144 | 45.15 | 66.78 | 0 | 0/36 |
| 4 | Single | 192 | 22.67 | 55.08 | 0 | 0/48 |
| 4 | All | 584 | 473.34 | 717.18 | 12 | 0/144 |

API cgroup peak during this run: 219,447,296 bytes (209.3 MiB). Query markers
were absent from API/edge logs. The default intentionally trades more request
volume and concurrent latency for coverage of all sources. The busy responses
are disclosed, not reported as an error-free run. All submitted searches
completed, but some suggestion requests did not. This is local workload
evidence, not representative sustained production capacity or a claim of
being faster than another workflow. After seeing the measurements and the
after-review's release gate, the owner explicitly selected "Deploy all filters"
on 2026-10-05. No limits or budgets were raised.

### After-change pair-programmer review

The independent read-only after-review found no code-level correctness or
security blocker. It confirmed bounded storage validation and failure handling,
separation of intent from availability, empty-state preservation, cancellation,
escaping, image delivery, and maintenance-mode scope. Technical approval was
conditional on the complete browser run and release evidence, which now passed.
It explicitly retained the legal uncertainty and requested owner acceptance of
the measured fan-out tradeoff before release. The owner accepted the measured
tradeoff and authorized deployment. Production activation remains pending;
the dataset is unchanged and checksum-verified.

Compact non-source evidence is under
`docs/validation/evidence/social-filter-preferences/`. Detailed screenshots,
runtime scripts and receipts are retained in ignored
`test-output/social-filters-mvqw2ufn/` until release verification and cleanup.
