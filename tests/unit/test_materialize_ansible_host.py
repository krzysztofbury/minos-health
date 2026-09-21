import base64
import os
import stat
import struct
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.materialize_ansible_host import (
    SECRET_REFERENCES,
    MaterializationError,
    materialize,
)


def _public_key(byte: int) -> str:
    key_type = b"ssh-ed25519"
    blob = struct.pack(">I", len(key_type)) + key_type + struct.pack(">I", 32)
    blob += bytes([byte]) * 32
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} synthetic"


def _values() -> dict[str, str]:
    private_begin = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
    private_end = "-----END OPENSSH " + "PRIVATE KEY-----"
    return {
        "ansible_host": "synthetic.example.test",
        "ansible_port": "12345",
        "ansible_user": "root",
        "expected_hostname": "synthetic-host",
        "cloudflared_tunnel_id": "11111111-2222-3333-4444-555555555555",
        "ssh_host_key": _public_key(1),
        "operator_public_key": _public_key(2),
        "operator_private_key": f"{private_begin}\n{'A' * 128}\n{private_end}\n",
    }


def _reader(values: dict[str, str]) -> Callable[[str], str]:
    by_reference = {SECRET_REFERENCES[name]: value for name, value in values.items()}
    return by_reference.__getitem__


def _destinations(repository: Path, home: Path) -> tuple[Path, ...]:
    return (
        home / ".ssh/minos_mikrus_ed25519",
        repository / "infra/ansible/inventory/host_vars/minos-production.yml",
        repository / "infra/ansible/known_hosts",
        repository / "infra/ansible/files/root_authorized_keys",
    )


def _transaction_artifact(
    destination: Path, transaction_id: str, index: int, suffix: str
) -> Path:
    return destination.with_name(
        f".{destination.name}.{transaction_id}.{index}.{suffix}"
    )


