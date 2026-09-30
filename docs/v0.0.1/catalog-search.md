# Local Source Catalog Search

`/zrodla` searches only the bounded, sanitized RSK and BAW public metadata
projections. It never opens PDF/XLSX/XML/DOCX contents or private archive/control
data. Medical reference-code search is a different interface.

## Behavior

- Exact catalog identifiers/aliases first, then exact document identifiers and
  names, followed by token/prefix matches with boosted identifier/title fields.
- Words are ANDed within one metadata record, independently of order. Terms from
  unrelated files or document versions cannot manufacture a combined match.
- Polish accents are folded for catalog lookup. Identifier punctuation is ignored
  only for exact identifiers; normal word boundaries are retained. Filename
  letter/digit boundaries are tokenized, so `Załącznik9.xlsx` supports `załącznik 9`.
- Prefixes start at three letters; numeric terms are exact. Suggestions use
  MiniSearch's one-edit fuzzy lookup only for nonnumeric words of four or more
  letters, only on zero hits, and only within the selected source tab.
- Up to three suggestions are explicitly labelled `Czy chodziło o`. Nothing is
  silently corrected; accepting a suggestion performs the local lookup.
- Up to three matching record labels identify filename, version/act date and
  document role. RSK observations remain searchable when a UUID is reused. Historic
  BAW file matches stay under the correct original act and retain full history.
- Exact tiers and scores order cards, with stable catalog order as the tie-breaker.
  This is metadata relevance, never clinical or legal applicability ranking.

Queries are limited to 80 characters, eight tokens and 64 characters per token.
Invalid input is labelled accessibly. The validated projections bound the index
to 24,576 records. Cards render in batches of 50 with an explicit show-more button.
This does not impose a new limit on source-file details or hide result counts.
Loading/retry failures retain an explicitly stale previous valid index, while a
missing source leaves the other source available. Clear resets the local query
and keeps keyboard focus in the input.

## Dependency And Privacy

MiniSearch 7.2.0 is MIT and has no dependencies. Its upstream UMD code and full
license are vendored unchanged under `apps/web/vendor`; reviewed SHA-256 hashes
are enforced by `scripts/build_catalog_assets.py`. The served generated
`infra/ansible/files/sources.js` includes the full MIT notice and requires no CDN,
npm runtime or extra route. CI rejects stale/generated drift. Edit
`apps/web/sources.js`, then run:

```sh
uv run python scripts/build_catalog_assets.py
uv run python scripts/build_catalog_assets.py --check
node --test tests/js/sources.test.cjs
uv run pytest tests/unit/test_sources_page.py
```

Catalog queries remain in transient browser memory. They do not enter URLs,
requests, browser storage, cookies or analytics. Safe DOM rendering uses
`textContent`, not HTML interpretation. The existing strict CSP, exact query-free
routes and denied artifact namespace remain unchanged. Browser tests exercise
real Chromium through pinned nginx, historical-file evidence, suggestions,
keyboard focus, invalid/empty/reset states, source failure/retry and 390/320 px
layouts. This browser engine has no hand-maintained form dictionary and does not
claim the reference CLI's Morfeusz/SGJP morphology.
