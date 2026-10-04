# Prepared Conditions Rollout

This prepares #32 for owner review. It does not activate a bundle or change the
live site. The catalog-only selector remains available for rollback.

## Selection And Build

`manifests/sources/reference-conditions-public-validation.json` adds the pinned
characteristics and directional lists to the reviewed January-2026 catalog
lineage. Its dataset identity is
`cb0cc740f0cf509fd248c4a1f2bde8a3f94691f49d210ce56a2e56891930268b`, the dataset
used by the isolated VPS measurement. MODTAR legal bindings are not asserted by
this selector.

```sh
uv run python -m minos data build \
  --manifest manifests/sources/reference-conditions-public-validation.json \
  --archive-root /path/to/private-verified-archive \
  --output /path/to/new-bundle --json
```

Catalog coverage starts on January 1. Conditions coverage starts on May 1;
January-April conditions stay explicitly uncovered. These are different source
boundaries, not a reason to borrow May conditions for earlier services. Group
and dictionary membership links retain their service dates. Membership is
reference evidence, not treatment advice or encounter eligibility.

## Prepared Activation Checks

- The API healthcheck start period is 120 seconds, based on the measured
  96-110-second isolated startups. The existing host activation wait remains
  bounded at 180 seconds. This is preparation, not proof of startup under load.
- Deployment plans select a covered condition group from the verified bundle
  and retain its service date, characteristics release and interpretation state.
- Verification requires that exact dated source and state in the public API,
  in addition to dataset readiness and catalog base-point checks. A catalog-only
  answer, an uncovered date or a foreign characteristics release fails.
- Reviewed modifier metadata is checked against the planned binding state;
  it does not change the base-point semantics of a group page.
- Existing pinned-image, checksum, operational-service preservation and rollback
  checks remain in place. No legal overlay is promoted from a pending candidate.

The VPS result covers the measured conditions selector, not a future expanded
search index or a newly bound legal dataset. Those artifact changes need their
own representative runtime evidence before activation.
