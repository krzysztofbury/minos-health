import base64
import hashlib
import math
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, Protocol, Self, TypeIs, cast, final

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)

from minos.ingest.watchers.control import (
    CONTROL_SCHEMA_VERSION,
    ControlSnapshotError,
    ControlStore,
)
from minos.ingest.watchers.protocols import PreflightProbe
from minos.ingest.watchers.recovery import (
    PublishedRecoveryImage,
    RecoveryImageRequest,
    RecoveryImageStagingArea,
    StagedRecoveryImage,
    VerifiedRecoveryImage,
)

GIBIBYTE = 1024 * 1024 * 1024
MEBIBYTE = 1024 * 1024
_MAX_STAGING_ENTRIES = 32
_MAX_PREFLIGHT_PROBES = 32
_MAX_SECRET_FILES = 16
_CONTROL_COPY_TIMEOUT_SECONDS = 300.0
_CONTROL_COPY_MAXIMUM_BYTES = 2 * GIBIBYTE
_CONTROL_COPY_PAGES_PER_STEP = 256
_SQLITE_MINIMUM_PAGE_BYTES = 512
_AWS_ACCOUNT_ID = re.compile(r"[0-9]{12}")
_S3_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
_PRODUCTION_PROBE_NAMES = (
    "capacity",
    "image_digest",
    "numeric_identity",
    "secrets",
    "control_database",
    "s3_checksum",
    "recovery_round_trip",
    "healthchecks",
)


class PreflightError(RuntimeError):
    """A paused watcher preflight or runtime capacity gate failed."""


class DiskUsage(NamedTuple):
    total: int
    used: int
    free: int


@dataclass(frozen=True, slots=True)
class CapacityLimits:
    maximum_database_size_bytes: int = 2 * GIBIBYTE
    maximum_download_size_bytes: int = 512 * MEBIBYTE
    staging_quota_bytes: int = 600 * MEBIBYTE
    minimum_free_space_bytes: int = GIBIBYTE

    def __post_init__(self) -> None:
        values = (
            self.maximum_database_size_bytes,
            self.maximum_download_size_bytes,
            self.staging_quota_bytes,
            self.minimum_free_space_bytes,
        )
        if any(not _is_strict_integer(value) or value < 1 for value in values):
            raise PreflightError("capacity limits must be positive integers")
        if self.staging_quota_bytes < self.maximum_download_size_bytes:
            raise PreflightError("staging quota is below the download ceiling")


@dataclass(frozen=True, slots=True)
class CapacityReport:
    database_size_bytes: int
    database_logical_size_bytes: int
    staging_size_bytes: int
    required_free_space_bytes: int
    database_free_space_bytes: int
    staging_free_space_bytes: int


@dataclass(frozen=True, slots=True)
class NamedPreflightProbe:
    name: str
    probe: PreflightProbe


@dataclass(frozen=True, slots=True)
class PreflightReport:
    completed: tuple[str, ...]


class _S3Client(Protocol):
    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...


class _Boto3(Protocol):
    def client(
        self,
        service_name: str,
        *,
        region_name: str,
        config: object,
    ) -> object: ...


class _RecoveryRoundTripStore(Protocol):
    def publish_probe(self, image: StagedRecoveryImage) -> PublishedRecoveryImage: ...

    def download_probe_verified(
        self,
        published: PublishedRecoveryImage,
        staging: RecoveryImageStagingArea,
    ) -> AbstractContextManager[VerifiedRecoveryImage]: ...


@final
class ImageDigestProbe:
    def __init__(self, *, actual_digest: str, expected_digest: str) -> None:
        self._actual_digest = actual_digest
        self._expected_digest = expected_digest

    def check(self) -> None:
        if not _is_image_digest(self._actual_digest):
            raise PreflightError("runtime image digest is invalid")
        if not _is_image_digest(self._expected_digest):
            raise PreflightError("expected image digest is invalid")
        if self._actual_digest != self._expected_digest:
            raise PreflightError("runtime image digest does not match")


@final
class NumericIdentityProbe:
    def __init__(self, *, expected_uid: int, expected_gid: int) -> None:
        if not _is_strict_integer(expected_uid) or expected_uid < 1:
            raise PreflightError("expected watcher UID is invalid")
        if not _is_strict_integer(expected_gid) or expected_gid < 1:
            raise PreflightError("expected watcher GID is invalid")
        self._expected_uid = expected_uid
        self._expected_gid = expected_gid

    def check(self) -> None:
        if os.geteuid() != self._expected_uid or os.getegid() != self._expected_gid:
            raise PreflightError("runtime numeric identity does not match")


