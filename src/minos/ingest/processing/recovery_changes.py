"""Bounded read-only identities from a transport-verified recovery image lease."""

import hashlib
import os
import sqlite3
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated, ClassVar, Self, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from minos.ingest.manifest_types import Digest
from minos.ingest.watchers.recovery import (
    RecoveryError,
    VerifiedRecoveryImage,
    parse_recovery_key,
    validate_recovery_image,
)

MAX_CHANGE_BATCH = 128
READ_TIMEOUT_SECONDS = 300
type Revision = Annotated[int, Field(ge=0, le=2**63 - 1)]
type Token = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]


class ProcessingReadError(RuntimeError):
    """A fixed privacy-safe failure, never raw SQLite or filesystem diagnostics."""


class _ProcessingModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )


class RecoveryIdentity(_ProcessingModel):
    key: str = Field(min_length=1, max_length=1024)
    version_id: str = Field(pattern=r"^[!-~]{1,1024}$")
    sha256: Digest
    size_bytes: int = Field(ge=4096, le=2 * 1024 * 1024 * 1024)
    state_revision: Revision
    schema_version: int = Field(ge=1, le=2**31 - 1)
    migration_set_sha256: Digest

    @model_validator(mode="after")
    def require_pinned_key(self) -> Self:
        try:
            parsed = parse_recovery_key(self.key)
        except RecoveryError:
            raise ValueError("recovery key is unsupported") from None
        if self.version_id == "null":
            raise ValueError("unversioned recovery image is unsupported")
        if parsed.sha256 != self.sha256 or parsed.state_revision != self.state_revision:
            raise ValueError("recovery key differs from image identity")
        return self


class ChangeWindow(_ProcessingModel):
    source_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    after_state_revision: Revision


class ChangeIdentity(_ProcessingModel):
    source_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    watcher_type: Token
    change_id: Token
    run_id: Token
    observation_id: Token
    change_category: Token
    state_revision: int = Field(ge=1, le=2**63 - 1)
    configuration_revision: int = Field(ge=1, le=2**63 - 1)
    configuration_sha256: Digest
    observation_kind: Token
    observation_sha256: Digest
    observation_size_bytes: int = Field(ge=0, le=2**63 - 1)
    source_identity_sha256: Digest
    observation_metadata_sha256: Digest
    change_summary_sha256: Digest


class ChangeBatch(_ProcessingModel):
    recovery: RecoveryIdentity
    window: ChangeWindow
    changes: Annotated[tuple[ChangeIdentity, ...], Field(max_length=MAX_CHANGE_BATCH)]

    @model_validator(mode="after")
    def require_complete_window(self) -> Self:
        if self.window.after_state_revision > self.recovery.state_revision:
            raise ValueError("checkpoint exceeds captured revision")
        identities = tuple(
            (item.state_revision, item.change_id) for item in self.changes
        )
        if identities != tuple(sorted(set(identities))):
            raise ValueError("change order or uniqueness differs")
        if len({item.change_id for item in self.changes}) != len(self.changes):
            raise ValueError("duplicate change identity")
        for item in self.changes:
            if item.source_id != self.window.source_id:
                raise ValueError("change source differs from requested window")
            if (
                not self.window.after_state_revision
                < item.state_revision
                <= self.recovery.state_revision
            ):
                raise ValueError("change is outside the captured window")
        return self


def read_change_batch(
    image: VerifiedRecoveryImage, window: ChangeWindow
) -> ChangeBatch:
    """Call inside the transport's download lease; no cursor is advanced here."""
    with _verified_connection(image) as (connection, pin):
        if window.after_state_revision > pin.state_revision:
            raise ProcessingReadError(
                "processing checkpoint is ahead of recovery image"
            )
        if (
            connection.execute(
                "SELECT 1 FROM sources WHERE source_id = ?", (window.source_id,)
            ).fetchone()
            is None
        ):
            raise ProcessingReadError("processing source is absent from recovery image")
        # Limit narrow identities before joining private text. No private payload
        # participates in a sort, and only one row of private text is held at once.
        cursor = connection.execute(
            _BATCH_QUERY,
            (window.source_id, window.after_state_revision, MAX_CHANGE_BATCH + 1),
        )
        changes: list[ChangeIdentity] = []
        for _ in range(MAX_CHANGE_BATCH + 1):
            row = cast(sqlite3.Row | None, cursor.fetchone())
            if row is None:
                break
            if len(changes) == MAX_CHANGE_BATCH:
                # Never return a partial revision a downstream cursor could skip.
                raise ProcessingReadError("processing change batch exceeds its bound")
            changes.append(_change_identity(row, pin))
        changes.sort(key=lambda item: (item.state_revision, item.change_id))
        return ChangeBatch(recovery=pin, window=window, changes=tuple(changes))


def require_selected_change(
    image: VerifiedRecoveryImage,
    expected_recovery: RecoveryIdentity,
    expected_change: ChangeIdentity,
) -> ChangeIdentity:
    """Re-select every pinned identity, not merely a change ID or mutable latest."""
    with _verified_connection(image) as (connection, pin):
        if pin != expected_recovery:
            raise ProcessingReadError("selected recovery identity changed")
        row = cast(
            sqlite3.Row | None,
            connection.execute(
                _SINGLE_QUERY,
                (expected_change.change_id,),
            ).fetchone(),
        )
        if row is None:
            raise ProcessingReadError("selected change is absent from recovery image")
        actual = _change_identity(row, pin)
        if actual != expected_change:
            raise ProcessingReadError("selected change identity changed")
        return actual


