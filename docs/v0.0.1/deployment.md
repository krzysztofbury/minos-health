# Deployment

## Goals

The delivery system must:

- build one portable API image;
- build one immutable static web bundle for mobile and desktop browsers;
- build checksummed standalone CLI and data-bundle artifacts for supported
  platforms;
- promote an already tested digest rather than rebuild for release;
- avoid long-lived cloud credentials in GitHub;
- keep data-bearing images and raw datasets private;
- make production reproducible from Git, immutable artifacts, and remote state;
- support fast rollback without mutating or restoring a server disk;
- remain inexpensive at low launch traffic.

## Artifact Lifecycle

Private GHCR is the canonical image registry. The image tag helps discovery, but
the digest is the deployment identity:

```text
ghcr.io/krzysztofbury/minos-health@sha256:<digest>
```

`latest` is not a deployable reference.

The lifecycle is:

1. A pull request builds a fixture API image and static web bundle, runs them
   through Docker Compose, and performs an actual browser-to-API search.
2. `main` builds the pinned full dataset, supported standalone CLI bundles and
   data bundle, the static web bundle, and the `linux/amd64` API image.
3. `main` pushes `sha-<commit>`, stores CLI/data artifacts immutably by commit and
   checksum, stores the web bundle archive immutably by commit and checksum in
   private artifact storage, and creates build provenance binding the image
   digest and web bundle checksum to the source commit and workflow identity.
4. The main workflow verifies that the deployable digest is one runnable
   single-architecture image manifest, not an OCI index or attestation manifest.
5. A mandatory signed `v*` tag points to a commit with successful prebuilt
   artifacts.
6. The release workflow verifies the Git tag, resolves `sha-<commit>` to a digest,
   and verifies the digest's commit-bound attestation. A moved mutable tag fails
   this check.
7. GitHub authenticates to GHCR with `GITHUB_TOKEN` package permission and copies
   the verified image manifest into same-region private ECR using short-lived AWS
   OIDC credentials.
8. A narrowly scoped release step retrieves the exact prebuilt web archive,
   uploads immutable hashed assets under the commit release prefix, publishes
   and directly verifies the new Lambda version, then moves `live`.
9. The release updates the short-cached entry HTML only after every referenced
   object exists and the new API is active, invalidates that entry object, and
   waits until CloudFront returns its expected checksum. The API remains
   compatible with the previous frontend for at least the maximum HTML cache
   and rollback window.
10. Public readiness and known-search smoke tests run through the canonical host
    in desktop and mobile browser viewports.
11. Failed verification restores the prior entry-object version, invalidates and
    verifies it at CloudFront, then restores the prior compatible alias version;
    immutable assets remain available for rollback until retention permits
    deletion.
12. The release promotes the existing CLI/data artifacts without rebuilding.

The release workflow never rebuilds application code or data.

## GitHub Actions

### Pull Requests

Required checks:

- repository hygiene and document formatting;
- Ruff formatting and linting with warnings denied;
- BasedPyright and mypy type checking;
- pytest unit and integration tests;
- documented mutation or fault-injection cases for safety-critical decisions,
  automated after a Python 3.14-compatible runner is selected;
- fixture ingestion and deterministic artifact checks;
- dependency license/advisory policy;
- Compose build and real browser-to-API search smoke test.

PRs use committed fixtures and do not receive production secrets or raw source
access.

### Main

The main workflow obtains short-lived AWS credentials through GitHub OIDC only
when it needs the private raw snapshot or immutable build-artifact storage. It
builds the pinned complete artifact, runs integrity tests, builds and scans the
image, executes the runtime path, pushes to private GHCR, signs the digest, and
stores commit-bound checksums for the CLI/data artifacts.

### Scheduled Watchers

