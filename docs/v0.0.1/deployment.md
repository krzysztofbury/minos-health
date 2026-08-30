# Deployment

## Goals

The delivery system must:

- build one portable HTTP image;
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

1. A pull request builds a fixture image and runs an actual container search.
2. `main` builds the pinned full dataset, supported standalone CLI bundles and
   data bundle, and the `linux/amd64` HTTP image.
3. `main` pushes `sha-<commit>`, stores CLI/data artifacts immutably by commit and
   checksum, and creates a signed build attestation binding the image digest to
   the source commit and workflow identity.
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
8. A narrowly scoped release step publishes a Lambda version and moves `live`.
9. Public readiness and known-search smoke tests run through the canonical host.
10. Failed verification restores the prior alias version.
11. The release promotes the existing CLI/data artifacts without rebuilding.

The release workflow never rebuilds application code or data.

## GitHub Actions

### Pull Requests

Required checks:

- repository hygiene and document formatting;
- Ruff formatting and linting with warnings denied;
- mypy type checking;
- pytest unit and integration tests;
- fixture ingestion and deterministic artifact checks;
- dependency license/advisory policy;
- image build and container search smoke test.

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
archive every fetched raw file to the private versioned S3 snapshot bucket
(with checksums computed at archive time), retrieve BAW document graphs and
files through anonymous HTTP APIs, and open data pull requests carrying
manifest changes and human-readable diffs. A browser is fallback only after a
captured challenge. Watcher jobs hold credentials for the snapshot bucket and
PR creation only; they have no deployment, ECR, or Lambda permissions.
Watcher failures and upstream schema drift raise alerts rather than failing
silently.

### Release Tags

The AWS trust policy restricts audience and repository/ref claims to the
expected repository and release tags. The release role can write the deployment
ECR repository and update only the specific Lambda function and alias. It has no
Terraform-state permission and no GitHub package permission. GHCR access comes
from `GITHUB_TOKEN` with explicit `packages: read` workflow permission.

A separate infrastructure workflow and OIDC role run reviewed Terraform plans
and applies. That role can lock and update remote state but cannot publish
application releases unless an infrastructure change requires it.

GitHub Free private repository planning limits observed during design were
2,000 Actions minutes per month, 500 MB shared package/artifact storage, and
10 GB Actions cache. Private deployment environments were unavailable. These
limits can change and must be rechecked before implementation. The design avoids
tag rebuilds and retains only a bounded number of rollback images.

## Container

The multi-stage image targets `linux/amd64` for portability across GitHub-hosted
builders, QNAP, Mikr.us, and Lambda. It includes the pinned Python environment,
ordinary FastAPI/ASGI service, static assets, read-only database, provenance,
and Lambda Web Adapter.

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

Compose runs the same image locally without Lambda-specific orchestration.

## AWS Architecture

### Request Path

```text
Browser
  -> Cloudflare DNS-only record
  -> CloudFront distribution
  -> Origin Access Control signed request
  -> AWS_IAM Lambda Function URL qualified by live
  -> Lambda Web Adapter
  -> minos.http FastAPI service
```

The Function URL is created for the `live` alias qualifier, not `$LATEST`, so an
alias update changes public traffic without changing the URL. It is not public
anonymous origin access. CloudFront signs origin requests with OAC. Terraform
adds both `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` resource-policy
permissions for `cloudfront.amazonaws.com`, restricted to the exact CloudFront
distribution ARN and alias-qualified function.

Initial search uses GET requests; signed POST requests to a Lambda Function URL
require correct `x-amz-content-sha256` handling and are not needed for `v0.0.1`.
The `v0.0.2` stateless grouping endpoint is a POST and requires that handling
to be designed and tested before implementation, including confirmation that
encounter payloads never reach CloudFront or platform logs.

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

- private ECR repository and lifecycle policy;
- GitHub OIDC provider or references to an account-level provider;
- narrowly scoped build and release roles;
- Lambda function using the exact ECR image digest;
- Lambda versions and a `live` alias whose target is release-managed;
- reserved concurrency as a cost and overload guard;
- alias-qualified Function URL with `AWS_IAM` authorization;
- both CloudFront invocation policy statements scoped to the distribution ARN;
- CloudFront Origin Access Control and distribution;
- explicit cache and origin-request policies per behaviour;
- ACM certificate in `us-east-1` for CloudFront;
- CloudWatch log group with finite retention;
- alarms for errors, throttles, duration, and readiness failures;
- account or project budget notifications;
- private versioned S3 storage for source snapshots and artifacts where needed.

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
one-time local state, then migrates the main stack to it. The bucket uses:

- versioning;
- encryption;
- public access blocks;
- least-privilege access;
- native S3 state locking;
- a recovery procedure for prior state versions.

Production state is never stored as a GitHub artifact or committed to Git.
Bootstrap state and recovery metadata are backed up securely after migration,
including an encrypted independent copy outside the AWS deployment account.

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
- dataset release and checksums;
- CLI/data artifact checksums;
- infrastructure revision, plus Terraform plan/apply evidence if infrastructure
  or route selection changed;
- prior and new Lambda version and alias weights;
- readiness and known-search results through the public hostname;
- representative post-deploy latency/error sample.

A quiet period alone is not release verification.
