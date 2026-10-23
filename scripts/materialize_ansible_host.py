#!/usr/bin/env python3

import base64
import binascii
import os
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

DEPLOYMENT_ITEM = "op://MinosHealth/minos-production-deployment"
SSH_ITEM = "op://MinosHealth/minos-mikrus-production-ssh"
SECRET_REFERENCES = {
    "ansible_host": f"{DEPLOYMENT_ITEM}/ansible_host",
    "ansible_port": f"{DEPLOYMENT_ITEM}/ansible_port",
    "ansible_user": f"{DEPLOYMENT_ITEM}/ansible_user",
    "expected_hostname": f"{DEPLOYMENT_ITEM}/expected_hostname",
    "cloudflared_tunnel_id": f"{DEPLOYMENT_ITEM}/cloudflared_tunnel_id",
    "ssh_host_key": f"{DEPLOYMENT_ITEM}/ssh_host_ed25519_public_key",
    "operator_public_key": f"{SSH_ITEM}/public key",
    "operator_private_key": f"{SSH_ITEM}/private key?ssh-format=openssh",
}
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_UNRESOLVED_MARKERS = ("{{", "}}", "op://")
_PRIVATE_KEY_BEGIN = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
_PRIVATE_KEY_END = "-----END OPENSSH " + "PRIVATE KEY-----"
_TRANSACTION_ID = re.compile(r"(?:[a-f0-9]{32}|[0-9]+)")
_STALE_RECOVERY_INSTRUCTIONS = (
    "stale materializer transaction artifacts require manual recovery; "
    "preserve every .bak file, restore every destination only from one transaction "
    "identifier with all expected output indices, remove only verified orphan .tmp "
    "files, then rerun"
)


class MaterializationError(RuntimeError):
    pass


def materialize(
    repository: Path,
    home: Path,
    *,
    read_secret: Callable[[str], str] | None = None,
    derive_public_key: Callable[[Path], str] | None = None,
) -> None:
    reader = read_secret or _read_secret
    derive = derive_public_key or _derive_public_key
    values: dict[str, str] = {}
    for name, reference in SECRET_REFERENCES.items():
        try:
            values[name] = reader(reference)
        except MaterializationError:
            raise MaterializationError(f"1Password read failed for {name}") from None
    _reject_unresolved(values)

    host = _validate_host(values["ansible_host"])
    port = _validate_port(values["ansible_port"])
    user = _validate_user(values["ansible_user"])
    expected_hostname = _validate_expected_hostname(values["expected_hostname"])
    tunnel_id = _validate_tunnel_id(values["cloudflared_tunnel_id"])
    host_key = _normalize_ed25519_public_key(values["ssh_host_key"], "host key")
    operator_key = _normalize_ed25519_public_key(
        values["operator_public_key"], "operator public key"
    )
    private_key = _normalize_private_key(values["operator_private_key"])

    inventory = _serialize_inventory(host, port, user, expected_hostname, tunnel_id)
    # OpenSSH records and looks up port 22 under the bare host name.
    known_hosts = (
        f"{host} {host_key}\n" if port == 22 else f"[{host}]:{port} {host_key}\n"
    )
    authorized_keys = f"{operator_key}\n"
    private_path = home / ".ssh" / "minos_mikrus_ed25519"
    _require_key_pair(private_path, private_key, operator_key, derive)
    _install_atomically(
        {
            private_path: private_key.encode(),
            repository
            / "infra/ansible/inventory/host_vars/minos-production.yml": inventory,
            repository / "infra/ansible/known_hosts": known_hosts.encode(),
            repository
            / "infra/ansible/files/root_authorized_keys": authorized_keys.encode(),
        }
    )


