# Source catalog discovery

Focused #80 slice: publisher/format facets, deterministic sorting, bounded pages
and shareable non-sensitive catalog selections. This does not close the broader
Cloudflare, indexing, practitioner, legal or capacity acceptance.

## Contract

- Publisher comes from source metadata: the RSK publisher, NFZ for BAW/NFZ
  publications, and AOTMiT for its signals. RSK/CeZ distribution is not treated
  as authorship. Unrecognized or missing publishers have an explicit other group.
- Format selection includes historical files shown by each card. A BAW act can
  match XLSX through an older attachment without claiming that its current file
  set includes that workbook. NFZ CZS XML uses its declared channel, not its
  extensionless download URL.
- Default search precedence remains unchanged. Optional name and observed-time
  sorting use stable source/record identity ties. Observation time is not legal
  applicability or publication time.
- At most 50 result cards are rendered per page. Changes to query, source or
  facets reset the page; refresh clamps pages to available results.
- Shared links use a bounded, allowlisted URL fragment: source, publisher,
  format, sort and page. Free text is never serialized. When text is present,
  the shared page is 1 because that text is intentionally absent from the link.
  Unknown keys, duplicate keys, invalid values and excessive size are rejected.
  No new localStorage, sessionStorage, cookies or analytics fields are used.
- Initial HTML and stable source/dataset/observation links remain available
  without JavaScript. A failed provider retains its initial HTML or labelled
  in-memory last-good data. Initial HTML is explicitly labelled as unfiltered
  while browser filters operate on the providers that loaded successfully.

## Verification

Node tests cover private-text exclusion, malformed shared state, publisher/format
evidence including historical attachments, and timestamp/identity ordering.
Real Chromium exercises facet selection, reload of a shared view, reset, two-page
navigation, narrow-panel fit, query exclusion from requests, keyboard, provider
failure and recovery. Desktop, 390px and 320px screenshots are visually reviewed.
Existing server-side integration tests retain source/citation and failure-isolation
coverage. Production activation and representative field-performance acceptance
remain separate from these local regression checks.
