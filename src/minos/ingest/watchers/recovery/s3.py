# pyright: reportMissingTypeStubs=false

import base64
import binascii
import hashlib
import io
import math
import os
import re
import secrets
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO, Never, Protocol, Self, TypeIs, cast, final, override

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ReadTimeoutError,
)

from minos.ingest.watchers.control import RecoveryReceipt
from minos.ingest.watchers.recovery.image import (
    RecoveryImageStagingArea,
    validate_recovery_image,
)
from minos.ingest.watchers.recovery.keys import (
    parse_recovery_key,
    recovery_key,
    recovery_prefix,
)
from minos.ingest.watchers.recovery.models import (
    MAX_RECOVERY_IMAGE_SIZE_BYTES,
    ListedRecoveryImage,
    PublishedRecoveryImage,
    RecoveryClass,
    RecoveryConflictError,
    RecoveryListingBoundError,
    RecoveryStorageError,
    RecoveryVerificationError,
    StagedRecoveryImage,
    VerifiedRecoveryCandidate,
    VerifiedRecoveryImage,
)

_AWS_ACCOUNT_ID = re.compile(r"[0-9]{12}")
_S3_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
_RECOVERY_PROBE_KEY = re.compile(
    r"\Aprobes/recovery/[0-9]{4}-[0-9]{2}-[0-9]{2}/"
    + r"[0-9a-f]{32}/([0-9a-f]{64})\.sqlite3\Z"
)
_MAX_LIST_OBJECTS_PER_CLASS = 2048
_MAX_TOTAL_LIST_OBJECTS = 4096
_MAX_LIST_PAGE_SIZE = 256
_MAX_SELECTION_OBJECTS = 256
_DEFAULT_SELECTION_REQUESTS = _MAX_SELECTION_OBJECTS * 3 + 4
_MAX_SELECTION_REQUESTS = 1024
_DEFAULT_SELECTION_TIMEOUT_SECONDS = 900.0
_MAX_SELECTION_TIMEOUT_SECONDS = 3600.0
# LastModified is S3 server time. Allow modest host skew, but require an upload
# to follow image creation promptly. Listing captures host time once per class.
_MAX_CREATION_TO_S3_DELAY_MS = 15 * 60 * 1000
_MAX_CLOCK_SKEW_MS = 5 * 60 * 1000
_LIST_HEAD_TIMESTAMP_TOLERANCE_MS = 1000
_MAX_TIMESTAMP_MS = 253_402_300_799_999
_STREAM_CHUNK_BYTES = 1024 * 1024
_MAX_STREAM_READS = MAX_RECOVERY_IMAGE_SIZE_BYTES // 4096 + 2


class _StreamingBody(Protocol):
    def read(self, amount: int | None = None) -> object: ...

    def close(self) -> None: ...


class _S3Client(Protocol):
    @property
    def meta(self) -> _ClientMeta: ...

    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...


class _ClientConfig(Protocol):
    @property
    def retries(self) -> Mapping[str, object] | None: ...


class _ClientMeta(Protocol):
    @property
    def config(self) -> _ClientConfig: ...


class _Boto3(Protocol):
    def client(
        self,
        service_name: str,
        *,
        region_name: str,
        config: object,
    ) -> object: ...


class _ClientErrorLike(Protocol):
    response: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _HeadIdentity:
    version_id: str
    sha256: str
    size_bytes: int
    last_modified_ms: int


@dataclass(slots=True)
class _SelectionBudget:
    deadline_monotonic: float
    remaining_requests: int
    monotonic: Callable[[], float]

    def check(self) -> None:
        current = self.monotonic()
        if (
            not _is_real_number(current)
            or not math.isfinite(current)
            or current >= self.deadline_monotonic
        ):
            raise RecoveryStorageError("recovery selection deadline expired")

    def consume_request(self) -> None:
        self.check()
        if self.remaining_requests < 1:
            raise RecoveryStorageError("recovery selection request bound exhausted")
        self.remaining_requests -= 1


