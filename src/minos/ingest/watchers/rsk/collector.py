import hashlib
import json
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, ClassVar, Literal, cast, final
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.ingest.watchers.archive import BlobStagingArea, StagedBlob
from minos.ingest.watchers.archive.files import open_verified_staged_blob
from minos.ingest.watchers.config import RskConfiguration, RskSettings
from minos.ingest.watchers.control import (
    ChangeDraft,
    JsonValue,
    ObservationBlob,
    ObservationDraft,
)
from minos.ingest.watchers.models import DownloadResult, HttpObservation
from minos.ingest.watchers.protocols import (
    CollectedRun,
    CollectionContext,
    RetryableWatcherError,
    WatcherRuntimeError,
)
from minos.ingest.watchers.rsk.capture import (
    _validate_complete_xml,  # pyright: ignore[reportPrivateUsage]
    select_active_icd10_xml,
)
from minos.ingest.watchers.rsk.capture_models import (
    MAX_CAPTURE_BYTES,
    MAX_CAPTURE_FILES,
    MAX_CAPTURE_RESOURCES,
    MAX_PRODUCT_BYTES,
)
from minos.ingest.watchers.rsk.client import (
    RskDeadlineExceededError,
    RskProtocolError,
    RskSourceDriftError,
    create_rsk_client,
    download_rsk_file,
    get_default_rsk_role_catalog,
    get_rsk_catalog_basic_info,
    parse_rsk_catalog_basic_info,
    parse_rsk_role_catalog,
)
from minos.ingest.watchers.rsk.models import (
    RSK_XML_EXTENSION,
    RskCatalogFile,
    RskCatalogMetadata,
    RskRoleUriModel,
)
from minos.ingest.watchers.rsk.public_models import (
    RskPublicObservation,
    RskPublicObservedFile,
    public_timestamp,
)

_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_PRODUCT_MEDIA_TYPES = frozenset(
    {"application/octet-stream", "application/xml", "text/xml"}
)
_Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_Hcd = Annotated[str, Field(pattern=r"^[0-9A-Z]{6}$")]


class _CursorModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)


class _CatalogCursor(_CursorModel):
    semantic_sha256: _Sha256
    hcds: list[_Hcd] = Field(min_length=1, max_length=MAX_CAPTURE_RESOURCES)


class _ResourceCursor(_CursorModel):
    metadata_sha256: _Sha256
    file_set_sha256: _Sha256
    version: Annotated[str, Field(min_length=1, max_length=512)]
    file_count: int = Field(ge=0, le=512)
    metadata: dict[str, JsonValue]


