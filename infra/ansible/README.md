# Mikr.us Host Baseline

This directory owns the reproducible, non-secret baseline for the dedicated
Mikr.us `3.0` production host. Run commands from the repository root so the
pinned SSH host-key file resolves correctly.

The playbook pins key-only root SSH, a default-deny inbound firewall, unattended
security upgrades, bounded persistent journald storage, and exact Docker Engine
and Compose packages from Docker's official repository. Docker exposes only its
root-owned Unix socket, has no unprivileged operators, defaults containers to
`no-new-privileges`, and uses bounded local logs. The root password is console
recovery material stored only in 1Password; it cannot authenticate over SSH.
The watcher image and host identity use the release-contract UID/GID
`10001:10001`. Watcher secrets use the separate supplementary GID `10002` and
are mounted only into the watcher. The AWS credentials and Healthchecks.io target
remain operator-supplied host files; no secret value is stored in this directory.

The dedicated private key is the `minos-mikrus-production-ssh` SSH Key item in
the `MinosHealth` 1Password vault. Restore it locally as
`~/.ssh/minos_mikrus_ed25519` with mode `0600`; only its public key is committed.

The firewall intentionally permits guest port `22/tcp` on IPv4 and on the
globally routed guest IPv6 address. Mikr.us maps the public IPv4 port `10159` to
that guest port; IPv6 reaches guest port `22` directly. Both paths require the
pinned public key after hardening.

## Apply

Verify first:

```sh
ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
  uvx --from ansible-core==2.21.4 ansible-playbook \
  infra/ansible/site.yml --check --diff \
  -e watcher_archive_backend=s3 \
  -e watcher_s3_bucket=<phase0-snapshot-bucket> \
  -e watcher_s3_expected_bucket_owner=<twelve-digit-account-id> \
  -e watcher_expected_image_digest=sha256:<64-hex-digest> \
  -e watcher_source_revision=<40-hex-source-commit>
```

Check mode cannot prove live netfilter activation. Before the first apply, keep
the provider console available and prove a fresh, non-multiplexed key-only
session:

```sh
ssh -p 10159 -o ControlMaster=no -o ControlPath=none \
  -o BatchMode=yes -o IdentitiesOnly=yes \
  -o PreferredAuthentications=publickey \
  -o KbdInteractiveAuthentication=no -o PasswordAuthentication=no \
  root@andrzej159.mikrus.xyz true
```

Before the first apply, retrieve the tunnel credential, watcher AWS credentials,
and watcher Healthchecks.io targets JSON to mode-`0600` temporary files outside
the repository. Obtain the exact watcher image digest from the reviewed release.
Apply only after the check targets `andrzej159` and key authentication works in
a second SSH session:

```sh
umask 077
op document get minos-production-cloudflared-credentials \
  --vault MinosHealth --out /tmp/minos-cloudflared-credentials.json
ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
  uvx --from ansible-core==2.21.4 ansible-playbook \
  infra/ansible/site.yml --diff \
  -e watcher_archive_backend=s3 \
  -e watcher_s3_bucket=<phase0-snapshot-bucket> \
  -e watcher_s3_expected_bucket_owner=<twelve-digit-account-id> \
  -e watcher_expected_image_digest=sha256:<64-hex-digest> \
  -e watcher_source_revision=<40-hex-source-commit> \
  -e cloudflared_credentials_source=/tmp/minos-cloudflared-credentials.json \
  -e watcher_aws_credentials_source=/tmp/minos-watcher-aws-credentials \
  -e watcher_healthchecks_targets_source=/tmp/minos-watcher-healthchecks-targets.json
rm /tmp/minos-cloudflared-credentials.json \
  /tmp/minos-watcher-aws-credentials \
  /tmp/minos-watcher-healthchecks-targets.json
```

The expected digest and source revision come from the reviewed release. The host
requires that digest to resolve directly to one runnable manifest, pulls it,
then derives the runtime digest from Docker's observed repository identity and
checks the image revision label. The in-container preflight compares the
reviewed digest with that host-derived value; it cannot independently query the
root-owned Docker daemon. Run the playbook again afterward; an unchanged
candidate must not run preflight, stop, promote, or restart the active stack.
Later applies still supply all non-secret runtime settings, omit secret source
variables, and preserve the installed credentials.

For a changed candidate, the playbook compares all staged runtime files and the
running watcher image reference before activation. The candidate application
creates a consistent, 2 GiB-bounded SQLite backup with a 240-second internal
deadline and a 300-second outer process timeout. It requires the copied live
schema version to equal the candidate schema version before the active stack is
stopped. An absent database on an initial empty host remains allowed. Schema
changes therefore require a separately reviewed migration activation design;
this playbook does not migrate the live database and cannot roll an older writer
onto a newer schema.

After stopping the active stack, the same bounded path captures a second
preactivation image before promotion. A failed activation restores the prior
runtime files and image reference against the unchanged, schema-identical live
database. It never rolls back or deletes the live database. Candidate evidence
remains under `/var/lib/minos/watcher/candidate-preflight` and stopped
preactivation evidence remains under `/opt/minos/rollback/control`, including
evidence from a schema rejection. A failed initial apply removes only promoted
runtime configuration. The playbook also fails closed if the LXC cannot enforce
the disk-backed staging bind mount with `noexec,nosuid,nodev`.
The temporary edge serves an explicit maintenance page, reports edge liveness at
`/healthz`, reports product unavailability at `/readyz`, redirects `.com` to the
matching `.pl` path, and publishes no host port. It is not the Minos application.

Acceptance also runs `unattended-upgrade --dry-run --debug`, verifies the APT
timers and reboot marker, tests a fresh key-only SSH connection, proves that
keyboard-interactive login fails, checks both UFW address families, runs
`journalctl --verify`, and exercises an immutable `hello-world` image without
network access or host port publication. It also requires healthy exact-digest
edge, `cloudflared`, and watcher containers with no Docker port bindings. The
watcher assertion covers its exact digest, numeric identity, supplementary secret
group, read-only root, dropped capabilities, restart count, resource limits,
bounded logs, mounts, and healthy state after a 30-second stability interval.

## Recovery Boundary

- Git stores this baseline, Compose definitions, release manifests, and other
  reviewed non-secret configuration.
- 1Password stores SSH, console-recovery, Cloudflare, GHCR, AWS, and monitoring
  credentials. Secrets never enter Git or a general filesystem backup.
- Private versioned S3 stores immutable source blobs, control-database recovery
  images, trusted recovery copies, signed inventories, release manifests, and
  deployment receipts according to the operations runbook.
- The live Mikr.us filesystem is not a backup. Empty-host recovery reapplies this
  playbook, installs fresh secrets, restores the verified control database, and
  pulls the exact recorded image digests.

The phase-0 S3 bucket, IAM policy, upload jobs, and restore drill are not active
yet. Until their reviewed Terraform plan is approved and applied, Git plus
1Password can reconstruct this host baseline, but no future mutable watcher state
is protected.

The provider LXC currently reports `sys-kernel-config.mount` and
`systemd-remount-fs.service` as failed because the host controls those kernel and
mount operations. Do not hide additional failed units behind that known provider
behavior.

## Replacement Host Rebind

An empty replacement receives a new provider hostname, port, guest identity, and
SSH host key. Obtain the ED25519 fingerprint through the Mikr.us panel or console,
compare it through a second provider path, then update the inventory,
`known_hosts`, and strict hostname assertion in one reviewed change. Retrieve the
operator key and console password from 1Password. Never bypass strict host-key
checking to make a replacement fit the old inventory.
