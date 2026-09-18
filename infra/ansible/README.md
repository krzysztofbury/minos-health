# Mikr.us Host Baseline

This directory owns the reproducible, non-secret baseline for the dedicated
Mikr.us `3.0` production host. Run commands from the repository root so the
pinned SSH host-key file resolves correctly.

The playbook pins key-only root SSH, a default-deny inbound firewall, unattended
security upgrades, and bounded persistent journald storage. The root password is
console recovery material stored only in 1Password; it cannot authenticate over
SSH. Application containers, Docker, secrets, and data recovery are added by
later reviewed changes.

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
  infra/ansible/site.yml --check --diff
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

Apply only after the check targets `andrzej159` and key authentication works in
a second SSH session:

```sh
ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
  uvx --from ansible-core==2.21.4 ansible-playbook \
  infra/ansible/site.yml --diff
```

Run it again afterward; an unchanged host must report no changes.

Acceptance also runs `unattended-upgrade --dry-run --debug`, verifies the APT
timers and reboot marker, tests a fresh key-only SSH connection, proves that
keyboard-interactive login fails, checks both UFW address families, and runs
`journalctl --verify`.

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