@final
class ReadableSecretsProbe:
    def __init__(self, paths: tuple[Path, ...]) -> None:
        if len(paths) < 1 or len(paths) > _MAX_SECRET_FILES:
            raise PreflightError("secret file count is outside its bound")
        self._paths = paths

    def check(self) -> None:
        for path in self._paths:
            descriptor = -1
            try:
                descriptor = os.open(
                    path,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                )
                status = os.fstat(descriptor)
                if not stat.S_ISREG(status.st_mode) or status.st_size < 1:
                    raise PreflightError("runtime secret file is invalid")
                if not os.read(descriptor, 1):
                    raise PreflightError("runtime secret file is unreadable")
            except PreflightError:
                raise
            except OSError:
                raise PreflightError("runtime secret file is unreadable") from None
            finally:
                if descriptor >= 0:
                    os.close(descriptor)


@final
class ControlDatabaseCopyProbe:
    def __init__(self, database_path: Path, disposable_directory: Path) -> None:
        self._database_path = database_path
        self._disposable_directory = disposable_directory

    def check(self) -> None:
        descriptor = -1
        copy_path: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=".minos-preflight-",
                suffix=".sqlite3",
                dir=self._disposable_directory,
            )
            copy_path = Path(raw_path)
            os.close(descriptor)
            descriptor = -1
            if self._database_path.exists():
                copy_path.unlink()
                _ = copy_control_database(
                    self._database_path,
                    copy_path,
                    deadline_monotonic=(
                        time.monotonic() + _CONTROL_COPY_TIMEOUT_SECONDS
                    ),
                )
            with ControlStore.open(copy_path):
                verification = sqlite3.connect(copy_path, isolation_level=None)
                try:
                    journal_mode = cast(
                        tuple[str] | None,
                        verification.execute("PRAGMA journal_mode").fetchone(),
                    )
                    if journal_mode != ("wal",):
                        raise PreflightError("disposable control copy did not use WAL")
                    _ = verification.execute("BEGIN IMMEDIATE")
                    verification.rollback()
                finally:
                    verification.close()
        except PreflightError:
            raise
        except ControlSnapshotError, OSError, sqlite3.Error:
            raise PreflightError("disposable control database check failed") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if copy_path is not None:
                _remove_sqlite_files(copy_path)


def copy_control_database(
    source_path: Path,
    destination_path: Path,
    *,
    deadline_monotonic: float,
    expected_schema_version: int = CONTROL_SCHEMA_VERSION,
    maximum_size_bytes: int = _CONTROL_COPY_MAXIMUM_BYTES,
) -> int:
    _validate_control_copy_request(expected_schema_version, maximum_size_bytes)
    _create_control_evidence_file(source_path, destination_path)

    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    evidence_consistent = False
    primary_error: BaseException | None = None
    try:
        source = sqlite3.connect(
            f"{source_path.resolve().as_uri()}?mode=ro",
            uri=True,
            isolation_level=None,
        )
        destination = sqlite3.connect(destination_path, isolation_level=None)
        source.set_progress_handler(
            lambda: int(time.monotonic() >= deadline_monotonic),
            1000,
        )
        _backup_unmigrated_database(
            source,
            destination,
            deadline_monotonic=deadline_monotonic,
            maximum_size_bytes=maximum_size_bytes,
        )
        destination.set_progress_handler(
            lambda: int(time.monotonic() >= deadline_monotonic),
            1000,
        )
        integrity = destination.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise PreflightError("control database evidence integrity check failed")
        evidence_consistent = True
        schema_version = _read_control_schema_version(destination)
        if time.monotonic() >= deadline_monotonic:
            raise PreflightError("control database evidence deadline expired")
        if schema_version != expected_schema_version:
            raise PreflightError(
                "candidate control schema version differs from live database"
            )
        return schema_version
    except PreflightError as error:
        primary_error = error
        raise
    except sqlite3.Error as error:
        failure = PreflightError("control database evidence copy failed")
        primary_error = failure
        raise failure from error
    finally:
        if destination is not None:
            destination.set_progress_handler(None, 0)
            destination.close()
        if source is not None:
            source.set_progress_handler(None, 0)
            source.close()
        if not evidence_consistent:
            try:
                _remove_sqlite_files(destination_path)
            except PreflightError as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(
                    f"control evidence cleanup also failed: {cleanup_error!r}"
                )