Separate scheduled workflows implement phase 0 (see
[data-sources.md](data-sources.md)): they poll RSK and NFZ endpoints daily,
archive every exact metadata observation and selected in-scope source file to
the private versioned S3 snapshot bucket (with checksums computed at archive
time), and retrieve BAW document graphs and files through anonymous HTTP APIs.
The watcher role can conditionally create content-addressed objects under
archive prefixes but cannot overwrite or delete them; conditional
compare-and-swap updates are allowed only under a separate state prefix. Issues
#6-#8 emit immutable change records or incidents; issue #21 later opens data
pull requests carrying manifest changes and human-readable diffs. A blocked
GitHub runner falls back to local/QNAP execution. Browser automation requires a
separate task backed by evidence that it works from the affected environment.
Watcher jobs have no deployment, ECR, or Lambda permissions. Watcher failures
and upstream schema drift raise alerts rather than failing silently.

### Release Tags

The AWS trust policy restricts audience and repository/ref claims to the
expected repository and release tags. The release role can write the deployment
ECR repository, update only the specific Lambda function and alias, read only
the selected immutable web archive, create versioned objects in the frontend
bucket, read only the recorded current and prior frontend object versions,
never delete object versions, and invalidate only the project CloudFront
distribution. It has no Terraform-state permission and no GitHub package
permission. GHCR access comes from `GITHUB_TOKEN` with explicit `packages: read`
workflow permission.

A separate infrastructure workflow and OIDC role run reviewed Terraform plans
and applies. That role can lock and update remote state but cannot publish
application releases unless an infrastructure change requires it.

GitHub Free private repository planning limits observed during design were
2,000 Actions minutes per month, 500 MB shared package/artifact storage, and
10 GB Actions cache. Private deployment environments were unavailable. These
limits can change and must be rechecked before implementation. The design avoids
tag rebuilds and retains only a bounded number of rollback images.

## Container

The multi-stage API image targets `linux/amd64` for portability across
GitHub-hosted builders, QNAP, Mikr.us, and Lambda. It includes the pinned Python
environment, ordinary FastAPI/ASGI service, read-only database, provenance, and
Lambda Web Adapter. Static frontend assets remain a separate checksummed
artifact.

Before promotion, CI inspects the registry media type and architecture and runs
the exact ECR digest with Lambda-compatible settings. Build provenance and
signatures are attached as supply-chain metadata, not substituted for the
runnable image manifest.

Runtime requirements:

- non-root user and unprivileged port;
- read-only filesystem;
- no shell or package manager unless required for a demonstrated runtime need;
- no watcher sources, test fixtures, compilers, credentials, or raw snapshots;
- deterministic database path and dataset release configuration;
- liveness and readiness endpoints;
- graceful termination;
- OCI labels for source commit, build, dataset release, and licenses.

Compose runs the same API image locally without Lambda-specific orchestration.
A small unprivileged local edge container serves the exact static web bundle and
proxies `/api/*`, `/healthz`, and `/readyz` to the API image, preserving the same
origin and relative URLs used in production. A real browser-to-API request tests
that route.

Issue #6 owns a separate immutable watcher image or Dockerfile target. Compose
and scheduled GitHub Actions execute the same watcher digest, entry point, and
arguments, with a persistent filesystem archive mounted at
`/var/lib/minos/archive` locally and S3 selected in the cloud. The API image
contains no watcher source. Temporary directories are reserved for tests and
incomplete downloads, not durable local state.

## AWS Architecture

### Request Path

```text
Browser
  -> Cloudflare DNS-only record
  -> CloudFront distribution
     -> default/static paths -> S3 OAC -> private frontend S3 bucket
     -> /api/* and probes -> Lambda OAC -> AWS_IAM Function URL qualified by live
        -> Lambda Web Adapter
        -> minos.http FastAPI service
```

The frontend executes in mobile and desktop browsers. CloudFront edge delivery
keeps static startup independent of Lambda cold starts. Search, filtering,
version comparison, and later grouping use bounded API calls. An undefined
future conversational `ask` capability is not assumed to fit this runtime; its
streaming, model, privacy, and latency requirements need a separate decision.

The Function URL is created for the `live` alias qualifier, not `$LATEST`, so an
alias update changes public traffic without changing the URL. It is not public
anonymous origin access. CloudFront signs origin requests with OAC. Terraform
uses separate S3 and Lambda origin access controls. The S3 REST-origin bucket
policy and both `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`
resource-policy permissions are restricted to the exact CloudFront distribution
ARN and, for Lambda, the alias-qualified function.