@final
class S3RecoveryStore:
    def __init__(
        self,
        client: _S3Client,
        *,
        bucket: str,
        expected_bucket_owner: str,
        maximum_conflict_attempts: int = 3,
        conflict_delay_seconds: float = 0.05,
        selection_timeout_seconds: float = _DEFAULT_SELECTION_TIMEOUT_SECONDS,
        maximum_selection_requests: int = _DEFAULT_SELECTION_REQUESTS,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        _validate_s3_configuration(
            client,
            bucket,
            expected_bucket_owner,
            maximum_conflict_attempts,
            conflict_delay_seconds,
            selection_timeout_seconds,
            maximum_selection_requests,
        )
        self._client = client
        self._bucket = bucket
        self._expected_bucket_owner = expected_bucket_owner
        self._maximum_conflict_attempts = maximum_conflict_attempts
        self._conflict_delay_seconds = conflict_delay_seconds
        self._selection_timeout_seconds = float(selection_timeout_seconds)
        self._maximum_selection_requests = maximum_selection_requests
        self._clock_ms = clock_ms
        self._monotonic = monotonic
        self._sleep = sleep

    @classmethod
    def create(
        cls,
        *,
        bucket: str,
        region: str,
        expected_bucket_owner: str,
        maximum_conflict_attempts: int = 3,
        conflict_delay_seconds: float = 0.05,
        selection_timeout_seconds: float = _DEFAULT_SELECTION_TIMEOUT_SECONDS,
        maximum_selection_requests: int = _DEFAULT_SELECTION_REQUESTS,
        connect_timeout_seconds: float = 10,
        read_timeout_seconds: float = 60,
    ) -> Self:
        if not region or len(region) > 64:
            raise RecoveryStorageError("S3 region is unsupported")
        _validate_timeout(connect_timeout_seconds, "S3 connect timeout")
        _validate_timeout(read_timeout_seconds, "S3 read timeout")
        boto3_api = cast(_Boto3, boto3)  # pyright: ignore[reportInvalidCast]
        try:
            client = cast(
                _S3Client,
                boto3_api.client(
                    "s3",
                    region_name=region,
                    config=Config(
                        connect_timeout=connect_timeout_seconds,
                        read_timeout=read_timeout_seconds,
                        retries={"total_max_attempts": 1, "mode": "standard"},
                    ),
                ),
            )
        except BotoCoreError as error:
            raise RecoveryStorageError("S3 recovery client creation failed") from error
        return cls(
            client,
            bucket=bucket,
            expected_bucket_owner=expected_bucket_owner,
            maximum_conflict_attempts=maximum_conflict_attempts,
            conflict_delay_seconds=conflict_delay_seconds,
            selection_timeout_seconds=selection_timeout_seconds,
            maximum_selection_requests=maximum_selection_requests,
        )

    def publish(self, image: StagedRecoveryImage) -> PublishedRecoveryImage:
        key = recovery_key(image.metadata, image.sha256)
        return self._publish_to_key(image, key)

    def publish_probe(self, image: StagedRecoveryImage) -> PublishedRecoveryImage:
        created_at = datetime.fromtimestamp(
            image.metadata.created_at_ms / 1000,
            tz=UTC,
        )
        key = (
            f"probes/recovery/{created_at.date().isoformat()}/"
            f"{secrets.token_hex(16)}/{image.sha256}.sqlite3"
        )
        return self._publish_to_key(image, key)

    def _publish_to_key(
        self,
        image: StagedRecoveryImage,
        key: str,
    ) -> PublishedRecoveryImage:
        checksum_base64 = _encode_sha256(image.sha256)
        with image.open() as source:
            body = _BoundedBody(source, image.size_bytes)
            for attempt in range(1, self._maximum_conflict_attempts + 1):
                body.reset()
                try:
                    _ = self._client.put_object(
                        Bucket=self._bucket,
                        Key=key,
                        ContentLength=image.size_bytes,
                        ChecksumAlgorithm="SHA256",
                        ChecksumSHA256=checksum_base64,
                        IfNoneMatch="*",
                        ExpectedBucketOwner=self._expected_bucket_owner,
                        Body=body,
                    )
                    body.verify_complete()
                    break
                except ClientError as error:
                    if _client_error_code(error) in {"BadDigest", "InvalidDigest"}:
                        raise RecoveryVerificationError(
                            "S3 rejected the recovery image checksum"
                        ) from error
                    status = _client_error_status(error)
                    if status == 412:
                        break
                    if status != 409:
                        raise RecoveryStorageError(
                            "S3 conditional recovery write failed"
                        ) from error
                    if attempt == self._maximum_conflict_attempts:
                        raise RecoveryConflictError(
                            "S3 recovery write exhausted conflict retries"
                        ) from error
                    self._sleep(self._conflict_delay_seconds)
                except (ConnectionClosedError, ReadTimeoutError) as error:
                    return self._published_after_ambiguous_write(image, key, error)
                except BotoCoreError as error:
                    if body.verification_error is not None:
                        raise body.verification_error from error
                    raise RecoveryStorageError(
                        "S3 recovery write failed before its outcome became ambiguous"
                    ) from error
        head = self._head(key)
        _require_object_identity(head, image.sha256, image.size_bytes)
        _validate_s3_timestamps(
            image.metadata.created_at_ms,
            head.last_modified_ms,
            self._clock_ms(),
        )
        return PublishedRecoveryImage(
            key,
            head.version_id,
            image.sha256,
            image.size_bytes,
            image.metadata,
        )

    @contextmanager
    def download_probe_verified(
        self,
        published: PublishedRecoveryImage,
        staging: RecoveryImageStagingArea,
    ) -> Generator[VerifiedRecoveryImage]:
        match = _RECOVERY_PROBE_KEY.fullmatch(published.key)
        if match is None or match.group(1) != published.sha256:
            raise RecoveryVerificationError("recovery probe key is invalid")
        head = self._head(published.key, version_id=published.version_id)
        _require_object_identity(head, published.sha256, published.size_bytes)
        captured_at_ms = self._clock_ms()
        _validate_captured_time(captured_at_ms)
        with staging.reserve_download() as (path, descriptor):
            self._download_to_descriptor(
                published.key,
                head,
                descriptor,
                budget=None,
            )
            metadata = validate_recovery_image(
                path,
                expected_key=None,
                expected_sha256=head.sha256,
                expected_size_bytes=head.size_bytes,
                validation_time_ms=captured_at_ms,
            )
            _validate_s3_timestamps(
                metadata.created_at_ms,
                head.last_modified_ms,
                captured_at_ms,
            )
            if metadata != published.metadata:
                raise RecoveryVerificationError(
                    "downloaded recovery probe metadata differs from the publication"
                )
            candidate = VerifiedRecoveryCandidate(
                published.key,
                head.version_id,
                head.sha256,
                head.size_bytes,
                head.last_modified_ms,
                metadata,
            )
            yield VerifiedRecoveryImage(path, candidate)

    def list_images(
        self,
        recovery_class: RecoveryClass,
        *,
        maximum_objects: int,
        page_size: int = _MAX_LIST_PAGE_SIZE,
        _budget: _SelectionBudget | None = None,
    ) -> tuple[ListedRecoveryImage, ...]:
        _validate_listing_bounds(maximum_objects, page_size)
        prefix = recovery_prefix(recovery_class)
        captured_at_ms = self._clock_ms()
        _validate_captured_time(captured_at_ms)
        results: list[ListedRecoveryImage] = []
        continuation_token: str | None = None
        seen_tokens: set[str] = set()
        maximum_pages = math.ceil(maximum_objects / page_size) + 1
        for _page in range(maximum_pages):
            response = self._list_page(
                prefix,
                page_size,
                continuation_token,
                budget=_budget,
            )
            contents = response.get("Contents", [])
            if not isinstance(contents, list):
                raise RecoveryStorageError("S3 recovery listing contents are malformed")
            for raw_item in cast(list[object], contents):
                results.append(
                    _parse_listed_item(
                        raw_item,
                        prefix,
                        captured_at_ms=captured_at_ms,
                    )
                )
                if len(results) > maximum_objects:
                    raise RecoveryListingBoundError(
                        "S3 recovery listing exceeds its object bound"
                    )
            truncated = response.get("IsTruncated")
            if truncated is False:
                _reject_duplicate_keys(results)
                return tuple(sorted(results, key=lambda item: item.key))
            if truncated is not True:
                raise RecoveryStorageError(
                    "S3 recovery listing truncation state is malformed"
                )
            token = response.get("NextContinuationToken")
            if not isinstance(token, str) or not token or len(token) > 2048:
                raise RecoveryStorageError("S3 recovery listing token is malformed")
            if token in seen_tokens:
                raise RecoveryStorageError("S3 recovery listing token repeated")
            seen_tokens.add(token)
            continuation_token = token
        raise RecoveryListingBoundError("S3 recovery listing exceeded its page bound")

    def list_all_images(
        self,
        bounds: Mapping[RecoveryClass, int],
        *,
        page_size: int = _MAX_LIST_PAGE_SIZE,
        _budget: _SelectionBudget | None = None,
    ) -> tuple[ListedRecoveryImage, ...]:
        if set(bounds) != {"run", "daily", "weekly", "monthly"}:
            raise RecoveryStorageError("recovery listing bounds must cover every class")
        for maximum_objects in bounds.values():
            _validate_listing_bounds(maximum_objects, page_size)
        if sum(bounds.values()) > _MAX_TOTAL_LIST_OBJECTS:
            raise RecoveryStorageError("recovery total listing bound is unsupported")
        results: list[ListedRecoveryImage] = []
        for recovery_class in ("run", "daily", "weekly", "monthly"):
            results.extend(
                self.list_images(
                    recovery_class,
                    maximum_objects=bounds[recovery_class],
                    page_size=page_size,
                    _budget=_budget,
                )
            )
        _reject_duplicate_keys(results)
        return tuple(results)

    @contextmanager
    def download_verified(
        self,
        listed: ListedRecoveryImage | VerifiedRecoveryCandidate,
        staging: RecoveryImageStagingArea,
        *,
        _budget: _SelectionBudget | None = None,
    ) -> Generator[VerifiedRecoveryImage]:
        version_id = (
            listed.version_id if isinstance(listed, VerifiedRecoveryCandidate) else None
        )
        head = self._head(listed.key, version_id=version_id, budget=_budget)
        captured_at_ms = self._clock_ms()
        _validate_captured_time(captured_at_ms)
        if head.size_bytes != listed.size_bytes:
            raise RecoveryVerificationError("S3 recovery listing byte count changed")
        if (
            isinstance(listed, ListedRecoveryImage)
            and abs(head.last_modified_ms - listed.last_modified_ms)
            > _LIST_HEAD_TIMESTAMP_TOLERANCE_MS
        ):
            raise RecoveryVerificationError(
                "S3 recovery listing timestamp changed before download"
            )
        parsed = parse_recovery_key(listed.key)
        _require_object_identity(head, parsed.sha256, listed.size_bytes)
        with staging.reserve_download() as (path, descriptor):
            self._download_to_descriptor(
                listed.key,
                head,
                descriptor,
                budget=_budget,
            )
            metadata = validate_recovery_image(
                path,
                expected_key=listed.key,
                expected_sha256=head.sha256,
                expected_size_bytes=head.size_bytes,
                validation_time_ms=captured_at_ms,
                progress_check=_budget.check if _budget is not None else None,
            )
            _validate_s3_timestamps(
                metadata.created_at_ms,
                head.last_modified_ms,
                captured_at_ms,
            )
            candidate = VerifiedRecoveryCandidate(
                listed.key,
                head.version_id,
                head.sha256,
                head.size_bytes,
                head.last_modified_ms,
                metadata,
            )
            if isinstance(listed, VerifiedRecoveryCandidate) and candidate != listed:
                raise RecoveryVerificationError("version-pinned recovery image changed")
            yield VerifiedRecoveryImage(path, candidate)

    @contextmanager
    def download_published_verified(
        self,
        published: PublishedRecoveryImage,
        staging: RecoveryImageStagingArea,
    ) -> Generator[VerifiedRecoveryImage]:
        parsed = parse_recovery_key(published.key)
        if parsed.sha256 != published.sha256:
            raise RecoveryVerificationError(
                "published recovery key does not match its checksum"
            )
        head = self._head(published.key, version_id=published.version_id)
        _require_object_identity(head, published.sha256, published.size_bytes)
        candidate = VerifiedRecoveryCandidate(
            published.key,
            head.version_id,
            head.sha256,
            head.size_bytes,
            head.last_modified_ms,
            published.metadata,
        )
        with self.download_verified(candidate, staging) as verified:
            if verified.candidate.metadata != published.metadata:
                raise RecoveryVerificationError(
                    "downloaded recovery metadata differs from the publication"
                )
            yield verified

    def select_newest(
        self,
        bounds: Mapping[RecoveryClass, int],
        staging: RecoveryImageStagingArea,
        *,
        page_size: int = _MAX_LIST_PAGE_SIZE,
    ) -> VerifiedRecoveryCandidate:
        if set(bounds) != {"run", "daily", "weekly", "monthly"}:
            raise RecoveryStorageError("recovery listing bounds must cover every class")
        for maximum_objects in bounds.values():
            _validate_listing_bounds(maximum_objects, page_size)
        if sum(bounds.values()) > _MAX_SELECTION_OBJECTS:
            raise RecoveryStorageError("recovery selection object bound is unsupported")
        started = self._monotonic()
        if not _is_real_number(started) or not math.isfinite(started):
            raise RecoveryStorageError("recovery selection clock is invalid")
        budget = _SelectionBudget(
            started + self._selection_timeout_seconds,
            self._maximum_selection_requests,
            self._monotonic,
        )
        verified: list[VerifiedRecoveryCandidate] = []
        for listed in self.list_all_images(
            bounds,
            page_size=page_size,
            _budget=budget,
        ):
            budget.check()
            try:
                with self.download_verified(
                    listed,
                    staging,
                    _budget=budget,
                ) as downloaded:
                    verified.append(downloaded.candidate)
            except RecoveryVerificationError:
                continue
        budget.check()
        if not verified:
            raise RecoveryVerificationError("no valid recovery image is available")
        return max(verified, key=_selection_key)

    def _published_after_ambiguous_write(
        self,
        image: StagedRecoveryImage,
        key: str,
        write_error: BaseException,
    ) -> PublishedRecoveryImage:
        try:
            head = self._head(key)
            _require_object_identity(head, image.sha256, image.size_bytes)
            _validate_s3_timestamps(
                image.metadata.created_at_ms,
                head.last_modified_ms,
                self._clock_ms(),
            )
        except RecoveryVerificationError:
            raise
        except RecoveryStorageError:
            raise RecoveryStorageError(
                "S3 recovery write outcome could not be resolved"
            ) from write_error
        return PublishedRecoveryImage(
            key,
            head.version_id,
            image.sha256,
            image.size_bytes,
            image.metadata,
        )

    def _head(
        self,
        key: str,
        *,
        version_id: str | None = None,
        budget: _SelectionBudget | None = None,
    ) -> _HeadIdentity:
        parameters: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": key,
            "ChecksumMode": "ENABLED",
            "ExpectedBucketOwner": self._expected_bucket_owner,
        }
        if version_id is not None:
            _validate_version_id(version_id)
            parameters["VersionId"] = version_id
        try:
            if budget is not None:
                budget.consume_request()
            response = self._client.head_object(**parameters)
        except (BotoCoreError, ClientError) as error:
            raise RecoveryStorageError(
                "S3 recovery verification request failed"
            ) from error
        return _parse_object_identity(response)

    def _list_page(
        self,
        prefix: str,
        page_size: int,
        continuation_token: str | None,
        *,
        budget: _SelectionBudget | None,
    ) -> Mapping[str, object]:
        parameters: dict[str, object] = {
            "Bucket": self._bucket,
            "Prefix": prefix,
            "MaxKeys": page_size,
            "ExpectedBucketOwner": self._expected_bucket_owner,
        }
        if continuation_token is not None:
            parameters["ContinuationToken"] = continuation_token
        try:
            if budget is not None:
                budget.consume_request()
            return self._client.list_objects_v2(**parameters)
        except (BotoCoreError, ClientError) as error:
            raise RecoveryStorageError("S3 recovery listing failed") from error

    def _download_to_descriptor(
        self,
        key: str,
        head: _HeadIdentity,
        descriptor: int,
        *,
        budget: _SelectionBudget | None,
    ) -> None:
        try:
            if budget is not None:
                budget.consume_request()
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=key,
                VersionId=head.version_id,
                ChecksumMode="ENABLED",
                ExpectedBucketOwner=self._expected_bucket_owner,
            )
        except (BotoCoreError, ClientError) as error:
            raise RecoveryStorageError(
                "S3 version-pinned recovery download failed"
            ) from error
        body = response.get("Body")
        if body is None or not hasattr(body, "read") or not hasattr(body, "close"):
            body_error = RecoveryStorageError("S3 recovery download body is missing")
            if body is not None and hasattr(body, "close"):
                _close_stream(cast(_StreamingBody, body), body_error)
            raise body_error
        stream = cast(_StreamingBody, body)
        try:
            downloaded = _parse_object_identity(response)
        except BaseException as error:
            _close_stream(stream, error)
            raise
        if downloaded != head:
            identity_error = RecoveryVerificationError(
                "S3 recovery download identity differs from head"
            )
            _close_stream(stream, identity_error)
            raise identity_error
        try:
            digest, size_bytes = _stream_to_descriptor(
                stream,
                descriptor,
                maximum_size_bytes=head.size_bytes,
                progress_check=budget.check if budget is not None else None,
            )
        except (BotoCoreError, OSError) as cause:
            download_error = RecoveryStorageError("S3 recovery download failed")
            _close_stream(stream, download_error)
            raise download_error from cause
        except BaseException as error:
            _close_stream(stream, error)
            raise
        else:
            _close_stream(stream, None)
        try:
            os.fsync(descriptor)
        except OSError as error:
            raise RecoveryStorageError("recovery download fsync failed") from error
        if digest != head.sha256 or size_bytes != head.size_bytes:
            raise RecoveryVerificationError(
                "downloaded recovery image identity differs"
            )