def _validate_control_copy_request(
    expected_schema_version: int,
    maximum_size_bytes: int,
) -> None:
    if (
        not _is_strict_integer(expected_schema_version)
        or expected_schema_version < 1
        or expected_schema_version > CONTROL_SCHEMA_VERSION
    ):
        raise PreflightError("candidate control schema version is invalid")
    if (
        not _is_strict_integer(maximum_size_bytes)
        or maximum_size_bytes < 4096
        or maximum_size_bytes > _CONTROL_COPY_MAXIMUM_BYTES
    ):
        raise PreflightError("control copy byte ceiling is invalid")


def _create_control_evidence_file(
    source_path: Path,
    destination_path: Path,
) -> None:
    try:
        source_status = source_path.lstat()
        if not stat.S_ISREG(source_status.st_mode):
            raise PreflightError("live control database is not a regular file")
        descriptor = os.open(
            destination_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(descriptor)
    except PreflightError:
        raise
    except OSError:
        raise PreflightError("control database evidence file is unavailable") from None


def _backup_unmigrated_database(
    source: sqlite3.Connection,
    destination: sqlite3.Connection,
    *,
    deadline_monotonic: float,
    maximum_size_bytes: int = _CONTROL_COPY_MAXIMUM_BYTES,
) -> None:
    started_monotonic = time.monotonic()
    if (
        not math.isfinite(deadline_monotonic)
        or deadline_monotonic <= started_monotonic
        or deadline_monotonic - started_monotonic > _CONTROL_COPY_TIMEOUT_SECONDS
    ):
        raise PreflightError("disposable control copy deadline is invalid")
    quick_check = source.execute("PRAGMA quick_check(1)").fetchall()
    if quick_check != [("ok",)]:
        raise PreflightError("live control database quick check failed")
    page_size = _positive_pragma(source, "page_size")
    page_count = _positive_pragma(source, "page_count")
    if page_size * page_count > maximum_size_bytes:
        raise PreflightError("live control database exceeds the copy ceiling")
    maximum_callbacks = (
        maximum_size_bytes // _SQLITE_MINIMUM_PAGE_BYTES // _CONTROL_COPY_PAGES_PER_STEP
        + 2
    )
    callbacks = 0
    completed = False

    def validate_progress(status: int, remaining: int, total: int) -> None:
        nonlocal callbacks, completed
        callbacks += 1
        if callbacks > maximum_callbacks or time.monotonic() >= deadline_monotonic:
            raise PreflightError("disposable control copy exceeded its bound")
        if (
            not _is_strict_integer(status)
            or status
            not in (
                sqlite3.SQLITE_OK,
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
                sqlite3.SQLITE_DONE,
            )
            or not _is_strict_integer(remaining)
            or not _is_strict_integer(total)
            or remaining < 0
            or total < 0
            or remaining > total
            or page_size * total > maximum_size_bytes
        ):
            raise PreflightError("disposable control copy progress is invalid")
        if status == sqlite3.SQLITE_DONE:
            if remaining != 0:
                raise PreflightError("disposable control copy retained pages")
            completed = True

    source.backup(
        destination,
        pages=_CONTROL_COPY_PAGES_PER_STEP,
        progress=validate_progress,
        sleep=0.01,
    )
    if not completed or callbacks < 1 or time.monotonic() >= deadline_monotonic:
        raise PreflightError("disposable control copy did not complete")


def _read_control_schema_version(connection: sqlite3.Connection) -> int:
    user_version_row = cast(
        tuple[object] | None,
        connection.execute("PRAGMA user_version").fetchone(),
    )
    state_rows = cast(
        list[tuple[object]],
        connection.execute(
            "SELECT schema_version FROM control_state WHERE singleton_id = 1"
        ).fetchall(),
    )
    migration_rows = cast(
        list[tuple[object]],
        connection.execute(
            "SELECT version FROM control_schema_migrations ORDER BY version"
        ).fetchall(),
    )
    versions = tuple(row[0] for row in migration_rows)
    if (
        user_version_row is None
        or len(user_version_row) != 1
        or not _is_strict_integer(user_version_row[0])
        or len(state_rows) != 1
        or len(state_rows[0]) != 1
        or not _is_strict_integer(state_rows[0][0])
        or not versions
        or any(not _is_strict_integer(version) for version in versions)
    ):
        raise PreflightError("live control schema version is invalid")
    schema_version = user_version_row[0]
    if (
        schema_version < 1
        or schema_version > 1_000_000
        or state_rows[0][0] != schema_version
        or versions != tuple(range(1, schema_version + 1))
    ):
        raise PreflightError("live control schema versions are inconsistent")
    return schema_version


def _positive_pragma(connection: sqlite3.Connection, name: str) -> int:
    row = cast(tuple[object] | None, connection.execute(f"PRAGMA {name}").fetchone())
    if row is None or len(row) != 1 or not _is_strict_integer(row[0]) or row[0] < 1:
        raise PreflightError("live control database geometry is invalid")
    return row[0]


@final
class S3ChecksumProbe:
    def __init__(
        self,
        client: _S3Client,
        *,
        bucket: str,
        expected_bucket_owner: str,
    ) -> None:
        _validate_s3_identity(bucket, expected_bucket_owner)
        self._client = client
        self._bucket = bucket
        self._expected_bucket_owner = expected_bucket_owner

    @classmethod
    def create(
        cls,
        *,
        bucket: str,
        region: str,
        expected_bucket_owner: str,
    ) -> Self:
        if not region or len(region) > 64:
            raise PreflightError("S3 probe region is unsupported")
        boto3_api = cast(_Boto3, boto3)  # pyright: ignore[reportInvalidCast]
        try:
            client = cast(
                _S3Client,
                boto3_api.client(
                    "s3",
                    region_name=region,
                    config=Config(
                        connect_timeout=10,
                        read_timeout=60,
                        retries={"total_max_attempts": 1, "mode": "standard"},
                    ),
                ),
            )
        except BotoCoreError as error:
            raise PreflightError("S3 checksum probe client creation failed") from error
        return cls(
            client,
            bucket=bucket,
            expected_bucket_owner=expected_bucket_owner,
        )

    def check(self) -> None:
        payload = secrets.token_bytes(32)
        digest = hashlib.sha256(payload).digest()
        checksum = base64.b64encode(digest).decode("ascii")
        utc_date = datetime.now(UTC).date().isoformat()
        key = (
            f"probes/{utc_date}/{secrets.token_hex(16)}/"
            f"{hashlib.sha256(payload).hexdigest()}"
        )
        try:
            _ = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=payload,
                ContentLength=len(payload),
                ChecksumAlgorithm="SHA256",
                ChecksumSHA256=checksum,
                IfNoneMatch="*",
                ExpectedBucketOwner=self._expected_bucket_owner,
            )
            response = self._client.head_object(
                Bucket=self._bucket,
                Key=key,
                ChecksumMode="ENABLED",
                ExpectedBucketOwner=self._expected_bucket_owner,
            )
        except BotoCoreError, ClientError:
            raise PreflightError("S3 checksum probe failed") from None
        if (
            response.get("ContentLength") != len(payload)
            or response.get("ChecksumSHA256") != checksum
            or response.get("ChecksumType") != "FULL_OBJECT"
        ):
            raise PreflightError("S3 checksum probe verification failed")


