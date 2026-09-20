import json
import os
import stat
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal, Protocol, cast, final

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from minos.ingest.watchers.recovery.image import RecoveryImageStagingArea
from minos.ingest.watchers.recovery.inventory import (
    MAX_INVENTORY_OBJECTS,
    RecoveryInventory,
    RecoveryInventoryStorage,
    VerifiedInventory,
    VerifiedInventoryChain,
    VerifiedRecoverySource,
    append_trusted_recovery,
    select_pre_cutoff_trusted_image,
    verify_inventory_chain,
)
from minos.ingest.watchers.recovery.inventory_signing import (
    RECOVERY_INVENTORY_NAMESPACE,
    InventorySigner,
    InventoryVerifier,
    SshKeygenEd25519Signer,
    SshKeygenEd25519Verifier,
)
from minos.ingest.watchers.recovery.models import (
    RecoveryClass,
    VerifiedRecoveryCandidate,
)
from minos.ingest.watchers.recovery.operator_s3 import S3OperatorRecoveryStorage
from minos.ingest.watchers.recovery.restore import (
    TrustedRecoveryDownloader as RestoreDownloader,
)
from minos.ingest.watchers.recovery.restore import install_trusted_recovery
from minos.ingest.watchers.recovery.s3 import S3RecoveryStore
from minos.ingest.watchers.service import (
    WatcherProcessLock,
    watcher_process_lock_path,
)

_MAX_CONFIG_FILE_BYTES = 8 * 1024
_MAX_ALLOWED_SIGNERS = 16
_MAX_OUTPUT_BYTES = 4096
_MAX_RECOVERY_IMAGES_PER_CLASS = 4096
_MAX_TIMESTAMP_MS = 253_402_300_799_999
_MAX_SEQUENCE = 10**20 - 1


class RecoveryCommandError(RuntimeError):
    """An operator recovery command failed without disclosing sensitive context."""


@dataclass(frozen=True, slots=True)
class RecoveryCommandArguments:
    action: str
    bucket: str | None = None
    expected_bucket_owner: str | None = None
    region: str | None = None
    maximum_inventories: str | None = None
    public_key_file: Path | None = None
    allowed_signers_file: Path | None = None
    signer_identity: str | None = None
    private_signing_key: Path | None = None
    staging_directory: Path | None = None
    maximum_run_images: str | None = None
    maximum_daily_images: str | None = None
    maximum_weekly_images: str | None = None
    maximum_monthly_images: str | None = None
    compromise_cutoff: str | None = None
    sequence: str | None = None
    database: Path | None = None
    process_lock: Path | None = None
    confirm_watcher_stopped: bool = False


@dataclass(frozen=True, slots=True)
class RecoveryCommandResult:
    operation: Literal["inventory-verify", "trust", "select", "restore"]
    inventory_count: int
    sequence: int | None
    state_revision: int | None
    sha256: str | None
    object_key: str | None
    version_id: str | None
    last_modified_ms: int | None
    installed: bool = False

    def as_json(self) -> str:
        payload: dict[str, bool | int | str | None] = {
            "installed": self.installed,
            "inventory_count": self.inventory_count,
            "last_modified_ms": self.last_modified_ms,
            "object_key": self.object_key,
            "operation": self.operation,
            "sequence": self.sequence,
            "sha256": self.sha256,
            "state_revision": self.state_revision,
            "version_id": self.version_id,
        }
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(encoded.encode("utf-8")) > _MAX_OUTPUT_BYTES:
            raise RecoveryCommandError("recovery command output exceeds its bound")
        return encoded


class RecoveryCommand(Protocol):
    def execute(self) -> RecoveryCommandResult: ...


class _OperatorStorage(
    RecoveryInventoryStorage,
    RestoreDownloader,
    Protocol,
):
    pass


class _RecoverySelector(Protocol):
    def select_newest(
        self,
        bounds: Mapping[RecoveryClass, int],
        staging: RecoveryImageStagingArea,
        *,
        page_size: int = 1000,
    ) -> VerifiedRecoveryCandidate: ...