def test_materializer_serializes_validated_host_material_atomically(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    home = tmp_path / "home"
    values = _values()

    materialize(
        repository,
        home,
        read_secret=_reader(values),
        derive_public_key=lambda _path: values["operator_public_key"],
    )

    inventory = (
        repository / "infra/ansible/inventory/host_vars/minos-production.yml"
    ).read_text()
    assert inventory == (
        "---\n"
        'ansible_host: "synthetic.example.test"\n'
        "ansible_port: 12345\n"
        'ansible_user: "root"\n'
        'expected_hostname: "synthetic-host"\n'
        'cloudflared_tunnel_id: "11111111-2222-3333-4444-555555555555"\n'
    )
    assert (repository / "infra/ansible/known_hosts").read_text() == (
        f"[synthetic.example.test]:12345 {_public_key(1).rsplit(' ', 1)[0]}\n"
    )
    assert (
        repository / "infra/ansible/files/root_authorized_keys"
    ).read_text() == f"{_public_key(2).rsplit(' ', 1)[0]}\n"
    assert (home / ".ssh/minos_mikrus_ed25519").stat().st_mode & 0o777 == 0o600
    assert all(
        path.stat().st_mode & 0o777 == 0o600
        for path in (
            repository / "infra/ansible/known_hosts",
            repository / "infra/ansible/files/root_authorized_keys",
            repository / "infra/ansible/inventory/host_vars/minos-production.yml",
        )
    )


def test_materializer_fsyncs_every_output_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    home = tmp_path / "home"
    destinations = _destinations(repository, home)
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_text("prior\n")
    real_fsync = os.fsync
    synced_directories: set[tuple[int, int]] = set()

    def fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if stat.S_ISDIR(metadata.st_mode):
            synced_directories.add((metadata.st_dev, metadata.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fsync)
    values = _values()

    materialize(
        repository,
        home,
        read_secret=_reader(values),
        derive_public_key=lambda _path: values["operator_public_key"],
    )

    expected_directories = {
        (metadata.st_dev, metadata.st_ino)
        for destination in destinations
        if stat.S_ISDIR((metadata := destination.parent.stat()).st_mode)
    }
    assert expected_directories <= synced_directories


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ansible_host", "single-label"),
        ("ansible_host", "host.example.test\nmalicious"),
        ("ansible_port", "0"),
        ("ansible_port", "65536"),
        ("ansible_user", "ubuntu"),
        ("expected_hostname", "invalid.example"),
        ("cloudflared_tunnel_id", "{{ unresolved }}"),
        ("ssh_host_key", "ssh-rsa invalid"),
        ("ssh_host_key", f"{_public_key(1)}\n{_public_key(1)}"),
        ("operator_public_key", "op://unresolved/key"),
        ("operator_private_key", "not-a-private-key"),
    ],
)
def test_materializer_rejects_invalid_values_without_replacing_outputs(
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    repository = tmp_path / "repository"
    destination = repository / "infra/ansible/inventory/host_vars/minos-production.yml"
    destination.parent.mkdir(parents=True)
    _ = destination.write_text("prior\n")
    values = _values()
    values[name] = value

    with pytest.raises(MaterializationError):
        materialize(
            repository,
            tmp_path / "home",
            read_secret=_reader(values),
            derive_public_key=lambda _path: values["operator_public_key"],
        )

    assert destination.read_text() == "prior\n"


def test_materializer_rejects_mismatched_operator_key_pair(tmp_path: Path) -> None:
    values = _values()

    with pytest.raises(MaterializationError, match="do not match"):
        materialize(
            tmp_path / "repository",
            tmp_path / "home",
            read_secret=_reader(values),
            derive_public_key=lambda _path: _public_key(3),
        )


def test_materializer_verifies_a_synthetic_openssh_key_pair(tmp_path: Path) -> None:
    key_path = tmp_path / "synthetic-key"
    generated = subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert generated.returncode == 0
    values = _values()
    values["operator_private_key"] = key_path.read_text()
    values["operator_public_key"] = key_path.with_suffix(".pub").read_text()

    materialize(
        tmp_path / "repository",
        tmp_path / "home",
        read_secret=_reader(values),
    )


def test_ansible_ssh_trusts_only_the_materialized_ed25519_pin() -> None:
    root = Path(__file__).parents[2]
    configuration = (root / "infra/ansible/ansible.cfg").read_text()

    for option in (
        "StrictHostKeyChecking=yes",
        "UserKnownHostsFile=infra/ansible/known_hosts",
        "GlobalKnownHostsFile=/dev/null",
        "KnownHostsCommand=none",
        "VerifyHostKeyDNS=no",
        "UpdateHostKeys=no",
        "CheckHostIP=no",
        "HostKeyAlgorithms=ssh-ed25519",
    ):
        assert f"-o {option}" in configuration


def test_materializer_rolls_back_all_outputs_after_install_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    home = tmp_path / "home"
    destinations = _destinations(repository, home)
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_text("prior\n")
    real_replace = os.replace
    failed = False

    def replace(source: Path, destination: Path) -> None:
        nonlocal failed
        if (
            not failed
            and source.name.endswith(".tmp")
            and destination.name == "known_hosts"
        ):
            failed = True
            raise OSError
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace)
    values = _values()

    with pytest.raises(MaterializationError, match="installation failed"):
        materialize(
            repository,
            home,
            read_secret=_reader(values),
            derive_public_key=lambda _path: values["operator_public_key"],
        )

    assert failed
    assert all(destination.read_text() == "prior\n" for destination in destinations)


def test_materializer_retains_backups_when_rollback_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    home = tmp_path / "home"
    destinations = _destinations(repository, home)
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_text("prior\n")
    inventory = destinations[1]
    real_replace = os.replace
    install_failed = False
    rollback_failed = False

    def replace(source: Path, destination: Path) -> None:
        nonlocal install_failed, rollback_failed
        if (
            not install_failed
            and source.name.endswith(".tmp")
            and destination.name == "known_hosts"
        ):
            install_failed = True
            raise OSError
        if (
            install_failed
            and not rollback_failed
            and source.name.endswith(".bak")
            and destination == inventory
        ):
            rollback_failed = True
            raise OSError
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace)
    values = _values()

    with pytest.raises(
        MaterializationError, match="rollback or cleanup was incomplete"
    ):
        materialize(
            repository,
            home,
            read_secret=_reader(values),
            derive_public_key=lambda _path: values["operator_public_key"],
        )

    assert install_failed
    assert rollback_failed
    assert inventory.read_text() != "prior\n"
    assert any(path.suffix == ".bak" for path in inventory.parent.iterdir())


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("replacement_boundary", range(4))
@pytest.mark.parametrize("prior_outputs_exist", [False, True])
def test_materializer_rolls_back_process_interruptions_at_every_output_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[BaseException],
    replacement_boundary: int,
    prior_outputs_exist: bool,
) -> None:
    repository = tmp_path / "repository"
    home = tmp_path / "home"
    destinations = _destinations(repository, home)
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if prior_outputs_exist:
            _ = destination.write_text("prior\n")
    real_replace = os.replace
    replacement_index = 0

    def replace(source: Path, destination: Path) -> None:
        nonlocal replacement_index
        real_replace(source, destination)
        if source.name.endswith(".tmp"):
            current_index = replacement_index
            replacement_index += 1
            if current_index == replacement_boundary:
                raise exception_type

    monkeypatch.setattr(os, "replace", replace)
    values = _values()

    with pytest.raises(exception_type):
        materialize(
            repository,
            home,
            read_secret=_reader(values),
            derive_public_key=lambda _path: values["operator_public_key"],
        )

    assert replacement_index == replacement_boundary + 1
    if prior_outputs_exist:
        assert all(destination.read_text() == "prior\n" for destination in destinations)
    else:
        assert all(not destination.exists() for destination in destinations)
    for parent in {destination.parent for destination in destinations}:
        assert not any(path.suffix in {".tmp", ".bak"} for path in parent.iterdir())