@final
class RecoveryRoundTripProbe:
    def __init__(
        self,
        disposable_directory: Path,
        staging: RecoveryImageStagingArea,
        recovery_store: _RecoveryRoundTripStore,
    ) -> None:
        self._disposable_directory = disposable_directory
        self._staging = staging
        self._recovery_store = recovery_store

    def check(self) -> None:
        descriptor = -1
        database_path: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=".minos-recovery-probe-",
                suffix=".sqlite3",
                dir=self._disposable_directory,
            )
            database_path = Path(raw_path)
            os.close(descriptor)
            descriptor = -1
            with ControlStore.open(database_path) as store:
                request = RecoveryImageRequest(
                    "daily",
                    time.time_ns() // 1_000_000,
                    None,
                    None,
                )
                with self._staging.create(store, request) as image:
                    published = self._recovery_store.publish_probe(image)
            with self._recovery_store.download_probe_verified(
                published,
                self._staging,
            ) as verified:
                if verified.candidate.metadata != published.metadata:
                    raise PreflightError("recovery round trip metadata changed")
        except PreflightError:
            raise
        except ControlSnapshotError, OSError, sqlite3.Error:
            raise PreflightError("recovery round trip failed") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if database_path is not None:
                _remove_sqlite_files(database_path)


@final
class RuntimeCapacityGate:
    def __init__(
        self,
        database_path: str | Path,
        staging_directory: str | Path,
        *,
        limits: CapacityLimits | None = None,
        disk_usage: Callable[[Path], DiskUsage] | None = None,
        allow_missing_database: bool = False,
    ) -> None:
        self._database_path = Path(database_path)
        self._staging_directory = Path(staging_directory)
        self._limits = limits or CapacityLimits()
        self._disk_usage = disk_usage or _read_system_disk_usage
        self._allow_missing_database = allow_missing_database

    def require_capacity(self) -> None:
        _ = self.check()

    def check(self) -> CapacityReport:
        try:
            _ = self._database_path.lstat()
        except FileNotFoundError:
            if not self._allow_missing_database:
                raise PreflightError("control database is unavailable") from None
            database_size_bytes = 0
            database_logical_size_bytes = 0
        except OSError:
            raise PreflightError("control database is unavailable") from None
        else:
            database_size_bytes = _sqlite_physical_size(self._database_path)
            if database_size_bytes >= self._limits.maximum_database_size_bytes:
                raise PreflightError("control database reached its configured ceiling")
            database_logical_size_bytes = _sqlite_logical_size(self._database_path)
        effective_database_size_bytes = max(
            database_size_bytes,
            database_logical_size_bytes,
        )
        if effective_database_size_bytes >= self._limits.maximum_database_size_bytes:
            raise PreflightError("control database reached its configured ceiling")
        staging_size_bytes = _bounded_directory_size(self._staging_directory)
        if (
            staging_size_bytes + self._limits.maximum_download_size_bytes
            > self._limits.staging_quota_bytes
        ):
            raise PreflightError("download staging quota would be exceeded")
        required_free_space_bytes = max(
            self._limits.minimum_free_space_bytes,
            3 * effective_database_size_bytes
            + self._limits.maximum_download_size_bytes,
        )
        database_usage = self._read_disk_usage(self._database_path.parent)
        staging_usage = self._read_disk_usage(self._staging_directory)
        if database_usage.free < required_free_space_bytes:
            raise PreflightError("control volume free space is below its floor")
        if staging_usage.free < required_free_space_bytes:
            raise PreflightError("staging volume free space is below its floor")
        return CapacityReport(
            database_size_bytes,
            database_logical_size_bytes,
            staging_size_bytes,
            required_free_space_bytes,
            database_usage.free,
            staging_usage.free,
        )

    def _read_disk_usage(self, path: Path) -> DiskUsage:
        try:
            usage = self._disk_usage(path)
        except OSError:
            raise PreflightError("volume free space could not be read") from None
        if any(not _is_strict_integer(value) or value < 0 for value in usage):
            raise PreflightError("volume free space result is invalid")
        if usage.free > usage.total or usage.used > usage.total:
            raise PreflightError("volume free space result is inconsistent")
        return DiskUsage(*usage)