class RecoveryCommandDependencies:
    def operator_storage(
        self,
        *,
        bucket: str,
        region: str,
        expected_bucket_owner: str,
    ) -> _OperatorStorage:
        return S3OperatorRecoveryStorage.create(
            bucket=bucket,
            region=region,
            expected_bucket_owner=expected_bucket_owner,
        )

    def recovery_selector(
        self,
        *,
        bucket: str,
        region: str,
        expected_bucket_owner: str,
    ) -> _RecoverySelector:
        return S3RecoveryStore.create(
            bucket=bucket,
            region=region,
            expected_bucket_owner=expected_bucket_owner,
        )

    def verifier(self, public_key: str, signer_identity: str) -> InventoryVerifier:
        return SshKeygenEd25519Verifier(
            public_key,
            signer_identity=signer_identity,
        )

    def signer(self, private_key: Path) -> InventorySigner:
        return SshKeygenEd25519Signer(private_key)

    def staging(self, path: Path) -> RecoveryImageStagingArea:
        return RecoveryImageStagingArea(path)

    def process_lock(self, path: Path) -> AbstractContextManager[object]:
        return WatcherProcessLock(path)

    def install(
        self,
        storage: RestoreDownloader,
        inventory: RecoveryInventory,
        database: Path,
        *,
        watcher_is_stopped: Callable[[], bool],
    ) -> None:
        _ = install_trusted_recovery(
            storage,
            inventory,
            database,
            watcher_is_stopped=watcher_is_stopped,
        )


class RecoveryEnvironmentSettings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MINOS_RECOVERY_",
        extra="ignore",
    )

    s3_bucket: str | None = None
    s3_bucket_file: Path | None = None
    expected_bucket_owner: str | None = None
    expected_bucket_owner_file: Path | None = None
    aws_region: str | None = None
    aws_region_file: Path | None = None
    maximum_inventories: int | None = Field(
        default=None, ge=1, le=MAX_INVENTORY_OBJECTS
    )
    maximum_inventories_file: Path | None = None
    public_key_file: Path | None = None
    allowed_signers_file: Path | None = None
    signer_identity: str = "minos-recovery-operator"
    private_signing_key_file: Path | None = None
    staging_directory: Path | None = None
    maximum_run_images: int | None = Field(
        default=None, ge=1, le=_MAX_RECOVERY_IMAGES_PER_CLASS
    )
    maximum_daily_images: int | None = Field(
        default=None, ge=1, le=_MAX_RECOVERY_IMAGES_PER_CLASS
    )
    maximum_weekly_images: int | None = Field(
        default=None, ge=1, le=_MAX_RECOVERY_IMAGES_PER_CLASS
    )
    maximum_monthly_images: int | None = Field(
        default=None, ge=1, le=_MAX_RECOVERY_IMAGES_PER_CLASS
    )
    database: Path | None = None
    process_lock: Path | None = None


@dataclass(frozen=True, slots=True)
class _CommonConfiguration:
    bucket: str
    region: str
    expected_bucket_owner: str
    maximum_inventories: int
    signer_identity: str
    public_key: str