class _ProductCursor(_CursorModel):
    hcd: Literal["00CD10"]
    uuid: Annotated[
        str,
        Field(
            pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    ]
    creation_date: Annotated[str, Field(min_length=20, max_length=35)]
    size_bytes: int = Field(ge=1, le=MAX_PRODUCT_BYTES)
    extension: Literal["xml"]
    file_set_sha256: _Sha256
    sha256: _Sha256


class _RskCursor(_CursorModel):
    schema_version: Literal[1]
    catalog: _CatalogCursor
    resources: dict[_Hcd, _ResourceCursor] = Field(
        min_length=1,
        max_length=MAX_CAPTURE_RESOURCES,
    )
    state_sha256: _Sha256
    product: _ProductCursor


@dataclass(frozen=True, slots=True)
class _MetadataRecord:
    hcd: str
    metadata: RskCatalogMetadata
    blob: StagedBlob


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    catalog_blob: StagedBlob
    catalog: _CatalogCursor
    records: tuple[_MetadataRecord, ...]
    resources: dict[str, _ResourceCursor]
    selected: RskCatalogFile
    product_identity: dict[str, object]
    total_bytes: int


@dataclass(frozen=True, slots=True)
class _ProductAcquisition:
    result: DownloadResult | None = None
    product_blob: StagedBlob | None = None
    refetch_blob: StagedBlob | None = None


class RskMetadataRotationError(RetryableWatcherError):
    """The RSK metadata changed during a download and needs a fresh snapshot."""


@final
class RskSourceCollector:
    def __init__(
        self,
        staging: BlobStagingArea,
        *,
        client_factory: Callable[
            [RskSettings], AbstractContextManager[httpx.Client]
        ] = create_rsk_client,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._staging = staging
        self._client_factory = client_factory
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)

    @contextmanager
    def collect(self, context: CollectionContext) -> Generator[CollectedRun]:
        try:
            collected = self._collect(context)
        except ValidationError:
            raise RskSourceDriftError("RSK response schema changed") from None
        except RskDeadlineExceededError:
            raise RetryableWatcherError("transient RSK collection failure") from None
        except httpx.TransportError:
            raise RetryableWatcherError("transient RSK collection failure") from None
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _RETRYABLE_STATUS_CODES:
                raise RetryableWatcherError(
                    "transient RSK collection failure"
                ) from None
            raise RskSourceDriftError(
                "RSK returned a non-retryable HTTP status"
            ) from None
        yield collected

    def _collect(self, context: CollectionContext) -> CollectedRun:
        settings = _settings(context.configuration)
        prior = _prior_cursor(context.cursor)
        archived_digests: set[str] = set()
        observed_at_ms = context.run.started_at_ms
        captured_at = datetime.fromtimestamp(observed_at_ms / 1000, UTC)

        with self._client_factory(settings) as client:
            snapshot = self._fetch_snapshot(
                context,
                client,
                settings,
                captured_at,
                archived_digests,
            )
            acquisition = _ProductAcquisition()
            if _must_download(
                context.run.run_mode,
                prior,
                snapshot.product_identity,
            ):
                acquisition = self._acquire_product(
                    context,
                    client,
                    settings,
                    snapshot,
                    archived_digests,
                )
            self._require_deadline(context)

        self._require_deadline(context)
        product_sha256 = (
            acquisition.result.sha256
            if acquisition.result is not None
            else _prior_product_digest(prior)
        )
        cursor = _build_cursor(
            snapshot.catalog,
            snapshot.resources,
            snapshot.product_identity,
            product_sha256,
        )
        self._require_deadline(context)
        observations = _observations(
            context,
            observed_at_ms,
            snapshot,
            acquisition,
            cursor.product,
        )
        self._require_deadline(context)
        changes = _changes(context, prior, cursor, observations[0].observation_id)
        self._require_deadline(context)
        return CollectedRun(
            observations,
            cast(dict[str, JsonValue], cursor.model_dump(mode="json")),
            changes,
        )

    def _fetch_snapshot(
        self,
        context: CollectionContext,
        client: httpx.Client,
        settings: RskSettings,
        captured_at: datetime,
        archived_digests: set[str],
    ) -> _SourceSnapshot:
        catalog_observation = get_default_rsk_role_catalog(
            client,
            _operation_settings(settings, context, self._now_ms()),
        )
        self._require_deadline(context)
        total_bytes = len(catalog_observation.body)
        _require_collection_bytes(total_bytes)
        catalog_blob = self._archive_body(
            context,
            catalog_observation.body,
            _metadata_media_type(catalog_observation),
            archived_digests,
        )
        catalog = parse_rsk_role_catalog(catalog_observation)
        self._require_deadline(context)
        if len(catalog) > MAX_CAPTURE_RESOURCES:
            raise RskSourceDriftError("RSK resource count exceeds its bound")

        records: list[_MetadataRecord] = []
        total_file_count = 0
        for resource in sorted(catalog, key=lambda item: item.hcd):
            record = self._fetch_metadata_record(
                context,
                client,
                settings,
                resource.hcd,
                total_bytes,
                archived_digests,
            )
            total_bytes += record.blob.size_bytes
            total_file_count += len(record.metadata.files)
            if total_file_count > MAX_CAPTURE_FILES:
                raise RskSourceDriftError("RSK file count exceeds its bound")
            records.append(record)

        metadata_by_hcd = {record.hcd: record.metadata for record in records}
        _validate_global_uuid_identities(metadata_by_hcd)
        selected = select_active_icd10_xml(metadata_by_hcd, captured_at)
        _validate_selected_product(selected)
        self._require_deadline(context)
        catalog_cursor, resource_cursors = _source_projections(
            catalog,
            metadata_by_hcd,
        )
        product_identity = _product_identity(
            selected,
            resource_cursors["00CD10"].file_set_sha256,
        )
        return _SourceSnapshot(
            catalog_blob,
            catalog_cursor,
            tuple(records),
            resource_cursors,
            selected,
            product_identity,
            total_bytes,
        )

    def _fetch_metadata_record(
        self,
        context: CollectionContext,
        client: httpx.Client,
        settings: RskSettings,
        hcd: str,
        current_total_bytes: int,
        archived_digests: set[str],
    ) -> _MetadataRecord:
        observation = get_rsk_catalog_basic_info(
            client,
            _operation_settings(settings, context, self._now_ms()),
            hcd,
        )
        self._require_deadline(context)
        _require_collection_bytes(current_total_bytes + len(observation.body))
        blob = self._archive_body(
            context,
            observation.body,
            _metadata_media_type(observation),
            archived_digests,
        )
        metadata = parse_rsk_catalog_basic_info(observation, hcd)
        self._require_deadline(context)
        return _MetadataRecord(hcd, metadata, blob)

    def _acquire_product(
        self,
        context: CollectionContext,
        client: httpx.Client,
        settings: RskSettings,
        snapshot: _SourceSnapshot,
        archived_digests: set[str],
    ) -> _ProductAcquisition:
        selected_size_bytes = snapshot.selected.size_bytes
        if selected_size_bytes is None:
            raise RskSourceDriftError("RSK selected product size is missing")
        _require_collection_bytes(snapshot.total_bytes + selected_size_bytes)
        result, product_blob = self._download_product(
            context,
            client,
            settings,
            snapshot.selected,
            archived_digests,
        )
        self._require_deadline(context)
        refetch = get_rsk_catalog_basic_info(
            client,
            _operation_settings(settings, context, self._now_ms()),
            "00CD10",
        )
        self._require_deadline(context)
        _require_collection_bytes(
            snapshot.total_bytes + selected_size_bytes + len(refetch.body)
        )
        refetch_blob = self._archive_body(
            context,
            refetch.body,
            _metadata_media_type(refetch),
            archived_digests,
        )
        refetched_metadata = parse_rsk_catalog_basic_info(refetch, "00CD10")
        self._require_deadline(context)
        original_metadata = next(
            record.metadata for record in snapshot.records if record.hcd == "00CD10"
        )
        if _canonical_metadata(refetched_metadata) != _canonical_metadata(
            original_metadata
        ):
            raise RskMetadataRotationError(
                "RSK 00CD10 metadata changed during product download"
            )
        return _ProductAcquisition(result, product_blob, refetch_blob)

    def _archive_body(
        self,
        context: CollectionContext,
        body: bytes,
        media_type: str,
        archived_digests: set[str],
    ) -> StagedBlob:
        self._require_deadline(context)
        with self._staging.stage() as writer:
            _write_all(writer.write, body)
            self._require_deadline(context)
            blob = writer.seal(media_type=media_type)
            if blob.sha256 not in archived_digests:
                self._require_deadline(context)
                _ = context.archive.put(blob)
                self._require_deadline(context)
                archived_digests.add(blob.sha256)
            return blob

    def _download_product(
        self,
        context: CollectionContext,
        client: httpx.Client,
        settings: RskSettings,
        selected: RskCatalogFile,
        archived_digests: set[str],
    ) -> tuple[DownloadResult, StagedBlob]:
        if selected.size_bytes is None:
            raise RskSourceDriftError("RSK selected product size is missing")
        if selected.extension is None:
            raise RskSourceDriftError("RSK selected product extension is missing")
        with self._staging.stage() as writer:
            result = download_rsk_file(
                client,
                _operation_settings(settings, context, self._now_ms()),
                selected.uuid,
                selected.size_bytes,
                selected.extension,
                writer,
            )
            self._require_deadline(context)
            media_type = _product_media_type(result)
            blob = writer.seal(
                media_type=media_type,
                expected_sha256=result.sha256,
                expected_size_bytes=result.size_bytes,
            )
            self._require_deadline(context)
            with open_verified_staged_blob(blob) as source:
                _validate_complete_xml(source, result.size_bytes)
            self._require_deadline(context)
            if blob.sha256 not in archived_digests:
                _ = context.archive.put(blob)
                self._require_deadline(context)
                archived_digests.add(blob.sha256)
            return result, blob

    def _require_deadline(self, context: CollectionContext) -> None:
        _require_claim_deadline(context, self._now_ms())


def _settings(configuration: dict[str, JsonValue]) -> RskSettings:
    try:
        validated = RskConfiguration.model_validate(configuration)
        return RskSettings.model_validate(validated.model_dump(mode="python"))
    except ValidationError:
        raise WatcherRuntimeError("persisted RSK configuration is invalid") from None


def _operation_settings(
    settings: RskSettings,
    context: CollectionContext,
    now_ms: int,
) -> RskSettings:
    _require_claim_deadline(context, now_ms)
    remaining_seconds = (context.run.identity.deadline_at_ms - now_ms) / 1000
    if remaining_seconds < 0.001:
        raise RskDeadlineExceededError("RSK collection claim deadline expired")
    values = settings.model_dump(mode="python")
    values["operation_deadline_seconds"] = min(
        settings.operation_deadline_seconds,
        remaining_seconds,
    )
    values["timeout_seconds"] = min(settings.timeout_seconds, remaining_seconds)
    values["connect_timeout_seconds"] = min(
        settings.connect_timeout_seconds,
        remaining_seconds,
    )
    return RskSettings.model_validate(values)


def _require_claim_deadline(context: CollectionContext, now_ms: int) -> None:
    if now_ms >= context.run.identity.deadline_at_ms:
        raise RskDeadlineExceededError("RSK collection claim deadline expired")


def _prior_cursor(cursor: dict[str, JsonValue]) -> _RskCursor | None:
    if not cursor:
        return None
    try:
        validated = _RskCursor.model_validate(cursor)
        _validate_persisted_cursor(validated)
        return validated
    except ValidationError, RskProtocolError:
        raise WatcherRuntimeError("stored RSK cursor is invalid") from None


def _validate_persisted_cursor(cursor: _RskCursor) -> None:
    resource_hcds = sorted(cursor.resources)
    if cursor.catalog.hcds != resource_hcds:
        raise RskProtocolError("stored RSK cursor catalog membership is inconsistent")

    metadata_by_hcd: dict[str, RskCatalogMetadata] = {}
    for hcd in resource_hcds:
        resource = cursor.resources[hcd]
        metadata = RskCatalogMetadata.model_validate_json(
            _canonical_bytes(resource.metadata)
        )
        normalized = cast(dict[str, JsonValue], _normalized_metadata(metadata))
        if resource.metadata != normalized or metadata.hcd != hcd:
            raise RskProtocolError("stored RSK cursor metadata is not canonical")
        if resource.metadata_sha256 != _digest(normalized):
            raise RskProtocolError("stored RSK cursor metadata digest is inconsistent")
        files = normalized["files"]
        if resource.file_set_sha256 != _digest(files):
            raise RskProtocolError("stored RSK cursor file-set digest is inconsistent")
        if resource.version != metadata.version or resource.file_count != len(
            metadata.files
        ):
            raise RskProtocolError("stored RSK cursor resource summary is inconsistent")
        metadata_by_hcd[hcd] = metadata

    _validate_global_uuid_identities(metadata_by_hcd)
    resource_00cd10 = cursor.resources.get("00CD10")
    if resource_00cd10 is None:
        raise RskProtocolError("stored RSK cursor has no 00CD10 resource")
    if cursor.product.file_set_sha256 != resource_00cd10.file_set_sha256:
        raise RskProtocolError("stored RSK cursor product file set is inconsistent")
    metadata_00cd10 = metadata_by_hcd.get("00CD10")
    if metadata_00cd10 is None:
        raise RskProtocolError("stored RSK cursor has no 00CD10 metadata")
    selected = select_active_icd10_xml(
        metadata_by_hcd,
        metadata_00cd10.valid_date_from,
    )
    expected_identity = _product_identity(selected, resource_00cd10.file_set_sha256)
    product_identity = cursor.product.model_dump(mode="python", exclude={"sha256"})
    if product_identity != expected_identity:
        raise RskProtocolError("stored RSK cursor product identity is inconsistent")
    expected_state_sha256 = _digest(
        _cursor_state(
            cursor.catalog,
            cursor.resources,
            cursor.product,
        )
    )
    if cursor.state_sha256 != expected_state_sha256:
        raise RskProtocolError("stored RSK cursor state digest is inconsistent")


def _source_projections(
    catalog: list[RskRoleUriModel],
    metadata_by_hcd: Mapping[str, RskCatalogMetadata],
) -> tuple[_CatalogCursor, dict[str, _ResourceCursor]]:
    ordered_catalog = sorted(
        (item.model_dump(mode="json", by_alias=True) for item in catalog),
        key=lambda item: cast(str, item["hcd"]),
    )
    hcds = [cast(str, item["hcd"]) for item in ordered_catalog]
    catalog_cursor = _CatalogCursor(
        semantic_sha256=_digest(ordered_catalog),
        hcds=hcds,
    )
    resources: dict[str, _ResourceCursor] = {}
    for hcd in sorted(metadata_by_hcd):
        metadata = metadata_by_hcd[hcd]
        files = sorted(
            (item.model_dump(mode="json", by_alias=True) for item in metadata.files),
            key=lambda item: cast(str, item["uuid"]),
        )
        resources[hcd] = _ResourceCursor(
            metadata_sha256=_digest(_normalized_metadata(metadata)),
            file_set_sha256=_digest(files),
            version=metadata.version,
            file_count=len(files),
            metadata=cast(dict[str, JsonValue], _normalized_metadata(metadata)),
        )
    return catalog_cursor, resources


def _normalized_metadata(metadata: RskCatalogMetadata) -> dict[str, object]:
    value = cast(
        dict[str, object],
        metadata.model_dump(mode="json", by_alias=True),
    )
    value["files"] = sorted(
        cast(list[dict[str, object]], value["files"]),
        key=lambda item: cast(str, item["uuid"]),
    )
    links = value["educationLinks"]
    if isinstance(links, list):
        value["educationLinks"] = sorted(
            cast(list[object], links),
            key=_canonical_bytes,
        )
    return value


def _canonical_metadata(metadata: RskCatalogMetadata) -> bytes:
    return _canonical_bytes(_normalized_metadata(metadata))


def _product_identity(
    selected: RskCatalogFile,
    file_set_sha256: str,
) -> dict[str, object]:
    if selected.creation_date is None:
        raise RskSourceDriftError("RSK selected product creation date is missing")
    if selected.size_bytes is None:
        raise RskSourceDriftError("RSK selected product size is missing")
    if selected.extension is None:
        raise RskSourceDriftError("RSK selected product extension is missing")
    return {
        "hcd": "00CD10",
        "uuid": str(selected.uuid),
        "creation_date": selected.creation_date.isoformat(),
        "size_bytes": selected.size_bytes,
        "extension": selected.extension,
        "file_set_sha256": file_set_sha256,
    }


def _must_download(
    run_mode: str,
    prior: _RskCursor | None,
    product_identity: dict[str, object],
) -> bool:
    if run_mode in {"manual", "weekly_verification"} or prior is None:
        return True
    current_identity = dict(product_identity)
    prior_identity = prior.product.model_dump(mode="python", exclude={"sha256"})
    return current_identity != prior_identity


def _prior_product_digest(prior: _RskCursor | None) -> str:
    if prior is None:
        raise WatcherRuntimeError("RSK product cursor is absent after skipped download")
    return prior.product.sha256


def _build_cursor(
    catalog: _CatalogCursor,
    resources: dict[str, _ResourceCursor],
    product_identity: dict[str, object],
    product_sha256: str,
) -> _RskCursor:
    product = _ProductCursor.model_validate(
        {**product_identity, "sha256": product_sha256}
    )
    source_state = _cursor_state(catalog, resources, product)
    return _RskCursor(
        schema_version=1,
        catalog=catalog,
        resources=resources,
        state_sha256=_digest(source_state),
        product=product,
    )


def _cursor_state(
    catalog: _CatalogCursor,
    resources: Mapping[str, _ResourceCursor],
    product: _ProductCursor,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "catalog": catalog.model_dump(mode="json"),
        "resources": {
            hcd: resources[hcd].model_dump(mode="json") for hcd in sorted(resources)
        },
        "product": product.model_dump(mode="json"),
    }


def _observations(
    context: CollectionContext,
    observed_at_ms: int,
    snapshot: _SourceSnapshot,
    acquisition: _ProductAcquisition,
    product: _ProductCursor,
) -> tuple[ObservationDraft, ...]:
    observations = [
        ObservationDraft(
            _stable_id(context, "catalog"),
            "rsk_catalog",
            "UZYNLG",
            snapshot.catalog_blob.sha256,
            observed_at_ms,
            {
                "resource_count": len(snapshot.catalog.hcds),
                "semantic_sha256": snapshot.catalog.semantic_sha256,
            },
            (ObservationBlob("response", 0, snapshot.catalog_blob.sha256),),
        )
    ]
    observations.extend(
        _metadata_observations(context, observed_at_ms, snapshot, product)
    )
    if acquisition.product_blob is not None and acquisition.result is not None:
        observations.append(
            ObservationDraft(
                _stable_id(context, "product:00CD10"),
                "rsk_product",
                str(snapshot.product_identity["uuid"]),
                acquisition.product_blob.sha256,
                observed_at_ms,
                {
                    "hcd": "00CD10",
                    "size_bytes": acquisition.result.size_bytes,
                },
                (ObservationBlob("product", 0, acquisition.product_blob.sha256),),
            )
        )
    if acquisition.refetch_blob is not None:
        projection = snapshot.resources["00CD10"]
        observations.append(
            ObservationDraft(
                _stable_id(context, "basic-info-refetch:00CD10"),
                "rsk_basic_info_refetch",
                "00CD10",
                acquisition.refetch_blob.sha256,
                observed_at_ms,
                {
                    "file_count": projection.file_count,
                    "file_set_sha256": projection.file_set_sha256,
                    "metadata_sha256": projection.metadata_sha256,
                    "version": projection.version,
                },
                (ObservationBlob("response", 0, acquisition.refetch_blob.sha256),),
            )
        )
    return tuple(observations)


def _metadata_observations(
    context: CollectionContext,
    observed_at_ms: int,
    snapshot: _SourceSnapshot,
    product: _ProductCursor,
) -> list[ObservationDraft]:
    observations: list[ObservationDraft] = []
    for record in snapshot.records:
        observations.append(
            ObservationDraft(
                _stable_id(context, f"basic-info:{record.hcd}"),
                "rsk_basic_info",
                record.hcd,
                record.blob.sha256,
                observed_at_ms,
                {"public": _public_observation(record, product)},
                (ObservationBlob("response", 0, record.blob.sha256),),
            )
        )
    return observations


def _public_observation(
    record: _MetadataRecord,
    product: _ProductCursor,
) -> dict[str, JsonValue]:
    metadata = record.metadata
    publisher = metadata.publisher
    files: list[RskPublicObservedFile] = []
    for source_file in sorted(metadata.files, key=lambda item: str(item.uuid)):
        is_archived_product = (
            record.hcd == product.hcd and str(source_file.uuid) == product.uuid
        )
        files.append(
            RskPublicObservedFile(
                uuid=str(source_file.uuid),
                file_name=source_file.file_name,
                extension=source_file.extension,
                type_label=source_file.extension_name,
                size_bytes=source_file.size_bytes,
                file_created_at=public_timestamp(source_file.creation_date)
                if source_file.creation_date is not None
                else None,
                source_archived=source_file.archived,
                source_archived_at=public_timestamp(source_file.archivization_date)
                if source_file.archivization_date is not None
                else None,
                education_file=source_file.education_file,
                product_sha256=product.sha256 if is_archived_product else None,
                archive_status="privately_archived_verified"
                if is_archived_product
                else "metadata_only",
            )
        )
    public = RskPublicObservation(
        schema_version=1,
        hcd=record.hcd,
        resource_name=metadata.name,
        resource_short_name=metadata.short_name,
        publisher_name=publisher.name if publisher is not None else None,
        publisher_short_name=publisher.short_name if publisher is not None else None,
        resource_type=metadata.resource_type,
        resource_version=metadata.version,
        valid_from=public_timestamp(metadata.valid_date_from),
        valid_to=public_timestamp(metadata.valid_date_to)
        if metadata.valid_date_to is not None
        else None,
        metadata_observation_sha256=record.blob.sha256,
        files=files,
        notices=[],
    )
    return cast(dict[str, JsonValue], public.model_dump(mode="json"))


def _changes(
    context: CollectionContext,
    prior: _RskCursor | None,
    current: _RskCursor,
    observation_id: str,
) -> tuple[ChangeDraft, ...]:
    if prior is not None and prior.state_sha256 == current.state_sha256:
        return ()
    prior_resources = {} if prior is None else prior.resources
    current_hcds = set(current.resources)
    prior_hcds = set(prior_resources)
    metadata_changed = sorted(
        hcd
        for hcd in current_hcds & prior_hcds
        if current.resources[hcd].metadata_sha256
        != prior_resources[hcd].metadata_sha256
    )
    file_sets_changed = sorted(
        hcd
        for hcd in current_hcds & prior_hcds
        if current.resources[hcd].file_set_sha256
        != prior_resources[hcd].file_set_sha256
    )
    prior_product_sha256 = None if prior is None else prior.product.sha256
    summary = cast(
        dict[str, JsonValue],
        {
            "added_hcds": sorted(current_hcds - prior_hcds),
            "catalog_changed": prior is None or prior.catalog != current.catalog,
            "current_product_sha256": current.product.sha256,
            "current_state_sha256": current.state_sha256,
            "file_sets_changed": file_sets_changed,
            "metadata_changed": metadata_changed,
            "previous_product_sha256": prior_product_sha256,
            "previous_state_sha256": None if prior is None else prior.state_sha256,
            "removed_hcds": sorted(prior_hcds - current_hcds),
        },
    )
    category = "rsk_baseline" if prior is None else "rsk_source_change"
    identity = _canonical_bytes(
        {
            "category": category,
            "run_id": context.run.identity.run_id,
            "summary": summary,
        }
    )
    return (
        ChangeDraft(
            hashlib.sha256(identity).hexdigest(),
            observation_id,
            category,
            summary,
        ),
    )


def _validate_global_uuid_identities(
    metadata_by_hcd: Mapping[str, RskCatalogMetadata],
) -> None:
    seen: dict[UUID, str] = {}
    for hcd in sorted(metadata_by_hcd):
        for file in sorted(metadata_by_hcd[hcd].files, key=lambda item: str(item.uuid)):
            prior_hcd = seen.setdefault(file.uuid, hcd)
            if prior_hcd != hcd:
                raise RskSourceDriftError(
                    "RSK file UUID is reused across resource metadata"
                )


def _validate_selected_product(selected: RskCatalogFile) -> None:
    if selected.extension != RSK_XML_EXTENSION:
        raise RskSourceDriftError("RSK selected product extension is not allowlisted")
    if selected.size_bytes is None or not 1 <= selected.size_bytes <= MAX_PRODUCT_BYTES:
        raise RskSourceDriftError("RSK selected product size is outside its bound")
    if selected.creation_date is None:
        raise RskSourceDriftError("RSK selected product has no creation date")


def _require_collection_bytes(size_bytes: int) -> None:
    if size_bytes < 1 or size_bytes > MAX_CAPTURE_BYTES:
        raise RskSourceDriftError("RSK collection byte count exceeds its bound")


def _metadata_media_type(observation: HttpObservation) -> str:
    media_type = observation.header_map().get("content-type", "").partition(";")[0]
    if media_type.lower() != "application/json":
        raise RskProtocolError("RSK metadata media type is invalid")
    return "application/json"


def _product_media_type(result: DownloadResult) -> str:
    media_type = dict(result.headers).get("content-type", "").partition(";")[0].lower()
    if media_type not in _PRODUCT_MEDIA_TYPES:
        raise RskProtocolError("RSK product media type is invalid")
    return media_type


def _write_all(write: Callable[[bytes], int], content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = write(content[offset:])
        if written < 1:
            raise OSError("RSK metadata staging write was incomplete")
        offset += written


def _stable_id(context: CollectionContext, identity: str) -> str:
    return hashlib.sha256(
        f"rsk:{context.run.identity.run_id}:{identity}".encode("ascii")
    ).hexdigest()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