def test_materializer_rejects_and_preserves_a_stale_temporary_file(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    home = tmp_path / "home"
    destinations = _destinations(repository, home)
    for index, destination in enumerate(destinations):
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(f"prior-{index}".encode())
        destination.chmod(0o600)
    stale_temporary = _transaction_artifact(destinations[2], "a" * 32, 2, "tmp")
    _ = stale_temporary.write_bytes(b"stale-candidate")
    stale_temporary.chmod(0o600)
    values = _values()

    with pytest.raises(MaterializationError, match="manual recovery"):
        materialize(
            repository,
            home,
            read_secret=_reader(values),
            derive_public_key=lambda _path: values["operator_public_key"],
        )

    assert stale_temporary.read_bytes() == b"stale-candidate"
    assert all(
        destination.read_bytes() == f"prior-{index}".encode()
        for index, destination in enumerate(destinations)
    )


def test_materializer_recovers_one_complete_stale_backup_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    home = tmp_path / "home"
    destinations = _destinations(repository, home)
    transaction_id = "12345"
    backups: list[Path] = []
    for index, destination in enumerate(destinations):
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(f"partial-{index}".encode())
        destination.chmod(0o600)
        backup = _transaction_artifact(destination, transaction_id, index, "bak")
        _ = backup.write_bytes(f"prior-{index}".encode())
        backup.chmod(0o600)
        backups.append(backup)
    stale_temporary = _transaction_artifact(destinations[2], transaction_id, 2, "tmp")
    _ = stale_temporary.write_bytes(b"stale-candidate")
    stale_temporary.chmod(0o600)
    recovered_backups: list[Path] = []
    real_link = os.link

    def link(source: Path, destination: Path) -> None:
        if source.suffix == ".bak":
            recovered_backups.append(source)
        real_link(source, destination)

    monkeypatch.setattr(os, "link", link)
    values = _values()

    materialize(
        repository,
        home,
        read_secret=_reader(values),
        derive_public_key=lambda _path: values["operator_public_key"],
    )

    assert set(recovered_backups) == set(backups)
    assert not stale_temporary.exists()
    assert all(not backup.exists() for backup in backups)
    assert all(destination.exists() for destination in destinations)


def test_materializer_rejects_and_preserves_an_incomplete_backup_generation(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    home = tmp_path / "home"
    destinations = _destinations(repository, home)
    transaction_id = "b" * 32
    backups: list[Path] = []
    for index, destination in enumerate(destinations):
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(f"current-{index}".encode())
        destination.chmod(0o600)
        if index < len(destinations) - 1:
            backup = _transaction_artifact(destination, transaction_id, index, "bak")
            _ = backup.write_bytes(f"prior-{index}".encode())
            backup.chmod(0o600)
            backups.append(backup)
    values = _values()

    with pytest.raises(MaterializationError, match="manual recovery"):
        materialize(
            repository,
            home,
            read_secret=_reader(values),
            derive_public_key=lambda _path: values["operator_public_key"],
        )

    assert all(backup.exists() for backup in backups)
    assert all(
        destination.read_bytes() == f"current-{index}".encode()
        for index, destination in enumerate(destinations)
    )


def test_gitignore_covers_every_repository_transaction_artifact_pattern() -> None:
    root = Path(__file__).parents[2]
    ignored_patterns = set((root / ".gitignore").read_text().splitlines())

    assert {
        "/infra/ansible/inventory/host_vars/.minos-production.yml.*.*.tmp",
        "/infra/ansible/inventory/host_vars/.minos-production.yml.*.*.bak",
        "/infra/ansible/.known_hosts.*.*.tmp",
        "/infra/ansible/.known_hosts.*.*.bak",
        "/infra/ansible/files/.root_authorized_keys.*.*.tmp",
        "/infra/ansible/files/.root_authorized_keys.*.*.bak",
    } <= ignored_patterns
