# Validation Deployment Updates

Status: the update procedure below is manual and was last performed on
2026-10-02 for `b599f3e` (#106). A guarded one-command tool is specified at the
end of this document and tracked in #109. This is the temporary public-data
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
identities and nothing else.

## Inputs

| Input | Source | Check |
| --- | --- | --- |
| Source revision | reviewed `main` commit | `git ls-remote origin refs/heads/main` |
| API and edge digests | manual `reference-validation.yml` dispatch on that commit | job verifies `linux/amd64` and the `org.opencontainers.image.revision` label |
| Reference bundle | `minos data build` from a reviewed source manifest and the private archive | database SHA-256 in the bundle manifest; deterministic rebuild |
| Previous identities | live `/opt/minos/compose.yaml` on the host | must equal the last activation record |

The publish job prints the digests only to the step summary. The log lines
of `docker pull` contain the exact `ghcr.io/...@sha256:` references and were
used for `b599f3e`.

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

`deployment.json` uses the `Deployment` model of
`scripts/render_validation_compose.py`: `api_image`, `edge_image`,
`source_revision` and `database_sha256`.

Nothing prunes these paths yet. Measured on 2026-10-02 after five validation
revisions: three bundles of 62 to 130 MiB, stage directories of up to 74 MiB,
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
   bind source before replacing those four values. Any other starting state
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

## Automated Update Tool

Goal: one reviewed command performs the procedure above with the same
assertions, without granting CI any access to the host.

### Shape

`scripts/deploy_validation.py`, standard library plus Pydantic, reusing the
`Deployment` model and the bind/identity checks of
`scripts/render_validation_compose.py`. Subcommands are separate so each
boundary can be approved and resumed independently:

| Command | Effect | Host change |
| --- | --- | --- |
| `plan --revision <sha> --bundle <dir>` | resolve digests, read live state, print and save the plan | none |
| `stage <plan>` | steps 3 to 7 | new files only; running containers untouched |
| `activate <plan>` | steps 8 and 9, automatic rollback on failure | live Compose |
| `verify <plan>` | step 10 and the receipt | none |
| `rollback <revision>` | restore that revision's rollback file | live Compose |
| `prune --keep <n>` | list removable stages, bundles and images; delete only with `--apply` | deletes unreferenced artifacts |

`deploy <revision> --bundle <dir>` may chain `plan`, `stage`, `activate` and
`verify`, stopping at the first failure.

### Required behaviour

- **Exact identities only.** Digests come from a machine-readable
  `validation-release.json` that the publish job uploads as a run artifact
  (revision, run ID, both `@sha256:` references). The tool rejects tags,
  a run on another commit, or a revision that is not remote `main`.
- **Plan binding.** The plan records the SHA-256 of the live Compose file.
  `stage` and `activate` refuse if it changed, so two operators or a manual
  edit cannot interleave.
- **Idempotent staging.** An existing bundle directory is accepted only if its
  database checksum matches; an existing stage directory is accepted only if
  its `deployment.json` is identical. Otherwise the command stops.
- **Bounded remote execution.** Each host step is one SSH call with a fixed
  remote program streamed on stdin. Inputs are validated locally against the
  `Deployment` patterns and passed as environment values, never interpolated
  into shell text. SSH options match the Ansible configuration: the
  materialized `known_hosts`, `StrictHostKeyChecking=yes`, `BatchMode=yes`.
- **Isolation proof.** `activate` fails unless the operational container IDs
  and restart counts are unchanged, and leaves the rollback in place.
- **Explicit rollback.** A failed `up` restores the previous Compose file and
  waits for health before exiting non-zero. A later manual `rollback` uses the
  retained file only.
- **Retention.** `prune` keeps the live revision plus the newest `n` retained
  rollbacks with every bundle and image they reference, and is a dry run unless
  `--apply` is given.
- **Privacy.** Logs and receipts contain identities, timings and status only:
  no request bodies, medical queries, signed URLs, credentials or local paths.
- **Receipts.** `verify` writes `activation-receipt.json` to the stage
  directory and a local copy, with the fields listed in step 11.

### Boundaries

- No GitHub workflow, token or runner receives SSH access, host secrets or the
  Docker socket. Publication stays the existing manual dispatch.
- Not a substitute for #41 signatures and receipt chain or #27 cutover and
  measurements; the tool must be retired or folded into #27 when that lands.
- No load, stress or profiling on the host.
- An agent may run the tool only after an explicit owner instruction for that
  revision, through one exact permission rule for
  `uv run python scripts/deploy_validation.py`.

### Tests

- Unit: the identity swap on representative Compose JSON, including refusal of
  an unexpected previous image, bundle path, environment value, a missing or
  writable `/app/data` bind, and extra changed keys.
- Unit: plan parsing rejects tags, mismatched revisions and malformed digests.
- Unit: `prune` never selects the live or a retained-rollback reference.
- Integration: run `stage`, `activate`, `verify` and `rollback` against a
  disposable local Compose project built from the validation images in
  `reference-validation.yml`, with a temporary root in place of `/opt/minos`,
  and assert that a sentinel service keeps its container ID.

### Acceptance

- One command updates a validation deployment from reviewed `main` with no
  hand-copied identity.
- A changed live Compose file, unexpected previous identity, missing digest,
  checksum mismatch or unhealthy candidate stops the update with the previous
  release serving.
- Watcher, tunnel and Kuma container IDs and restart counts are unchanged.
- A receipt with the step 11 fields exists on the host and locally.
- `prune` reclaims unreferenced artifacts without touching rollback targets.

Estimate: 4 to 6 hours including the publish artifact, tests and one real
update.
