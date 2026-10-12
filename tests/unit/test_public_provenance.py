import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

import minos.ingest.watchers.publication.citations as citations
import minos.ingest.watchers.rsk.publication as rsk_publication
from minos.ingest.watchers.control import (
    CommittedBlobEvidence,
    CommittedObservation,
    CommittedObservationHistory,
    CommittedObservationRevision,
    CommittedProductEvidence,
    JsonValue,
)
from minos.ingest.watchers.protocols import WatcherRuntimeError
from minos.ingest.watchers.publication import (
    ImmutableGenerationPublisher,
    PublicPublicationError,
    prepare_public_root,
)
from minos.ingest.watchers.rsk.public_models import (
    MAX_PUBLIC_UNIQUE_NOTICES,
    RskProvenanceEntry,
    RskProvenanceProjection,
    RskPublicObservation,
)
from minos.ingest.watchers.rsk.publication import (
    build_rsk_projection,
    validate_deployed_rsk_projection,
)

_UUID_1 = "00000000-0000-4000-8000-000000000011"
_UUID_2 = "00000000-0000-4000-8000-000000000012"


def _public_payload(
    response_sha256: str,
    file_uuid: str,
    *,
    product_sha256: str | None = None,
    source_archived: bool = False,
    education_file: bool = False,
    extension: str = "xml",
    resource_name: str = "Synthetic resource",
    hcd: str = "00CD10",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "hcd": hcd,
        "resource_name": resource_name,
        "resource_short_name": "SYN",
        "publisher_name": "Public publisher",
        "publisher_short_name": "PUB",
        "resource_type": "SLOWNIK",
        "resource_version": "1",
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": None,
        "metadata_observation_sha256": response_sha256,
        "files": [
            {
                "uuid": file_uuid,
                "file_name": "synthetic.xml",
                "extension": extension,
                "type_label": "XML",
                "size_bytes": 123,
                "file_created_at": "2026-01-02T00:00:00Z",
                "source_archived": source_archived,
                "source_archived_at": None,
                "education_file": education_file,
                "product_sha256": product_sha256,
                "archive_status": "privately_archived_verified"
                if product_sha256 is not None
                else "metadata_only",
            }
        ],
        "notices": [],
    }


def _revision(
    revision: int,
    file_uuid: str,
    *,
    response_sha256: str | None = None,
    product_sha256: str | None = None,
    files_present: bool = True,
    resource_name: str = "Synthetic resource",
    hcd: str = "00CD10",
) -> CommittedObservationRevision:
    digest = response_sha256 or hashlib.sha256(str(revision).encode()).hexdigest()
    payload = _public_payload(
        digest,
        file_uuid,
        product_sha256=product_sha256,
        resource_name=resource_name,
        hcd=hcd,
    )
    if not files_present:
        payload["files"] = []
    observation = CommittedObservation(
        f"observation-{revision:04d}",
        "rsk_basic_info",
        hcd,
        digest,
        1_800_000_000_000 + revision,
        cast(dict[str, JsonValue], {"public": payload}),
    )
    return CommittedObservationRevision(
        revision,
        1_800_000_001_000 + revision,
        (observation,),
    )


def _history(
    *revisions: CommittedObservationRevision,
    product_evidence: tuple[CommittedProductEvidence, ...] = (),
) -> CommittedObservationHistory:
    return CommittedObservationHistory(
        "rsk",
        revisions[-1].state_revision,
        revisions,
        False,
        product_evidence,
    )


def _product_evidence(
    digest: str,
    *,
    state_revision: int = 1,
    hcd: str = "00CD10",
    file_uuid: str = _UUID_1,
    extension_media_type: str = "application/xml",
    size_bytes: int = 123,
    include_blob: bool = True,
) -> CommittedProductEvidence:
    blobs = (
        (
            CommittedBlobEvidence(
                "product",
                0,
                digest,
                size_bytes,
                extension_media_type,
            ),
        )
        if include_blob
        else ()
    )
    return CommittedProductEvidence(
        state_revision,
        f"product-observation-{state_revision:04d}",
        file_uuid,
        digest,
        1_800_000_000_001,
        cast(dict[str, JsonValue], {"hcd": hcd, "size_bytes": size_bytes}),
        size_bytes,
        extension_media_type,
        blobs,
    )