Initial search uses GET requests; signed POST requests to a Lambda Function URL
require correct `x-amz-content-sha256` handling and are not needed for `v0.0.1`.
The `v0.0.2` stateless grouping endpoint is a POST and requires that handling
to be designed and tested before implementation. Its encrypted payload
necessarily traverses CloudFront, but it must never appear in CloudFront,
Lambda platform, or application logs.

CloudFront uses separate behaviours:

- hashed static assets use a long-lived cache policy;
- HTML uses a short revalidation policy;
- `/api/*`, `/healthz`, and `/readyz` use caching disabled;
- the API origin request policy forwards only `system`, `q`, `date`, and
  `limit`, with no cookies or viewer authorization header;
- CloudFront access logging remains disabled until a privacy review addresses
  medical terms in URLs and client identifiers.

### Terraform Resources

The AWS stack owns:

- private frontend S3 bucket with versioning, encryption, and CloudFront OAC;
- private versioned build-artifact S3 bucket for commit-addressed web archives;
- private ECR repository and lifecycle policy;
- GitHub OIDC provider or references to an account-level provider;
- narrowly scoped build and release roles;
- Lambda function using the exact ECR image digest;
- Lambda versions and a `live` alias whose target is release-managed;
- reserved concurrency as a cost and overload guard;
- alias-qualified Function URL with `AWS_IAM` authorization;
- both CloudFront invocation policy statements scoped to the distribution ARN;
- separate S3 and Lambda CloudFront Origin Access Controls and one distribution;
- explicit cache and origin-request policies per behaviour;
- ACM certificate in `us-east-1` for CloudFront;
- CloudWatch log group with finite retention;
- alarms for errors, throttles, duration, and readiness failures;
- account or project budget notifications;
- private versioned, content-addressed S3 storage for source snapshots and
  artifacts, with write-once archive prefixes and a separate conditional state
  prefix for the watcher role.

Terraform owns stable resource configuration. The release workflow owns Lambda
image updates, published versions, and the `live` alias target through narrowly
scoped AWS APIs. Terraform declares lifecycle ignores for only those dynamic
release fields; all other drift remains visible. This avoids both Terraform
state writes during a release and unacknowledged ownership conflicts.

Provisioned concurrency starts at zero. Memory, timeout, and reserved concurrency
are selected from measured data rather than copied from a generic template.

CloudFront pay-as-you-go is the declarative baseline. Support for the newer
flat-rate PricingPlanManager product was incomplete in the Terraform AWS provider
during research and must not force a console-managed production exception.

### Remote State

`infra/bootstrap` creates a dedicated private S3 state bucket through a small
one-time local state. Bootstrap state remains separate and receives an encrypted
independent backup. Each Terraform root initializes directly against a distinct
remote-state key; there is no empty phase-0 state to migrate. The state bucket
uses:

- versioning;
- encryption;
- public access blocks;
- least-privilege access;
- native S3 state locking;
- a recovery procedure for prior state versions.

The `phase0` root owns the snapshot bucket, watcher identity, and monitoring.
Later `foundation` and `service` roots consume its outputs where needed but do
not import or co-own those resources. Production state is never stored as a
GitHub artifact or committed to Git. Bootstrap state and recovery metadata are
backed up securely, including an encrypted independent copy outside the AWS
deployment account.

## Cloudflare And Domains

After clearance and registration:

- `minoshealth.com` is canonical;
- `minoshealth.pl` is proxied by Cloudflare and uses a Terraform-managed
  Redirect Rule to redirect permanently to `.com` while preserving a safe path
  and query string;
- `minos.health`, if purchased after counsel review, redirects the same way;
- Cloudflare manages DNS with Terraform;
- the primary CloudFront record is DNS-only to avoid an unnecessary double CDN;
- the apex uses Cloudflare CNAME flattening where required;
- DNSSEC is enabled after registrar and Cloudflare delegation are verified;
- domain registration, account recovery, and renewals remain outside Terraform.

Cloudflare Tunnel is not part of the AWS profile. It is reserved for local/QNAP
or Mikr.us fallback ingress.