def recovery_receipt(image: PublishedRecoveryImage) -> RecoveryReceipt:
    metadata = image.metadata
    if (
        metadata.recovery_class != "run"
        or metadata.run_id is None
        or metadata.committed_state_revision is None
    ):
        raise RecoveryVerificationError(
            "only a run image can produce a recovery receipt"
        )
    return RecoveryReceipt(
        run_id=metadata.run_id,
        committed_state_revision=metadata.committed_state_revision,
        state_revision=metadata.state_revision,
        sha256=image.sha256,
        size_bytes=image.size_bytes,
        archive_key=image.key,
        version_id=image.version_id,
        verified_sha256=image.sha256,
        verified_size_bytes=image.size_bytes,
    )


def _selection_key(candidate: VerifiedRecoveryCandidate) -> tuple[int, int, int, str]:
    rank = {"monthly": 0, "weekly": 1, "daily": 2, "run": 3}
    metadata = candidate.metadata
    return (
        metadata.state_revision,
        metadata.created_at_ms,
        rank[metadata.recovery_class],
        candidate.sha256,
    )


def _parse_object_identity(response: Mapping[str, object]) -> _HeadIdentity:
    size_bytes = response.get("ContentLength")
    checksum = response.get("ChecksumSHA256")
    checksum_type = response.get("ChecksumType")
    version_id = response.get("VersionId")
    last_modified = response.get("LastModified")
    if (
        not _is_strict_integer(size_bytes)
        or size_bytes < 1
        or size_bytes > MAX_RECOVERY_IMAGE_SIZE_BYTES
        or not isinstance(checksum, str)
        or checksum_type != "FULL_OBJECT"
        or not isinstance(version_id, str)
        or not isinstance(last_modified, datetime)
        or last_modified.tzinfo is None
    ):
        raise RecoveryVerificationError("S3 recovery identity response is incomplete")
    _validate_version_id(version_id)
    last_modified_ms = _datetime_to_timestamp_ms(last_modified)
    return _HeadIdentity(
        version_id,
        _decode_sha256(checksum),
        size_bytes,
        last_modified_ms,
    )


