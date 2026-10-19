import base64
import hashlib
import io
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast, final

import pytest
from botocore.exceptions import (  # type: ignore[import-untyped] # pyright: ignore[reportMissingTypeStubs]
    ClientError,
    ReadTimeoutError,
)

from minos.ingest.watchers.control import ControlStore
from minos.ingest.watchers.recovery import (
    PublishedRecoveryImage,
    RecoveryClass,
    RecoveryConflictError,
    RecoveryImageMetadata,
    RecoveryImageRequest,
    RecoveryImageStagingArea,
    RecoveryListingBoundError,
    RecoveryStorageError,
    RecoveryVerificationError,
    S3RecoveryStore,
    recovery_receipt,
)

_BUCKET = "minos-recovery-test"
_CREATED_AT_MS = 1_758_225_600_000


class _Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


@final
class _FakeConfig:
    def __init__(self) -> None:
        self.retries: Mapping[str, object] = {
            "total_max_attempts": 1,
            "mode": "standard",
        }


@final
class _FakeMeta:
    def __init__(self) -> None:
        self.config = _FakeConfig()


@final
class _StoredObject:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.etag = f'"{hashlib.md5(content, usedforsecurity=False).hexdigest()}"'
        self.checksum = base64.b64encode(hashlib.sha256(content).digest()).decode(
            "ascii"
        )


@final
class _FakeS3Client:
    def __init__(self) -> None:
        self.meta = _FakeMeta()
        self.objects: dict[str, _StoredObject] = {}
        self.put_outcomes: list[BaseException | None] = []
        self.put_calls: list[dict[str, object]] = []
        self.head_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.store_before_error = False
        self.override_head_checksum: str | None = None
        self.override_last_modified: datetime | None = None
        self.download_body: object | None = None
        self.repeat_listing_token = False
        self.replace_before_get = False

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        body = cast(_Readable, kwargs["Body"])
        chunks: list[bytes] = []
        while chunk := body.read(7):
            chunks.append(chunk)
        content = b"".join(chunks)
        assert len(content) == kwargs["ContentLength"]
        checksum = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
        assert checksum == kwargs["ChecksumSHA256"]
        call = {key: value for key, value in kwargs.items() if key != "Body"}
        self.put_calls.append(call)
        key = cast(str, kwargs["Key"])
        outcome = (
            self.put_outcomes.pop(0)
            if self.put_outcomes
            else _client_error(412)
            if key in self.objects
            else None
        )
        if outcome is None or self.store_before_error:
            self.objects[key] = _StoredObject(content)
        if outcome is not None:
            raise outcome
        return {"ETag": self.objects[key].etag}

    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        self.head_calls.append(dict(kwargs))
        key = cast(str, kwargs["Key"])
        stored = self.objects.get(key)
        if stored is None:
            raise _client_error(404)
        return self._identity(stored)

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        self.list_calls.append(dict(kwargs))
        prefix = cast(str, kwargs["Prefix"])
        maximum = cast(int, kwargs["MaxKeys"])
        token = kwargs.get("ContinuationToken")
        offset = int(cast(str, token)) if token is not None else 0
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        selected = keys[offset : offset + maximum]
        next_offset = offset + len(selected)
        truncated = next_offset < len(keys)
        response: dict[str, object] = {
            "Contents": [
                {
                    "Key": key,
                    "Size": len(self.objects[key].content),
                    "LastModified": self._last_modified(),
                }
                for key in selected
            ],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextContinuationToken"] = (
                cast(str, token)
                if self.repeat_listing_token and token is not None
                else str(next_offset)
            )
        return response

    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        self.get_calls.append(dict(kwargs))
        key = cast(str, kwargs["Key"])
        stored = self.objects.get(key)
        if stored is None:
            raise _client_error(404)
        if self.replace_before_get:
            stored.etag = '"' + "f" * 32 + '"'
        if kwargs.get("IfMatch") not in (None, stored.etag):
            raise _client_error(412)
        body = self.download_body or io.BytesIO(stored.content)
        return {**self._identity(stored), "Body": body}

    def _identity(self, stored: _StoredObject) -> dict[str, object]:
        return {
            "ContentLength": len(stored.content),
            # R2 sends no ChecksumType or VersionId for single-part objects.
            "ChecksumSHA256": self.override_head_checksum or stored.checksum,
            "ETag": stored.etag,
            "LastModified": self._last_modified(),
        }

    def _last_modified(self) -> datetime:
        return self.override_last_modified or datetime.fromtimestamp(
            (_CREATED_AT_MS + 1000) / 1000,
            tz=UTC,
        )


