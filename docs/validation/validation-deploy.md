# Validation Deployment Updates

Status: updates run through `scripts/deploy_validation.py` (#109), described in
[Automated Updates](#automated-updates). The manual procedure below is what the
tool automates, and it stays the reference and fallback; it was last performed by
hand on 2026-10-02 for `b599f3e` (#106). This is the temporary public-data
validation path from [reference-web-rollout.md](reference-web-rollout.md), not
the signed product release of #41 or the full Compose deployment of #27.

## Scope

An update replaces exactly three identities on the Mikr.us host:

- the reference API image digest, which also moves the API to the other colour
  (`reference-api-blue` or `reference-api-green`, see
  [Colour Activation](#colour-activation-without-downtime-192));
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
   startup grace to 120 seconds. The API moves to the other colour, and the
   edge moves to that colour's network and dependency. Any other starting
   state or network topology is refused.
7. **Pull** both digests into `image-pull.log`.
8. **Activate.** Require `MemAvailable` of at least 300 MiB plus the API
   memory limit, record the operational container IDs and restart counts
   (`ob`), install `compose.json` as `compose.yaml`, then use the host helper's
   bounded readiness transition. It starts each service separately and retires
   the superseded colour last:

   ```sh
   docker compose --project-name minos --file /opt/minos/compose.yaml \
      up --detach --no-deps reference-api-blue
   # The helper requires real API health before starting edge.
   docker compose --project-name minos --file /opt/minos/compose.yaml \
     up --detach --no-deps edge
   # Only after real edge health: stop and remove the previous colour.
   docker stop --time 10 <previous API container ID>
   docker rm <previous API container ID>
   ```

   Never restart `minos-compose.service`: its start path stops the watcher.
   Both starts and all inspections share one 300-second deadline. Initial
   unhealthy status may recover; exits, OOMs, new restarts, ambiguous state or
   lost API readiness fail the transition. If activation fails, reinstall the
   rollback file and attempt the same transition once with a new deadline.
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
`compose.yaml` and repeats the step 8 bounded readiness transition. The previous
colour is a different service, so it starts beside the current one; if it is
still running (a failed activation) Compose reuses it unchanged. Without room
for two APIs, rollback retires the current API first and accepts downtime rather
than refusing recovery. The previous bundle and
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
2. Dispatch full CI and `reference-validation.yml` on `main` and wait for both
   to succeed. CI no longer runs on pushes to `main` (#174), so this is the only
   full check of merged PRs combined, including any merged while behind `main`:

   ```sh
   gh workflow run ci.yml --ref main
   gh workflow run reference-validation.yml --ref main
   gh run list --branch main --limit 2
   ```

3. Build the bundle from the reviewed manifest (see Inputs for missing blobs):

   ```sh
   uv run python -m minos data build \
     --manifest manifests/sources/reference-catalog-public-validation.json \
     --archive-root data/archive --output data/bundles/bundle-<revision>
   ```

4. Deploy:

   ```sh
   uv run python -m scripts.deploy_validation deploy \
     --revision <main sha> --run-id <publish run id> \
     --bundle /path/to/new/bundle --chromium /usr/bin/chromium
   ```

   Then look at the screenshots in
   `test-output/validation-deploy/<revision>/browser/`.

`verify` also refuses a public homepage containing `/cdn-cgi/`: a proxy
rewrite such as Cloudflare email obfuscation changes served HTML and injects a
script (see [the site audit](site-audit.md)).

`deploy` runs `plan`, `stage`, `activate`, `verify` and, unless `--no-prune`,
`prune --keep 1 --apply`. It stops at the first failure with one JSON `failed`
event; every step can be rerun on its own with `--revision <sha>`.

### Commands

| Command | Checks and effect | Host change |
| --- | --- | --- |
| `inspect` | live identities, live revision, Compose SHA-256, memory, disk, container health | none |
| `plan` | revision is remote `main`; publish run is a successful `workflow_dispatch` of that commit on `main`; artifact matches; bundle opens and its database matches its manifest; records the live Compose SHA-256; writes `test-output/validation-deploy/<revision>/plan.json` | none |
| `stage` | live Compose unchanged since `plan`; deterministic archive upload (skipped if the bundle is installed); checksum; atomic install as root `0755`/`0444`; `deployment.json`; rollback copy; identity swap that asserts all previous identities; pulls both digests and requires `linux/amd64` plus the source revision label | new files only |
| `activate` | Compose unchanged since `plan`; at least 300 MiB plus the API memory limit available; operational snapshot; installs the staged file; starts the new API colour beside the live one, then edge, under one 300-second real-health deadline, then retires the old colour under a separate 60-second bound; on failure restores the rollback file with one separately bounded attempt; requires unchanged watcher, tunnel and Kuma container IDs and restart counts and healthy validation services; rerunning on an active release reports `already_active` | live Compose |
| `verify` | public `/readyz` serves the planned dataset identity; for rank bundles one exact page shows the expected rank and, with `--chromium`, the desktop and mobile rank acceptance runs; writes `activation-receipt.json` on the host (with the host's own activation time) and locally | receipt only |
| `rollback --revision <sha>` | accepts only that candidate or its exact retained prior Compose; restores both validation services; requires healthy API/edge, expected running images and unchanged operational services; checks public readiness and homepage before reporting complete rollback | live Compose |
| `prune [--keep n] [--apply]` | lists, or with `--apply` removes, unreferenced images, bundles and staged archives | deletes only with `--apply` |

Plans, receipts and screenshots stay in the ignored `test-output/` tree.

### Failure Diagnostics And Explicit Recovery (#162)

Host refusals use a bounded validated JSON envelope even when SSH exits nonzero.
The caller preserves its error and structured recovery details; a failed process
cannot turn a success-shaped response into success. Empty or invalid responses
remain explicit transport/protocol failures, not inferred authentication errors.

Compose stdout and stderr are drained together with a 256 KiB output ceiling
and a command deadline capped by the remaining shared startup budget.
Timeout/output exhaustion kills the command process group. Raw
output is discarded: retained records contain outcome, exit code, elapsed time,
byte count and allowlisted failure classes only. This avoids retaining paths,
environment values or credentials printed by dependencies. A generic command
failure remains generic if no known class matched; it is never guessed to be
a timeout. The actual causes of the earlier #161/#164 failures remain unknown.

`compose-operations.log` and `rollback-operations.log` are private bounded JSON
records, written on success or failure. Each keeps at most one predecessor as
`*.previous.log`. `rollback-result.json` records both service states (including
created/missing), running-image agreement, operational isolation and the explicit
recovery command. `rollback-ob.json` preserves the original operational baseline
across retries. Stopped containers are inspected too, so an unstarted edge cannot
disappear from the result. A missing, unhealthy or wrong-image service prevents
success, even if Compose exits zero. Failed activation retains its own failure
details alongside the result of its single automatic restoration attempt.

For an incomplete rollback, inspect the returned fixed-field diagnostics and
resolve the identified failure, then explicitly repeat:

```sh
uv run python -m scripts.deploy_validation rollback --revision <failed revision>
```

The repeat is idempotent for the retained prior Compose, but refuses unrelated
live Compose or a changed operational baseline. No automatic unbounded retry or
watcher/tunnel/monitoring restart is used. The host reports `restored` only after
both services are healthy with expected images and unchanged operational
identities/restart counts. Its public-ingress field is `not_checked`; the local
command probes public `/readyz` and `/` before emitting `rolled_back`. A public
failure reports restored local state and failed ingress rather than complete
recovery. Automatic activation restoration also leaves public ingress explicitly
unchecked; it remains a failed activation requiring operator verification.

### Bounded Startup Readiness (#181)

The 2026-10-12 activation incident showed that Compose may abort on an initial
unhealthy result before its wait timeout. An isolated candidate reached readiness
after 173.15 seconds under the existing one-CPU/512 MiB limits, with no OOM or
restart and observed CPU/I/O pressure. The source of that contention remains
unidentified. A five-minute owner-approved wait then enabled activation.

Activation and rollback now share the same explicit readiness path: API first,
then edge, one 300-second deadline per attempt including launches and inspections.
The exact prior Compose bytes and health policy survive rollback. A created but
unstarted container is valid pre-start state; after launch, missing healthchecks,
missing/duplicate containers, exits, OOMs, new restarts and identity changes fail.
The API must remain healthy while the edge starts. Diagnostics retain only fixed
fields, including the phase and which services were observed initially unhealthy.

Deterministic tests exercise deadline exhaustion and process/state failures.
The disposable container probe additionally forces initial API unhealthy status,
injects a partial edge restoration failure, retries the exact prior Compose and
checks HTTP readiness and three unchanged operational sentinels. Local synthetic
checks do not establish production contention performance.

Production acceptance: the first activation on the permanent path, release
`736ba9f` at 2026-10-13T14:09:23Z, observed the API initially unhealthy (the
pattern that aborted the 2026-10-12 activations near 160 seconds) and completed
the edge phase in 212.7 seconds of the 300-second deadline, exit 0, with no
failure class and no rollback. Watcher, tunnel and Kuma kept their IDs and restart
counts. This is one activation under the host load of that moment, leaving about
87 seconds of headroom; the 2026-10-12 contention source remains unidentified.

### Startup Work Before Readiness (#189)

The next activation, `680476d` with a freshly uploaded bundle, needed 267.6 of
300 seconds. The host log showed about 203 seconds between container start and
the first warm-up step, against about 5 seconds locally. Before readiness the API
opened four pool connections, and each re-hashed the whole ~445 MB database.
Page cache counts against the 512 MiB container limit, so the file could not stay
cached beside the process and each hash plausibly re-read the VPS disk; a cold,
just-uploaded bundle made it worse. Characteristics re-validation also scanned
all evidence once per group (78 million string strips).

The database is now hashed once per process; later connections accept it only
while its device, inode, size, mtime and ctime are unchanged, so a rewritten or
replaced file is hashed again and refused. Evidence lookup is indexed in one pass
with identical interpretation output (fingerprinted on all five real sheets).
Every validation still runs. Under the production limits locally, readiness fell
from about 12 to about 8.5 seconds. The API now logs fixed-field `api_startup`
timings (`pool_ms`, `trees_ms`).

On the host, release `f9a0a72` (2026-10-13) reached `api_startup` 87 seconds
after container start (pool 59.4 s, trees 6.1 s) against about 203 before, and
the edge transition took 120.5 of 300 seconds against 267.6; the API was never
unhealthy. That activation reused the bundle installed an hour earlier, so the
page cache may have been warm; a freshly uploaded bundle records the cold case
in the same log.

Local regression evidence covers nonzero remote refusals, partial restoration,
missing edge despite a successful Compose exit, unrelated Compose refusal,
changed operational baseline on retry, bounded timeout/output capture and the
public-ingress success gate. The real disposable API/edge smoke injects failure
after API restoration while the dependent edge is `created`, then repeats
rollback and verifies both healthchecks plus HTTP readiness/homepage. Three
inert operational sentinels retain IDs/restart counts. This smoke runs in the
validation-image CI workflow. It does not fault-inject production.

### Colour Activation Without Downtime (#192)

Until #192, activation recreated `reference-api` in place, and the edge
returned 502 for the whole API startup: 87 to 270 seconds on the host. Uptime
Kuma recorded 502 episodes on 2026-10-09 through 2026-10-14. Those on 2026-10-13
and 2026-10-14 matched release activations, and each sent Telegram down/up
alerts.

The API now alternates between `reference-api-blue` and `reference-api-green`.
Each colour is alone on its own internal network (`reference-blue`,
`reference-green`) under the alias `reference-api`. The edge image and its
`proxy_pass http://reference-api:8081` are unchanged: nginx resolves the name
once at startup on the only API network the edge joins. The legacy
`reference-api` service on `reference` is accepted as the starting colour, and
the first activation moves it to blue.

One transition serves activation and rollback:

1. start the target API beside the live one (the live API keeps serving
   through the old edge) and wait for real health;
2. recreate the edge on the target colour's network and wait for real health;
3. stop (10-second grace) and remove every API container that the live Compose
   no longer defines, under a separate 60-second bound. Retirement is cleanup
   after real health, so it never consumes the startup deadline; without room
   for two APIs, rollback retires first and the 300 seconds start afterwards.

The shared 300-second deadline, fail-closed process checks, exact prior Compose
rollback and operational isolation are unchanged. Retirement happens only after
both new services are healthy, so a failed activation restores by reusing the
untouched previous API and edge. A retained superseded colour fails the
`restored` check. The edge shares a network with exactly one API, so one edge
never mixes colours. A retired colour's empty internal network may remain; the
next activation of that colour reuses it.

The edge recreation remains a short interruption. The disposable container
smoke (`scripts/smoke_validation_rollback.py`) starts a candidate API that
waits 10 seconds before serving and polls edge readiness every 100 ms. On
2026-10-16 locally: 184 probes, longest unready interval 0.5 seconds, legacy API
retired after the new edge was healthy, and the partial-rollback recovery passed
with both colours. Production overlap costs one extra API (memory limit 512 MiB)
on the one-CPU host while the candidate starts, so responses may slow but not
fail.

The first production activation (`2b6e26b`, 2026-10-19) kept the API startup
outage-free: Uptime Kuma (60-second interval) recorded no down beat, and the
whole transition took 107.4 seconds. An external probe at one request per second
saw about 24 seconds of 502 during the edge recreation, against 87 to 270 seconds
before #192 and 0.5 seconds in the local smoke. The owner accepted this short
edge window as a known limit; no edge overlap is planned. `b0dee0d` activated
the same way the same day.

### Failed-start Diagnostics (#198)

Two of the first four colour activations rolled back because the new API exited
during startup (2026-10-16 `2b6e26b` and 2026-10-19 `b0dee0d`). The second exited
with code 3, Uvicorn's lifespan failure, 72 seconds in, during the reference
pool load, without an OOM kill; the retry succeeded both times. The cause is
unknown: the `local` log driver deletes logs with the container that rollback
removes, and Uvicorn runs at `--log-level critical`.

The API now logs `{"event": "api_startup_failed", "error_class": ...}` on a
startup exception, the class name only and never the message or traceback. When a
candidate fails the startup check, the host reads its last 200 log lines and its
cgroup `memory.events` under a separate 10-second bound before rollback, and
keeps `candidate`: running, OOM flag, restarts, the validated `error_class`, and
the `max`, `oom` and `oom_kill` counters. A missing log or cgroup file leaves
`null` and never changes the outcome. Raw log lines are not kept.

### Data-Only Stages (#141 Phase C)

`stage-data` stages a new database under the live API and edge images, code
revision and Compose topology; it copies a bundle directory instead of an
upload and pulls no image. Its stage key is `data-<database SHA-256>`, used
for `validation-stage/`, `validation-rollback/`, `activate`, `receipt` and
`rollback --revision`. `inspect` keeps reporting the code revision as
`live_revision`, adds the stage as `live_stage` and lists the live bundle's
release identities as `live_sources`; `plan` refuses a bundle without all of
them. The processor runner calls these functions in-process under the
deployment lock it already holds.

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