def _parse_listed_item(
    raw_item: object,
    prefix: str,
    *,
    captured_at_ms: int,
) -> ListedRecoveryImage:
    if not isinstance(raw_item, Mapping):
        raise RecoveryStorageError("S3 recovery listing item is malformed")
    item = cast(Mapping[str, object], raw_item)
    key = item.get("Key")
    size_bytes = item.get("Size")
    last_modified = item.get("LastModified")
    if (
        not isinstance(key, str)
        or not key.startswith(prefix)
        or len(key) > 256
        or not _is_strict_integer(size_bytes)
        or size_bytes < 1
        or size_bytes > MAX_RECOVERY_IMAGE_SIZE_BYTES
        or not isinstance(last_modified, datetime)
        or last_modified.tzinfo is None
    ):
        raise RecoveryStorageError("S3 recovery listing item is incomplete")
    last_modified_ms = _datetime_to_timestamp_ms(last_modified)
    _validate_not_future(last_modified_ms, captured_at_ms, "S3 recovery object")
    return ListedRecoveryImage(key, size_bytes, last_modified_ms)


def _validate_s3_timestamps(
    created_at_ms: int,
    last_modified_ms: int,
    captured_at_ms: int,
) -> None:
    _validate_not_future(created_at_ms, captured_at_ms, "recovery image creation")
    _validate_not_future(last_modified_ms, captured_at_ms, "S3 recovery object")
    if last_modified_ms < created_at_ms - _MAX_CLOCK_SKEW_MS:
        raise RecoveryVerificationError(
            "S3 recovery object predates its embedded creation time"
        )
    if last_modified_ms > created_at_ms + _MAX_CREATION_TO_S3_DELAY_MS:
        raise RecoveryVerificationError(
            "S3 recovery object timestamp exceeds the publication window"
        )


