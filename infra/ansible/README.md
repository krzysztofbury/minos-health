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
are mounted only into the watcher. The AWS credentials and Healthchecks.io target
remain operator-supplied host files; no secret value is stored in this directory.

The dedicated key pair is the `minos-mikrus-production-ssh` SSH Key item in the
`MinosHealth` 1Password vault. Restore its private key locally as
`~/.ssh/minos_mikrus_ed25519` with mode `0600`. Its public key is materialized
into an ignored Ansible source file.

The firewall intentionally permits guest port `22/tcp` on IPv4 and on the
globally routed guest IPv6 address. Mikr.us maps a provider-assigned public IPv4
port to that guest port; IPv6 reaches guest port `22` directly. Both paths
require the pinned public key after hardening.

## Materialize Host Metadata

Git tracks only the `minos-production` inventory alias, the Python interpreter,
and the constrained materializer. The `minos-production-deployment` item in the
`MinosHealth` vault has these fields:

- `ansible_host`: provider DNS name;
- `ansible_port`: provider-mapped SSH port;
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
  -e watcher_s3_bucket=<watcher-snapshot-bucket> \
  -e watcher_s3_expected_bucket_owner=<twelve-digit-account-id> \
  -e watcher_expected_image_digest=sha256:<64-hex-digest> \
  -e watcher_source_revision=<40-hex-source-commit>
```

Check mode cannot prove live netfilter activation. Before the first apply, keep
the provider console available and prove a fresh, non-multiplexed key-only
session:

```sh
ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
  uvx --from ansible-core==2.21.4 ansible minos-production \
  --module-name ansible.builtin.raw --args true
```

Before the first apply, retrieve the tunnel credential, watcher AWS credentials,
and watcher Healthchecks.io targets JSON to mode-`0600` temporary files outside
the repository. Obtain the exact watcher image digest from the reviewed release.
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
op document get minos-production-watcher-aws-credentials \
  --vault MinosHealth --out "$credential_dir/watcher-aws-credentials"
op document get minos-production-watcher-healthchecks-targets \
  --vault MinosHealth --out "$credential_dir/watcher-healthchecks-targets.json"
ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
  uvx --from ansible-core==2.21.4 ansible-playbook \
  infra/ansible/site.yml --diff \
  -e watcher_archive_backend=s3 \
  -e watcher_s3_bucket=<watcher-snapshot-bucket> \
  -e watcher_s3_expected_bucket_owner=<twelve-digit-account-id> \
  -e watcher_expected_image_digest=sha256:<64-hex-digest> \
  -e watcher_source_revision=<40-hex-source-commit> \
  -e "cloudflared_credentials_source=$credential_dir/cloudflared-credentials.json" \
  -e "watcher_aws_credentials_source=$credential_dir/watcher-aws-credentials" \
  -e "watcher_healthchecks_targets_source=$credential_dir/watcher-healthchecks-targets.json"
```

The AWS document must contain only a `[default]` profile with one long-lived
`aws_access_key_id` and `aws_secret_access_key`. The Healthchecks document uses
schema version `1`, a bounded `sources` mapping, and one `preflight` HTTPS URL.
The playbook validates all three bounded secret schemas without logging values,
stages the watcher files under `/etc/minos/watcher/candidate` and the tunnel
credential under `/etc/minos/cloudflared/candidate`, and validates the staged
files before activation. It replaces active files atomically only while the
service is stopped. Failed activation restores prior files or removes rejected
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

The watcher S3 bucket, IAM policy, upload jobs, and restore drill are not active
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