def _read_secret(reference: str) -> str:
    try:
        completed = subprocess.run(
            ["op", "read", "--no-newline", reference],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError, subprocess.TimeoutExpired:
        raise MaterializationError("1Password read failed") from None
    if completed.returncode != 0:
        raise MaterializationError("1Password read failed")
    return completed.stdout


def _derive_public_key(private_path: Path) -> str:
    try:
        completed = subprocess.run(
            ["ssh-keygen", "-y", "-f", str(private_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError, subprocess.TimeoutExpired:
        raise MaterializationError("operator private key validation failed") from None
    if completed.returncode != 0:
        raise MaterializationError("operator private key validation failed")
    return completed.stdout


def _reject_unresolved(values: Mapping[str, str]) -> None:
    for name, value in values.items():
        if any(marker in value for marker in _UNRESOLVED_MARKERS):
            raise MaterializationError(f"{name} is unresolved")


def _validate_host(value: str) -> str:
    if len(value) > 253 or value.endswith("."):
        raise MaterializationError("ansible_host is invalid")
    labels = value.split(".")
    if len(labels) < 2 or any(_HOST_LABEL.fullmatch(label) is None for label in labels):
        raise MaterializationError("ansible_host is invalid")
    return value


def _validate_port(value: str) -> int:
    if re.fullmatch(r"[0-9]{1,5}", value) is None:
        raise MaterializationError("ansible_port is invalid")
    port = int(value)
    if port < 1 or port > 65535:
        raise MaterializationError("ansible_port is invalid")
    return port


def _validate_user(value: str) -> str:
    if value != "root":
        raise MaterializationError("ansible_user must equal root")
    return value


def _validate_expected_hostname(value: str) -> str:
    if _HOST_LABEL.fullmatch(value) is None:
        raise MaterializationError("expected_hostname is invalid")
    return value


def _validate_tunnel_id(value: str) -> str:
    if re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value) is None:
        raise MaterializationError("cloudflared_tunnel_id is invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise MaterializationError("cloudflared_tunnel_id is invalid") from None
    if str(parsed) != value:
        raise MaterializationError("cloudflared_tunnel_id is invalid")
    return value


def _normalize_ed25519_public_key(value: str, name: str) -> str:
    if (
        len(value) < 64
        or len(value) > 1024
        or "\x00" in value
        or "\r" in value
        or len(value.splitlines()) != 1
    ):
        raise MaterializationError(f"{name} is invalid")
    parts = value.strip().split()
    if (
        len(parts) < 2
        or parts[0] != "ssh-ed25519"
        or _BASE64.fullmatch(parts[1]) is None
    ):
        raise MaterializationError(f"{name} is invalid")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except binascii.Error, ValueError:
        raise MaterializationError(f"{name} is invalid") from None
    expected_prefix = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"
    if len(blob) != 51 or not blob.startswith(expected_prefix):
        raise MaterializationError(f"{name} is invalid")
    return f"ssh-ed25519 {parts[1]}"


def _normalize_private_key(value: str) -> str:
    if (
        len(value.encode()) < 128
        or len(value.encode()) > 16_384
        or "\x00" in value
        or "\r" in value
    ):
        raise MaterializationError("operator private key is invalid")
    lines = value.strip().splitlines()
    if (
        len(lines) < 3
        or lines[0] != _PRIVATE_KEY_BEGIN
        or lines[-1] != _PRIVATE_KEY_END
        or any(_BASE64.fullmatch(line) is None for line in lines[1:-1])
    ):
        raise MaterializationError("operator private key is invalid")
    return "\n".join(lines) + "\n"


def _serialize_inventory(
    host: str, port: int, user: str, expected_hostname: str, tunnel_id: str
) -> bytes:
    return (
        "---\n"
        f'ansible_host: "{host}"\n'
        f"ansible_port: {port}\n"
        f'ansible_user: "{user}"\n'
        f'expected_hostname: "{expected_hostname}"\n'
        f'cloudflared_tunnel_id: "{tunnel_id}"\n'
        f'ssh_exposure: "{ssh_exposure(host, port)}"\n'
    ).encode()


def ssh_exposure(host: str, port: int) -> str:
    """How SSH reaches the host, derived from the address so they never disagree.

    A tailnet name on port 22 means public SSH is closed (#227); anything else
    is the provider's public mapping.
    """
    return "tailnet" if host.endswith(".ts.net") and port == 22 else "public"


def _require_key_pair(
    private_path: Path,
    private_key: str,
    expected_public_key: str,
    derive: Callable[[Path], str],
) -> None:
    if private_path.parent.is_symlink():
        raise MaterializationError("refusing to use a symbolic-link SSH directory")
    private_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(private_path.parent, 0o700)
    temporary = private_path.with_name(
        f".{private_path.name}.{uuid.uuid4().hex}.validate"
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            _ = stream.write(private_key)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(private_path.parent)
        derived = _normalize_ed25519_public_key(derive(temporary), "derived public key")
        if derived != expected_public_key:
            raise MaterializationError("operator public and private keys do not match")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        _fsync_directory(private_path.parent)


def _install_atomically(outputs: Mapping[Path, bytes]) -> None:
    _recover_stale_transaction(outputs)
    transaction_id = uuid.uuid4().hex
    temporary_paths = {
        destination: _transaction_path(destination, transaction_id, index, "tmp")
        for index, destination in enumerate(outputs)
    }
    backup_paths: dict[Path, Path] = {}
    prior_stats: dict[Path, os.stat_result | None] = {}
    created_backups: set[Path] = set()
    replacement_started: set[Path] = set()
    consistency_proven = False
    try:
        for destination in outputs:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.parent.is_symlink() or destination.is_symlink():
                raise MaterializationError("refusing to replace a non-regular file")
            if destination.exists() and not destination.is_file():
                raise MaterializationError("refusing to replace a non-regular file")
            prior_stats[destination] = (
                destination.stat() if destination.exists() else None
            )
        for destination, payload in outputs.items():
            temporary = temporary_paths[destination]
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                _ = stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _fsync_directory(destination.parent)
        for index, destination in enumerate(outputs):
            if prior_stats[destination] is not None:
                backup = _transaction_path(destination, transaction_id, index, "bak")
                os.link(destination, backup)
                backup_paths[destination] = backup
                created_backups.add(destination)
                _fsync_directory(destination.parent)
        for destination, temporary in temporary_paths.items():
            replacement_started.add(destination)
            os.replace(temporary, destination)
            _fsync_directory(destination.parent)
        for destination, payload in outputs.items():
            installed = destination.lstat()
            if (
                not stat.S_ISREG(installed.st_mode)
                or stat.S_IMODE(installed.st_mode) != 0o600
                or destination.read_bytes() != payload
            ):
                raise MaterializationError("installed host material failed validation")
        consistency_proven = True
    except BaseException as error:
        rollback_complete = _rollback_outputs(
            outputs, prior_stats, backup_paths, created_backups, replacement_started
        )
        cleanup_complete = _remove_transaction_files(temporary_paths.values())
        if rollback_complete:
            cleanup_complete &= _remove_transaction_files(backup_paths.values())
        if not isinstance(error, Exception):
            if not rollback_complete or not cleanup_complete:
                error.add_note("host material rollback or cleanup was incomplete")
            raise
        if (
            isinstance(error, MaterializationError)
            and rollback_complete
            and cleanup_complete
        ):
            raise
        message = "atomic host material installation failed"
        if not rollback_complete or not cleanup_complete:
            message += " and rollback or cleanup was incomplete"
        raise MaterializationError(message) from None
    if consistency_proven and not _remove_transaction_files(
        (*temporary_paths.values(), *backup_paths.values())
    ):
        raise MaterializationError("installed host material cleanup failed")


def _recover_stale_transaction(outputs: Mapping[Path, bytes]) -> None:
    transactions = _find_stale_transactions(outputs)
    if not transactions:
        return
    if len(transactions) != 1:
        raise MaterializationError(_STALE_RECOVERY_INSTRUCTIONS)
    transaction_id, (temporary_paths, backup_paths) = next(iter(transactions.items()))
    if _TRANSACTION_ID.fullmatch(transaction_id) is None or set(backup_paths) != set(
        outputs
    ):
        raise MaterializationError(_STALE_RECOVERY_INSTRUCTIONS)
    for destination, backup in backup_paths.items():
        try:
            backup_metadata = backup.lstat()
        except OSError:
            raise MaterializationError(_STALE_RECOVERY_INSTRUCTIONS) from None
        if (
            not stat.S_ISREG(backup_metadata.st_mode)
            or stat.S_IMODE(backup_metadata.st_mode) != 0o600
            or destination.parent.is_symlink()
            or destination.is_symlink()
            or (destination.exists() and not destination.is_file())
        ):
            raise MaterializationError(_STALE_RECOVERY_INSTRUCTIONS)
    recovery_paths = {
        destination: _transaction_path(destination, transaction_id, index, "tmp")
        for index, destination in enumerate(outputs)
    }
    try:
        for destination, recovery_path in recovery_paths.items():
            recovery_path.unlink(missing_ok=True)
            os.link(backup_paths[destination], recovery_path)
            _fsync_directory(destination.parent)
        for destination, recovery_path in recovery_paths.items():
            os.replace(recovery_path, destination)
            _fsync_directory(destination.parent)
        for destination, backup in backup_paths.items():
            restored = destination.lstat()
            prior = backup.lstat()
            if (
                not stat.S_ISREG(restored.st_mode)
                or stat.S_IMODE(restored.st_mode) != 0o600
                or restored.st_dev != prior.st_dev
                or restored.st_ino != prior.st_ino
            ):
                raise OSError("recovered output validation failed")
    except OSError:
        raise MaterializationError(
            "automatic stale materializer recovery failed; complete .bak files "
            + "were preserved, correct the local filesystem error, then rerun"
        ) from None
    if not _remove_transaction_files(
        (*temporary_paths.values(), *recovery_paths.values(), *backup_paths.values())
    ):
        raise MaterializationError(
            "stale materializer generation was recovered but artifact cleanup is "
            + "incomplete; preserve remaining .bak files and rerun"
        )


def _find_stale_transactions(
    outputs: Mapping[Path, bytes],
) -> dict[str, tuple[dict[Path, Path], dict[Path, Path]]]:
    transactions: dict[str, tuple[dict[Path, Path], dict[Path, Path]]] = {}
    for expected_index, destination in enumerate(outputs):
        if not destination.parent.exists():
            continue
        pattern = re.compile(
            rf"^\.{re.escape(destination.name)}\.(.+)\.([0-9]+)\.(tmp|bak)$"
        )
        for artifact in destination.parent.glob(f".{destination.name}.*"):
            match = pattern.fullmatch(artifact.name)
            if match is None:
                continue
            transaction_id, index_text, artifact_kind = match.groups()
            if int(index_text) != expected_index:
                raise MaterializationError(_STALE_RECOVERY_INSTRUCTIONS)
            temporary_paths, backup_paths = transactions.setdefault(
                transaction_id, ({}, {})
            )
            paths = temporary_paths if artifact_kind == "tmp" else backup_paths
            paths[destination] = artifact
    return transactions


def _transaction_path(
    destination: Path, transaction_id: str, index: int, suffix: str
) -> Path:
    return destination.with_name(
        f".{destination.name}.{transaction_id}.{index}.{suffix}"
    )


def _rollback_outputs(
    outputs: Mapping[Path, bytes],
    prior_stats: Mapping[Path, os.stat_result | None],
    backup_paths: Mapping[Path, Path],
    created_backups: set[Path],
    replacement_started: set[Path],
) -> bool:
    complete = True
    for destination in reversed(tuple(outputs)):
        if (
            destination not in created_backups
            and destination not in replacement_started
        ):
            continue
        try:
            if destination in created_backups:
                os.replace(backup_paths[destination], destination)
            elif destination in replacement_started:
                destination.unlink(missing_ok=True)
            _fsync_directory(destination.parent)
        except OSError:
            complete = False
    for destination, prior in prior_stats.items():
        try:
            if prior is None:
                complete &= not destination.exists() and not destination.is_symlink()
                continue
            restored = destination.lstat()
            complete &= (
                stat.S_ISREG(restored.st_mode)
                and restored.st_dev == prior.st_dev
                and restored.st_ino == prior.st_ino
                and stat.S_IMODE(restored.st_mode) == stat.S_IMODE(prior.st_mode)
            )
        except OSError:
            complete = False
    return complete


def _remove_transaction_files(paths: Iterable[Path]) -> bool:
    complete = True
    for path in paths:
        try:
            path.unlink()
            _fsync_directory(path.parent)
        except FileNotFoundError:
            continue
        except OSError:
            complete = False
    return complete


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    try:
        materialize(repository, Path.home())
    except MaterializationError as error:
        print(f"host materialization failed: {error}", file=sys.stderr)
        return 1
    except OSError:
        print("host materialization failed: local operation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
