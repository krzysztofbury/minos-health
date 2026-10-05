"""Source-specific product identities from pinned RSK recovery changes, not activation."""

import hashlib
import sqlite3
from typing import Annotated, ClassVar, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from minos.ingest.manifest_types import Digest
from minos.ingest.processing.recovery_changes import (
    ChangeIdentity,
    ProcessingReadError,
    RecoveryIdentity,
    Token,
    read_connection_change,
    select_connection_change,
    verified_recovery_connection,
)
from minos.ingest.watchers.recovery import VerifiedRecoveryImage
from minos.ingest.watchers.rsk.public_models import (
    RskPublicObservation,
    Timestamp,
    Uuid,
)

type Hcds = Annotated[
    tuple[Annotated[str, Field(pattern=r"^[0-9A-Z]{6}$")], ...], Field(max_length=128)
]


class _Model(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )


class RskChangeSummary(_Model):
    added_hcds: Hcds
    removed_hcds: Hcds
    catalog_changed: bool
    metadata_changed: Hcds
    file_sets_changed: Hcds
    current_product_sha256: Digest
    previous_product_sha256: Digest | None
    current_state_sha256: Digest
    previous_state_sha256: Digest | None

    @model_validator(mode="after")
    def require_consistent_summary(self) -> Self:
        for values in (
            self.added_hcds,
            self.removed_hcds,
            self.metadata_changed,
            self.file_sets_changed,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("RSK summary resources are not sorted and unique")
        if (self.previous_product_sha256 is None) != (
            self.previous_state_sha256 is None
        ):
            raise ValueError("RSK summary baseline identities disagree")
        if self.previous_state_sha256 == self.current_state_sha256:
            raise ValueError("RSK change has no source state transition")
        return self


class RskProductIdentity(_Model):
    hcd: Literal["00CD10"]
    file_uuid: Uuid
    file_created_at: Timestamp
    resource_version: str = Field(min_length=1, max_length=512)
    sha256: Digest
    size_bytes: int = Field(ge=1, le=64 * 1024 * 1024)
    metadata_observation_id: Token
    metadata_sha256: Digest
    metadata_size_bytes: int = Field(ge=1, le=64 * 1024 * 1024)
    product_observation_id: Token
    product_state_revision: int = Field(ge=1, le=2**63 - 1)


class RskProductSelection(_Model):
    status: Literal["rsk_product_identities_only"]
    recovery: RecoveryIdentity
    change: ChangeIdentity
    summary: RskChangeSummary
    previous_change_id: Token | None
    before: RskProductIdentity | None
    after: RskProductIdentity

    @model_validator(mode="after")
    def require_product_transition(self) -> Self:
        if self.after.sha256 != self.summary.current_product_sha256:
            raise ValueError("RSK after product differs from summary")
        if (self.before is None) != (self.previous_change_id is None):
            raise ValueError("RSK previous change and product disagree")
        previous = self.before.sha256 if self.before is not None else None
        if previous != self.summary.previous_product_sha256:
            raise ValueError("RSK before product differs from summary")
        if self.after.product_state_revision > self.change.state_revision:
            raise ValueError("RSK after product is newer than selected change")
        if (
            self.before is not None
            and self.before.product_state_revision >= self.change.state_revision
        ):
            raise ValueError("RSK before product is not an older captured identity")
        return self


def read_rsk_product_selection(
    image: VerifiedRecoveryImage,
    expected_recovery: RecoveryIdentity,
    expected_change: ChangeIdentity,
) -> RskProductSelection:
    """Re-select catalog, predecessor, metadata and retained exact product identities.

    Blob versions are not stored in watcher control state. A downstream archive
    resolver must pin and verify S3 versions before proposal/build. No latest
    cursor, source fetch, applicability decision or processing checkpoint is used.
    """
    with verified_recovery_connection(image) as (connection, pin):
        try:
            if pin != expected_recovery:
                raise ProcessingReadError("selected recovery identity changed")
            change = select_connection_change(connection, pin, expected_change)
            _require_rsk_change(change)
            row = _one_change(connection, change.source_id, change.state_revision)
            summary = RskChangeSummary.model_validate_json(
                cast(str, row["summary_json"])
            )
            previous = _previous_change(connection, change)
            before = None
            if previous is None:
                if (
                    change.change_category != "rsk_baseline"
                    or summary.previous_state_sha256 is not None
                ):
                    raise ProcessingReadError("RSK predecessor is absent")
            else:
                prior_identity = read_connection_change(
                    connection, pin, cast(str, previous["change_id"])
                )
                _require_rsk_change(prior_identity)
                prior_summary = RskChangeSummary.model_validate_json(
                    cast(str, previous["summary_json"])
                )
                if (
                    change.change_category != "rsk_source_change"
                    or prior_summary.current_state_sha256
                    != summary.previous_state_sha256
                    or prior_summary.current_product_sha256
                    != summary.previous_product_sha256
                ):
                    raise ProcessingReadError("RSK predecessor differs from summary")
                before = _product(
                    connection, previous, prior_summary.current_product_sha256
                )
            after = _product(connection, row, summary.current_product_sha256)
            return RskProductSelection(
                status="rsk_product_identities_only",
                recovery=pin,
                change=change,
                summary=summary,
                previous_change_id=cast(str, previous["change_id"])
                if previous is not None
                else None,
                before=before,
                after=after,
            )
        except ValidationError:
            raise ProcessingReadError("RSK product evidence is unsupported") from None


def _require_rsk_change(change: ChangeIdentity) -> None:
    if (
        change.watcher_type != "rsk"
        or change.change_category not in ("rsk_baseline", "rsk_source_change")
        or change.observation_kind != "rsk_catalog"
        or change.source_identity_sha256 != hashlib.sha256(b"UZYNLG").hexdigest()
    ):
        raise ProcessingReadError(
            "selected change is not a supported RSK catalog change"
        )


_CHANGE_QUERY = """SELECT c.change_id, c.source_id, c.run_id, c.state_revision,
    c.change_category, c.summary_json, r.status, r.committed_state_revision
FROM source_changes AS c
LEFT JOIN watcher_runs AS r ON r.run_id=c.run_id AND r.source_id=c.source_id
WHERE c.source_id=? AND c.state_revision=? ORDER BY c.change_id LIMIT 2"""


def _one_change(
    connection: sqlite3.Connection, source_id: str, revision: int
) -> sqlite3.Row:
    rows = cast(
        list[sqlite3.Row],
        connection.execute(_CHANGE_QUERY, (source_id, revision)).fetchall(),
    )
    if len(rows) != 1:
        raise ProcessingReadError("RSK captured revision has ambiguous changes")
    row = rows[0]
    if row["change_category"] not in ("rsk_baseline", "rsk_source_change") or (
        row["committed_state_revision"] != revision
        or row["status"] not in ("committed", "heartbeat_pending", "successful")
    ):
        raise ProcessingReadError("RSK change run is inconsistent")
    return row


def _previous_change(
    connection: sqlite3.Connection, change: ChangeIdentity
) -> sqlite3.Row | None:
    previous = cast(
        sqlite3.Row | None,
        connection.execute(
            """SELECT state_revision FROM source_changes
            WHERE source_id=? AND state_revision<?
            ORDER BY state_revision DESC, change_id LIMIT 1""",
            (change.source_id, change.state_revision),
        ).fetchone(),
    )
    if previous is None:
        return None
    revision = cast(int, previous["state_revision"])
    return _one_change(connection, change.source_id, revision)


def _product(
    connection: sqlite3.Connection, change: sqlite3.Row, digest: str
) -> RskProductIdentity:
    rows = cast(
        list[sqlite3.Row],
        connection.execute(
            _METADATA_QUERY,
            (change["source_id"], change["run_id"]),
        ).fetchall(),
    )
    if len(rows) != 1:
        raise ProcessingReadError("RSK 00CD10 metadata is absent or ambiguous")
    row = rows[0]
    _require_observation(row, cast(int, change["state_revision"]), "response")
    metadata = _Metadata.model_validate_json(cast(str, row["metadata_json"])).public
    if (
        metadata.hcd != "00CD10"
        or metadata.metadata_observation_sha256 != row["response_sha256"]
    ):
        raise ProcessingReadError("RSK metadata identity differs from observation")
    selected = [
        item
        for item in metadata.files
        if item.archive_status == "privately_archived_verified"
    ]
    if len(selected) != 1:
        raise ProcessingReadError("RSK selected product is absent or ambiguous")
    file = selected[0]
    if (
        file.product_sha256 != digest
        or file.extension != "xml"
        or file.source_archived is not False
        or file.education_file is not False
        or file.size_bytes is None
        or file.file_created_at is None
    ):
        raise ProcessingReadError(
            "RSK selected product differs from supported metadata"
        )
    products = cast(
        list[sqlite3.Row],
        connection.execute(
            _PRODUCT_QUERY,
            (change["source_id"], digest, file.uuid, change["state_revision"]),
        ).fetchall(),
    )
    if len(products) != 1:
        raise ProcessingReadError("RSK product archive evidence is absent or ambiguous")
    product = products[0]
    _require_observation(product, cast(int, product["state_revision"]), "product")
    details = _ProductMetadata.model_validate_json(cast(str, product["metadata_json"]))
    if (
        product["size_bytes"] != file.size_bytes
        or details.size_bytes != file.size_bytes
    ):
        raise ProcessingReadError("RSK product size differs from metadata")
    return RskProductIdentity(
        hcd="00CD10",
        file_uuid=file.uuid,
        file_created_at=file.file_created_at,
        resource_version=metadata.resource_version,
        sha256=digest,
        size_bytes=file.size_bytes,
        metadata_observation_id=cast(str, row["observation_id"]),
        metadata_sha256=cast(str, row["response_sha256"]),
        metadata_size_bytes=cast(int, row["size_bytes"]),
        product_observation_id=cast(str, product["observation_id"]),
        product_state_revision=cast(int, product["state_revision"]),
    )


class _Metadata(_Model):
    public: RskPublicObservation


class _ProductMetadata(_Model):
    hcd: Literal["00CD10"]
    size_bytes: int = Field(ge=1, le=64 * 1024 * 1024)


_METADATA_QUERY = """SELECT o.observation_id, o.metadata_json, o.response_sha256,
    o.state_revision, b.size_bytes, r.committed_state_revision, r.status,
    ob.sha256 AS role_sha256, ob.blob_role, ob.ordinal
FROM observations AS o
LEFT JOIN blobs AS b ON b.sha256=o.response_sha256
LEFT JOIN watcher_runs AS r ON r.run_id=o.run_id AND r.source_id=o.source_id
LEFT JOIN observation_blobs AS ob ON ob.observation_id=o.observation_id
WHERE o.source_id=? AND o.run_id=? AND o.observation_kind='rsk_basic_info'
AND o.source_identity='00CD10' ORDER BY o.observation_id, ob.ordinal LIMIT 2"""
_PRODUCT_QUERY = """WITH selected_product AS MATERIALIZED (
    SELECT source_id, response_sha256, source_identity, state_revision FROM observations
    WHERE source_id=? AND response_sha256=? AND source_identity=?
    AND observation_kind='rsk_product' AND state_revision<=?
    ORDER BY state_revision DESC, observation_id LIMIT 1)
SELECT o.observation_id, o.metadata_json, o.response_sha256,
    o.state_revision, b.size_bytes, r.committed_state_revision, r.status,
    ob.sha256 AS role_sha256, ob.blob_role, ob.ordinal
FROM observations AS o
LEFT JOIN blobs AS b ON b.sha256=o.response_sha256
LEFT JOIN watcher_runs AS r ON r.run_id=o.run_id AND r.source_id=o.source_id
LEFT JOIN observation_blobs AS ob ON ob.observation_id=o.observation_id
JOIN selected_product AS p ON p.source_id=o.source_id
AND p.response_sha256=o.response_sha256 AND p.source_identity=o.source_identity
AND p.state_revision=o.state_revision WHERE o.observation_kind='rsk_product'
ORDER BY o.observation_id, ob.ordinal LIMIT 2"""


def _require_observation(row: sqlite3.Row, revision: int, role: str) -> None:
    if row["state_revision"] != revision or row["committed_state_revision"] != revision:
        raise ProcessingReadError("RSK observation and run revisions disagree")
    if row["status"] not in ("committed", "heartbeat_pending", "successful"):
        raise ProcessingReadError("RSK observation run is incomplete")
    if row["role_sha256"] != row["response_sha256"] or row["ordinal"] != 0:
        raise ProcessingReadError("RSK observation blob reference differs")
    if row["blob_role"] != role:
        raise ProcessingReadError("RSK observation blob role is unsupported")