_CHANGE_SELECT = """
SELECT c.source_id, s.watcher_type, c.change_id, c.run_id, c.observation_id,
       c.change_category, c.state_revision, r.configuration_revision,
       conf.configuration_sha256, conf.configuration_json,
       o.observation_kind, o.response_sha256 AS observation_sha256,
       b.size_bytes AS observation_size_bytes,
       o.source_identity, o.metadata_json, c.summary_json,
       o.state_revision AS observation_state_revision,
       r.committed_state_revision, r.status
FROM selected_changes AS selected
JOIN source_changes AS c ON c.change_id = selected.change_id
LEFT JOIN sources AS s ON s.source_id = c.source_id
LEFT JOIN watcher_runs AS r ON r.run_id = c.run_id AND r.source_id = c.source_id
LEFT JOIN source_configuration_revisions AS conf
  ON conf.source_id = r.source_id
 AND conf.configuration_revision = r.configuration_revision
LEFT JOIN observations AS o ON o.observation_id = c.observation_id
 AND o.run_id = c.run_id AND o.source_id = c.source_id
LEFT JOIN blobs AS b ON b.sha256 = o.response_sha256
"""

_BATCH_PREFIX = """WITH selected_changes AS MATERIALIZED (
    SELECT change_id FROM source_changes
    WHERE source_id = ? AND state_revision > ?
    ORDER BY state_revision, change_id LIMIT ?
)"""
_SINGLE_PREFIX = """WITH selected_changes AS MATERIALIZED (
    SELECT change_id FROM source_changes WHERE change_id = ?
)"""
# Compose fixed SQL once. All runtime inputs use parameters, never SQL text.
_BATCH_QUERY = _BATCH_PREFIX + _CHANGE_SELECT
_SINGLE_QUERY = _SINGLE_PREFIX + _CHANGE_SELECT


def _change_identity(row: sqlite3.Row, pin: RecoveryIdentity) -> ChangeIdentity:
    values = cast(dict[str, object], dict(row))
    revision = values["state_revision"]
    if (
        not isinstance(revision, int)
        or not 0 < revision <= pin.state_revision
        or values["committed_state_revision"] != revision
        or values["status"] not in ("committed", "heartbeat_pending", "successful")
    ):
        raise ProcessingReadError("change does not belong to a captured run revision")
    if values["observation_state_revision"] != revision:
        raise ProcessingReadError("change and observation revisions disagree")
    configuration = _text_digest(values.pop("configuration_json"))
    if configuration != values["configuration_sha256"]:
        raise ProcessingReadError("captured configuration checksum differs")
    for source, target in (
        ("source_identity", "source_identity_sha256"),
        ("metadata_json", "observation_metadata_sha256"),
        ("summary_json", "change_summary_sha256"),
    ):
        values[target] = _text_digest(values.pop(source))
    _ = values.pop("observation_state_revision")
    _ = values.pop("committed_state_revision")
    _ = values.pop("status")
    return ChangeIdentity.model_validate(values)


def _text_digest(value: object) -> str:
    if not isinstance(value, str):
        raise ProcessingReadError("captured identity text is invalid")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def _verified_connection(
    image: VerifiedRecoveryImage,
) -> Generator[tuple[sqlite3.Connection, RecoveryIdentity]]:
    descriptor = -1
    connection: sqlite3.Connection | None = None
    deadline = time.monotonic() + READ_TIMEOUT_SECONDS

    def check_deadline() -> None:
        if time.monotonic() >= deadline:
            raise ProcessingReadError("processing recovery read deadline exceeded")

    try:
        candidate = image.candidate
        pin = RecoveryIdentity(
            key=candidate.key,
            version_id=candidate.version_id,
            sha256=candidate.sha256,
            size_bytes=candidate.size_bytes,
            state_revision=candidate.metadata.state_revision,
            schema_version=candidate.metadata.schema_version,
            migration_set_sha256=candidate.metadata.migration_set_sha256,
        )
        descriptor = os.open(
            image.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        )
        before = os.fstat(descriptor)
        metadata = validate_recovery_image(
            image.path,
            expected_key=pin.key,
            expected_sha256=pin.sha256,
            expected_size_bytes=pin.size_bytes,
            progress_check=check_deadline,
            _descriptor=descriptor,
        )
        if metadata != candidate.metadata:
            raise ProcessingReadError("recovery metadata differs from transport pin")
        uri = f"file:/proc/self/fd/{descriptor}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=0)
        connection.row_factory = sqlite3.Row
        _ = connection.execute("PRAGMA query_only = ON")
        _ = connection.execute("PRAGMA trusted_schema = OFF")
        _ = connection.execute("PRAGMA temp_store = MEMORY")
        _ = connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2 * 1024 * 1024)
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        check_deadline()
        yield connection, pin
        check_deadline()
        after = os.fstat(descriptor)
        path_status = image.path.lstat()
        if _file_identity(before) != _file_identity(after) or (
            after.st_dev,
            after.st_ino,
        ) != (path_status.st_dev, path_status.st_ino):
            raise ProcessingReadError("recovery image changed during processing read")
    except OSError, sqlite3.Error, ValidationError, RecoveryError:
        check_deadline()
        raise ProcessingReadError(
            "processing recovery input could not be verified"
        ) from None
    finally:
        _close_input(connection, descriptor)


def _file_identity(status: os.stat_result) -> tuple[int, ...]:
    return (
        status.st_dev,
        status.st_ino,
        status.st_mode,
        status.st_uid,
        status.st_nlink,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _close_input(connection: sqlite3.Connection | None, descriptor: int) -> None:
    primary = sys.exception()
    failed = False
    try:
        if connection is not None:
            connection.close()
    except sqlite3.Error:
        failed = True
    try:
        if descriptor >= 0:
            os.close(descriptor)
    except OSError:
        failed = True
    if failed:
        if primary is not None:
            primary.add_note("processing recovery cleanup also failed")
        else:
            raise ProcessingReadError("processing recovery cleanup failed") from None
