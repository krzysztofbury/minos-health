# Validation Deployment Updates

Status: updates run through `scripts/deploy_validation.py` (#109), described in
[Automated Updates](#automated-updates). The manual procedure below is what the
tool automates, and it stays the reference and fallback; it was last performed by
hand on 2026-10-02 for `b599f3e` (#106). This is the temporary public-data
validation path from [reference-web-rollout.md](reference-web-rollout.md), not
the signed product release of #41 or the full Compose deployment of #27.

## Scope

An update replaces exactly three identities on the Mikr.us host:

- the `reference-api` image digest;
- the `edge` image digest;
- the read-only reference bundle, selected by its database SHA-256.

Everything else stays as it is: `watcher`, `cloudflared`, `uptime-kuma`, their
volumes, secrets, schedules and control state. No watcher database is restored
and the maintenance-only Ansible playbook is never run over an active
validation deployment.

`scripts/render_validation_compose.py` covers only the first cutover from the
maintenance edge: it refuses a base that already contains `reference-api`.
Every later update edits the live Compose file instead, swapping the three
identities and the API startup grace period described below.

The update path migrates the known legacy `reference-api` healthcheck
`start_period` from `20s` to `120s`, matching the first-install renderer and
the measured full-conditions startup. An existing `120s` stays unchanged;
an unknown startup policy is refused rather than overwritten. Probe command,
interval, timeout and retries are preserved. Rollback restores the exact prior
Compose, including its prior startup policy. On 2026-10-04 the first #32/#130
cutover rolled back because the old update path had retained `20s` despite a
116-second isolated startup measurement. This migration fixes that path.

## Inputs

| Input | Source | Check |
| --- | --- | --- |
| Source revision | reviewed `main` commit | `git ls-remote origin refs/heads/main` |
| API and edge digests | manual `reference-validation.yml` dispatch on that commit | job verifies `linux/amd64` and the `org.opencontainers.image.revision` label |
| Reference bundle | `minos data build` from a reviewed source manifest and the private archive | database SHA-256 in the bundle manifest; deterministic rebuild |
| Previous identities | live `/opt/minos/compose.yaml` on the host | must equal the last activation record |

The publish job uploads `validation-release.json` as the `validation-release`
run artifact (revision, run ID and attempt, both exact `@sha256:` references,
90-day retention). Before #109 the digests were only in the step summary and
were copied from job logs.

The bundle build reads every pinned blob from the archive root. A blob that is
missing locally (on 2026-10-02 the three komunikat files existed only in the
private watcher archive) may be restored from the official upstream URL in the
reviewed manifest, but only if its byte size and SHA-256 match the manifest
exactly. Any mismatch stops the update; never re-pin a manifest to make a
build pass.

## Host Layout

All paths are under `/opt/minos`, root-owned.

| Path | Content | Mode |
| --- | --- | --- |
| `compose.yaml` | live Compose project `minos`, JSON | `0600` |
| `reference/<database-sha256>/` | `manifest.json`, `minos.db`, `review.txt` | dir `0755`, files `0444` |
| `validation-stage/<revision>/` | `bundle.tar.gz`, `deployment.json`, `compose.json`, `image-pull.log`, `ob`, `oa`, `compose-operations.log`, `activated-at.txt`, optional `activation-receipt.json` | dir `0700` |
| `validation-rollback/<revision>/compose.yaml` | the live Compose file captured before that revision was activated | `0600` |
| `.validation-deploy.lock` | exclusive lock held by every mutating tool command | `0600` |

`deployment.json` uses the `Deployment` model of
`scripts/render_validation_compose.py`: `api_image`, `edge_image`,
`source_revision` and `database_sha256`.

Before #109 nothing pruned these paths. Measured on 2026-10-02 after five
validation revisions: three bundles of 62 to 130 MiB, stage directories of up to 74 MiB,
and 2.9 GB of images of which 1.4 GB were reclaimable, with 19 GB of the 25 GB
disk free. Each update adds about 390 MB of images, one bundle and one staged
archive.

## Manual Update Procedure

Run from the repository root with the materialized inventory described in
`infra/ansible/README.md`. Each step stops on the first failure.

1. **Preflight.** Confirm that remote `main` is the reviewed revision and that
   the publish run for that revision succeeded. Read the host state without
   changing it: service health, available memory (at least 300 MiB), free
   disk, and the current `reference-api` and `edge` images and bundle path.
2. **Build the bundle** locally into a new directory and record its database
   SHA-256. Open it with `open_reference_bundle` and query one known exact code
   for every newly exposed feature.
3. **Upload.** Create `validation-stage/<revision>` (it must not exist), copy a
   deterministic `tar --owner=0 --group=0 --sort=name` archive of the three
   bundle files, and compare the archive SHA-256 on both sides.
4. **Install the bundle** into a temporary sibling directory, verify the
   database SHA-256, set ownership and modes, then rename it to
   `reference/<database-sha256>`. The final path must not exist beforehand.
5. **Record** `deployment.json` and copy the live `compose.yaml` to
   `validation-rollback/<revision>/compose.yaml`.
6. **Render** `validation-stage/<revision>/compose.json` by loading the live
   file and asserting the expected previous API image, edge image,
   `MINOS_REFERENCE_DATABASE_SHA256` and the single read-only `/app/data`
   bind source before replacing those four values and migrating the known API
   startup grace to 120 seconds. Any other starting state
   is refused.
7. **Pull** both digests into `image-pull.log`.
8. **Activate.** Record the operational container IDs and restart counts
   (`ob`), install `compose.json` as `compose.yaml`, then run:

   ```sh
   docker compose --project-name minos --file /opt/minos/compose.yaml \
     up --detach --no-deps --wait --wait-timeout 180 reference-api edge
   ```

   Never restart `minos-compose.service`: its start path stops the watcher.
   If `up` fails, reinstall the rollback file and run the same command.
9. **Confirm isolation.** Record `oa` and require it to equal `ob`: watcher,
   tunnel and Kuma keep the same container IDs and restart counts.
10. **Verify publicly** through `https://minoshealth.pl`: `/readyz` is ready;
    one exact request for each newly exposed API route returns the expected
    evidence; one code page renders it; then run the browser review at desktop
    and mobile width (see the public-target note in
    `reference-web-rollout.md`) and look at the screenshots. Do not run load or
    stress tests on the host.
11. **Receipt.** Record revision, digests, database SHA-256, readiness dataset
    identity, activation time, previous revision and rollback path, available
    memory, the isolation result, the checks run and their scope. Add the same
    facts to the delivering issue.

### Rollback

Rollback restores `validation-rollback/<revision>/compose.yaml` as
`compose.yaml` and repeats the step 8 `up` command. The previous bundle and
images remain installed, so rollback needs no network access. Bundles must not
be deleted while any retained rollback file references them.

## Lessons From 2026-10-02

- Identities were copied by hand from a job log, the live host and a local
  build into a one-off script. A typo would only have been caught by the
  previous-identity assertions.
- The publish job exposes digests only in human-readable output.
- The local archive lacked pinned blobs that the private watcher archive held.
- The rank browser script always mapped the hostname to loopback; #108 limits
  that to `http:` targets so the same script reviews the public site.
- The agent harness gates production reads, uploads and deploys separately, so
  an agent-run update needed three owner approvals. A single checked-in
  command can be allowed once by an exact rule instead.

## Automated Updates

`scripts/deploy_validation.py` runs the manual procedure with the same
assertions. Local steps resolve identities; host steps stream the stdlib-only
`scripts/validation_host.py` to the host's Python 3.12 over the SSH options of
`infra/ansible/ansible.cfg` (pinned ED25519 host key, key-only, batch mode).
Parameters travel base64-encoded and are validated on both sides; nothing is
interpolated into a remote shell. CI never receives SSH access, host secrets or
the Docker socket.

### Release

1. Merge the reviewed change to `main`.
2. Dispatch `reference-validation.yml` on `main` and wait for success:

   ```sh
   gh workflow run reference-validation.yml --ref main
   gh run list --workflow reference-validation.yml --branch main --limit 1
   ```

3. Build the bundle from the reviewed manifest (see Inputs for missing blobs):

   ```sh
   uv run python -m minos data build \
     --manifest manifests/sources/reference-ranks-public-validation.json \
     --archive-root ~/.cache/minos-local/archive --output /path/to/new/bundle
   ```

4. Deploy:

   ```sh
   uv run python -m scripts.deploy_validation deploy \
     --revision <main sha> --run-id <publish run id> \
     --bundle /path/to/new/bundle --chromium /usr/bin/chromium
   ```

   Then look at the screenshots in
   `test-output/validation-deploy/<revision>/browser/`.

`deploy` runs `plan`, `stage`, `activate`, `verify` and, unless `--no-prune`,
`prune --keep 1 --apply`. It stops at the first failure with one JSON `failed`
event; every step can be rerun on its own with `--revision <sha>`.

### Commands

| Command | Checks and effect | Host change |
| --- | --- | --- |
| `inspect` | live identities, live revision, Compose SHA-256, memory, disk, container health | none |
| `plan` | revision is remote `main`; publish run is a successful `workflow_dispatch` of that commit on `main`; artifact matches; bundle opens and its database matches its manifest; records the live Compose SHA-256; writes `test-output/validation-deploy/<revision>/plan.json` | none |
| `stage` | live Compose unchanged since `plan`; deterministic archive upload (skipped if the bundle is installed); checksum; atomic install as root `0755`/`0444`; `deployment.json`; rollback copy; identity swap that asserts all previous identities; pulls both digests and requires `linux/amd64` plus the source revision label | new files only |
| `activate` | Compose unchanged since `plan`; at least 300 MiB available; operational snapshot; installs the staged file; `up --no-deps --wait reference-api edge`; on failure restores the rollback file and waits for health; requires unchanged watcher, tunnel and Kuma container IDs and restart counts and healthy validation services; rerunning on an active release reports `already_active` | live Compose |
| `verify` | public `/readyz` serves the planned dataset identity; for rank bundles one exact page shows the expected rank and, with `--chromium`, the desktop and mobile rank acceptance runs; writes `activation-receipt.json` on the host (with the host's own activation time) and locally | receipt only |
| `rollback --revision <sha>` | restores the Compose file captured before that revision and requires unchanged operational services | live Compose |
| `prune [--keep n] [--apply]` | lists, or with `--apply` removes, unreferenced images, bundles and staged archives | deletes only with `--apply` |

Plans, receipts and screenshots stay in the ignored `test-output/` tree.

### Retention

`prune` keeps:

- every image or bundle whose digest appears in the live `compose.yaml`;
- the rollback chain: `validation-rollback/<live revision>`, then the rollback
  file of the release it restores, up to `--keep` steps (1 to 5);
- everything referenced by Compose files owned by other procedures:
  `rollback/`, `candidate/`, `catalog-deploy/*/*/` and `*.compose.yml`
  (watcher, maintenance edge, Kuma bootstrap);
- every image used by any container, running or stopped, and every image
  without a registry digest.

All other images are removed, which covers stale watcher images as well as old
validation releases. Unreferenced bundles, interrupted `.partial` installs and
staged `bundle.tar.gz` copies are removed too; small stage records, logs,
receipts and older rollback files remain as history. `docker image rm` runs
without `--force`, so an image still in use is never deleted. Never replace this
with `docker image prune -a` or `docker system prune -a`: they would delete the
rollback images, and rollback would then need the registry mid-incident.

### Permissions

The owner instructs every production update. An agent runs the tool only after
that instruction, through exact Claude Code permission rules for
`uv run python -m scripts.deploy_validation` subcommands; production reads,
deploys and deletions are separate classifier categories, so `prune --apply`
may need its own approval.

### Verification Of The Tool

`tests/unit/test_validation_deploy.py` runs every host command for real against
a temporary root and a stateful fake Docker CLI
(`tests/fixtures/fake_docker.py`): staging, resume, activation, failed
activation with restore, an operational restart during activation, changed
live Compose, a wrong image build, rollback, lock exclusion, and prune
selection and application. Targeted mutations of the previous-identity check,
the failure restore, rollback-chain retention and operational isolation each
fail the suite. The host program is also checked against Python 3.12 grammar.

### Boundaries

- Not a substitute for #41 signatures and receipt chain or #27 cutover and
  measurements; fold it into #27 when that lands.
- No load, stress or profiling on the host.
