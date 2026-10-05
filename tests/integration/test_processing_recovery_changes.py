import hashlib
import json
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from minos.ingest.processing.recovery_changes import (
    ChangeBatch,
    ChangeIdentity,
    ChangeWindow,
    ProcessingReadError,
    read_change_batch,
    require_selected_change,
)
from minos.ingest.watchers.config import RSK_SOURCE_CONFIGURATION_CODECS
from minos.ingest.watchers.control import (
    BlobReceipt,
    ChangeDraft,
    ClaimRequest,
    ControlStore,
    ObservationCommit,
    ObservationDraft,
    SourceConfiguration,
)
from minos.ingest.watchers.recovery import (
    RecoveryImageRequest,
    RecoveryImageStagingArea,
    VerifiedRecoveryCandidate,
    VerifiedRecoveryImage,
    recovery_key,
)


@contextmanager
def captured_changes(root: Path, *, count: int = 2) -> Generator[VerifiedRecoveryImage]:
    store = ControlStore.open(
        root / "control.sqlite3",
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        _ = store.configure_source(SourceConfiguration("other", "rsk", {}, True))
        now_ms = time.time_ns() // 1_000_000
        for source in ("rsk", "other"):
            claim = store.acquire_claim(ClaimRequest(source, "manual", now_ms, 300_000))
            digest = hashlib.sha256(b"{}").hexdigest()
            observation = ObservationDraft(
                f"{source}-catalog-observation",
                "catalog",
                "PRIVATE_SOURCE_URL?TOKEN",
                digest,
                now_ms,
                {"headers": {"authorization": "PRIVATE_SECRET"}},
            )
            _ = store.commit_observations(
                claim.identity,
                ObservationCommit(
                    (
                        BlobReceipt(
                            digest,
                            2,
                            "application/json",
                            f"blobs/sha256/{digest}",
                            digest,
                            2,
                        ),
                    ),
                    (observation,),
                    {"cursor": "captured-only"},
                    tuple(
                        ChangeDraft(
                            f"{source}-source-change-{index:04d}",
                            observation.observation_id,
                            "rsk_source_change",
                            {"private": "PRIVATE_SUMMARY", "ordinal": index},
                        )
                        for index in reversed(range(count))
                    ),
                ),
            )
        staging = RecoveryImageStagingArea(root / "staging")
        with staging.create(store, RecoveryImageRequest("daily", now_ms)) as image:
            yield VerifiedRecoveryImage(
                image.path,
                VerifiedRecoveryCandidate(
                    recovery_key(image.metadata, image.sha256),
                    "selected-version",
                    image.sha256,
                    image.size_bytes,
                    now_ms,
                    image.metadata,
                ),
            )
    finally:
        store.close()


def test_processing_reader_is_deterministic_read_only_and_exports_no_private_state(
    tmp_path: Path,
) -> None:
    with captured_changes(tmp_path) as image:
        before = image.path.read_bytes()
        window = ChangeWindow(source_id="rsk", after_state_revision=0)
        first = read_change_batch(image, window)
        assert read_change_batch(image, window) == first
        assert [change.change_id for change in first.changes] == [
            "rsk-source-change-0000",
            "rsk-source-change-0001",
        ]
        assert all(change.source_id == "rsk" for change in first.changes)
        assert first.recovery.version_id == "selected-version"
        assert first.recovery.sha256 == hashlib.sha256(before).hexdigest()
        for change in first.changes:
            assert require_selected_change(image, first.recovery, change) == change
            assert change.observation_sha256 == hashlib.sha256(b"{}").hexdigest()
        assert "PRIVATE_" not in first.model_dump_json()
        assert "headers" not in first.model_dump_json()
        assert str(tmp_path) not in first.model_dump_json()
        assert image.path.read_bytes() == before
        assert (
            read_change_batch(
                image,
                ChangeWindow(
                    source_id="rsk",
                    after_state_revision=first.changes[-1].state_revision,
                ),
            ).changes
            == ()
        )
    assert list((tmp_path / "staging").iterdir()) == []


@pytest.mark.parametrize(
    "field",
    [
        "source_id",
        "run_id",
        "observation_id",
        "state_revision",
        "observation_sha256",
        "change_summary_sha256",
        "configuration_sha256",
    ],
)
def test_reselection_checks_every_identity_not_just_change_id(
    tmp_path: Path, field: str
) -> None:
    with captured_changes(tmp_path) as image:
        batch = read_change_batch(
            image, ChangeWindow(source_id="rsk", after_state_revision=0)
        )
        raw = batch.changes[0].model_dump(mode="json")
        raw[field] = (
            raw[field] + 1
            if field == "state_revision"
            else "a" * 64
            if field.endswith("sha256")
            else "another-identity"
        )
        changed = ChangeIdentity.model_validate_json(json.dumps(raw))
        with pytest.raises(ProcessingReadError, match="change identity changed"):
            _ = require_selected_change(image, batch.recovery, changed)


def test_processing_reader_refuses_partial_batches_and_stale_or_unknown_windows(
    tmp_path: Path,
) -> None:
    with captured_changes(tmp_path, count=129) as image:
        for window, message in (
            (
                ChangeWindow(source_id="rsk", after_state_revision=0),
                "exceeds its bound",
            ),
            (
                ChangeWindow(source_id="absent", after_state_revision=0),
                "source is absent",
            ),
            (
                ChangeWindow(
                    source_id="rsk",
                    after_state_revision=image.candidate.metadata.state_revision + 1,
                ),
                "checkpoint is ahead",
            ),
        ):
            with pytest.raises(ProcessingReadError, match=message):
                _ = read_change_batch(image, window)


@pytest.mark.parametrize(
    "damage", ["bytes", "version", "revision", "migration", "symlink"]
)
def test_reader_reverifies_the_exact_image_and_pin_before_selecting(
    tmp_path: Path, damage: str
) -> None:
    with captured_changes(tmp_path) as original:
        image = original
        if damage == "bytes":
            _ = image.path.write_bytes(b"not SQLite")
        elif damage == "version":
            image = replace(
                image, candidate=replace(image.candidate, version_id="null")
            )
        elif damage in ("revision", "migration"):
            metadata = replace(
                image.candidate.metadata,
                **(
                    {"state_revision": image.candidate.metadata.state_revision + 1}
                    if damage == "revision"
                    else {"migration_set_sha256": "a" * 64}
                ),
            )
            image = replace(
                image, candidate=replace(image.candidate, metadata=metadata)
            )
        else:
            link = tmp_path / "image-link.sqlite3"
            link.symlink_to(image.path)
            image = replace(image, path=link)
        with pytest.raises(ProcessingReadError):
            _ = read_change_batch(
                image, ChangeWindow(source_id="rsk", after_state_revision=0)
            )


@pytest.mark.parametrize(
    "damage",
    [
        "change_revision",
        "observation_revision",
        "configuration",
        "foreign_key",
        "migration",
    ],
)
def test_sqlite_relationship_guards_are_independent_of_outer_checksum(
    tmp_path: Path, damage: str
) -> None:
    with captured_changes(tmp_path) as original:
        connection = sqlite3.connect(original.path)
        try:
            if damage == "change_revision":
                _ = connection.execute(
                    "UPDATE source_changes SET state_revision = 1 WHERE source_id = 'rsk'"
                )
            elif damage == "observation_revision":
                _ = connection.execute(
                    "UPDATE observations SET state_revision = 1 WHERE source_id = 'rsk'"
                )
            elif damage == "configuration":
                _ = connection.execute(
                    "UPDATE source_configuration_revisions SET configuration_sha256 = ? WHERE source_id = 'rsk'",
                    ("a" * 64,),
                )
            elif damage == "foreign_key":
                _ = connection.execute(
                    "UPDATE source_changes SET observation_id = 'other-catalog-observation' WHERE source_id = 'rsk'"
                )
            else:
                _ = connection.execute(
                    "UPDATE control_schema_migrations SET up_sha256 = ? WHERE version = 1",
                    ("a" * 64,),
                )
            connection.commit()
        finally:
            connection.close()
        digest = hashlib.sha256(original.path.read_bytes()).hexdigest()
        image = replace(
            original,
            candidate=replace(
                original.candidate,
                sha256=digest,
                key=recovery_key(original.candidate.metadata, digest),
            ),
        )
        expected = {
            "change_revision": "captured run revision",
            "observation_revision": "observation revisions disagree",
            "configuration": "configuration checksum differs",
            "foreign_key": "input could not be verified",
            "migration": "input could not be verified",
        }
        with pytest.raises(ProcessingReadError, match=expected[damage]):
            _ = read_change_batch(
                image, ChangeWindow(source_id="rsk", after_state_revision=0)
            )


def test_reader_deadline_and_input_validation_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for raw in (
        {"source_id": "rsk", "after_state_revision": True},
        {"source_id": "rsk'; DROP TABLE sources", "after_state_revision": 0},
    ):
        with pytest.raises(ValidationError):
            _ = ChangeWindow.model_validate(raw)
    with captured_changes(tmp_path) as image:
        calls = 0

        def expired() -> float:
            nonlocal calls
            calls += 1
            return 0.0 if calls == 1 else 301.0

        monkeypatch.setattr(time, "monotonic", expired)
        with pytest.raises(ProcessingReadError, match="deadline exceeded"):
            _ = read_change_batch(
                image, ChangeWindow(source_id="rsk", after_state_revision=0)
            )


@pytest.mark.parametrize(
    "damage", ["order", "duplicate", "source", "before-window", "after-image", "key"]
)
def test_serialized_batch_refuses_inconsistent_identities(
    tmp_path: Path, damage: str
) -> None:
    with captured_changes(tmp_path) as image:
        batch = read_change_batch(
            image, ChangeWindow(source_id="rsk", after_state_revision=0)
        )
        raw = batch.model_dump(mode="json")
        if damage == "order":
            raw["changes"] = [raw["changes"][1], raw["changes"][0]]
        elif damage == "duplicate":
            raw["changes"] = [raw["changes"][0], raw["changes"][0]]
        elif damage == "source":
            raw["changes"][0]["source_id"] = "other"
        elif damage == "before-window":
            raw["window"]["after_state_revision"] = raw["changes"][0]["state_revision"]
        elif damage == "after-image":
            raw["changes"] = [raw["changes"][0]]
            raw["changes"][0]["state_revision"] = raw["recovery"]["state_revision"] + 1
        else:
            raw["recovery"]["key"] = "terraform.tfstate"
        with pytest.raises(ValidationError):
            _ = ChangeBatch.model_validate_json(json.dumps(raw))


def test_reselection_refuses_a_different_transport_version_even_for_identical_bytes(
    tmp_path: Path,
) -> None:
    with captured_changes(tmp_path) as image:
        batch = read_change_batch(
            image, ChangeWindow(source_id="rsk", after_state_revision=0)
        )
        rotated = replace(
            image, candidate=replace(image.candidate, version_id="another-version")
        )
        with pytest.raises(ProcessingReadError, match="recovery identity changed"):
            _ = require_selected_change(rotated, batch.recovery, batch.changes[0])


def test_file_replacement_during_read_never_returns_a_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with captured_changes(tmp_path) as image:
        real_connect = sqlite3.connect
        opened = 0

        def replacing_connect(
            database: str,
            *,
            uri: bool,
            isolation_level: None,
            timeout: float = 5.0,
        ) -> sqlite3.Connection:
            nonlocal opened
            connection = real_connect(
                database, uri=uri, isolation_level=isolation_level, timeout=timeout
            )
            opened += 1
            if opened == 2:
                # The validator owns the first immutable connection. Rotate the
                # path only after the downstream descriptor-backed reader opens.
                replacement = tmp_path / "same-bytes.sqlite3"
                _ = replacement.write_bytes(image.path.read_bytes())
                _ = replacement.replace(image.path)
            return connection

        monkeypatch.setattr(sqlite3, "connect", replacing_connect)
        with pytest.raises(ProcessingReadError, match="changed during processing read"):
            _ = read_change_batch(
                image, ChangeWindow(source_id="rsk", after_state_revision=0)
            )