def _generation_files(
    marker: str,
    *,
    revision: int = 1,
) -> tuple[str, dict[str, bytes]]:
    projection = json.dumps(
        {"marker": marker},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(projection).hexdigest()
    manifest = json.dumps(
        {
            "projection_path": "api/v1/provenance/rsk.json",
            "projection_sha256": digest,
            "projection_size_bytes": len(projection),
            "redistribution": {"artifacts": [], "schema_version": 1},
            "schema_version": 1,
            "source": "RSK",
            "state_revision": revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return digest, {
        "manifest.json": manifest,
        "api/v1/provenance/rsk.json": projection,
    }


def test_projection_is_canonical_and_preserves_missing_history() -> None:
    first = _revision(1, _UUID_1)
    second = _revision(2, _UUID_2)

    first_bytes, first_digest, _manifest = build_rsk_projection(_history(first, second))
    reordered = CommittedObservationRevision(
        first.state_revision,
        first.committed_at_ms,
        tuple(reversed(first.observations)),
    )
    second_bytes, second_digest, _manifest = build_rsk_projection(
        _history(reordered, second)
    )

    assert first_bytes == second_bytes
    assert first_digest == second_digest
    document = cast(dict[str, object], json.loads(first_bytes))
    entries = cast(list[dict[str, object]], document["entries"])
    assert [item["status"] for item in entries] == [
        "current",
        "missing",
    ]
    assert document["history_truncated"] is False


def test_projection_truncates_only_complete_oldest_revisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rsk_publication, "MAX_PUBLIC_TIMELINE_ENTRIES", 1)

    projection, _digest, _manifest = build_rsk_projection(
        _history(_revision(1, _UUID_1), _revision(2, _UUID_2))
    )

    document = cast(dict[str, object], json.loads(projection))
    assert document["included_revisions"] == [2]
    assert document["history_truncated"] is True
    entries = cast(list[dict[str, object]], document["entries"])
    assert [item["file_uuid"] for item in entries] == [_UUID_2]


def test_projection_selects_work_bounded_complete_suffix_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rsk_publication, "_MAX_PUBLIC_WORK_FILE_RECORDS", 2)

    projection, _digest, _manifest = build_rsk_projection(
        _history(
            _revision(1, _UUID_1),
            _revision(2, _UUID_1),
            _revision(3, _UUID_1),
        )
    )

    document = cast(dict[str, object], json.loads(projection))
    assert document["included_revisions"] == [2, 3]
    assert document["history_truncated"] is True


def test_projection_rejects_latest_revision_before_exceeding_work_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rsk_publication, "_MAX_PUBLIC_WORK_FILE_RECORDS", 0)

    with pytest.raises(WatcherRuntimeError, match="latest.*work budget"):
        _ = build_rsk_projection(_history(_revision(1, _UUID_1)))


def test_projection_near_limit_processes_each_revision_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = cast(
        Callable[..., object],
        rsk_publication.__dict__["_snapshot_from_revision"],
    )
    calls = 0

    def counted_snapshot(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(rsk_publication, "MAX_PUBLIC_TIMELINE_ENTRIES", 64)
    monkeypatch.setattr(rsk_publication, "_snapshot_from_revision", counted_snapshot)
    revisions = tuple(_revision(index, _UUID_1) for index in range(1, 129))

    projection, _digest, _manifest = build_rsk_projection(_history(*revisions))

    document = cast(dict[str, object], json.loads(projection))
    assert calls == len(revisions)
    assert document["included_revisions"] == list(range(65, 129))
    assert document["history_truncated"] is True


def test_projection_handles_maximum_revision_and_work_record_budget() -> None:
    response_sha256 = hashlib.sha256(b"near-limit-metadata").hexdigest()
    payload = _public_payload(
        response_sha256,
        _UUID_1,
        hcd="99ZZ99",
    )
    template = cast(list[dict[str, object]], payload["files"])[0]
    payload["files"] = [
        {**template, "uuid": f"00000000-0000-4000-8000-{index:012x}"}
        for index in range(1, 33)
    ]
    metadata = cast(dict[str, JsonValue], {"public": payload})
    revisions = tuple(
        CommittedObservationRevision(
            revision,
            1_800_000_001_000 + revision,
            (
                CommittedObservation(
                    f"near-limit-observation-{revision:04d}",
                    "rsk_basic_info",
                    "99ZZ99",
                    response_sha256,
                    1_800_000_000_000 + revision,
                    metadata,
                ),
            ),
        )
        for revision in range(1, 513)
    )

    projection, _digest, _manifest = build_rsk_projection(_history(*revisions))

    document = cast(dict[str, object], json.loads(projection))
    assert len(cast(list[int], document["included_revisions"])) == 512
    assert len(cast(list[object], document["entries"])) == 32
    assert document["history_truncated"] is False


def test_projection_rejects_non_allowlisted_observation_payload() -> None:
    revision = _revision(1, _UUID_1)
    observation = revision.observations[0]
    poisoned = CommittedObservation(
        observation.observation_id,
        observation.observation_kind,
        observation.source_identity,
        observation.response_sha256,
        observation.observed_at_ms,
        {**observation.metadata, "private_description": "PII_CANARY"},
    )

    with pytest.raises(WatcherRuntimeError, match="not allowlisted"):
        _ = build_rsk_projection(
            _history(
                CommittedObservationRevision(1, revision.committed_at_ms, (poisoned,))
            )
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "invented",
        "wrong_hcd",
        "non_xml",
        "nonallowlisted",
        "missing_blob",
    ],
)
def test_projection_requires_exact_allowlisted_product_blob_evidence(
    mutation: str,
) -> None:
    product_digest = "a" * 64
    payload = _public_payload(
        hashlib.sha256(b"metadata").hexdigest(),
        _UUID_1,
        product_sha256=product_digest,
        extension="csv" if mutation == "non_xml" else "xml",
        education_file=mutation == "nonallowlisted",
    )
    revision = CommittedObservationRevision(
        1,
        1_800_000_001_001,
        (
            CommittedObservation(
                "observation-product-0001",
                "rsk_basic_info",
                "00CD10",
                cast(str, payload["metadata_observation_sha256"]),
                1_800_000_000_001,
                cast(dict[str, JsonValue], {"public": payload}),
            ),
        ),
    )
    evidence: tuple[CommittedProductEvidence, ...] = ()
    if mutation != "invented":
        evidence = (
            _product_evidence(
                product_digest,
                hcd="99ZZ99" if mutation == "wrong_hcd" else "00CD10",
                include_blob=mutation != "missing_blob",
            ),
        )

    with pytest.raises(WatcherRuntimeError):
        _ = build_rsk_projection(_history(revision, product_evidence=evidence))