@final
class _InterruptingBody:
    def read(self, _size: int = -1, /) -> bytes:
        raise KeyboardInterrupt

    def close(self) -> None:
        raise RuntimeError("injected stream cleanup failure")


def test_publish_uses_conditional_single_part_put_and_checksum_head(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    remote = S3RecoveryStore(
        client,
        bucket=_BUCKET,
        clock_ms=_clock_ms,
    )
    store = ControlStore.open(tmp_path / "control.sqlite3")
    staging = _staging(tmp_path / "staging")
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", _CREATED_AT_MS),
        ) as image:
            published = remote.publish(image)

        assert client.put_calls == [
            {
                "Bucket": _BUCKET,
                "Key": published.key,
                "ContentLength": published.size_bytes,
                "ChecksumAlgorithm": "SHA256",
                "ChecksumSHA256": base64.b64encode(
                    bytes.fromhex(published.sha256)
                ).decode("ascii"),
                "IfNoneMatch": "*",
            }
        ]
        assert client.head_calls == [
            {
                "Bucket": _BUCKET,
                "Key": published.key,
                "ChecksumMode": "ENABLED",
            }
        ]
    finally:
        store.close()


def test_published_recovery_image_round_trips_by_key_and_checksum(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    remote = S3RecoveryStore(
        client,
        bucket=_BUCKET,
        clock_ms=_clock_ms,
    )
    store = ControlStore.open(tmp_path / "control.sqlite3")
    staging = _staging(tmp_path / "staging")
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", _CREATED_AT_MS),
        ) as image:
            published = remote.publish(image)

        with remote.download_published_verified(published, staging) as verified:
            assert verified.candidate.sha256 == published.sha256
            assert verified.candidate.metadata == published.metadata
            assert verified.path.is_file()

        assert list((tmp_path / "staging").iterdir()) == []
    finally:
        store.close()


def test_recovery_probe_round_trip_uses_expiring_probe_namespace(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    remote = S3RecoveryStore(
        client,
        bucket=_BUCKET,
        clock_ms=_clock_ms,
    )
    store = ControlStore.open(tmp_path / "control.sqlite3")
    staging = _staging(tmp_path / "staging")
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", _CREATED_AT_MS),
        ) as image:
            published = remote.publish_probe(image)

        assert published.key.startswith("probes/recovery/2025-09-18/")
        assert published.key.endswith(f"/{published.sha256}.sqlite3")
        with remote.download_probe_verified(published, staging) as verified:
            assert verified.candidate.metadata == published.metadata
            assert verified.candidate.key == published.key
    finally:
        store.close()


def test_publish_reuses_412_and_retries_only_bounded_409_conflicts(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    delays: list[float] = []
    remote = S3RecoveryStore(
        client,
        bucket=_BUCKET,
        maximum_conflict_attempts=3,
        conflict_delay_seconds=0.25,
        clock_ms=_clock_ms,
        sleep=delays.append,
    )
    store = ControlStore.open(tmp_path / "control.sqlite3")
    staging = _staging(tmp_path / "staging")
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", _CREATED_AT_MS),
        ) as image:
            first = remote.publish(image)
            repeated = remote.publish(image)
            client.objects.clear()
            client.put_outcomes = [_client_error(409), _client_error(409), None]
            retried = remote.publish(image)
        assert repeated == first
        assert retried.sha256 == first.sha256
        assert delays == [0.25, 0.25]
    finally:
        store.close()