def _validate_not_future(value_ms: int, captured_at_ms: int, label: str) -> None:
    _validate_captured_time(captured_at_ms)
    if value_ms < 1 or value_ms > _MAX_TIMESTAMP_MS:
        raise RecoveryVerificationError(f"{label} timestamp is invalid")
    if value_ms > captured_at_ms + _MAX_CLOCK_SKEW_MS:
        raise RecoveryVerificationError(f"{label} timestamp is in the future")


def _validate_captured_time(captured_at_ms: int) -> None:
    if (
        not _is_strict_integer(captured_at_ms)
        or captured_at_ms < 1
        or captured_at_ms > _MAX_TIMESTAMP_MS
    ):
        raise RecoveryVerificationError("captured recovery clock is invalid")


def _datetime_to_timestamp_ms(value: datetime) -> int:
    try:
        timestamp_ms = int(value.astimezone(UTC).timestamp() * 1000)
    except (OSError, OverflowError, ValueError) as error:
        raise RecoveryVerificationError("S3 recovery timestamp is invalid") from error
    if timestamp_ms < 1 or timestamp_ms > _MAX_TIMESTAMP_MS:
        raise RecoveryVerificationError("S3 recovery timestamp is invalid")
    return timestamp_ms


def _stream_to_descriptor(
    stream: _StreamingBody,
    descriptor: int,
    *,
    maximum_size_bytes: int,
    progress_check: Callable[[], None] | None,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    for _read in range(_MAX_STREAM_READS):
        if progress_check is not None:
            progress_check()
        remaining_bytes = maximum_size_bytes - size_bytes
        chunk = stream.read(min(_STREAM_CHUNK_BYTES, remaining_bytes + 1))
        if not chunk:
            return digest.hexdigest(), size_bytes
        if not isinstance(chunk, bytes):
            raise RecoveryVerificationError("S3 recovery download returned non-bytes")
        size_bytes += len(chunk)
        if size_bytes > maximum_size_bytes:
            raise RecoveryVerificationError(
                "S3 recovery download exceeds its byte count"
            )
        _write_all(descriptor, chunk)
        digest.update(chunk)
    raise RecoveryVerificationError("S3 recovery download exceeded its read bound")


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    offset = 0
    for _write in range(len(content) + 1):
        if offset == len(content):
            return
        written = os.write(descriptor, view[offset:])
        if written < 1 or written > len(content) - offset:
            raise RecoveryStorageError(
                "recovery staging write returned an invalid count"
            )
        offset += written
    raise RecoveryStorageError("recovery staging write exceeded its bound")


def _require_object_identity(
    head: _HeadIdentity,
    expected_sha256: str,
    expected_size_bytes: int,
) -> None:
    if head.sha256 != expected_sha256 or head.size_bytes != expected_size_bytes:
        raise RecoveryVerificationError("S3 recovery object identity does not match")


def _close_stream(
    stream: _StreamingBody,
    primary_error: BaseException | None,
) -> None:
    try:
        stream.close()
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note(
                f"S3 recovery stream cleanup also failed: {cleanup_error!r}"
            )
        elif isinstance(cleanup_error, Exception):
            raise RecoveryStorageError(
                "S3 recovery stream cleanup failed"
            ) from cleanup_error
        else:
            raise


def _reject_duplicate_keys(images: list[ListedRecoveryImage]) -> None:
    keys = [item.key for item in images]
    if len(keys) != len(set(keys)):
        raise RecoveryStorageError("S3 recovery listing contains duplicate keys")


def _validate_listing_bounds(maximum_objects: int, page_size: int) -> None:
    if (
        not _is_strict_integer(maximum_objects)
        or maximum_objects < 1
        or maximum_objects > _MAX_LIST_OBJECTS_PER_CLASS
    ):
        raise RecoveryStorageError("recovery listing object bound is unsupported")
    if (
        not _is_strict_integer(page_size)
        or page_size < 1
        or page_size > _MAX_LIST_PAGE_SIZE
    ):
        raise RecoveryStorageError("recovery listing page size is unsupported")


def _validate_s3_configuration(
    client: _S3Client,
    bucket: str,
    expected_bucket_owner: str,
    attempts: int,
    delay_seconds: float,
    selection_timeout_seconds: float,
    maximum_selection_requests: int,
) -> None:
    if (
        _S3_BUCKET.fullmatch(bucket) is None
        or ".." in bucket
        or ".-" in bucket
        or "-." in bucket
    ):
        raise RecoveryStorageError("S3 bucket name is unsupported")
    if _AWS_ACCOUNT_ID.fullmatch(expected_bucket_owner) is None:
        raise RecoveryStorageError("expected S3 bucket owner must be an account ID")
    if not _is_strict_integer(attempts) or attempts < 1 or attempts > 10:
        raise RecoveryStorageError("S3 conflict attempt bound is unsupported")
    if (
        not _is_real_number(delay_seconds)
        or not math.isfinite(delay_seconds)
        or delay_seconds < 0
        or delay_seconds > 5
    ):
        raise RecoveryStorageError("S3 conflict delay is unsupported")
    if (
        not _is_real_number(selection_timeout_seconds)
        or not math.isfinite(selection_timeout_seconds)
        or selection_timeout_seconds <= 0
        or selection_timeout_seconds > _MAX_SELECTION_TIMEOUT_SECONDS
    ):
        raise RecoveryStorageError("recovery selection timeout is unsupported")
    if (
        not _is_strict_integer(maximum_selection_requests)
        or maximum_selection_requests < 1
        or maximum_selection_requests > _MAX_SELECTION_REQUESTS
    ):
        raise RecoveryStorageError("recovery selection request bound is unsupported")
    retries = client.meta.config.retries
    if retries is None or retries.get("total_max_attempts") != 1:
        raise RecoveryStorageError("S3 client retries must be disabled")


def _validate_timeout(value: object, label: str) -> None:
    if (
        not _is_real_number(value)
        or not math.isfinite(value)
        or value <= 0
        or value > 300
    ):
        raise RecoveryStorageError(f"{label} is outside the supported range")


def _validate_version_id(version_id: str) -> None:
    if (
        len(version_id) < 1
        or len(version_id) > 1024
        or any(
            ord(character) < 0x21 or ord(character) > 0x7E for character in version_id
        )
    ):
        raise RecoveryVerificationError("S3 recovery version ID is invalid")


def _encode_sha256(sha256: str) -> str:
    try:
        return base64.b64encode(bytes.fromhex(sha256)).decode("ascii")
    except ValueError as error:
        raise RecoveryVerificationError("recovery SHA-256 is malformed") from error


def _decode_sha256(checksum_base64: str) -> str:
    try:
        decoded = base64.b64decode(checksum_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise RecoveryVerificationError("S3 returned a malformed SHA-256") from error
    if len(decoded) != 32:
        raise RecoveryVerificationError("S3 returned a non-SHA-256 checksum")
    if base64.b64encode(decoded).decode("ascii") != checksum_base64:
        raise RecoveryVerificationError("S3 returned a non-canonical checksum")
    return decoded.hex()


def _client_error_status(error: ClientError) -> int | None:
    response = cast(_ClientErrorLike, cast(object, error)).response
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, Mapping):
        return None
    status = cast(Mapping[str, object], metadata).get("HTTPStatusCode")
    return status if _is_strict_integer(status) else None


def _client_error_code(error: ClientError) -> str | None:
    response = cast(_ClientErrorLike, cast(object, error)).response
    details = response.get("Error")
    if not isinstance(details, Mapping):
        return None
    code = cast(Mapping[str, object], details).get("Code")
    return code if isinstance(code, str) else None


def _is_real_number(value: object) -> TypeIs[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_strict_integer(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)


@final
class _BoundedBody(io.RawIOBase):
    def __init__(self, source: BinaryIO, size_bytes: int) -> None:
        super().__init__()
        self._source = source
        self._size_bytes = size_bytes
        self._position_bytes = 0
        self._verification_error: RecoveryVerificationError | None = None

    @property
    def verification_error(self) -> RecoveryVerificationError | None:
        return self._verification_error

    @override
    def readable(self) -> bool:
        return True

    @override
    def seekable(self) -> bool:
        return True

    @override
    def tell(self) -> int:
        return self._position_bytes

    @override
    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self._position_bytes + offset
        elif whence == os.SEEK_END:
            target = self._size_bytes + offset
        else:
            raise ValueError("unsupported seek origin")
        if target < 0 or target > self._size_bytes:
            raise ValueError("recovery request body seek is outside the image")
        self._position_bytes = self._source.seek(target)
        if self._position_bytes != target:
            self._fail("recovery image seek changed during publication")
        return target

    @override
    def read(self, size: int = -1, /) -> bytes:
        if size == 0 or self._position_bytes == self._size_bytes:
            return b""
        remaining = self._size_bytes - self._position_bytes
        requested = remaining if size < 0 else min(size, remaining)
        chunk = self._source.read(requested)
        if not chunk:
            self._fail("recovery image shrank during publication")
        if len(chunk) > requested:
            self._fail("recovery image read exceeded the publication bound")
        self._position_bytes += len(chunk)
        return chunk

    def reset(self) -> None:
        self._verification_error = None
        if self._source.seek(0, os.SEEK_END) != self._size_bytes:
            self._fail("recovery image size changed before publication")
        _ = self.seek(0)

    def verify_complete(self) -> None:
        if self._position_bytes != self._size_bytes:
            self._fail("S3 client did not consume the complete recovery image")
        if self._source.seek(0, os.SEEK_END) != self._size_bytes:
            self._fail("recovery image size changed during publication")

    def _fail(self, message: str) -> Never:
        error = RecoveryVerificationError(message)
        self._verification_error = error
        raise error