@final
class RecoveryCliCommand:
    def __init__(
        self,
        arguments: RecoveryCommandArguments,
        settings: RecoveryEnvironmentSettings,
        dependencies: RecoveryCommandDependencies,
    ) -> None:
        self._arguments = arguments
        self._settings = settings
        self._dependencies = dependencies

    def execute(self) -> RecoveryCommandResult:
        configuration = _resolve_common_configuration(
            self._arguments,
            self._settings,
        )
        storage = self._dependencies.operator_storage(
            bucket=configuration.bucket,
            region=configuration.region,
            expected_bucket_owner=configuration.expected_bucket_owner,
        )
        verifier = self._dependencies.verifier(
            configuration.public_key,
            configuration.signer_identity,
        )
        if self._arguments.action == "inventory-verify":
            chain = _verified_chain(storage, verifier, configuration)
            return _chain_result("inventory-verify", chain)
        if self._arguments.action == "select":
            chain = _verified_chain(storage, verifier, configuration)
            cutoff_ms = _parse_cutoff(self._arguments.compromise_cutoff)
            selected = select_pre_cutoff_trusted_image(chain, cutoff_ms=cutoff_ms)
            return _inventory_result("select", chain, selected)
        if self._arguments.action == "trust":
            return self._trust(storage, verifier, configuration)
        if self._arguments.action == "restore":
            return self._restore(storage, verifier, configuration)
        raise RecoveryCommandError("recovery command is unsupported")

    def _trust(
        self,
        storage: _OperatorStorage,
        verifier: InventoryVerifier,
        configuration: _CommonConfiguration,
    ) -> RecoveryCommandResult:
        private_key = self._arguments.private_signing_key or (
            self._settings.private_signing_key_file
        )
        staging_path = self._arguments.staging_directory or (
            self._settings.staging_directory
        )
        if private_key is None:
            raise RecoveryCommandError("private signing key is not configured")
        if staging_path is None:
            raise RecoveryCommandError("recovery staging directory is not configured")
        bounds = _resolve_recovery_bounds(self._arguments, self._settings)
        selector = self._dependencies.recovery_selector(
            bucket=configuration.bucket,
            region=configuration.region,
            expected_bucket_owner=configuration.expected_bucket_owner,
        )
        candidate = selector.select_newest(
            bounds,
            self._dependencies.staging(staging_path),
        )
        source = VerifiedRecoverySource(
            candidate.key,
            candidate.version_id,
            candidate.sha256,
            candidate.size_bytes,
            candidate.metadata.state_revision,
        )
        chain = append_trusted_recovery(
            storage,
            self._dependencies.signer(private_key),
            verifier,
            source,
            maximum_objects=configuration.maximum_inventories,
        )
        return _chain_result("trust", chain)

    def _restore(
        self,
        storage: _OperatorStorage,
        verifier: InventoryVerifier,
        configuration: _CommonConfiguration,
    ) -> RecoveryCommandResult:
        if not self._arguments.confirm_watcher_stopped:
            raise RecoveryCommandError("stopped watcher confirmation is required")
        database = self._arguments.database or self._settings.database
        if database is None:
            raise RecoveryCommandError("restore database is not configured")
        process_lock = watcher_process_lock_path(database)
        configured_lock = self._arguments.process_lock or self._settings.process_lock
        if configured_lock is not None and configured_lock != process_lock:
            raise RecoveryCommandError("restore process lock path is not canonical")
        sequence = _parse_sequence(self._arguments.sequence)
        cutoff_ms = _parse_cutoff(self._arguments.compromise_cutoff)
        with self._dependencies.process_lock(process_lock):
            chain = _verified_chain(storage, verifier, configuration)
            selected = _inventory_by_sequence(chain, sequence)
            if selected != select_pre_cutoff_trusted_image(chain, cutoff_ms=cutoff_ms):
                raise RecoveryCommandError(
                    "restore sequence is not the selected pre-cutoff image"
                )
            self._dependencies.install(
                storage,
                selected,
                database,
                watcher_is_stopped=lambda: self._arguments.confirm_watcher_stopped,
            )
        return _inventory_result("restore", chain, selected, installed=True)


def create_recovery_command(
    arguments: RecoveryCommandArguments,
    *,
    dependencies: RecoveryCommandDependencies | None = None,
) -> RecoveryCommand:
    try:
        settings = RecoveryEnvironmentSettings.model_validate({})
    except ValidationError:
        raise RecoveryCommandError("recovery configuration is invalid") from None
    return RecoveryCliCommand(
        arguments,
        settings,
        dependencies or RecoveryCommandDependencies(),
    )


def _resolve_common_configuration(
    arguments: RecoveryCommandArguments,
    settings: RecoveryEnvironmentSettings,
) -> _CommonConfiguration:
    bucket = _resolve_text(
        arguments.bucket,
        settings.s3_bucket,
        settings.s3_bucket_file,
        "S3 bucket",
    )
    owner = _resolve_text(
        arguments.expected_bucket_owner,
        settings.expected_bucket_owner,
        settings.expected_bucket_owner_file,
        "expected S3 bucket owner",
    )
    region = _resolve_text(
        arguments.region,
        settings.aws_region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION"),
        settings.aws_region_file,
        "AWS region",
    )
    maximum_inventories = _resolve_integer(
        arguments.maximum_inventories,
        settings.maximum_inventories,
        settings.maximum_inventories_file,
        "inventory object bound",
        maximum=MAX_INVENTORY_OBJECTS,
    )
    public_path = arguments.public_key_file or settings.public_key_file
    allowed_path = arguments.allowed_signers_file or settings.allowed_signers_file
    if (public_path is None) == (allowed_path is None):
        raise RecoveryCommandError(
            "exactly one recovery verification key source is required"
        )
    identity = arguments.signer_identity or settings.signer_identity
    _validate_signer_identity(identity)
    public_key = (
        _load_public_key(public_path)
        if public_path is not None
        else _load_allowed_signer(cast(Path, allowed_path), identity)
    )
    return _CommonConfiguration(
        bucket,
        region,
        owner,
        maximum_inventories,
        identity,
        public_key,
    )