def test_publish_fails_closed_on_conflict_checksum_and_ambiguous_timeout(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    remote = S3RecoveryStore(
        client,
        bucket=_BUCKET,
        maximum_conflict_attempts=2,
        conflict_delay_seconds=0,
        clock_ms=_clock_ms,
    )
    store = ControlStore.open(tmp_path / "control.sqlite3")
    staging = _staging(tmp_path / "staging")
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", _CREATED_AT_MS),
        ) as image:
            client.put_outcomes = [_client_error(409), _client_error(409)]
            with pytest.raises(RecoveryConflictError, match="exhausted"):
                _ = remote.publish(image)

            client.put_outcomes = [ReadTimeoutError(endpoint_url="https://s3.test")]
            with pytest.raises(RecoveryStorageError, match="could not be resolved"):
                _ = remote.publish(image)

            client.put_outcomes = [None]
            client.override_head_checksum = base64.b64encode(b"x" * 32).decode("ascii")
            with pytest.raises(RecoveryVerificationError, match="does not match"):
                _ = remote.publish(image)
    finally:
        store.close()


def test_listing_is_paginated_bounded_and_rejects_repeated_tokens(
    tmp_path: Path,
) -> None:
    client, remote, store, _staging = _archive_two_images(tmp_path)
    try:
        listed = remote.list_images("daily", maximum_objects=2, page_size=1)
        assert len(listed) == 2
        assert len(client.list_calls) == 2

        with pytest.raises(RecoveryListingBoundError, match="object bound"):
            _ = remote.list_images("daily", maximum_objects=1)

        existing_key = next(iter(client.objects))
        third_key = f"{existing_key.rsplit('/', 1)[0]}/{'c' * 64}.sqlite3"
        client.objects[third_key] = client.objects[existing_key]
        client.repeat_listing_token = True
        with pytest.raises(RecoveryStorageError, match="token repeated"):
            _ = remote.list_images("daily", maximum_objects=3, page_size=1)
    finally:
        store.close()


def test_download_is_etag_pinned_and_rejects_replacement_or_checksum_change(
    tmp_path: Path,
) -> None:
    client, remote, store, staging = _archive_two_images(tmp_path)
    try:
        listed = remote.list_images("daily", maximum_objects=2)[0]
        with remote.download_verified(listed, staging) as downloaded:
            assert downloaded.path.exists()
            selected = downloaded.candidate
        assert client.get_calls[-1]["IfMatch"] == client.objects[selected.key].etag

        # An object replaced between HEAD and GET never reaches the staging file.
        client.replace_before_get = True
        with pytest.raises(RecoveryStorageError, match="ETag-pinned"):
            with remote.download_verified(selected, staging):
                pass
        client.replace_before_get = False

        current = remote.list_images("daily", maximum_objects=2)[0]
        client.override_head_checksum = base64.b64encode(b"z" * 32).decode("ascii")
        with pytest.raises(RecoveryVerificationError, match="does not match"):
            with remote.download_verified(current, staging):
                pass
    finally:
        store.close()