@final
class PausedPreflight:
    def __init__(self, probes: tuple[NamedPreflightProbe, ...]) -> None:
        if len(probes) < 1 or len(probes) > _MAX_PREFLIGHT_PROBES:
            raise PreflightError("preflight probe count is outside its bound")
        names = [probe.name for probe in probes]
        if any(len(name) < 1 or len(name) > 64 for name in names):
            raise PreflightError("preflight probe name is invalid")
        if len(names) != len(set(names)):
            raise PreflightError("preflight probe names must be unique")
        self._probes = probes

    @classmethod
    def production(
        cls,
        *,
        capacity: PreflightProbe,
        image_digest: PreflightProbe,
        numeric_identity: PreflightProbe,
        secrets: PreflightProbe,
        control_database: PreflightProbe,
        s3_checksum: PreflightProbe,
        recovery_round_trip: PreflightProbe,
        healthchecks: PreflightProbe,
    ) -> PausedPreflight:
        probes = (
            NamedPreflightProbe("capacity", capacity),
            NamedPreflightProbe("image_digest", image_digest),
            NamedPreflightProbe("numeric_identity", numeric_identity),
            NamedPreflightProbe("secrets", secrets),
            NamedPreflightProbe("control_database", control_database),
            NamedPreflightProbe("s3_checksum", s3_checksum),
            NamedPreflightProbe("recovery_round_trip", recovery_round_trip),
            NamedPreflightProbe("healthchecks", healthchecks),
        )
        if tuple(probe.name for probe in probes) != _PRODUCTION_PROBE_NAMES:
            raise PreflightError("production preflight probe set is incomplete")
        return cls(probes)

    def run(self, *, scheduling_paused: bool) -> PreflightReport:
        if scheduling_paused is not True:
            raise PreflightError("preflight requires paused scheduling")
        completed: list[str] = []
        for named_probe in self._probes:
            try:
                _ = named_probe.probe.check()
            except PreflightError:
                raise
            except Exception:
                raise PreflightError("preflight probe failed") from None
            completed.append(named_probe.name)
        return PreflightReport(tuple(completed))


