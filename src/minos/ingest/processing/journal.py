"""Local processing evidence journal, never a release or activation authority.

Production placement, recovery and off-host access are separate review gates.
The coordinator must reverify source selections/gates before recording completion.
"""

import hashlib
import os
import sqlite3
import stat
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Self, cast, final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from minos.ingest.manifest_types import Digest
from minos.ingest.processing.recovery_changes import ChangeBatch, Revision, Token
from minos.store.artifact import canonical_json

MAX_CANDIDATES = 4096
MAX_SELECTION_BYTES = 256 * 1024
MAX_ATTEMPTS = 32
APPLICATION_ID = 0x4D50524A
_JOURNAL_OWNER = object()
type TimestampMs = Annotated[int, Field(ge=1, le=253_402_300_799_999)]
type FailureCode = Literal[
    "source_mismatch",
    "parse_failed",
    "integrity_failed",
    "policy_review_required",
    "build_failed",
    "runtime_failed",
]


class JournalError(RuntimeError):
    """A fixed redacted failure; no SQLite details, paths or candidate raw text."""


class _Model(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )


class ProcessingSelection(_Model):
    schema_version: Literal[1]
    batch: ChangeBatch
    baseline_dataset_sha256: Digest
    source_profile_sha256: Digest

    @model_validator(mode="after")
    def require_work(self) -> Self:
        if not self.batch.changes:
            raise ValueError("empty windows do not form processing candidates")
        return self


class ProcessingCandidate(_Model):
    status: Literal["queued", "processing", "failed", "processed"]
    candidate_id: Digest
    selection: ProcessingSelection
    attempt: int = Field(ge=0, le=MAX_ATTEMPTS)
    worker: Token | None
    lease_until_ms: TimestampMs | None
    audit_sha256: Digest | None
    failure_code: FailureCode | None

    @model_validator(mode="after")
    def require_state_shape(self) -> Self:
        if (self.worker is None) != (self.lease_until_ms is None):
            raise ValueError("worker and lease identities disagree")
        if (self.status == "processed") != (self.audit_sha256 is not None):
            raise ValueError("processed state and audit identity disagree")
        if self.status == "queued" and self.worker is not None:
            raise ValueError("queued candidate cannot retain a lease")
        if self.status != "queued" and (self.worker is None or self.attempt == 0):
            raise ValueError("attempt state has no claim identity")
        if self.status == "failed" and self.failure_code is None:
            raise ValueError("failed candidate has no error code")
        return self


class ProcessingClaim(_Model):
    candidate_id: Digest
    attempt: int = Field(ge=1, le=MAX_ATTEMPTS)
    worker: Token
    lease_until_ms: TimestampMs


class ProcessingCheckpoint(_Model):
    status: Literal["processing_checkpoint_only"]
    source_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    through_state_revision: Revision
    candidate_id: Digest | None
    audit_sha256: Digest | None


class _Audit(_Model):
    sha256: Digest


_CHECKPOINT_SCHEMA = """CREATE TABLE processing_checkpoints (
    source_id TEXT PRIMARY KEY,
    through_state_revision INTEGER NOT NULL CHECK(through_state_revision >= 0),
    candidate_id TEXT,
    audit_sha256 TEXT,
    CHECK((candidate_id IS NULL) = (audit_sha256 IS NULL)),
    FOREIGN KEY(candidate_id) REFERENCES processing_candidates(candidate_id)
) STRICT"""
_CANDIDATE_SCHEMA = """CREATE TABLE processing_candidates (
    candidate_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES processing_checkpoints(source_id),
    selection_json TEXT NOT NULL CHECK(length(selection_json) BETWEEN 1 AND 262144),
    status TEXT NOT NULL CHECK(status IN ('queued','processing','failed','processed')),
    attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 32),
    worker TEXT,
    lease_until_ms INTEGER,
    audit_sha256 TEXT,
    failure_code TEXT,
    CHECK((worker IS NULL) = (lease_until_ms IS NULL)),
    CHECK((status = 'processed') = (audit_sha256 IS NOT NULL)),
    CHECK(status != 'processing' OR (worker IS NOT NULL AND attempt > 0)),
    CHECK(status != 'failed' OR failure_code IS NOT NULL)
) STRICT"""
_PENDING_SCHEMA = """CREATE UNIQUE INDEX one_pending_candidate_per_source
ON processing_candidates(source_id) WHERE status != 'processed'"""
_ATTEMPT_SCHEMA = """CREATE TABLE processing_attempts (
    candidate_id TEXT NOT NULL REFERENCES processing_candidates(candidate_id),
    attempt INTEGER NOT NULL CHECK(attempt BETWEEN 1 AND 32),
    worker TEXT NOT NULL,
    lease_until_ms INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('processing','expired','failed','processed')),
    failure_code TEXT,
    audit_sha256 TEXT,
    PRIMARY KEY(candidate_id,attempt)
) STRICT"""
_SCHEMA = (_CHECKPOINT_SCHEMA, _CANDIDATE_SCHEMA, _PENDING_SCHEMA, _ATTEMPT_SCHEMA)


