# Operations

## Operating Model

Production is disposable compute serving an immutable, read-only artifact. The
system of record is the combination of Git, reviewed manifests, versioned source
snapshots, image digests, and Terraform state. A Lambda execution environment or
fallback VPS disk is never the only copy of anything required for recovery.

## Asset Inventory

| Asset | Primary location | Recovery protection |
| --- | --- | --- |
| Source and manifests | Private GitHub repository | Git history and remote |
| Raw source snapshots (RSK, NFZ, AOTMiT) | Private S3 data bucket | Versioning and lifecycle policy |
| Independent raw backup | Encrypted QNAP archive | Separate credentials and restore test |
| Generated database | Private GHCR image/artifacts | Digest and release retention |
| Prebuilt static web archive | Private build-artifact S3 bucket | Versioning, checksum, independent encrypted backup |
| Static web bundle | Private frontend S3 bucket | Versioning, checksum, release retention |
| AWS deploy image | Private ECR | Digest copy; reproducible from GHCR |
| Terraform state | Private S3 state bucket | Versioning, encryption, locking |
| Domain | Registrar | Lock, 2FA, auto-renewal, recovery codes |
| DNS | Cloudflare | Terraform and remote state |
| CI identity | GitHub OIDC and AWS IAM | No static AWS access key |
| Emergency credentials | Password manager | Off-host recovery access |

S3 data and Terraform state use separate buckets and policies. A compromise of
the application deployment role must not grant the ability to erase all source
and state versions.

## Deployment

The release workflow deploys an exact digest and records the previous Lambda
version. It then verifies:

1. `/readyz` through the public hostname.
2. A known exact-code search and expected dataset release.
3. A known normalized-text search.
4. Error, throttle, and duration metrics under representative launch traffic.
5. Static application loading from a clean browser session.
6. Responsive search and comparison flows in representative mobile and desktop
   viewports.

The workflow reports success only after these checks. A low-traffic quiet window
is not sufficient evidence.

## Rollback

Rollback first restores and verifies the previous versioned frontend entry HTML
that references its immutable hashed assets, then moves the `live` alias to the
previously recorded compatible Lambda version. It does not rebuild, retag,
mutate a database, or edit the function by hand.

The release workflow owns this alias target. Terraform intentionally ignores
only the dynamic code/version and alias-pointer fields while continuing to own
the alias, alias-qualified Function URL, permissions, and all stable settings.

After rollback, repeat readiness, static integrity, mobile rendering, and
known-search tests through the public host. Preserve failed-version logs and
release metadata for investigation. A rollback does not delete the failed image
or web assets until the incident is understood and retention policy permits
deletion.

## Recovery

### AWS Stack Loss

1. Authenticate through the documented emergency identity.
2. Confirm the correct account, region, state bucket, and Cloudflare zone.
3. Restore a prior Terraform state version only if the current state is corrupt;
   do not replace valid state merely because resources are missing.
4. Apply the reviewed foundation plan to recreate Terraform-owned registries
   and artifact buckets before writing objects to them.
5. Ensure the required release digest exists in GHCR and copy that manifest to
   the recreated same-region ECR repository.
6. Recover the exact static web archive from the build-artifact bucket or its
   independent encrypted backup by recorded key and checksum.
7. Apply the reviewed service plan to recreate the frontend bucket, Lambda,
   Function URL, CloudFront distribution, and stable permissions.
8. Restore the immutable web assets, publish or select the expected Lambda
   version, verify it directly, and set `live`.
9. Restore the recorded frontend entry-object version, then invalidate and
   verify it at CloudFront.
10. Verify DNS, TLS, readiness, mobile rendering, known searches, and
    representative load.

### Raw Dataset Loss

Recover the exact versioned S3 object by manifest key and checksum. If no valid
copy exists, restore the encrypted copy held on the independently administered
QNAP. That copy uses credentials outside the AWS deployment role and is included
in recovery drills. A current upstream version is not an equivalent backup.

### Registry Loss

Rebuild only from the exact source snapshot, source commit, toolchain, and
manifest recorded for the release. Compare the resulting logical data and image
digest. If byte-for-byte reproducibility is not achieved, treat it as a new
artifact requiring full verification rather than silently substituting it.

### Domain Or DNS Loss

Recover the registrar through stored recovery material, confirm delegation, and
apply Cloudflare Terraform. Verify DNSSEC chain state before enabling or changing
signing. Do not change nameservers during an unrelated application incident
unless DNS is proven to be the fault domain.

### Fallback Host

If AWS is unavailable for an unacceptable period, provision the documented
QNAP or Mikr.us Compose profile, pull the exact GHCR digest, and verify it through
a separate fallback hostname. Apply the Cloudflare Terraform fallback mode,
which changes the canonical `.com` record from DNS-only CloudFront to a proxied
Tunnel target. Verify TLS, readiness, known searches, and representative load.
Revert the same Terraform variable to return to AWS. The fallback is a
redeployment, not a restore of an old host filesystem.

## Recovery Drills

Before `v0.0.1`, perform and time at least:

- restoration of a previous Terraform state object in an isolated test stack;
- reconstruction of a generated database from the pinned raw snapshot;
- restoration of the raw snapshot from the independent encrypted backup;
- ECR recreation and digest copy from GHCR;
- Lambda alias rollback;
- complete deployment to an empty environment;
- recovery of registrar and Cloudflare access without relying on an active
  developer laptop.