def _regular_file_size(path: Path, label: str) -> int:
    try:
        status = path.lstat()
    except OSError:
        raise PreflightError(f"{label} is unavailable") from None
    if not stat.S_ISREG(status.st_mode):
        raise PreflightError(f"{label} is not a regular file")
    if status.st_size < 0:
        raise PreflightError(f"{label} size is invalid")
    return status.st_size


def _sqlite_physical_size(database_path: Path) -> int:
    total_bytes = _regular_file_size(database_path, "control database")
    for suffix in ("-wal", "-shm"):
        companion = Path(f"{database_path}{suffix}")
        try:
            _ = companion.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise PreflightError("control database companion is unavailable") from None
        total_bytes += _regular_file_size(companion, "control database companion")
    return total_bytes


def _sqlite_logical_size(database_path: Path) -> int:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro",
            uri=True,
            isolation_level=None,
        )
        page_size = cast(
            tuple[int] | None,
            connection.execute("PRAGMA page_size").fetchone(),
        )
        page_count = cast(
            tuple[int] | None,
            connection.execute("PRAGMA page_count").fetchone(),
        )
    except OSError, sqlite3.Error:
        raise PreflightError("control database logical size is unavailable") from None
    finally:
        if connection is not None:
            connection.close()
    if (
        page_size is None
        or page_count is None
        or not _is_strict_integer(page_size[0])
        or not _is_strict_integer(page_count[0])
        or page_size[0] < 512
        or page_count[0] < 1
    ):
        raise PreflightError("control database logical size is invalid")
    return page_size[0] * page_count[0]


def _bounded_directory_size(directory: Path) -> int:
    total_bytes = 0
    entries_seen = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                entries_seen += 1
                if entries_seen > _MAX_STAGING_ENTRIES:
                    raise PreflightError("staging entry count exceeds its bound")
                status = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(status.st_mode):
                    raise PreflightError("staging contains a non-regular entry")
                if status.st_size < 0:
                    raise PreflightError("staging entry size is invalid")
                total_bytes += status.st_size
    except PreflightError:
        raise
    except OSError:
        raise PreflightError("staging directory is unavailable") from None
    return total_bytes


def _is_strict_integer(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _read_system_disk_usage(path: Path) -> DiskUsage:
    usage = shutil.disk_usage(path)
    return DiskUsage(usage.total, usage.used, usage.free)


def _validate_s3_identity(bucket: str, expected_bucket_owner: str) -> None:
    if (
        _S3_BUCKET.fullmatch(bucket) is None
        or ".." in bucket
        or ".-" in bucket
        or "-." in bucket
    ):
        raise PreflightError("S3 probe bucket is unsupported")
    if _AWS_ACCOUNT_ID.fullmatch(expected_bucket_owner) is None:
        raise PreflightError("S3 probe expected owner is unsupported")


def _is_image_digest(value: str) -> bool:
    return (
        len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _remove_sqlite_files(database_path: Path) -> None:
    failures: list[OSError] = []
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)
        except OSError as error:
            failures.append(error)
    if failures:
        raise PreflightError("disposable control database cleanup failed") from None


# pyright: reportMissingTypeStubs=false