def test_projection_derives_verified_product_fields_from_committed_evidence() -> None:
    product_digest = "a" * 64
    projection, _digest, _manifest = build_rsk_projection(
        _history(
            _revision(1, _UUID_1, product_sha256=product_digest),
            product_evidence=(_product_evidence(product_digest),),
        )
    )

    document = cast(dict[str, object], json.loads(projection))
    entries = cast(list[dict[str, object]], document["entries"])
    assert entries[0]["product_sha256"] == product_digest
    assert entries[0]["archive_status"] == "privately_archived_verified"


def test_projection_allows_verified_same_content_uuid_rotation() -> None:
    product_digest = "a" * 64
    projection, _digest, _manifest = build_rsk_projection(
        _history(
            _revision(1, _UUID_1, product_sha256=product_digest),
            _revision(2, _UUID_2, product_sha256=product_digest),
            product_evidence=(
                _product_evidence(product_digest),
                _product_evidence(
                    product_digest,
                    state_revision=2,
                    file_uuid=_UUID_2,
                ),
            ),
        )
    )

    document = cast(dict[str, object], json.loads(projection))
    entries = cast(list[dict[str, object]], document["entries"])
    assert {
        (entry["file_uuid"], entry["product_sha256"], entry["status"])
        for entry in entries
    } == {
        (_UUID_1, product_digest, "missing"),
        (_UUID_2, product_digest, "current"),
    }


@pytest.mark.parametrize(
    ("extension_media_type", "size_bytes"),
    [
        ("text/xml", 123),
        ("application/xml", 124),
    ],
)
def test_projection_rejects_conflicting_same_digest_evidence(
    extension_media_type: str,
    size_bytes: int,
) -> None:
    product_digest = "a" * 64
    evidence = (
        _product_evidence(product_digest),
        _product_evidence(
            product_digest,
            state_revision=2,
            file_uuid=_UUID_2,
            extension_media_type=extension_media_type,
            size_bytes=size_bytes,
        ),
    )

    with pytest.raises(WatcherRuntimeError, match="product evidence conflicts"):
        _ = build_rsk_projection(
            _history(
                _revision(1, _UUID_1, product_sha256=product_digest),
                _revision(2, _UUID_2, product_sha256=product_digest),
                product_evidence=evidence,
            )
        )


def test_timeline_metadata_change_keeps_prior_present_file_historical() -> None:
    projection, _digest, _manifest = build_rsk_projection(
        _history(
            _revision(1, _UUID_1, resource_name="First metadata"),
            _revision(2, _UUID_1, resource_name="Second metadata"),
        )
    )

    document = cast(dict[str, object], json.loads(projection))
    entries = cast(list[dict[str, object]], document["entries"])
    assert [(item["resource_name"], item["status"]) for item in entries] == [
        ("Second metadata", "current"),
        ("First metadata", "historical"),
    ]