def _resolve_recovery_bounds(
    arguments: RecoveryCommandArguments,
    settings: RecoveryEnvironmentSettings,
) -> dict[RecoveryClass, int]:
    values = (
        ("run", arguments.maximum_run_images, settings.maximum_run_images),
        ("daily", arguments.maximum_daily_images, settings.maximum_daily_images),
        ("weekly", arguments.maximum_weekly_images, settings.maximum_weekly_images),
        ("monthly", arguments.maximum_monthly_images, settings.maximum_monthly_images),
    )
    result: dict[RecoveryClass, int] = {}
    for recovery_class, explicit, configured in values:
        result[cast(RecoveryClass, recovery_class)] = _resolve_integer(
            explicit,
            configured,
            None,
            f"{recovery_class} recovery image bound",
            maximum=_MAX_RECOVERY_IMAGES_PER_CLASS,
        )
    return result


def _verified_chain(
    storage: RecoveryInventoryStorage,
    verifier: InventoryVerifier,
    configuration: _CommonConfiguration,
) -> VerifiedInventoryChain:
    return verify_inventory_chain(
        storage,
        verifier,
        maximum_objects=configuration.maximum_inventories,
    )


def _chain_result(
    operation: Literal["inventory-verify", "trust"],
    chain: VerifiedInventoryChain,
) -> RecoveryCommandResult:
    if chain.head is None:
        return RecoveryCommandResult(operation, 0, None, None, None, None, None, None)
    return _verified_inventory_result(operation, chain, chain.head)


def _inventory_result(
    operation: Literal["select", "restore"],
    chain: VerifiedInventoryChain,
    inventory: RecoveryInventory,
    *,
    installed: bool = False,
) -> RecoveryCommandResult:
    verified = next(
        (
            item
            for item in chain.inventories
            if item.inventory.sequence == inventory.sequence
        ),
        None,
    )
    if verified is None:
        raise RecoveryCommandError("selected recovery inventory is not verified")
    return _verified_inventory_result(operation, chain, verified, installed=installed)


def _verified_inventory_result(
    operation: Literal["inventory-verify", "trust", "select", "restore"],
    chain: VerifiedInventoryChain,
    verified: VerifiedInventory,
    *,
    installed: bool = False,
) -> RecoveryCommandResult:
    inventory = verified.inventory
    return RecoveryCommandResult(
        operation,
        len(chain.inventories),
        inventory.sequence,
        inventory.trusted_object_revision,
        inventory.trusted_object_checksum_sha256,
        inventory.trusted_object_key,
        inventory.trusted_object_version_id,
        inventory.trusted_object_last_modified_ms,
        installed,
    )


def _inventory_by_sequence(
    chain: VerifiedInventoryChain,
    sequence: int,
) -> RecoveryInventory:
    for verified in chain.inventories:
        if verified.inventory.sequence == sequence:
            return verified.inventory
    raise RecoveryCommandError("requested recovery inventory is unavailable")


def _parse_cutoff(value: str | None) -> int:
    if value is None:
        raise RecoveryCommandError("compromise cutoff is required")
    if len(value) == 13 and value.isascii() and value.isdecimal():
        cutoff_ms = int(value)
    else:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            raise RecoveryCommandError("compromise cutoff is invalid") from None
        if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
            raise RecoveryCommandError("compromise cutoff is invalid")
        cutoff_ms = int(parsed.timestamp() * 1000)
    if cutoff_ms < 1 or cutoff_ms > _MAX_TIMESTAMP_MS:
        raise RecoveryCommandError("compromise cutoff is invalid")
    return cutoff_ms


def _parse_sequence(value: str | None) -> int:
    if value is None or not value.isascii() or not value.isdecimal():
        raise RecoveryCommandError("recovery inventory sequence is invalid")
    if len(value) > 20:
        raise RecoveryCommandError("recovery inventory sequence is invalid")
    sequence = int(value)
    if sequence < 1 or sequence > _MAX_SEQUENCE:
        raise RecoveryCommandError("recovery inventory sequence is invalid")
    return sequence