@final
class ProcessingJournal:
    def __init__(
        self, connection: sqlite3.Connection, *, _owner: object | None = None
    ) -> None:
        if _owner is not _JOURNAL_OWNER:
            raise JournalError("processing journal connections must be owned by open")
        self._connection = connection

    @classmethod
    def open(cls, path: Path) -> Self:
        connection: sqlite3.Connection | None = None
        try:
            parent = path.parent.lstat()
            if (
                not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.getuid()
                or parent.st_mode & 0o022
            ):
                raise JournalError("processing journal parent is unsupported")
            # Only the local operator supplies this path, never a processing request.
            # Refuse existing non-private/non-regular files rather than modifying them.
            try:
                descriptor = os.open(
                    path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600
                )
            except FileExistsError:
                info = path.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_nlink != 1
                    or info.st_mode & 0o077
                    or info.st_size > 64 * 1024 * 1024
                ):
                    raise JournalError(
                        "processing journal file is unsupported"
                    ) from None
            else:
                os.close(descriptor)
            connection = sqlite3.connect(path, isolation_level=None, timeout=1)
            connection.row_factory = sqlite3.Row
            _ = connection.execute("PRAGMA foreign_keys = ON")
            _ = connection.execute("PRAGMA trusted_schema = OFF")
            _ = connection.execute("PRAGMA synchronous = FULL")
            _ = connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2 * 1024 * 1024)
            result = cls(connection, _owner=_JOURNAL_OWNER)
            result._initialize()
            _ = connection.execute("PRAGMA max_page_count = 16384")
            return result
        except OSError, sqlite3.Error, JournalError:
            failure = JournalError("processing journal could not be opened")
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    failure.add_note("processing journal cleanup also failed")
            raise failure from None

    def _initialize(self) -> None:
        with self._transaction():
            mode = cast(
                sqlite3.Row, self._connection.execute("PRAGMA journal_mode").fetchone()
            )
            if mode[0] != "delete":
                raise JournalError("processing journal mode is unsupported")
            rows = cast(
                list[sqlite3.Row],
                self._connection.execute(
                    "SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY name LIMIT 6"
                ).fetchall(),
            )
            version = self._integer("PRAGMA user_version")
            application = self._integer("PRAGMA application_id")
            if self._integer("PRAGMA page_size") != 4096:
                raise JournalError("processing journal page size is unsupported")
            if not rows and version == 0 and application == 0:
                for statement in _SCHEMA:
                    _ = self._connection.execute(statement)
                _ = self._connection.execute("PRAGMA user_version = 1")
                _ = self._connection.execute("PRAGMA application_id = 1297109578")
            elif (
                version != 1
                or application != APPLICATION_ID
                or {cast(str, row["sql"]) for row in rows} != set(_SCHEMA)
            ):
                raise JournalError("processing journal schema is unsupported")
            if (
                self._connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok"
                or self._connection.execute("PRAGMA foreign_key_check").fetchone()
                is not None
            ):
                raise JournalError("processing journal integrity is unsupported")

    def close(self) -> None:
        try:
            self._connection.close()
        except sqlite3.Error:
            raise JournalError("processing journal cleanup failed") from None

    @contextmanager
    def _transaction(self) -> Generator[None]:
        deadline = time.monotonic() + 30
        try:
            self._connection.set_progress_handler(
                lambda: int(time.monotonic() >= deadline), 1000
            )
            _ = self._connection.execute("BEGIN IMMEDIATE")
            yield
            if time.monotonic() >= deadline:
                raise JournalError("processing journal transaction deadline exceeded")
            _ = self._connection.execute("COMMIT")
        except BaseException:
            primary = sys.exception()
            try:
                if self._connection.in_transaction:
                    _ = self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                if primary is not None:
                    primary.add_note("processing journal rollback also failed")
            if isinstance(primary, (sqlite3.Error, ValidationError)):
                raise JournalError(
                    "processing journal transaction could not be verified"
                ) from None
            raise
        finally:
            primary = sys.exception()
            try:
                self._connection.set_progress_handler(None, 0)
            except sqlite3.Error:
                if primary is not None:
                    primary.add_note("processing journal progress cleanup also failed")
                else:
                    raise JournalError(
                        "processing journal progress cleanup failed"
                    ) from None

    def enqueue(self, selection: ProcessingSelection) -> ProcessingCandidate:
        encoded = canonical_json(selection.model_dump(mode="json"))
        if len(encoded) > MAX_SELECTION_BYTES:
            raise JournalError("processing selection exceeds its byte bound")
        candidate_id = hashlib.sha256(encoded).hexdigest()
        source_id = selection.batch.window.source_id
        with self._transaction():
            existing = self._candidate(candidate_id)
            if existing is not None:
                return existing
            checkpoint = self._checkpoint(source_id)
            if (
                checkpoint.through_state_revision
                != selection.batch.window.after_state_revision
            ):
                raise JournalError("processing window differs from checkpoint")
            pending = cast(
                sqlite3.Row | None,
                self._connection.execute(
                    "SELECT 1 FROM processing_candidates WHERE source_id=? AND status != 'processed' LIMIT 1",
                    (source_id,),
                ).fetchone(),
            )
            if pending is not None:
                raise JournalError("processing source has an unresolved candidate")
            count = self._integer("SELECT count(*) FROM processing_candidates")
            if count >= MAX_CANDIDATES:
                raise JournalError("processing journal candidate bound exceeded")
            _ = self._connection.execute(
                "INSERT OR IGNORE INTO processing_checkpoints VALUES (?,0,NULL,NULL)",
                (source_id,),
            )
            _ = self._connection.execute(
                "INSERT INTO processing_candidates VALUES (?,?,?,'queued',0,NULL,NULL,NULL,NULL)",
                (candidate_id, source_id, encoded.decode()),
            )
            return self._require_candidate(candidate_id)

    def pending(self, source_id: str) -> ProcessingCandidate | None:
        with self._transaction():
            _ = self._checkpoint(source_id)
            row = cast(
                sqlite3.Row | None,
                self._connection.execute(
                    "SELECT candidate_id FROM processing_candidates WHERE source_id=? AND status != 'processed' LIMIT 1",
                    (source_id,),
                ).fetchone(),
            )
            return (
                self._require_candidate(cast(str, row[0])) if row is not None else None
            )

    def checkpoint(self, source_id: str) -> ProcessingCheckpoint:
        with self._transaction():
            return self._checkpoint(source_id)

    def claim(
        self, candidate_id: str, *, worker: str, lease_ms: int
    ) -> ProcessingClaim:
        if type(lease_ms) is not int or not 1 <= lease_ms <= 300_000:
            raise JournalError("processing lease duration is unsupported")
        with self._transaction():
            now_ms = time.time_ns() // 1_000_000
            request = ProcessingClaim(
                candidate_id=candidate_id,
                worker=worker,
                attempt=1,
                lease_until_ms=now_ms + lease_ms,
            )
            candidate = self._require_candidate(request.candidate_id)
            if candidate.status not in ("queued", "processing"):
                raise JournalError("processing candidate is not claimable")
            if (
                candidate.lease_until_ms is not None
                and candidate.lease_until_ms > now_ms
            ):
                raise JournalError("processing candidate lease is already held")
            if candidate.attempt == MAX_ATTEMPTS:
                raise JournalError("processing candidate attempt bound exceeded")
            claim = request.model_copy(update={"attempt": candidate.attempt + 1})
            if candidate.status == "processing":
                _ = self._connection.execute(
                    "UPDATE processing_attempts SET status='expired' WHERE candidate_id=? AND attempt=?",
                    (candidate.candidate_id, candidate.attempt),
                )
            _ = self._connection.execute(
                "UPDATE processing_candidates SET status='processing',attempt=?,worker=?,lease_until_ms=?,failure_code=NULL WHERE candidate_id=?",
                (claim.attempt, claim.worker, claim.lease_until_ms, claim.candidate_id),
            )
            _ = self._connection.execute(
                "INSERT INTO processing_attempts VALUES (?,?,?,?,'processing',NULL,NULL)",
                (claim.candidate_id, claim.attempt, claim.worker, claim.lease_until_ms),
            )
            return claim

    def fail(self, claim: ProcessingClaim, code: FailureCode) -> None:
        # Validation also prevents a typed caller accidentally passing raw exceptions.
        with self._transaction():
            code = TypeAdapter[FailureCode](FailureCode).validate_python(
                code, strict=True
            )
            self._require_lease(claim)
            _ = self._connection.execute(
                "UPDATE processing_candidates SET status='failed',failure_code=? WHERE candidate_id=?",
                (code, claim.candidate_id),
            )
            _ = self._connection.execute(
                "UPDATE processing_attempts SET status='failed',failure_code=? WHERE candidate_id=? AND attempt=?",
                (code, claim.candidate_id, claim.attempt),
            )

    def renew(self, claim: ProcessingClaim, *, lease_ms: int) -> ProcessingClaim:
        if type(lease_ms) is not int or not 1 <= lease_ms <= 300_000:
            raise JournalError("processing lease duration is unsupported")
        with self._transaction():
            self._require_lease(claim)
            until_ms = time.time_ns() // 1_000_000 + lease_ms
            if until_ms <= claim.lease_until_ms:
                raise JournalError("processing renewal must extend its lease")
            renewed = ProcessingClaim(
                candidate_id=claim.candidate_id,
                attempt=claim.attempt,
                worker=claim.worker,
                lease_until_ms=until_ms,
            )
            _ = self._connection.execute(
                "UPDATE processing_candidates SET lease_until_ms=? WHERE candidate_id=?",
                (until_ms, claim.candidate_id),
            )
            _ = self._connection.execute(
                "UPDATE processing_attempts SET lease_until_ms=? WHERE candidate_id=? AND attempt=?",
                (until_ms, claim.candidate_id, claim.attempt),
            )
            return renewed

    def retry(self, candidate_id: str) -> None:
        with self._transaction():
            candidate = self._require_candidate(candidate_id)
            if candidate.status != "failed":
                raise JournalError("processing candidate is not failed")
            _ = self._connection.execute(
                "UPDATE processing_candidates SET status='queued',worker=NULL,lease_until_ms=NULL WHERE candidate_id=?",
                (candidate_id,),
            )

    def complete(
        self, claim: ProcessingClaim, *, audit_sha256: str
    ) -> ProcessingCheckpoint:
        with self._transaction():
            evidence = _Audit(sha256=audit_sha256)
            candidate = self._require_candidate(claim.candidate_id)
            if candidate.status == "processed":
                if (
                    candidate.attempt != claim.attempt
                    or candidate.worker != claim.worker
                    or candidate.lease_until_ms != claim.lease_until_ms
                    or candidate.audit_sha256 != evidence.sha256
                ):
                    raise JournalError(
                        "processing completion conflicts with recorded evidence"
                    )
                return self._completed_checkpoint(candidate)
            self._require_lease(claim)
            checkpoint = self._checkpoint(candidate.selection.batch.window.source_id)
            if (
                checkpoint.through_state_revision
                != candidate.selection.batch.window.after_state_revision
            ):
                raise JournalError("processing predecessor checkpoint changed")
            _ = self._connection.execute(
                "UPDATE processing_candidates SET status='processed',audit_sha256=? WHERE candidate_id=?",
                (evidence.sha256, claim.candidate_id),
            )
            _ = self._connection.execute(
                "UPDATE processing_attempts SET status='processed',audit_sha256=? WHERE candidate_id=? AND attempt=?",
                (evidence.sha256, claim.candidate_id, claim.attempt),
            )
            checkpoint = self._completed_checkpoint(
                self._require_candidate(claim.candidate_id)
            )
            _ = self._connection.execute(
                "UPDATE processing_checkpoints SET through_state_revision=?,candidate_id=?,audit_sha256=? WHERE source_id=?",
                (
                    checkpoint.through_state_revision,
                    checkpoint.candidate_id,
                    checkpoint.audit_sha256,
                    checkpoint.source_id,
                ),
            )
            return checkpoint

    def require_claim(self, claim: ProcessingClaim) -> ProcessingCandidate:
        """Recheck a processing fence before effects; this never authorizes activation."""
        with self._transaction():
            self._require_lease(claim)
            return self._require_candidate(claim.candidate_id)

    def _require_lease(self, claim: ProcessingClaim) -> None:
        candidate = self._require_candidate(claim.candidate_id)
        if (
            candidate.status != "processing"
            or candidate.attempt != claim.attempt
            or candidate.worker != claim.worker
            or candidate.lease_until_ms != claim.lease_until_ms
            or claim.lease_until_ms <= time.time_ns() // 1_000_000
        ):
            raise JournalError("processing claim is stale")

    def _candidate(self, candidate_id: str) -> ProcessingCandidate | None:
        row = cast(
            sqlite3.Row | None,
            self._connection.execute(
                "SELECT * FROM processing_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone(),
        )
        if row is None:
            return None
        values = cast(dict[str, object], dict(row))
        selection = ProcessingSelection.model_validate_json(
            cast(str, values.pop("selection_json"))
        )
        encoded = canonical_json(selection.model_dump(mode="json"))
        if (
            hashlib.sha256(encoded).hexdigest() != values["candidate_id"]
            or values.pop("source_id") != selection.batch.window.source_id
        ):
            raise JournalError(
                "processing candidate identity differs from stored selection"
            )
        candidate = ProcessingCandidate.model_validate(
            {**values, "selection": selection}
        )
        if candidate.attempt > 0:
            last = cast(
                sqlite3.Row | None,
                self._connection.execute(
                    "SELECT * FROM processing_attempts WHERE candidate_id=? AND attempt=?",
                    (candidate.candidate_id, candidate.attempt),
                ).fetchone(),
            )
            expected_status = (
                "failed" if candidate.status == "queued" else candidate.status
            )
            if (
                last is None
                or last["status"] != expected_status
                or last["audit_sha256"] != candidate.audit_sha256
                or last["failure_code"] != candidate.failure_code
            ):
                raise JournalError(
                    "processing candidate differs from its attempt record"
                )
            if candidate.status != "queued" and (
                last["worker"] != candidate.worker
                or last["lease_until_ms"] != candidate.lease_until_ms
            ):
                raise JournalError(
                    "processing candidate claim differs from its attempt record"
                )
        return candidate

    def _require_candidate(self, candidate_id: str) -> ProcessingCandidate:
        result = self._candidate(candidate_id)
        if result is None:
            raise JournalError("processing candidate is absent")
        return result

    def _integer(self, statement: str) -> int:
        row = cast(sqlite3.Row | None, self._connection.execute(statement).fetchone())
        value: object = row[0] if row is not None else None
        if not isinstance(value, int):
            raise JournalError("processing journal numeric identity is unsupported")
        return value

    def _checkpoint(self, source_id: str) -> ProcessingCheckpoint:
        row = cast(
            sqlite3.Row | None,
            self._connection.execute(
                "SELECT * FROM processing_checkpoints WHERE source_id=?", (source_id,)
            ).fetchone(),
        )
        values: dict[str, object] = (
            cast(dict[str, object], dict(row))
            if row is not None
            else {
                "source_id": source_id,
                "through_state_revision": 0,
                "candidate_id": None,
                "audit_sha256": None,
            }
        )
        result = ProcessingCheckpoint.model_validate(
            {"status": "processing_checkpoint_only", **values}
        )
        if result.candidate_id is not None:
            candidate = self._require_candidate(result.candidate_id)
            if (
                candidate.status != "processed"
                or candidate.selection.batch.window.source_id != source_id
                or candidate.selection.batch.changes[-1].state_revision
                != result.through_state_revision
                or candidate.audit_sha256 != result.audit_sha256
            ):
                raise JournalError(
                    "processing checkpoint differs from completed evidence"
                )
        elif result.through_state_revision != 0 or result.audit_sha256 is not None:
            raise JournalError("processing checkpoint has no completed evidence")
        return result

    @staticmethod
    def _completed_checkpoint(candidate: ProcessingCandidate) -> ProcessingCheckpoint:
        return ProcessingCheckpoint(
            status="processing_checkpoint_only",
            source_id=candidate.selection.batch.window.source_id,
            through_state_revision=candidate.selection.batch.changes[-1].state_revision,
            candidate_id=candidate.candidate_id,
            audit_sha256=candidate.audit_sha256,
        )
