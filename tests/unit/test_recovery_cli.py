import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import cast, final, override

import pytest

from minos.cli.main import main
from minos.cli.recovery import (
    RecoveryCommand,
    RecoveryCommandArguments,
    RecoveryCommandDependencies,
    create_recovery_command,
)
from minos.ingest.watchers.recovery.image import RecoveryImageStagingArea
from minos.ingest.watchers.recovery.inventory import (
    BoundedInventoryListing,
    ConditionalWriteResult,
    ListedInventoryObject,
    RecoveryInventory,
    TrustedCopyRequest,
    TrustedObjectIdentity,
    canonical_inventory_bytes,
    canonical_inventory_payload_bytes,
)
from minos.ingest.watchers.recovery.inventory_signing import (
    InventorySigner,
    InventoryVerifier,
)
from minos.ingest.watchers.recovery.models import (
    RecoveryClass,
    RecoveryImageMetadata,
    VerifiedRecoveryCandidate,
)
from minos.ingest.watchers.recovery.restore import TrustedRecoveryDownloader
from minos.ingest.watchers.recovery.trusted_keys import (
    parse_trusted_recovery_key,
    recovery_inventory_key,
    trusted_recovery_key,
)

_BUCKET = "minos-recovery-test"
_OWNER = "123456789012"
_REGION = "eu-central-1"
_CREATED_AT_MS = 1_758_225_600_000
_SHA256_A = "a" * 64
_SHA256_B = "b" * 64