def _resolve_text(
    explicit: str | None,
    configured: str | None,
    configured_file: Path | None,
    label: str,
) -> str:
    if explicit is not None:
        value = explicit
    else:
        if configured is not None and configured_file is not None:
            raise RecoveryCommandError(f"{label} configuration is ambiguous")
        value = configured or (
            _decode_configuration_file(configured_file, "utf-8", label).strip()
            if configured_file is not None
            else ""
        )
    if len(value) < 1 or len(value) > 1024 or any(ord(char) < 0x21 for char in value):
        raise RecoveryCommandError(f"{label} is not configured safely")
    return value


def _resolve_integer(
    explicit: str | None,
    configured: int | None,
    configured_file: Path | None,
    label: str,
    *,
    maximum: int,
) -> int:
    if explicit is not None:
        raw = explicit
    elif configured is not None:
        if configured_file is not None:
            raise RecoveryCommandError(f"{label} configuration is ambiguous")
        return configured
    elif configured_file is not None:
        raw = _decode_configuration_file(configured_file, "ascii", label).strip()
    else:
        raise RecoveryCommandError(f"{label} is not configured")
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 10:
        raise RecoveryCommandError(f"{label} is invalid")
    value = int(raw)
    if value < 1 or value > maximum:
        raise RecoveryCommandError(f"{label} is invalid")
    return value


def _load_public_key(path: Path) -> str:
    try:
        return _read_bounded_file(path).decode("ascii").strip()
    except UnicodeDecodeError:
        raise RecoveryCommandError("recovery public key is invalid") from None


def _load_allowed_signer(path: Path, identity: str) -> str:
    try:
        content = _read_bounded_file(path).decode("ascii")
    except UnicodeDecodeError:
        raise RecoveryCommandError("allowed signers file is invalid") from None
    matches: list[str] = []
    lines = content.splitlines()
    if len(lines) > _MAX_ALLOWED_SIGNERS:
        raise RecoveryCommandError("allowed signers file exceeds its bound")
    for line in lines:
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        key_positions = [
            index for index, field in enumerate(fields) if field == "ssh-ed25519"
        ]
        if len(key_positions) != 1 or key_positions[0] + 1 >= len(fields):
            raise RecoveryCommandError("allowed signers file is invalid")
        if fields[0] == identity:
            key_position = key_positions[0]
            options = fields[1:key_position]
            required_namespace = f'namespaces="{RECOVERY_INVENTORY_NAMESPACE}"'
            if options and options != [required_namespace]:
                raise RecoveryCommandError("allowed signer options are unsafe")
            matches.append(f"{fields[key_position]} {fields[key_position + 1]}")
    if len(matches) != 1:
        raise RecoveryCommandError("allowed signer identity is not unique")
    return matches[0]


def _read_bounded_file(path: Path | None) -> bytes:
    if path is None:
        raise RecoveryCommandError("configuration file is not configured")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_size < 1
            or status.st_size > _MAX_CONFIG_FILE_BYTES
        ):
            raise RecoveryCommandError("configuration file is unsafe")
        chunks: list[bytes] = []
        remaining = _MAX_CONFIG_FILE_BYTES + 1
        for _read in range(_MAX_CONFIG_FILE_BYTES // 1024 + 2):
            chunk = os.read(descriptor, min(1024, remaining))
            if not chunk:
                content = b"".join(chunks)
                if len(content) != status.st_size:
                    raise RecoveryCommandError(
                        "configuration file changed while reading"
                    )
                return content
            chunks.append(chunk)
            remaining -= len(chunk)
            if remaining <= 0:
                raise RecoveryCommandError("configuration file exceeds its bound")
    except RecoveryCommandError:
        raise
    except OSError:
        raise RecoveryCommandError("configuration file is unavailable") from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    raise RecoveryCommandError("configuration file read exceeded its bound")


def _decode_configuration_file(path: Path, encoding: str, label: str) -> str:
    try:
        return _read_bounded_file(path).decode(encoding)
    except UnicodeDecodeError:
        raise RecoveryCommandError(f"{label} configuration is invalid") from None


def _validate_signer_identity(identity: str) -> None:
    if len(identity) < 1 or len(identity) > 128:
        raise RecoveryCommandError("recovery signer identity is invalid")
    if any(
        character
        not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._@+-"
        for character in identity
    ):
        raise RecoveryCommandError("recovery signer identity is invalid")