def test_selection_uses_revision_time_class_rank_and_sha_deterministically(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    remote = S3RecoveryStore(
        client,
        bucket=_BUCKET,
        clock_ms=_clock_ms,
    )
    store = ControlStore.open(tmp_path / "control.sqlite3")
    staging = _staging(tmp_path / "staging")
    try:
        for recovery_class in ("monthly", "weekly", "daily"):
            with staging.create(
                store,
                RecoveryImageRequest(recovery_class, _CREATED_AT_MS),
            ) as image:
                _ = remote.publish(image)
        bounds: dict[RecoveryClass, int] = {
            "run": 1,
            "daily": 4,
            "weekly": 4,
            "monthly": 4,
        }
        selected = remote.select_newest(bounds, staging, page_size=1)
        assert selected.metadata.recovery_class == "daily"

        with staging.create(
            store,
            RecoveryImageRequest("monthly", _CREATED_AT_MS + 1),
        ) as image:
            newer = remote.publish(image)
        selected = remote.select_newest(bounds, staging, page_size=1)
        assert selected.key == newer.key
        assert all("IfMatch" in call for call in client.get_calls)
    finally:
        store.close()


def test_recovery_receipt_requires_run_metadata() -> None:
    metadata = RecoveryImageMetadata(
        "daily",
        1,
        1,
        "a" * 64,
        _CREATED_AT_MS,
        None,
        None,
    )
    published = PublishedRecoveryImage("key", "b" * 64, 1, metadata)
    with pytest.raises(RecoveryVerificationError, match="run image"):
        _ = recovery_receipt(published)


def test_selection_enforces_object_request_and_time_budgets(tmp_path: Path) -> None:
    client = _FakeS3Client()
    staging = _staging(tmp_path / "staging")
    oversized: dict[RecoveryClass, int] = {
        "run": 65,
        "daily": 64,
        "weekly": 64,
        "monthly": 64,
    }
    remote = S3RecoveryStore(
        client,
        bucket=_BUCKET,
        clock_ms=_clock_ms,
    )
    with pytest.raises(RecoveryStorageError, match="object bound"):
        _ = remote.select_newest(oversized, staging)
    assert client.list_calls == []

    bounds: dict[RecoveryClass, int] = {
        "run": 1,
        "daily": 1,
        "weekly": 1,
        "monthly": 1,
    }
    request_bounded = S3RecoveryStore(
        client,
        bucket=_BUCKET,
        maximum_selection_requests=1,
        clock_ms=_clock_ms,
    )
    with pytest.raises(RecoveryStorageError, match="request bound"):
        _ = request_bounded.select_newest(bounds, staging)

    monotonic_values = iter((0.0, 0.0, 1.0))
    time_bounded = S3RecoveryStore(
        _FakeS3Client(),
        bucket=_BUCKET,
        selection_timeout_seconds=0.5,
        clock_ms=_clock_ms,
        monotonic=lambda: next(monotonic_values),
    )
    with pytest.raises(RecoveryStorageError, match="deadline expired"):
        _ = time_bounded.select_newest(bounds, staging)


def test_s3_timestamps_are_checked_at_list_head_and_publish(tmp_path: Path) -> None:
    client, remote, store, staging = _archive_two_images(tmp_path)
    try:
        client.override_last_modified = datetime.fromtimestamp(
            (_clock_ms() + 6 * 60 * 1000) / 1000,
            tz=UTC,
        )
        with pytest.raises(RecoveryVerificationError, match="future"):
            _ = remote.list_images("daily", maximum_objects=2)

        client.override_last_modified = None
        listed = remote.list_images("daily", maximum_objects=2)[0]
        client.override_last_modified = datetime.fromtimestamp(
            (listed.last_modified_ms + 2000) / 1000,
            tz=UTC,
        )
        with pytest.raises(RecoveryVerificationError, match="timestamp changed"):
            with remote.download_verified(listed, staging):
                pass

        client.override_last_modified = datetime.fromtimestamp(
            (_CREATED_AT_MS + 16 * 60 * 1000) / 1000,
            tz=UTC,
        )
        late_remote = S3RecoveryStore(
            client,
            bucket=_BUCKET,
            clock_ms=lambda: _CREATED_AT_MS + 20 * 60 * 1000,
        )
        with staging.create(
            store,
            RecoveryImageRequest("weekly", _CREATED_AT_MS),
        ) as image:
            with pytest.raises(RecoveryVerificationError, match="publication window"):
                _ = late_remote.publish(image)
    finally:
        store.close()


def test_download_cleanup_does_not_mask_control_flow_exception(
    tmp_path: Path,
) -> None:
    client, remote, store, staging = _archive_two_images(tmp_path)
    try:
        listed = remote.list_images("daily", maximum_objects=2)[0]
        client.download_body = _InterruptingBody()
        with pytest.raises(KeyboardInterrupt) as captured:
            with remote.download_verified(listed, staging):
                pass
        assert any("stream cleanup" in note for note in captured.value.__notes__)
    finally:
        store.close()


def _archive_two_images(
    tmp_path: Path,
) -> tuple[_FakeS3Client, S3RecoveryStore, ControlStore, RecoveryImageStagingArea]:
    client = _FakeS3Client()
    remote = S3RecoveryStore(
        client,
        bucket=_BUCKET,
        clock_ms=_clock_ms,
    )
    store = ControlStore.open(tmp_path / "control.sqlite3")
    staging = _staging(tmp_path / "staging")
    for offset in (0, 1):
        with staging.create(
            store,
            RecoveryImageRequest("daily", _CREATED_AT_MS + offset),
        ) as image:
            _ = remote.publish(image)
    return client, remote, store, staging


def _client_error(status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": str(status), "Message": "injected"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "S3Operation",
    )


def _clock_ms() -> int:
    return _CREATED_AT_MS + 1000


def _staging(path: Path) -> RecoveryImageStagingArea:
    return RecoveryImageStagingArea(path, clock_ms=_clock_ms)