def test_historical_entry_remains_usable_without_origin_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_origin(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("public history must not request the RSK origin")

    monkeypatch.setattr("socket.create_connection", reject_origin)
    projection, _digest, _manifest = build_rsk_projection(
        _history(
            _revision(1, _UUID_1, resource_name="Historical metadata"),
            _revision(2, _UUID_1, resource_name="Current metadata"),
        )
    )

    document = cast(dict[str, object], json.loads(projection))
    entries = cast(list[dict[str, object]], document["entries"])
    assert [(item["resource_name"], item["status"]) for item in entries] == [
        ("Current metadata", "current"),
        ("Historical metadata", "historical"),
    ]


def test_timeline_uses_global_file_uuid_presence_across_hcd_metadata_move() -> None:
    projection, _digest, _manifest = build_rsk_projection(
        _history(
            _revision(1, _UUID_1),
            _revision(2, _UUID_1, hcd="99ZZ99"),
        )
    )

    document = cast(dict[str, object], json.loads(projection))
    entries = cast(list[dict[str, object]], document["entries"])
    assert [(item["hcd"], item["status"]) for item in entries] == [
        ("99ZZ99", "current"),
        ("00CD10", "historical"),
    ]


def test_timeline_disappearance_and_reappearance_are_separate_intervals() -> None:
    response_sha256 = hashlib.sha256(b"stable-metadata").hexdigest()
    projection, _digest, _manifest = build_rsk_projection(
        _history(
            _revision(1, _UUID_1, response_sha256=response_sha256),
            _revision(
                2,
                _UUID_1,
                response_sha256=hashlib.sha256(b"absent").hexdigest(),
                files_present=False,
            ),
            _revision(3, _UUID_1, response_sha256=response_sha256),
        )
    )

    document = cast(dict[str, object], json.loads(projection))
    entries = cast(list[dict[str, object]], document["entries"])
    assert len(entries) == 2
    assert [item["status"] for item in entries] == ["current", "historical"]
    assert entries[0]["first_observed_at"] == entries[0]["last_observed_at"]
    assert entries[1]["first_observed_at"] == entries[1]["last_observed_at"]


def test_timeline_absent_latest_file_is_missing() -> None:
    projection, _digest, _manifest = build_rsk_projection(
        _history(
            _revision(1, _UUID_1),
            _revision(2, _UUID_1, files_present=False),
        )
    )

    document = cast(dict[str, object], json.loads(projection))
    entries = cast(list[dict[str, object]], document["entries"])
    assert [item["status"] for item in entries] == ["missing"]


def test_interval_order_uses_parsed_same_second_utc_datetimes() -> None:
    projection, _digest, _manifest = build_rsk_projection(
        _history(_revision(1, _UUID_1))
    )
    document = cast(dict[str, object], json.loads(projection))
    entry = cast(list[dict[str, object]], document["entries"])[0]
    entry["first_observed_at"] = "2026-01-01T00:00:00Z"
    entry["last_observed_at"] = "2026-01-01T00:00:00.000001Z"

    validated = RskProvenanceEntry.model_validate(entry)
    assert validated.first_observed_at.endswith("00Z")

    reversed_entry = dict(entry)
    reversed_entry["first_observed_at"] = "2026-01-01T00:00:00.000001Z"
    reversed_entry["last_observed_at"] = "2026-01-01T00:00:00Z"
    with pytest.raises(ValidationError, match="interval is reversed"):
        _ = RskProvenanceEntry.model_validate(reversed_entry)

    invalid_entry = dict(entry)
    invalid_entry["last_observed_at"] = "2026-02-30T00:00:00.1Z"
    with pytest.raises(ValidationError, match="valid UTC datetime"):
        _ = RskProvenanceEntry.model_validate(invalid_entry)


@pytest.mark.parametrize(
    ("model_name", "field_name"),
    [
        ("observation", "valid_from"),
        ("observation", "valid_to"),
        ("observed_file", "file_created_at"),
        ("observed_file", "source_archived_at"),
        ("projection", "projected_at"),
        ("entry", "valid_from"),
        ("entry", "valid_to"),
        ("entry", "file_created_at"),
        ("entry", "source_archived_at"),
        ("entry", "first_observed_at"),
        ("entry", "last_observed_at"),
    ],
)
def test_every_public_timestamp_field_rejects_invalid_calendar_values(
    model_name: str,
    field_name: str,
) -> None:
    invalid_timestamp = "2026-02-30T00:00:00.1Z"
    if model_name in {"observation", "observed_file"}:
        payload = _public_payload(hashlib.sha256(b"metadata").hexdigest(), _UUID_1)
        target = (
            cast(list[dict[str, object]], payload["files"])[0]
            if model_name == "observed_file"
            else payload
        )
        target[field_name] = invalid_timestamp
        with pytest.raises(ValidationError, match="valid UTC datetime"):
            _ = RskPublicObservation.model_validate(payload)
        return

    projection, _digest, _manifest = build_rsk_projection(
        _history(_revision(1, _UUID_1))
    )
    document = cast(dict[str, object], json.loads(projection))
    target = (
        cast(list[dict[str, object]], document["entries"])[0]
        if model_name == "entry"
        else document
    )
    target[field_name] = invalid_timestamp
    with pytest.raises(ValidationError, match="valid UTC datetime"):
        _ = RskProvenanceProjection.model_validate(document)


@pytest.mark.parametrize(
    "timestamp",
    [
        "0001-01-01T00:00:00Z",
        "2000-02-29T23:59:59.000001Z",
        "9999-12-31T23:59:59.999999Z",
    ],
)
def test_public_timestamp_contract_accepts_utc_calendar_values(timestamp: str) -> None:
    projection, _digest, _manifest = build_rsk_projection(
        _history(_revision(1, _UUID_1))
    )
    document = cast(dict[str, object], json.loads(projection))
    document["projected_at"] = timestamp

    assert RskProvenanceProjection.model_validate(document).projected_at == timestamp


@pytest.mark.parametrize(
    "timestamp",
    [
        "0000-01-01T00:00:00Z",
        "1900-02-29T00:00:00Z",
        "2026-01-01T24:00:00Z",
        "2026-01-01T00:00:60Z",
        "2026-01-01T00:00:00.0000001Z",
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:00z",
    ],
)
def test_public_timestamp_contract_rejects_non_contract_values(timestamp: str) -> None:
    projection, _digest, _manifest = build_rsk_projection(
        _history(_revision(1, _UUID_1))
    )
    document = cast(dict[str, object], json.loads(projection))
    document["projected_at"] = timestamp

    with pytest.raises(ValidationError):
        _ = RskProvenanceProjection.model_validate(document)


def test_validity_order_uses_parsed_same_second_utc_datetimes() -> None:
    payload = _public_payload(hashlib.sha256(b"metadata").hexdigest(), _UUID_1)
    payload["valid_from"] = "2026-01-01T00:00:00.000001Z"
    payload["valid_to"] = "2026-01-01T00:00:00Z"

    with pytest.raises(ValidationError, match="validity interval is reversed"):
        _ = RskPublicObservation.model_validate(payload)

    projection, _digest, _manifest = build_rsk_projection(
        _history(_revision(1, _UUID_1))
    )
    document = cast(dict[str, object], json.loads(projection))
    entry = cast(list[dict[str, object]], document["entries"])[0]
    entry["valid_from"] = "2026-01-01T00:00:00.000001Z"
    entry["valid_to"] = "2026-01-01T00:00:00Z"
    with pytest.raises(ValidationError, match="validity interval is reversed"):
        _ = RskProvenanceEntry.model_validate(entry)


def test_projection_bounds_unique_notices_across_all_entries() -> None:
    projection, _digest, _manifest = build_rsk_projection(
        _history(_revision(1, _UUID_1))
    )
    document = cast(dict[str, object], json.loads(projection))
    entry = cast(list[dict[str, object]], document["entries"])[0]
    entries: list[dict[str, object]] = []
    notice_index = 0
    while notice_index <= MAX_PUBLIC_UNIQUE_NOTICES:
        notices = [
            f"Public notice {index}."
            for index in range(
                notice_index,
                min(notice_index + 16, MAX_PUBLIC_UNIQUE_NOTICES + 1),
            )
        ]
        entries.append({**entry, "notices": notices})
        notice_index += len(notices)
    document["entries"] = entries

    with pytest.raises(ValidationError, match="unique notice count exceeds"):
        _ = RskProvenanceProjection.model_validate(document)


def test_deployment_projection_validation_allows_absent_and_valid_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public"
    prepare_public_root(root)
    assert validate_deployed_rsk_projection(root) == {"status": "absent"}

    projection, digest, manifest = build_rsk_projection(_history(_revision(1, _UUID_1)))
    _ = ImmutableGenerationPublisher(root).publish(
        1,
        digest,
        {
            "api/v1/provenance/rsk.json": projection,
            "manifest.json": manifest,
        },
    )

    assert validate_deployed_rsk_projection(root) == {
        "revision": 1,
        "sha256": digest,
        "status": "valid",
    }


def test_deployment_projection_validation_rejects_schema_invalid_generation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public"
    prepare_public_root(root)
    projection, _digest, manifest = build_rsk_projection(
        _history(_revision(1, _UUID_1))
    )
    document = cast(dict[str, object], json.loads(projection))
    document["unexpected"] = True
    invalid_projection = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    invalid_digest = hashlib.sha256(invalid_projection).hexdigest()
    manifest_document = cast(dict[str, object], json.loads(manifest))
    manifest_document["projection_sha256"] = invalid_digest
    manifest_document["projection_size_bytes"] = len(invalid_projection)
    invalid_manifest = json.dumps(
        manifest_document,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    _ = ImmutableGenerationPublisher(root).publish(
        1,
        invalid_digest,
        {
            "api/v1/provenance/rsk.json": invalid_projection,
            "manifest.json": invalid_manifest,
        },
    )

    with pytest.raises(WatcherRuntimeError, match="projection is invalid"):
        _ = validate_deployed_rsk_projection(root)


def test_filesystem_publication_is_atomic_idempotent_and_refuses_downgrade(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public"
    prepare_public_root(root)
    digest_1, files_1 = _generation_files("one")
    publisher = ImmutableGenerationPublisher(root)

    generation_1 = publisher.publish(1, digest_1, files_1)
    assert publisher.publish(1, digest_1, files_1) == generation_1
    assert (root / "current").readlink() == Path(f"generations/{generation_1}")
    assert (root / "current" / "manifest.json").stat().st_mode & 0o777 == 0o444

    digest_2, files_2 = _generation_files("two", revision=2)
    generation_2 = publisher.publish(2, digest_2, files_2)
    assert publisher.current() == (2, generation_2)
    with pytest.raises(PublicPublicationError, match="downgrade"):
        _ = publisher.publish(1, digest_1, files_1)
    assert publisher.current() == (2, generation_2)


def test_filesystem_publication_rejects_same_revision_different_digest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public"
    prepare_public_root(root)
    digest_1, files_1 = _generation_files("one")
    publisher = ImmutableGenerationPublisher(root)
    _ = publisher.publish(1, digest_1, files_1)
    digest_2, files_2 = _generation_files("two")

    with pytest.raises(PublicPublicationError, match="different projection digest"):
        _ = publisher.publish(1, digest_2, files_2)

    assert publisher.current() == (1, f"1-{digest_1}")
    rebuilt = publisher.rebuild(
        expected_current_revision=1,
        expected_current_sha256=digest_1,
        target_revision=1,
        target_sha256=digest_2,
        files=files_2,
    )
    assert publisher.current() == (1, rebuilt)


def test_explicit_rebuild_is_fenced_by_current_and_target_digests(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public"
    prepare_public_root(root)
    digest_1, files_1 = _generation_files("one")
    digest_2, files_2 = _generation_files("two", revision=2)
    publisher = ImmutableGenerationPublisher(root)
    _ = publisher.publish(1, digest_1, files_1)
    _ = publisher.publish(2, digest_2, files_2)

    with pytest.raises(PublicPublicationError, match="expectation is stale"):
        _ = publisher.rebuild(
            expected_current_revision=1,
            expected_current_sha256=digest_1,
            target_revision=1,
            target_sha256=digest_1,
            files=files_1,
        )

    rebuilt = publisher.rebuild(
        expected_current_revision=2,
        expected_current_sha256=digest_2,
        target_revision=1,
        target_sha256=digest_1,
        files=files_1,
    )
    assert rebuilt == f"1-{digest_1}"
    assert publisher.current() == (1, rebuilt)


def test_publication_rebuilds_absent_exact_dangling_target_only(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public"
    prepare_public_root(root)
    digest, files = _generation_files("one")
    generation = f"1-{digest}"
    (root / "generations").mkdir()
    (root / "current").symlink_to(f"generations/{generation}")

    publisher = ImmutableGenerationPublisher(root)
    assert publisher.publish(1, digest, files) == generation
    assert publisher.current() == (1, generation)

    projection = root / "generations" / generation / "api/v1/provenance/rsk.json"
    projection.chmod(0o644)
    _ = projection.write_bytes(b"corrupt")
    projection.chmod(0o444)
    with pytest.raises(PublicPublicationError):
        _ = publisher.publish(1, digest, files)


def test_current_rejects_existing_generation_manifest_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "public"
    prepare_public_root(root)
    digest, files = _generation_files("one")
    generation = ImmutableGenerationPublisher(root).publish(1, digest, files)
    manifest_path = root / "generations" / generation / "manifest.json"
    manifest = cast(dict[str, object], json.loads(manifest_path.read_bytes()))
    manifest["state_revision"] = 2
    manifest_path.chmod(0o644)
    _ = manifest_path.write_bytes(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    )
    manifest_path.chmod(0o444)

    with pytest.raises(PublicPublicationError, match="manifest fields"):
        _ = ImmutableGenerationPublisher(root).current()


@pytest.mark.parametrize(
    "relative_directory",
    ["api", "api/v1", "api/v1/provenance"],
)
def test_current_rejects_undeclared_nested_generation_files(
    tmp_path: Path,
    relative_directory: str,
) -> None:
    root = tmp_path / relative_directory.replace("/", "-")
    prepare_public_root(root)
    digest, files = _generation_files("one")
    publisher = ImmutableGenerationPublisher(root)
    generation = publisher.publish(1, digest, files)
    directory = root / "generations" / generation / relative_directory
    directory.chmod(0o755)
    undeclared = directory / "undeclared.json"
    _ = undeclared.write_bytes(b"{}")
    undeclared.chmod(0o444)
    directory.chmod(0o555)

    with pytest.raises(PublicPublicationError, match="tree layout differs"):
        _ = publisher.current()
    with pytest.raises(PublicPublicationError, match="tree layout differs"):
        _ = publisher.publish(1, digest, files)


@pytest.mark.parametrize(
    "mutation",
    ["projection", "revision", "path", "digest", "size"],
)
def test_filesystem_recomputes_digest_and_strictly_validates_manifest(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = tmp_path / mutation
    prepare_public_root(root)
    digest, files = _generation_files("one")
    mutated = dict(files)
    if mutation == "projection":
        mutated["api/v1/provenance/rsk.json"] = b'{"marker":"different"}'
    else:
        manifest = cast(dict[str, object], json.loads(mutated["manifest.json"]))
        key = {
            "revision": "state_revision",
            "path": "projection_path",
            "digest": "projection_sha256",
            "size": "projection_size_bytes",
        }[mutation]
        manifest[key] = {
            "revision": 2,
            "path": "other.json",
            "digest": "b" * 64,
            "size": 999,
        }[mutation]
        mutated["manifest.json"] = json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    with pytest.raises(PublicPublicationError):
        _ = ImmutableGenerationPublisher(root).publish(1, digest, mutated)


def test_filesystem_publication_sets_explicit_modes_under_restrictive_umask(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public"
    previous_umask = os.umask(0o077)
    try:
        prepare_public_root(root)
        digest, files = _generation_files("restricted-umask")
        generation = ImmutableGenerationPublisher(root).publish(1, digest, files)
    finally:
        _ = os.umask(previous_umask)

    generation_path = root / "generations" / generation
    assert root.stat().st_mode & 0o777 == 0o755
    assert (root / "generations").stat().st_mode & 0o777 == 0o755
    assert generation_path.stat().st_mode & 0o777 == 0o555
    assert (generation_path / "api/v1/provenance").stat().st_mode & 0o777 == 0o555
    assert (generation_path / "manifest.json").stat().st_mode & 0o777 == 0o444


@pytest.mark.parametrize(
    ("phase", "expected_revision"),
    [("generation_fsynced", 1), ("current_switched", 2)],
)
def test_filesystem_publication_fault_boundaries(
    tmp_path: Path,
    phase: str,
    expected_revision: int,
) -> None:
    root = tmp_path / "public"
    prepare_public_root(root)
    digest_1, files_1 = _generation_files("one")
    _ = ImmutableGenerationPublisher(root).publish(1, digest_1, files_1)

    def inject(current_phase: str) -> None:
        if current_phase == phase:
            raise PublicPublicationError("injected publication fault")

    digest_2, files_2 = _generation_files("two", revision=2)
    with pytest.raises(PublicPublicationError, match="injected"):
        _ = ImmutableGenerationPublisher(root, fault=inject).publish(
            2,
            digest_2,
            files_2,
        )

    publisher = ImmutableGenerationPublisher(root)
    current = publisher.current()
    assert current is not None
    assert current[0] == expected_revision
    _ = publisher.publish(2, digest_2, files_2)
    current = publisher.current()
    assert current is not None
    assert current[0] == 2


@pytest.mark.parametrize(
    "phase",
    [
        "gc_victim_renamed",
        "gc_rename_fsynced",
        "gc_file_unlinked",
        "gc_directory_removed",
        "gc_delete_fsynced",
    ],
)
def test_generation_gc_recovers_each_tombstone_fault(
    tmp_path: Path,
    phase: str,
) -> None:
    root = tmp_path / phase
    prepare_public_root(root)
    digest_1, files_1 = _generation_files("one")
    _ = ImmutableGenerationPublisher(root, retained_generations=1).publish(
        1,
        digest_1,
        files_1,
    )

    def inject(current_phase: str) -> None:
        if current_phase == phase:
            raise PublicPublicationError("injected GC fault")

    digest_2, files_2 = _generation_files("two", revision=2)
    with pytest.raises(PublicPublicationError, match="injected GC fault"):
        _ = ImmutableGenerationPublisher(
            root,
            retained_generations=1,
            fault=inject,
        ).publish(2, digest_2, files_2)

    publisher = ImmutableGenerationPublisher(root, retained_generations=1)
    assert publisher.publish(2, digest_2, files_2) == f"2-{digest_2}"
    names = {item.name for item in (root / "generations").iterdir()}
    assert names == {f"2-{digest_2}"}


def test_publication_sweeps_bounded_partial_building_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public"
    prepare_public_root(root)
    generations = root / "generations"
    generations.mkdir()
    stale = generations / f".building-{'a' * 32}"
    (stale / "api").mkdir(parents=True)
    _ = (stale / "manifest.json").write_bytes(b"partial")
    digest, files = _generation_files("one")

    _ = ImmutableGenerationPublisher(root).publish(1, digest, files)

    assert not stale.exists()


def test_filesystem_publication_rejects_current_symlink_and_generation_hardlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public"
    prepare_public_root(root)
    digest, files = _generation_files("one")
    publisher = ImmutableGenerationPublisher(root)
    generation = publisher.publish(1, digest, files)

    (root / "current").unlink()
    (root / "current").symlink_to("../outside")
    with pytest.raises(PublicPublicationError, match="target"):
        _ = publisher.publish(1, digest, files)

    (root / "current").unlink()
    (root / "current").symlink_to(f"generations/{generation}")
    generation_path = root / "generations" / generation
    generation_path.chmod(0o755)
    manifest = generation_path / "manifest.json"
    manifest.chmod(0o644)
    linked = tmp_path / "manifest-hardlink"
    os.link(manifest, linked)
    manifest.chmod(0o444)
    generation_path.chmod(0o555)
    with pytest.raises(PublicPublicationError, match="file is unsafe"):
        _ = publisher.publish(1, digest, files)


def test_checked_redistribution_manifest_is_deny_all() -> None:
    manifest_path = Path(__file__).parents[2] / "manifests" / "redistribution.json"
    assert json.loads(manifest_path.read_bytes()) == {
        "schema_version": 1,
        "artifacts": [],
    }


def test_citations_survive_generation_gc_and_legacy_upgrade(tmp_path: Path) -> None:
    root = tmp_path / "public"
    prepare_public_root(root)
    first_digest, first_files = _generation_files("first")
    legacy = ImmutableGenerationPublisher(root, retained_generations=1)
    _ = legacy.publish(1, first_digest, first_files)
    legacy_manifest = (root / "current/manifest.json").read_bytes()
    publisher = ImmutableGenerationPublisher(
        root, retained_generations=1, retain_citations=True
    )
    _ = publisher.publish(1, first_digest, first_files)
    assert (root / "current/manifest.json").read_bytes() == legacy_manifest
    digest = first_digest
    for revision in range(2, 6):
        digest, files = _generation_files(str(revision), revision=revision)
        _ = publisher.publish(revision, digest, files)
    assert len(list((root / "generations").iterdir())) == 1
    assert publisher.citation_revisions() == frozenset(range(1, 6))
    assert (root / "citations" / f"1-{first_digest}.json").read_bytes() == first_files[
        "api/v1/provenance/rsk.json"
    ]
    # The operator's explicit rebuild cannot change an already-cited fact.
    replacement_digest, replacement_files = _generation_files("replacement")
    with pytest.raises(
        PublicPublicationError, match="citation revision already differs"
    ):
        _ = publisher.rebuild(
            expected_current_revision=5,
            expected_current_sha256=digest,
            target_revision=1,
            target_sha256=replacement_digest,
            files=replacement_files,
        )
    assert publisher.current() == (5, f"5-{digest}")


@pytest.mark.parametrize("bound", ["count", "bytes"])
def test_citation_capacity_refuses_new_pointer_without_evicting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    prepare_public_root(tmp_path)
    publisher = ImmutableGenerationPublisher(tmp_path, retain_citations=True)
    digest, files = _generation_files("first")
    _ = publisher.publish(1, digest, files)
    if bound == "count":
        monkeypatch.setattr(citations, "MAX_CITATION_REVISIONS", 1)
    else:
        monkeypatch.setattr(
            citations, "MAX_CITATION_BYTES", len(files["api/v1/provenance/rsk.json"])
        )
    new_digest, new_files = _generation_files("second", revision=2)
    with pytest.raises(PublicPublicationError, match="capacity is exhausted"):
        _ = publisher.publish(2, new_digest, new_files)
    assert publisher.current() == (1, f"1-{digest}")
    assert publisher.citation_revisions() == frozenset({1})
    # Retrying a retained record at the quota remains possible.
    assert publisher.publish(1, digest, files) == f"1-{digest}"


def test_citation_precedes_pointer_and_resumes_after_publication_fault(
    tmp_path: Path,
) -> None:
    prepare_public_root(tmp_path)
    digest, files = _generation_files("first")
    publisher = ImmutableGenerationPublisher(tmp_path, retain_citations=True)
    _ = publisher.publish(1, digest, files)

    def interrupt(phase: str) -> None:
        if phase == "generation_fsynced":
            raise PublicPublicationError("injected interruption")

    second_digest, second_files = _generation_files("second", revision=2)
    with pytest.raises(PublicPublicationError, match="injected interruption"):
        _ = ImmutableGenerationPublisher(
            tmp_path, retain_citations=True, fault=interrupt
        ).publish(2, second_digest, second_files)
    assert publisher.current() == (1, f"1-{digest}")
    assert publisher.citation_revisions() == frozenset({1, 2})
    _ = publisher.publish(2, second_digest, second_files)
    assert publisher.current() == (2, f"2-{second_digest}")
    assert len(list((tmp_path / "citations").iterdir())) == 2


@pytest.mark.parametrize("mutation", ["symlink", "hardlink", "bytes", "extra_file"])
def test_citation_recovery_rejects_unsafe_or_corrupt_records(
    tmp_path: Path, mutation: str
) -> None:
    prepare_public_root(tmp_path)
    digest, files = _generation_files("first")
    publisher = ImmutableGenerationPublisher(tmp_path, retain_citations=True)
    _ = publisher.publish(1, digest, files)
    record = tmp_path / "citations" / f"1-{digest}.json"
    if mutation == "symlink":
        record.unlink()
        record.symlink_to(tmp_path / "current/api/v1/provenance/rsk.json")
    elif mutation == "hardlink":
        os.link(record, tmp_path / "private-copy")
    elif mutation == "bytes":
        record.chmod(0o644)
        _ = record.write_bytes(b"x" * record.stat().st_size)
        record.chmod(0o444)
    else:
        _ = (tmp_path / "citations/private.sqlite").write_bytes(b"private")
    with pytest.raises(PublicPublicationError):
        _ = publisher.citation_revisions()


def test_citation_resume_removes_only_recognized_staging_files(tmp_path: Path) -> None:
    root = tmp_path / "citations"
    prepare_public_root(root)
    interrupted = root / (".building-" + "a" * 32)
    _ = interrupted.write_bytes(b"partial")
    interrupted.chmod(0o600)
    archive = citations.CitationArchive(root)
    data = b'{"public":"metadata"}'
    digest = hashlib.sha256(data).hexdigest()
    archive.publish(1, digest, data)
    assert not interrupted.exists()
    assert [(item.revision, item.sha256) for item in archive.records()] == [(1, digest)]


@pytest.mark.parametrize("after_rename", [False, True])
def test_citation_write_failure_keeps_current_and_retry_is_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_rename: bool
) -> None:
    prepare_public_root(tmp_path)
    publisher = ImmutableGenerationPublisher(tmp_path, retain_citations=True)
    digest, files = _generation_files("first")
    _ = publisher.publish(1, digest, files)
    second_digest, second_files = _generation_files("second", revision=2)
    original_rename = os.rename

    def interrupt(
        source: str, target: str, *, src_dir_fd: int, dst_dir_fd: int
    ) -> None:
        if target == f"2-{second_digest}.json":
            if after_rename:
                original_rename(
                    source, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd
                )
            raise OSError("synthetic private path must not escape")
        original_rename(source, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "rename", interrupt)
        with pytest.raises(
            PublicPublicationError, match="^citation publication failed$"
        ):
            _ = publisher.publish(2, second_digest, second_files)
    assert publisher.current() == (1, f"1-{digest}")
    assert not list((tmp_path / "citations").glob(".building-*"))

    original_fsync = os.fsync
    archive_inode = (tmp_path / "citations").stat().st_ino
    parent_inode = tmp_path.stat().st_ino
    synced = False
    parent_synced = False

    def observe_fsync(descriptor: int) -> None:
        nonlocal synced, parent_synced
        if os.fstat(descriptor).st_ino == archive_inode:
            synced = True
        if os.fstat(descriptor).st_ino == parent_inode:
            parent_synced = True
        original_fsync(descriptor)

    def require_citation_durability(phase: str) -> None:
        if phase == "generation_fsynced":
            assert synced, "current must not advertise a non-durable citation"
            assert parent_synced, "the citation directory must also be durable"

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", observe_fsync)
        _ = ImmutableGenerationPublisher(
            tmp_path, retain_citations=True, fault=require_citation_durability
        ).publish(2, second_digest, second_files)
    assert publisher.current() == (2, f"2-{second_digest}")