@final
class _Signer:
    def sign(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


@final
class _Verifier:
    def verify(self, content: bytes, signature: str) -> bool:
        return signature == _Signer().sign(content)


@final
class _MemoryStorage:
    def __init__(self) -> None:
        self.inventories: dict[str, bytes] = {}
        self.trusted: dict[str, TrustedObjectIdentity] = {}

    def list_inventories(self, *, maximum_objects: int) -> BoundedInventoryListing:
        keys = sorted(self.inventories)
        return BoundedInventoryListing(
            tuple(ListedInventoryObject(key) for key in keys[:maximum_objects]),
            len(keys) <= maximum_objects,
        )

    def read_inventory(self, key: str, *, maximum_bytes: int) -> bytes:
        content = self.inventories[key]
        assert len(content) <= maximum_bytes
        return content

    def copy_trusted_if_absent(
        self,
        request: TrustedCopyRequest,
    ) -> ConditionalWriteResult:
        if request.destination_key in self.trusted:
            return ConditionalWriteResult.PRECONDITION_FAILED
        sequence = parse_trusted_recovery_key(request.destination_key).sequence
        self.trusted[request.destination_key] = TrustedObjectIdentity(
            request.destination_key,
            f"trusted-version-{sequence}",
            request.source.sha256,
            request.source.size_bytes,
            _CREATED_AT_MS + sequence,
        )
        return ConditionalWriteResult.CREATED

    def head_trusted(self, key: str) -> TrustedObjectIdentity:
        return self.trusted[key]

    def put_inventory_if_absent(
        self,
        key: str,
        content: bytes,
    ) -> ConditionalWriteResult:
        if key in self.inventories:
            return ConditionalWriteResult.PRECONDITION_FAILED
        self.inventories[key] = content
        return ConditionalWriteResult.CREATED

    def download_trusted(
        self,
        identity: TrustedObjectIdentity,
        destination_descriptor: int,
    ) -> None:
        _ = identity
        _ = destination_descriptor
        raise AssertionError("CLI restore tests inject the installer")


@final
class _Selector:
    def __init__(self, candidate: VerifiedRecoveryCandidate) -> None:
        self.candidate = candidate
        self.bounds: Mapping[RecoveryClass, int] | None = None

    def select_newest(
        self,
        bounds: Mapping[RecoveryClass, int],
        staging: RecoveryImageStagingArea,
        *,
        page_size: int = 1000,
    ) -> VerifiedRecoveryCandidate:
        _ = staging
        _ = page_size
        self.bounds = bounds
        return self.candidate


@final
class _Lock(AbstractContextManager[object]):
    def __init__(self, dependencies: _Dependencies) -> None:
        self._dependencies = dependencies

    @override
    def __enter__(self) -> object:
        _ = super().__enter__()
        if self._dependencies.lock_error is not None:
            raise self._dependencies.lock_error
        self._dependencies.lock_active = True
        return self

    @override
    def __exit__(
        self,
        _error_type: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self._dependencies.lock_active = False


@final
class _Dependencies(RecoveryCommandDependencies):
    def __init__(
        self,
        storage: _MemoryStorage,
        candidate: VerifiedRecoveryCandidate,
        tmp_path: Path,
    ) -> None:
        self.storage = storage
        self.selector = _Selector(candidate)
        self.tmp_path = tmp_path
        self.signer_paths: list[Path] = []
        self.operator_configurations: list[tuple[str, str, str]] = []
        self.installed: list[tuple[RecoveryInventory, Path]] = []
        self.lock_paths: list[Path] = []
        self.lock_active = False
        self.lock_error: BaseException | None = None

    @override
    def operator_storage(
        self,
        *,
        bucket: str,
        region: str,
        expected_bucket_owner: str,
    ) -> _MemoryStorage:
        self.operator_configurations.append((bucket, region, expected_bucket_owner))
        return self.storage

    @override
    def recovery_selector(
        self,
        *,
        bucket: str,
        region: str,
        expected_bucket_owner: str,
    ) -> _Selector:
        assert (bucket, region, expected_bucket_owner) == (_BUCKET, _REGION, _OWNER)
        return self.selector

    @override
    def verifier(self, public_key: str, signer_identity: str) -> InventoryVerifier:
        assert public_key
        assert signer_identity == "minos-recovery-operator"
        return _Verifier()

    @override
    def signer(self, private_key: Path) -> InventorySigner:
        self.signer_paths.append(private_key)
        return _Signer()

    @override
    def staging(self, path: Path) -> RecoveryImageStagingArea:
        return RecoveryImageStagingArea(path)

    @override
    def process_lock(self, path: Path) -> AbstractContextManager[object]:
        self.lock_paths.append(path)
        return _Lock(self)

    @override
    def install(
        self,
        storage: TrustedRecoveryDownloader,
        inventory: RecoveryInventory,
        database: Path,
        *,
        watcher_is_stopped: Callable[[], bool],
    ) -> None:
        assert storage is self.storage
        assert self.lock_active
        assert watcher_is_stopped()
        self.installed.append((inventory, database))


def test_verify_and_select_do_not_require_or_construct_signing_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    storage = _storage_with_chain()
    dependencies = _dependencies(storage, tmp_path)
    public_key = _write_public_key(tmp_path)
    common = _common_arguments(public_key)

    main(
        ["recovery", "inventory", "verify", *common],
        recovery_factory=_factory(dependencies),
    )
    verified = _json_output(capsys)
    assert verified == {
        "installed": False,
        "inventory_count": 2,
        "last_modified_ms": 200,
        "object_key": trusted_recovery_key(2, _SHA256_B),
        "operation": "inventory-verify",
        "sequence": 2,
        "sha256": _SHA256_B,
        "state_revision": 20,
        "version_id": "version-2",
    }

    main(
        [
            "recovery",
            "select",
            *common,
            "--compromise-cutoff",
            "1970-01-01T00:00:01Z",
        ],
        recovery_factory=_factory(dependencies),
    )
    selected = _json_output(capsys)
    assert selected["operation"] == "select"
    assert selected["sequence"] == 2
    assert dependencies.signer_paths == []


def test_trust_selects_verified_recovery_and_uses_private_key_only_there(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    storage = _MemoryStorage()
    dependencies = _dependencies(storage, tmp_path)
    public_key = _write_public_key(tmp_path)
    private_key = tmp_path / "private-operator-key"
    bounds = [
        "--maximum-run-images",
        "2",
        "--maximum-daily-images",
        "3",
        "--maximum-weekly-images",
        "4",
        "--maximum-monthly-images",
        "5",
    ]

    main(
        [
            "recovery",
            "trust",
            *_common_arguments(public_key),
            "--private-signing-key",
            str(private_key),
            "--staging-directory",
            str(tmp_path / "staging"),
            *bounds,
        ],
        recovery_factory=_factory(dependencies),
    )

    result = _json_output(capsys)
    assert result["operation"] == "trust"
    assert result["sequence"] == 1
    assert result["state_revision"] == 7
    assert dependencies.signer_paths == [private_key]
    assert dependencies.selector.bounds == {
        "run": 2,
        "daily": 3,
        "weekly": 4,
        "monthly": 5,
    }
    assert str(private_key) not in json.dumps(result)


def test_restore_holds_exclusive_lock_and_requires_confirmation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    storage = _storage_with_chain()
    dependencies = _dependencies(storage, tmp_path)
    database = tmp_path / "control" / "control.sqlite3"
    lock = tmp_path / "control" / "watcher.lock"

    main(
        [
            "recovery",
            "restore",
            *_common_arguments(_write_public_key(tmp_path)),
            "--sequence",
            "1",
            "--compromise-cutoff",
            "0000000000150",
            "--database",
            str(database),
            "--confirm-watcher-stopped",
        ],
        recovery_factory=_factory(dependencies),
    )

    result = _json_output(capsys)
    assert result["operation"] == "restore"
    assert result["installed"] is True
    assert result["sequence"] == 1
    assert dependencies.installed[0][0].sequence == 1
    assert dependencies.installed[0][1] == database
    assert dependencies.lock_paths == [lock]
    assert not dependencies.lock_active


def test_restore_rejects_sequence_not_selected_by_compromise_cutoff(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dependencies = _dependencies(_storage_with_chain(), tmp_path)

    with pytest.raises(SystemExit) as captured:
        main(
            [
                "recovery",
                "restore",
                *_common_arguments(_write_public_key(tmp_path)),
                "--sequence",
                "2",
                "--compromise-cutoff",
                "0000000000150",
                "--database",
                str(tmp_path / "control" / "control.sqlite3"),
                "--confirm-watcher-stopped",
            ],
            recovery_factory=_factory(dependencies),
        )

    assert captured.value.code == 1
    assert capsys.readouterr().err == "minos: recovery command failed\n"
    assert dependencies.installed == []
    assert dependencies.lock_active is False


def test_environment_file_configuration_and_allowed_signer_are_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bucket_file = tmp_path / "bucket"
    owner_file = tmp_path / "owner"
    region_file = tmp_path / "region"
    maximum_file = tmp_path / "maximum"
    allowed_file = tmp_path / "allowed-signers"
    _ = bucket_file.write_text(_BUCKET, encoding="utf-8")
    _ = owner_file.write_text(_OWNER, encoding="utf-8")
    _ = region_file.write_text(_REGION, encoding="utf-8")
    _ = maximum_file.write_text("8", encoding="ascii")
    _ = allowed_file.write_text(
        'minos-recovery-operator namespaces="minos-health-recovery-inventory-v1" '
        + "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA==\n",
        encoding="ascii",
    )
    monkeypatch.setenv("MINOS_RECOVERY_S3_BUCKET_FILE", str(bucket_file))
    monkeypatch.setenv("MINOS_RECOVERY_EXPECTED_BUCKET_OWNER_FILE", str(owner_file))
    monkeypatch.setenv("MINOS_RECOVERY_AWS_REGION_FILE", str(region_file))
    monkeypatch.setenv("MINOS_RECOVERY_MAXIMUM_INVENTORIES_FILE", str(maximum_file))
    monkeypatch.setenv("MINOS_RECOVERY_ALLOWED_SIGNERS_FILE", str(allowed_file))
    dependencies = _dependencies(_storage_with_chain(), tmp_path)

    main(
        ["recovery", "inventory", "verify"],
        recovery_factory=_factory(dependencies),
    )

    result = _json_output(capsys)
    assert result["inventory_count"] == 2
    assert dependencies.operator_configurations == [(_BUCKET, _REGION, _OWNER)]


@pytest.mark.parametrize(
    "options",
    [
        'namespaces="another-namespace"',
        'namespaces="minos-health-recovery-inventory-v1",valid-before="20300101Z"',
        'cert-authority namespaces="minos-health-recovery-inventory-v1"',
    ],
)
def test_allowed_signer_rejects_unsafe_authorization_options(
    options: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    allowed_file = tmp_path / "allowed-signers"
    _ = allowed_file.write_text(
        f"minos-recovery-operator {options} "
        + "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA==\n",
        encoding="ascii",
    )

    with pytest.raises(SystemExit) as captured:
        main(
            [
                "recovery",
                "inventory",
                "verify",
                *_common_configuration(),
                "--allowed-signers-file",
                str(allowed_file),
            ],
            recovery_factory=_factory(_dependencies(_storage_with_chain(), tmp_path)),
        )

    assert captured.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "minos: recovery command failed\n"


def test_restore_rejects_process_lock_not_derived_from_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dependencies = _dependencies(_storage_with_chain(), tmp_path)
    database = tmp_path / "control" / "control.sqlite3"

    with pytest.raises(SystemExit) as captured:
        main(
            [
                "recovery",
                "restore",
                *_common_arguments(_write_public_key(tmp_path)),
                "--sequence",
                "1",
                "--compromise-cutoff",
                "0000000000150",
                "--database",
                str(database),
                "--process-lock",
                str(tmp_path / "unrelated" / "watcher.lock"),
                "--confirm-watcher-stopped",
            ],
            recovery_factory=_factory(dependencies),
        )

    assert captured.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "minos: recovery command failed\n"
    assert dependencies.lock_paths == []
    assert dependencies.installed == []


def test_invalid_file_encoding_fails_without_disclosure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bucket_file = tmp_path / "private-bucket"
    _ = bucket_file.write_bytes(b"\xffcredential-value")
    monkeypatch.setenv("MINOS_RECOVERY_S3_BUCKET_FILE", str(bucket_file))

    with pytest.raises(SystemExit) as captured:
        main(
            [
                "recovery",
                "inventory",
                "verify",
                "--expected-bucket-owner",
                _OWNER,
                "--region",
                _REGION,
                "--maximum-inventories",
                "8",
                "--public-key-file",
                str(_write_public_key(tmp_path)),
            ],
            recovery_factory=_factory(_dependencies(_storage_with_chain(), tmp_path)),
        )

    assert captured.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "minos: recovery command failed\n"
    assert "credential-value" not in output.err
    assert str(bucket_file) not in output.err


@pytest.mark.parametrize(
    "arguments",
    [
        ["recovery", "inventory", "verify"],
        [
            "recovery",
            "select",
            "--compromise-cutoff",
            "not-a-cutoff",
        ],
    ],
)
def test_malformed_configuration_and_cutoff_fail_without_disclosure(
    arguments: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dependencies = _dependencies(_storage_with_chain(), tmp_path)
    with pytest.raises(SystemExit) as captured:
        main(arguments, recovery_factory=_factory(dependencies))
    assert captured.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "minos: recovery command failed\n"
    assert "not-a-cutoff" not in output.err


def test_lock_failure_and_dependency_errors_are_sanitized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    storage = _storage_with_chain()
    dependencies = _dependencies(storage, tmp_path)
    dependencies.lock_error = RuntimeError("/secret/process.lock credential-value")
    database = tmp_path / "private" / "control.sqlite3"
    arguments = [
        "recovery",
        "restore",
        *_common_arguments(_write_public_key(tmp_path)),
        "--sequence",
        "1",
        "--compromise-cutoff",
        "0000000000150",
        "--database",
        str(database),
        "--confirm-watcher-stopped",
    ]

    with pytest.raises(SystemExit) as captured:
        main(arguments, recovery_factory=_factory(dependencies))

    assert captured.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "minos: recovery command failed\n"
    assert "/secret/process.lock" not in output.err
    assert "credential-value" not in output.err
    assert dependencies.lock_paths == [database.parent / "watcher.lock"]
    assert dependencies.installed == []


def test_restore_confirmation_flag_is_required_by_parser(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as captured:
        main(
            [
                "recovery",
                "restore",
                *_common_arguments(_write_public_key(tmp_path)),
                "--sequence",
                "1",
                "--compromise-cutoff",
                "0000000000150",
                "--database",
                str(tmp_path / "control.sqlite3"),
                "--process-lock",
                str(tmp_path / "watcher.lock"),
            ]
        )
    assert captured.value.code == 2


def _factory(
    dependencies: _Dependencies,
) -> Callable[[RecoveryCommandArguments], RecoveryCommand]:
    def factory(arguments: RecoveryCommandArguments) -> RecoveryCommand:
        return create_recovery_command(
            arguments,
            dependencies=dependencies,
        )

    return factory


def _json_output(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    return cast(dict[str, object], json.loads(capsys.readouterr().out))


def _dependencies(storage: _MemoryStorage, tmp_path: Path) -> _Dependencies:
    metadata = RecoveryImageMetadata(
        "daily",
        7,
        2,
        "c" * 64,
        _CREATED_AT_MS,
        None,
        None,
    )
    candidate = VerifiedRecoveryCandidate(
        f"control-recovery/daily/2025-09-18/{7:020d}/{_SHA256_A}.sqlite3",
        "source-version",
        _SHA256_A,
        4096,
        _CREATED_AT_MS,
        metadata,
    )
    return _Dependencies(storage, candidate, tmp_path)


def _common_arguments(public_key: Path) -> list[str]:
    return [*_common_configuration(), "--public-key-file", str(public_key)]


def _common_configuration() -> list[str]:
    return [
        "--bucket",
        _BUCKET,
        "--expected-bucket-owner",
        _OWNER,
        "--region",
        _REGION,
        "--maximum-inventories",
        "8",
    ]


def _write_public_key(tmp_path: Path) -> Path:
    path = tmp_path / "operator-key.pub"
    _ = path.write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA== operator\n",
        encoding="ascii",
    )
    return path


def _storage_with_chain() -> _MemoryStorage:
    storage = _MemoryStorage()
    first = _signed_inventory(1, None, _SHA256_A, revision=10, modified_ms=100)
    first_content = canonical_inventory_bytes(first)
    second = _signed_inventory(
        2,
        hashlib.sha256(first_content).hexdigest(),
        _SHA256_B,
        revision=20,
        modified_ms=200,
    )
    storage.inventories[recovery_inventory_key(1)] = first_content
    storage.inventories[recovery_inventory_key(2)] = canonical_inventory_bytes(second)
    return storage


def _signed_inventory(
    sequence: int,
    prior_sha256: str | None,
    sha256: str,
    *,
    revision: int,
    modified_ms: int,
) -> RecoveryInventory:
    unsigned = RecoveryInventory(
        sequence=sequence,
        prior_inventory_sha256=prior_sha256,
        trusted_object_key=trusted_recovery_key(sequence, sha256),
        trusted_object_version_id=f"version-{sequence}",
        trusted_object_checksum_sha256=sha256,
        trusted_object_revision=revision,
        trusted_object_size_bytes=4096,
        trusted_object_last_modified_ms=modified_ms,
        signature="pending",
    )
    return unsigned.model_copy(
        update={
            "signature": _Signer().sign(canonical_inventory_payload_bytes(unsigned))
        }
    )