Terraform models public origin selection explicitly. Normal mode creates the
DNS-only `.com` record to CloudFront. Fallback mode replaces it with the proxied
Cloudflare Tunnel target after the exact image digest passes tests on a separate
fallback hostname. Reverting the variable and applying the reviewed plan returns
traffic to CloudFront. The runbook records DNS/TLS validation and propagation;
operators do not improvise a console-only route change.

## Secrets

GitHub stores only secrets that cannot be replaced by OIDC. AWS credentials are
short-lived. The runtime should not require application secrets for a bundled
read-only database.

Local development reads ignored `.env` files based on committed `.env.example`
for application secrets. Local cloud credentials stay in an external profile or
keychain. Production workload secrets live in AWS Secrets Manager and are
injected into the relevant runtime or workflow. Operator, registrar, SSH, and
recovery credentials stay in the password manager. Ordinary endpoints,
timeouts, regions, bucket names, and feature flags are configuration, not
secrets, and remain Terraform inputs or environment variables. The phase-0 RSK
watcher uses anonymous source endpoints and should require no application
secret.

Registrar credentials, recovery codes, SSH material for fallback hosts, and
emergency credentials live in the password manager. They must not be copied
from another project's deployment files.

## Why AWS Lambda

AWS Lambda best matches the chosen priorities:

- Terraform covers stable production resources and route selection;
- GitHub OIDC removes static AWS keys;
- immutable Lambda versions and aliases provide atomic rollback;
- Lambda Web Adapter preserves the standard HTTP image;
- no host patching or runtime disk backup is required;
- low traffic can remain inexpensive.

Private S3 plus CloudFront serves the responsive frontend independently, so
mobile startup does not wait for Lambda. Lambda is evaluated on API latency,
including cold starts. Provisioned concurrency is enabled only if representative
measurements justify its fixed cost.

These benefits are hypotheses until representative benchmarks confirm startup,
memory, throughput, and cost.

## Mikr.us Fallback

Point-in-time service facts observed during planning:

| Plan | Memory | Disk | Price | Docker |
| --- | ---: | ---: | ---: | --- |
| 1.0 | 384 MB | 5 GB | 35 PLN/year | No |
| 2.1 | 1 GB | 10 GB | 75 PLN/year | Yes |
| 3.0 | 2 GB | 25 GB | 130 PLN/year | Yes |

Mikr.us uses LXC containers on shared infrastructure in Finland. It supplies
IPv6 and forwarded IPv4 ports, including defaults based on server ID and a
limited number of additional ports. Terms allow commercial services but offer
no SLA or downtime compensation. The user is responsible for backups. The
optional shared "Strych" storage observed during research was only 200 MB and is
not an adequate recovery strategy.

If selected, use `3.0`. Purchase and initial credentials are manual. Ansible
bootstraps Docker and Compose over pinned SSH. The host pulls a private GHCR
digest with a read-only package token. Minos and `cloudflared` run without
publishing the application port. Deployment waits for readiness and a real
search, then restores the previous digest on failure.

Watchtower does not promote production releases.

## Other Evaluated Targets

- **QNAP**: useful as a local or emergency profile, not the primary public
  service. It uses Compose and Cloudflare Tunnel and must not reuse credentials
  from another project.
- **Cloudflare Containers**: potentially inexpensive and able to sleep, but adds
  Worker/Durable Object orchestration and platform-specific startup behaviour
  without improving the current Terraform and artifact story.
- **Kubernetes**: rejected. The service does not need a cluster, Helm, or its
  operational overhead.

## Promotion And Rollback Evidence

A successful release record contains:

- source commit and signed tag;
- verified commit-to-digest build attestation;
- single-architecture runnable image media type;
- GHCR and ECR digest equality;
- web bundle checksum, immutable artifact key, frontend entry-object VersionId
  and ETag, and their previous release values;
- dataset release and checksums;
- CLI/data artifact checksums;
- infrastructure revision, plus Terraform plan/apply evidence if infrastructure
  or route selection changed;
- prior and new Lambda version and alias weights;
- API/web compatibility range and CloudFront entry invalidation result;
- readiness and known-search results through the public hostname;
- representative post-deploy latency/error sample.

A quiet period alone is not release verification.
