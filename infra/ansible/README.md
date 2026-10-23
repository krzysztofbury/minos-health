# Mikr.us Host Baseline

This directory owns the reproducible, non-secret baseline for the dedicated
Mikr.us `3.0` production host. Run commands from the repository root so the
materialized SSH host-key file resolves correctly.

The playbook pins key-only root SSH, a default-deny inbound firewall, unattended
security upgrades, bounded persistent journald storage, and exact Docker Engine
and Compose packages from Docker's official repository. Docker exposes only its
root-owned Unix socket, has no unprivileged operators, defaults containers to
`no-new-privileges`, and uses bounded local logs. The root password is console
recovery material stored only in 1Password; it cannot authenticate over SSH.
The watcher image and host identity use the release-contract UID/GID
`10001:10001`. Watcher secrets use the separate supplementary GID `10002` and
are mounted only into the watcher. The R2 key (#200) remains an
operator-supplied host file in the S3-compatible credential format; no secret
value is stored in this directory.
Self-hosted Uptime Kuma 2.5.5 uses dedicated UID/GID `10003:10003`, the pinned
official MIT `slim-rootless` `linux/amd64` image, no Docker socket or host port,
a private monitoring network, a separate outbound HTTP monitor and notification
network, and persistent `/var/lib/minos/uptime-kuma` data.

The dedicated key pair is the `minos-mikrus-production-ssh` SSH Key item in the
`MinosHealth` 1Password vault. Restore its private key locally as
`~/.ssh/minos_mikrus_ed25519` with mode `0600`. Its public key is materialized
into an ignored Ansible source file.

The public site never reaches the host directly: every service sits behind the
Cloudflare Tunnel and no container publishes a host port. SSH is the one
management path, and `ssh_exposure` (derived by the materializer from the
address) says where it listens:

- `public`: the firewall permits guest port `22/tcp` on IPv4 and on the globally
  routed guest IPv6 address. Mikr.us maps a provider-assigned public IPv4 port
  to that guest port; IPv6 reaches guest port `22` directly. Both paths require
  the pinned public key.
- `tailnet` (#227): `sshd` listens on loopback only and the firewall allows no
  inbound traffic. `tailscaled` in userspace mode proxies tailnet connections to
  loopback, so only the owner's tailnet devices reach SSH, still with the pinned
  key. The cutover is described under Tailnet SSH.

## Materialize Host Metadata

Git tracks only the `minos-production` inventory alias, the Python interpreter,
and the constrained materializer. The `minos-production-deployment` item in the
`MinosHealth` vault has these fields:

- `ansible_host`: provider DNS name, or the tailnet name
  `minos-production.<tailnet>.ts.net` after the cutover;
- `ansible_port`: provider-mapped SSH port, or `22` on the tailnet;
- `ansible_user`: exactly `root`;
- `expected_hostname`: guest short hostname asserted by the playbook;
- `cloudflared_tunnel_id`: Cloudflare tunnel UUID;
- `ssh_host_ed25519_public_key`: full `ssh-ed25519` host public key.

Materialize all local host inputs before any inventory, check, or apply command:

```sh
uv run python scripts/materialize_ansible_host.py
```

The helper reads fixed 1Password references, rejects unresolved or malformed
values, serializes YAML directly, validates one-line ED25519 keys, and proves the
operator private key matches its public key before replacing any output. Each
file is installed from a mode-`0600` same-directory temporary file. The three
repository-local outputs are ignored; never add them to Git.

The Ansible SSH configuration keeps `StrictHostKeyChecking=yes`, accepts only an
ED25519 host key from the materialized `known_hosts`, and disables global known
hosts, `KnownHostsCommand`, DNS host-key verification, host-IP lookup, learned
host-key updates, and connection multiplexing. Authentication permits only the
dedicated identity with public-key authentication.

Current-tree externalization does not remove host metadata and public keys from
existing Git history. Removing history requires a separately coordinated rewrite
and clone invalidation. Rotate or rebind the provider endpoint, host key, operator
key, and tunnel identity where practical before treating the externalized values
as private operational metadata.

## Tailnet SSH (#227)

`sshd` once accepted connections from the whole internet. Key-only login does
not protect against a flaw in `sshd` itself, so the host moves SSH onto a
Tailscale tailnet and closes the public paths. The site is unaffected: it uses
the Cloudflare Tunnel, and the management path does not depend on it.

Design:

- `tailscaled` comes from the official repository, its signing key pinned by
  fingerprint and checked before apt trusts it. Security updates arrive through
  unattended upgrades (`origin=Tailscale`).
- Userspace networking, because the LXC guest has no guaranteed TUN device.
  Inbound tailnet connections reach `sshd` from `127.0.0.1`, so the SSH log and
  `MaxStartups` no longer see client addresses.
- The host joins once with a one-time, pre-authorised auth key tagged
  `tag:minos-server`; tagged devices do not expire. The key file never enters
  Git or the host's disk beyond a tmpfs copy removed after the join.
- `infra/tailscale/policy.hujson` is the reviewed tailnet policy: admins reach
  the server tag on TCP 22 only, and the server starts nothing.
- Break-glass: the Mikr.us panel console with the root password from 1Password.
  The prior `sshd` configuration also listens on loopback, so a failed cutover
  rolls back without losing the tailnet path.

Rollout, each production run approved separately:

1. In the owner's tailnet (one user; MagicDNS on; the workstation already
   joined), replace the default allow-all policy with
   `infra/tailscale/policy.hujson`: own devices keep reaching each other, and
   the new `tag:minos-server` accepts SSH from admin devices only. Then
   generate a one-time, pre-authorised, non-ephemeral auth key with that tag
   and store it in 1Password as `minos-production-tailscale`, field `auth key`.
2. Prove a login through the Mikr.us panel console.
3. Join, over the current public SSH (public SSH is unchanged):

   ```sh
   umask 077
   key_file="$(mktemp)"
   trap 'rm -f -- "$key_file"' EXIT
   op read "op://MinosHealth/minos-production-tailscale/auth key" > "$key_file"
   ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
     uvx --from ansible-core==2.21.4 ansible-playbook \
     infra/ansible/ssh-tailnet.yml -e ssh_tailnet_stage=join \
     -e "tailscale_auth_key_source=$key_file"
   ```

   It ends with a fresh SSH login over the tailnet name it prints.
4. Set `ansible_host` to that name and `ansible_port` to `22` in the
   `minos-production-deployment` item, then re-materialize. `known_hosts` now
   pins the same host key under the bare tailnet name, and `ssh_exposure`
   becomes `tailnet`.
5. Close the public paths, over the tailnet:

   ```sh
   ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
     uvx --from ansible-core==2.21.4 ansible-playbook \
     infra/ansible/ssh-tailnet.yml -e ssh_tailnet_stage=private
   ```

   It converts socket activation to `ssh.service` if needed, makes `sshd`
   listen on loopback, proves a fresh login and loopback-only listeners, and
   only then deletes the UFW SSH rule. Any failure before that restores the
   prior drop-in.
6. From the workstation, prove the provider IPv4 port and IPv6 port 22 refuse,
   and record the evidence in #227.

`site.yml`, `scripts.deploy_validation` and `scripts.deploy_watcher` then work
over the tailnet unchanged. In `tailnet` mode the baseline refuses to run unless
the session itself arrived over the tailnet, and never re-adds the SSH rule.

## Apply

A fresh-host production apply is blocked until the open GHCR task installs a
root-owned Docker credential using a package-read-only token. This playbook does
not create registry authentication and must not be worked around with a public
image, mutable tag, or broader token.

Verify first:

```sh
ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
  uvx --from ansible-core==2.21.4 ansible-playbook \
  infra/ansible/site.yml --check --diff \
  -e watcher_archive_backend=s3 \
  -e watcher_s3_bucket=minos-archive \
  -e watcher_s3_endpoint=https://<cloudflare-account-id>.eu.r2.cloudflarestorage.com \
  -e watcher_expected_image_digest=sha256:<64-hex-digest> \
  -e watcher_source_revision=<40-hex-source-commit> \
  -e '{"watcher_uptime_kuma_required":false,"uptime_kuma_public_status_enabled":false}'
```

Check mode cannot prove live netfilter activation. Before the first apply, keep
the provider console available and prove a fresh, non-multiplexed key-only
session:

```sh
ssh -o ControlMaster=no -o ControlPath=none -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=infra/ansible/known_hosts \
  -o GlobalKnownHostsFile=/dev/null -o HostKeyAlgorithms=ssh-ed25519 \
  -o IdentitiesOnly=yes -o PreferredAuthentications=publickey \
  -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
  -i ~/.ssh/minos_mikrus_ed25519 -p <reviewed-port> \
  root@<reviewed-host> /usr/bin/true
```

The normal playbook reuses SSH connections for speed, with control sockets in
the operator's mode-`0700` `~/.ssh` directory. If the SSH drop-in changes, it
checks a separate fresh, pinned-host-key login before closing the old connection.
Keep the fresh-session preflight above for first activation and host recovery;
an Ansible ping through an existing control socket does not prove new logins work.

Before the first apply, retrieve the tunnel credential and watcher R2
credentials to mode-`0600` temporary files outside the repository. Obtain the
exact watcher image digest from the reviewed release.
Apply only after the playbook's hostname assertion passes and key authentication
works in a second SSH session:

```sh
set -euo pipefail
umask 077
credential_dir="$(mktemp -d)"
cleanup() {
  rm -rf -- "$credential_dir"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

op document get minos-production-cloudflared-credentials \
  --vault MinosHealth --out "$credential_dir/cloudflared-credentials.json"
op document get minos-production-watcher-r2-credentials \
  --vault MinosHealth --out "$credential_dir/watcher-r2-credentials"
ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
  uvx --from ansible-core==2.21.4 ansible-playbook \
  infra/ansible/site.yml --diff \
  -e watcher_archive_backend=s3 \
  -e watcher_s3_bucket=minos-archive \
  -e watcher_s3_endpoint=https://<cloudflare-account-id>.eu.r2.cloudflarestorage.com \
  -e watcher_expected_image_digest=sha256:<64-hex-digest> \
  -e watcher_source_revision=<40-hex-source-commit> \
  -e '{"watcher_uptime_kuma_required":false,"uptime_kuma_public_status_enabled":false}' \
  -e "cloudflared_credentials_source=$credential_dir/cloudflared-credentials.json" \
  -e "watcher_aws_credentials_source=$credential_dir/watcher-r2-credentials"
```

The R2 document must contain only a `[default]` profile with the 32-hex
`aws_access_key_id` and 64-hex `aws_secret_access_key` of the bucket-scoped
`minos-r2-watcher` token. The playbook validates the bounded credential and
target schemas without logging values, stages the R2 credential under
`/etc/minos/watcher/candidate` and the tunnel credential under
`/etc/minos/cloudflared/candidate`, and validates the staged files before
activation. It replaces active files atomically only while the service is
stopped. Failed activation restores prior files or removes rejected
first-install files.

The expected digest and source revision come from the reviewed release. The host
requires that digest to resolve directly to one runnable manifest, pulls it,
then derives the runtime digest from Docker's observed repository identity and
checks the image revision label. The in-container preflight compares the
reviewed digest with that host-derived value; it cannot independently query the
root-owned Docker daemon. Run the playbook again afterward; an unchanged
candidate must not run preflight, stop, promote, or restart the active stack.
Later applies still supply all non-secret runtime settings, omit secret source
variables, and preserve the installed credentials.

For a changed candidate, the playbook compares keyed checksums for all staged
runtime files and the running watcher image reference before activation. The
candidate application creates a consistent, 2 GiB-bounded SQLite backup with a
240-second internal deadline and a 300-second outer process timeout. Supported
v1 and v2 copies are migrated to the candidate v3 schema and then validated;
the original live database remains untouched. A missing database on an initial
empty host remains allowed. A newer, malformed, or unmigratable copy fails before
the active stack is stopped.

After stopping the active stack, the same bounded path captures a second
preactivation image before promotion. The promoted candidate then migrates the
quiesced live database and verifies an exact structured result. If activation
fails without changing the schema, rescue restores the prior runtime. If the
schema changed, rescue preserves the promoted candidate and migrated database
stopped rather than starting an older writer. It never rolls back or deletes the
live database. Candidate evidence remains under
`/var/lib/minos/watcher/candidate-preflight` and stopped preactivation evidence
remains under `/opt/minos/rollback/control`. The playbook also fails closed if
the LXC cannot enforce the disk-backed staging bind mount with
`noexec,nosuid,nodev`.

After promotion and before starting active services, the activation block runs
the profile-gated `watcher-bootstrap-rsk` service with `--no-deps`. The atomic
bootstrap creates canonical RSK state only when the source is absent, is a no-op
for an exact match, and rejects disabled, customized, or partial registration.
A rejection enters the normal activation rescue before the candidate watcher
starts. The bootstrap service is not a Compose dependency. If a later activation
step fails after a fresh bootstrap, the canonical database registration remains
as recoverable, idempotent state; runtime and secret rollback never deletes the
live control database.
Before startup, a read-only `validate-public-rsk` command accepts an absent fresh
projection or validates the current immutable tree, manifest, digest, schema, and
revision fence. It does not invoke a collector. An existing malformed projection
enters activation rescue before any candidate service starts.
The playbook then runs one 900-second-bounded manual RSK canary with the same
pinned image, live volume, R2 credentials, and public
projection root. Manual mode forces the allowlisted XML download. The canary must
commit, complete durable local finalization, and produce a canonical valid
projection before active services start.
The temporary edge serves an explicit maintenance page, reports edge liveness at
`/healthz`, reports product unavailability at `/readyz`, redirects `.com` to the
matching `.pl` path, and publishes no host port. It is not the Minos application.

Acceptance also runs `unattended-upgrade --dry-run --debug`, verifies the APT
timers and reboot marker, tests a fresh key-only SSH connection, proves that
keyboard-interactive login fails, checks both UFW address families, runs
`journalctl --verify`, and exercises an immutable `hello-world` image without
network access or host port publication. It also requires healthy exact-digest
edge, `cloudflared`, Uptime Kuma, and watcher containers with no Docker port bindings. The
watcher assertion covers its exact digest, numeric identity, supplementary secret
group, read-only root, dropped capabilities, restart count, resource limits,
bounded logs, mounts, and healthy state after a 30-second stability interval.
The watcher alone mounts `/var/lib/minos/public` read-write. The edge mounts that
directory read-only and receives no watcher control, archive, staging, R2, or
other watcher secret mounts. Its exact provenance route reads the atomic
`current` generation; the artifact namespace remains an unconditional 404.
The same reviewed edge artifacts provide the exact `/zrodla`, `/site.css`,
and `/sources.js` routes. Pages are rendered from `apps/web/site` by
`scripts/build_site.py`; the maintenance runtime still deploys the shared
stylesheet under its inventoried name `legal.css`, which nginx serves as
`/site.css`, while `home.css` and `sources.css` are retired placeholders kept
until the runtime inventory drops them. During activation, Ansible requests those routes,
strict CSP headers, and the exact provenance endpoint from inside the running
production edge container. Provenance must return HTTP 200 and its bytes must
match the digest of the revalidated current generation. The page reads only the public same-origin provenance
projection; it receives no private archive, control database, R2, or artifact
route.

## Uptime Kuma Safe Bootstrap

Uptime Kuma is self-hosted on the same Mikr.us host. This avoids paid external
monitoring SaaS, but it cannot detect or send a notification for a total
Mikr.us host, network, or power outage. This same-host limitation is accepted
for now. The public page may expose only coarse service state. Detailed watcher
schedules, runs, changes, incidents, and products remain private through
`minos watcher status`; issue #54 remains the future private detailed admin
portal.

The accepted bootstrap is deliberately two-stage:

When the watcher release and R2 credentials are not ready, Kuma can be
initialized privately ahead of the full stack. The reviewed standalone model
`infra/ansible/uptime-kuma-bootstrap.compose.yml` uses the same exact image,
numeric identity, writable data directory, and resource boundaries as the
production service. Install it at `/opt/minos/uptime-kuma-bootstrap.compose.yml`
on the pinned host after creating the `minos-uptime-kuma` identity and
`/var/lib/minos/uptime-kuma` directory with ownership `10003:10003` and mode
`0700`. Pin `/var/lib/minos/uptime-kuma/.minos-image-reference` to the same
exact image reference. Start it with:

```sh
docker compose --project-name minos-kuma-bootstrap \
  --file /opt/minos/uptime-kuma-bootstrap.compose.yml up --detach --wait
```

This model has no host port, no Docker socket, and no public Cloudflare route.
The private container IP is on `minos-kuma-bootstrap_monitoring`. Host SSH
hardening sets `AllowTcpForwarding no`, so an SSH browser tunnel requires a
separately reviewed, short-lived exception appended at the end of
`/etc/ssh/sshd_config`: `Match User root`, `AllowTcpForwarding local`, and
`PermitOpen <private-container-ip>:3001`. Verify all other effective SSH
directives remain unchanged and schedule automatic removal before reloading
SSH. Remove the exception immediately after operator setup using the reviewed
`scripts/expire_kuma_ssh_setup.py`, verify the baseline, and close the local
tunnel. Never leave an unrestricted SSH forwarder or publish a host port.
Before the full Ansible apply,
stop the standalone project with `docker compose --project-name
minos-kuma-bootstrap --file /opt/minos/uptime-kuma-bootstrap.compose.yml down`.
The full playbook rejects concurrent database writers and reuses the same
preserved `/var/lib/minos/uptime-kuma` directory. `down` does not remove the
bind-mounted data. Do not run the two Kuma services concurrently.

1. Apply the playbook with `uptime_kuma_public_status_enabled=false` and
   `watcher_uptime_kuma_required=false`, as shown above. With no installed or
   supplied target file, Ansible installs the exact bootstrap document
   `{"version":1,"sources":{}}`. This starts Kuma internally but does not add
   `status.minoshealth.pl` to the Cloudflare ingress configuration.
2. Access initial setup only through reviewed temporary SSH forwarding to the
   private container IP.
   An already-reviewed Cloudflare Access policy is the only alternative. On the
   host, obtain the current private IP without publishing a port:

   ```sh
   kuma_container="$(docker compose --project-name minos \
     --file /opt/minos/compose.yaml ps --quiet uptime-kuma)"
   docker inspect --format \
     '{{(index .NetworkSettings.Networks "minos_monitoring").IPAddress}}' \
     "$kuma_container"
   ```

   From the workstation, use the same materialized host, mapped SSH port,
   pinned identity, and strict known-hosts file as Ansible:

   ```sh
   ssh -N -L 127.0.0.1:3001:<private-container-ip>:3001 \
     -i ~/.ssh/minos_mikrus_ed25519 -p <provider-mapped-port> \
     -o StrictHostKeyChecking=yes \
     -o UserKnownHostsFile=infra/ansible/known_hosts \
     root@<provider-hostname>
   ```

   Open `http://127.0.0.1:3001`, create the admin with a strong unique password,
   and enable 2FA before any public route exists.
3. In Kuma, create the required HTTP monitors, notification channels, and one
   RSK push monitor. Set the RSK push monitor's maximum retries to `0`; set its
   heartbeat interval and grace longer than the daily schedule plus the maximum
   watcher runtime so a valid long run is not marked down early. Record only the
   generated 32-character alphanumeric push token. Create a public status page
   with custom domain `status.minoshealth.pl` that contains coarse service state
   only. Do not expose tokens, internal paths, watcher incidents, source details,
   or the Kuma administration UI as public status content.
4. Retrieve the operator-prepared 1Password Document
   `minos-production-watcher-uptime-kuma-targets` into a regular mode-`0600`
   file outside the repository. Its exact content is:

   ```json
   {"version":1,"sources":{"rsk":"<32-character-alphanumeric-token>"}}
   ```

   It was prepared from the rotated token on 2026-09-23 and read back from
   1Password. Recreate it if the Push monitor token changes again. Never put
   that token or document content in shell arguments, Git, or chat.

   Install and require it while the public route remains off:

   ```sh
   ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
     uvx --from ansible-core==2.21.4 ansible-playbook \
     infra/ansible/site.yml --diff \
     -e watcher_archive_backend=s3 \
     -e watcher_s3_bucket=minos-archive \
     -e watcher_s3_endpoint=https://<cloudflare-account-id>.eu.r2.cloudflarestorage.com \
     -e watcher_expected_image_digest=sha256:<64-hex-digest> \
     -e watcher_source_revision=<40-hex-source-commit> \
      -e '{"watcher_uptime_kuma_required":true,"uptime_kuma_public_status_enabled":false}' \
     -e watcher_uptime_kuma_targets_source=/absolute/path/to/targets.json
   ```

   Configure the Cloudflare DNS/tunnel hostname for `status.minoshealth.pl`.
   Only after the admin, 2FA, monitors, notifications, coarse status page,
   target installation, and DNS configuration are complete, enable the
   conditional ingress route:

   ```sh
   ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
     uvx --from ansible-core==2.21.4 ansible-playbook \
     infra/ansible/site.yml --diff \
     -e watcher_archive_backend=s3 \
     -e watcher_s3_bucket=minos-archive \
     -e watcher_s3_endpoint=https://<cloudflare-account-id>.eu.r2.cloudflarestorage.com \
     -e watcher_expected_image_digest=sha256:<64-hex-digest> \
     -e watcher_source_revision=<40-hex-source-commit> \
      -e '{"watcher_uptime_kuma_required":true,"uptime_kuma_public_status_enabled":true}'
   ```
5. Visually verify `https://status.minoshealth.pl` in real desktop and mobile
   browsers. Exercise each notification channel and a controlled RSK up/down
   transition. Do not infer notification delivery or browser correctness from
   container health, HTTP status, or a quiet window.
6. After configuration, stop Kuma and create an encrypted off-host backup of
   the complete `/app/data` tree, which is the host directory
   `/var/lib/minos/uptime-kuma`. It can contain credentials and push tokens.
   Never copy live SQLite files. Stop it with:

   ```sh
   docker compose --project-name minos --file /opt/minos/compose.yaml \
     stop uptime-kuma
   ```

   Keep Kuma stopped while the complete directory is archived, encrypted, moved
   off-host, and checked. Restore the same pinned Uptime Kuma version first. The
   accepted image is version 2.5.5, official MIT `slim-rootless`,
   `linux/amd64`, at
   `sha256:8bdad438d293e998fd7d45004cefb3baea0f96d3bc774502bba99c17d616e1f6`.
   Ansible fails closed on an image change while preserved data exists; no image
   upgrade is allowed until a reviewed stopped backup and migration procedure
   exists.

On 2026-09-23 the pinned standalone Kuma container was installed privately on
Mikr.us with no host port or public route. The operator initialized SQLite,
created an admin account with 2FA, configured an HTTP monitor, a paused RSK Push
monitor, and a page whose domain mapping resolves `status.minoshealth.pl`.
After stopping the container, the full data tree was uploaded to a 1Password
Document and its restored archive, image pin, and SQLite integrity were checked.
The rotated RSK Push token was also stored as a separate watcher-only target
document in 1Password; it is not yet installed on Mikr.us.
The operator configured a dedicated Telegram bot and tested delivery using
Kuma's notification Test action. A read-only check confirmed that the saved
Telegram notification is attached to the HTTP public-edge monitor, while the
RSK Push monitor remains paused. A fresh stopped 1Password backup includes
this notification configuration; the archive and SQLite integrity were checked
after retrieval. The bot token and chat ID remain in 1Password and are not
recorded in this repository.
The temporary SSH forwarding exception expired successfully; effective SSH
forwarding is back to `no`, and no other effective SSH directives changed. Kuma
was restarted healthy. An isolated restore of the latest 1Password Document on
the operator workstation started the same pinned image as UID `10003` from a
disposable Docker volume without a network or host port. It became healthy,
served the restored status page, and the test container and volume were removed.
This verifies Kuma application recovery, not a full Mikr.us replacement. The
watcher cutover, public DNS and tunnel routing, full host-loss recovery,
representative host load, and public desktop/mobile browser verification remain
pending.

Inspect the deployed watcher through its read-only SQLite status path and a
bounded window from Docker's local log store:

```sh
docker compose --project-name minos --file /opt/minos/compose.yaml \
  exec --no-TTY watcher /app/.venv/bin/minos watcher status --json
docker compose --project-name minos --file /opt/minos/compose.yaml \
  exec --no-TTY watcher /app/.venv/bin/minos watcher status rsk --json
docker compose --project-name minos --file /opt/minos/compose.yaml \
  exec --no-TTY watcher /app/.venv/bin/minos watcher status baw --json
docker compose --project-name minos --file /opt/minos/compose.yaml \
  logs --since 24h --tail 500 --timestamps watcher
```

BAW uses the same watcher, archive, and read-only status contract as RSK. After
the reviewed watcher image is installed, configure a separate BAW Push monitor
in Kuma and add its token under `sources.baw` in the existing protected targets
file before enabling BAW. Then run `minos watcher bootstrap-baw` once against
the live control database with dispatch quiesced; `minos watcher run-once baw`
performs the initial bounded collection. The public Kuma status page must be
updated by the operator to include BAW if BAW state is intended to be visible
there. The `/zrodla` directory reads only the sanitized public RSK and BAW
projections. Neither endpoint serves archived source files.

These commands require access to the host and therefore do not detect a complete
Mikr.us host, network, or power outage from outside its fault domain.

## Recovery Boundary

- Git stores this baseline, Compose definitions, release manifests, and other
  reviewed non-secret configuration.
- 1Password stores SSH, console-recovery, Cloudflare, GHCR, R2, and monitoring
  credentials. Secrets never enter Git or a general filesystem backup.
- The private R2 bucket `minos-archive` stores immutable source blobs, control-database recovery
  images, trusted recovery copies, signed inventories, release manifests, and
  deployment receipts according to the operations runbook.
- The live Mikr.us filesystem is not a backup. Empty-host recovery reapplies this
  playbook, installs fresh secrets, restores the verified control database, and
  pulls the exact recorded image digests.

The provider LXC currently reports `sys-kernel-config.mount` and
`systemd-remount-fs.service` as failed because the host controls those kernel and
mount operations. Do not hide additional failed units behind that known provider
behavior.

## Replacement Host Rebind

An empty replacement receives a new provider hostname, port, guest identity, and
SSH host key. Obtain the ED25519 fingerprint through the Mikr.us panel or console,
compare it through a second provider path, and update the corresponding
`ansible_host`, `ansible_port`, `expected_hostname`, and
`ssh_host_ed25519_public_key` fields in the 1Password deployment item. Update
`cloudflared_tunnel_id` only when the replacement uses a new tunnel, and store its
matching credential document in 1Password.

Run the materializer again, inspect the regenerated files locally,
and verify their modes are `0600`. Keep the provider console open, run check mode,
then prove a fresh strict key-only session with the command above before applying.
Retrieve the operator key and console password from 1Password. Never use
`StrictHostKeyChecking=no`, accept a key interactively, or copy the prior host key
to make a replacement fit the old trust record.