Record the observed recovery time and unresolved manual steps. Documentation
alone is not a tested backup.

## Logging

Loguru is configured once at process entry and emits allowlisted structured JSON
to stdout in cloud environments. Local development may use a human-readable
console format with the same fields. Libraries and domain code must not install
independent sinks or serialize arbitrary request and exception objects.

Application logs are structured and contain:

- timestamp and severity;
- request or trace ID;
- route template and method;
- status class;
- bounded duration;
- result count or error category;
- dataset release and application version;
- cold/warm runtime context where safely available.

Application logs do not contain:

- raw query strings or medical terms;
- encounter payloads or any grouping request content (`v0.0.2`);
- request headers, cookies, authorization values, or URL query text;
- patient data, prescriptions, or user-supplied free text;
- credentials, signed URLs, source data, stack traces, or local paths in public
  responses.

CloudFront and platform access logs require a separate retention and privacy
review before enablement because IP addresses and complete URLs can become
personal or medical data. Query parameters should be excluded or transformed at
the source rather than scrubbed after broad collection.

Loguru is not an external error-reporting service. GitHub Actions failures and
CloudWatch metrics and alarms provide the initial operational signal. Any future
service such as Sentry requires a separate privacy review, explicit event
allowlisting, bounded retention, and proof that medical terms and encounter
content cannot leave the application.

## Secrets And Configuration

Local development uses ignored `.env` files created from `.env.example` for
application secrets. Local cloud credentials stay in external profiles or
keychains. Production workload secrets live in AWS Secrets Manager and are
injected only into the component that needs them. Operator and recovery
credentials stay in the password manager. Non-secret endpoints, timeouts,
bucket names, regions, and release identifiers remain normal configuration and
must not be hidden in Secrets Manager.

GitHub Actions uses OIDC for AWS access and stores no long-lived AWS access
keys. Local AWS integration uses the developer's authenticated profile outside
the repository; credentials are never copied into `.env` or a container image.
The anonymous RSK source requires no application credential.

## Metrics And Alerts

Initial service metrics:

- request count by route and status class;
- Lambda errors and throttles;
- warm and cold duration distributions;
- maximum memory used;
- readiness and known-search synthetic failures;
- CloudFront 4xx/5xx rates;
- image and database sizes;
- AWS spend and forecast;
- per-source dataset age relative to observed upstream state (the watchers
  know what upstream currently publishes; serving a stale ruleset must be a
  visible, deliberate condition);
- watcher run failures and upstream schema-drift detections.

Alerts must identify the deployed image digest and dataset release. Empty log
results obtained through a filter are not evidence of no errors until the filter
is confirmed against actual running resources.

## Representative Verification

Before selecting Lambda memory and concurrency, measure:

- container startup and readiness on `linux/amd64`;
- process RSS and Lambda maximum memory;
- database open and sentinel-query time;
- exact code, common prefix, no-result, and worst-case bounded text searches;
- JGP group lookup with service-date resolution;
- warm p50/p95/p99 latency;
- cold-start p50/p95 latency;
- error and throttle rate at expected launch concurrency and a documented burst;
- estimated monthly cost at measured duration and traffic.
- mobile and desktop static startup, interaction readiness, and end-to-end
  search and comparison latency through CloudFront.

The launch SLO and budget are recorded before the final benchmark so results are
not judged against thresholds invented afterward. If Lambda fails, use the same
image on Mikr.us `3.0` and compare under the same request profile.

## Security Operations

- Rotate any credential exposed in source, logs, terminal output, or another
  project's setup file before considering it reusable.
- Prefer OIDC and workload identity over stored cloud keys.
- Restrict package tokens to read-only access and the necessary package.
- Keep production registries and source-data buckets private.
- Review Terraform plans for resource replacement, public exposure, IAM scope,
  and retention changes.
- Scan dependencies and images, then exercise the actual runtime path after any
  formatter or autofix pass.
- Report vulnerabilities through GitHub private vulnerability reporting.

## Data Refresh Operations

An upstream refresh is not an automatic production update. The flow is:

1. A scheduled watcher detects the upstream change (new zarządzenie,
   komunikat, ICD-9 version, changed source file set, or changed bytes behind a
   reused URL) and immediately archives the raw files by identity and
   archive-time checksum.
2. The source collector emits an immutable change record. The automation in
   issue #21 opens a data pull request updating the manifest, including the
   ruleset pairing for the affected reporting periods and a human-readable
   diff.
3. The operator reviews source metadata, terms, effective dates (which may be
   retroactive), and the diff: added, removed, changed, and reparented
   concepts; group, condition, and point-value changes.
4. CI runs full ingestion, integrity, diff, and search regression tests, plus
   grouper golden cases once `v0.0.2` ships.
5. Merge builds a new application image and dataset release.
6. Deployment goes through the normal signed-tag promotion path.

Large or unexplained source diffs block release. A new base act (observed
every ~2 years) restructures załączniki and always requires manual review
beyond the automated diff.

## Mutable Data Gate

There is no runtime database backup because `v0.0.1` has no mutable production
data. If accounts, favourites, annotations, analytics, or other writes are added,
work stops until the design includes data classification, retention, access
control, encrypted off-site backups, point-in-time recovery where appropriate,
and a tested restore procedure. The immutable-artifact recovery model does not
cover user data.
