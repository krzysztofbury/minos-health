import hashlib
import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, final

from pydantic import ValidationError

from minos.ingest.watchers.control import (
    CommittedObservationHistory,
    CommittedObservationRevision,
    CommittedProductEvidence,
    CommittedRun,
    ControlStore,
)
from minos.ingest.watchers.protocols import WatcherRuntimeError
from minos.ingest.watchers.publication import ImmutableGenerationPublisher
from minos.ingest.watchers.rsk.public_models import (
    MAX_PUBLIC_JSON_BYTES,
    MAX_PUBLIC_REVISIONS,
    MAX_PUBLIC_TIMELINE_ENTRIES,
    PublicGenerationManifest,
    RedistributionManifest,
    RskProvenanceEntry,
    RskProvenanceProjection,
    RskPublicObservation,
    RskPublicObservedFile,
    public_entry_sort_key,
    public_timestamp,
)

_PUBLIC_OBSERVATION_KIND = "rsk_basic_info"
_PROJECTION_PATH: Literal["api/v1/provenance/rsk.json"] = "api/v1/provenance/rsk.json"
_PUBLIC_NOTICE = (
    "Artifact redistribution is disabled pending an approved legal allowlist."
)
_MAX_PUBLIC_WORK_FILE_RECORDS = 16_384
_PRODUCT_MEDIA_TYPES = frozenset(
    {"application/octet-stream", "application/xml", "text/xml"}
)
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


@dataclass(frozen=True, slots=True)
class _VerifiedProductEvidence:
    state_revision: int
    file_uuid: str
    sha256: str
    size_bytes: int
    media_type: str


@dataclass(frozen=True, slots=True)
class _SnapshotVersion:
    observation: RskPublicObservation
    file_index: int
    product_sha256: str | None
    fingerprint: bytes
    observed_at_ms: int


@dataclass(slots=True)
class _Interval:
    stable_identity: str
    version: _SnapshotVersion
    first_observed_at_ms: int
    last_observed_at_ms: int
    end_revision_index: int


class _WorkBudgetExceeded(RuntimeError):
    pass


@final
class RskPublicProjectionPublisher:
    def __init__(self, generations: ImmutableGenerationPublisher) -> None:
        self._generations = generations

    def publish(self, store: ControlStore, run: CommittedRun) -> None:
        if run.source_id != "rsk":
            return
        projection_bytes, projection_sha256, manifest_bytes = self._build_for_revision(
            store, run.committed_state_revision
        )
        _ = self._generations.publish(
            run.committed_state_revision,
            projection_sha256,
            {
                "manifest.json": manifest_bytes,
                _PROJECTION_PATH: projection_bytes,
            },
        )

    def restore(self, store: ControlStore) -> None:
        run = store.latest_recovery_reconciled_run("rsk")
        if run is not None:
            self.publish(store, run)

    def rebuild(
        self,
        store: ControlStore,
        *,
        expected_current_revision: int,
        expected_current_sha256: str,
        target_revision: int,
        target_sha256: str,
    ) -> str:
        _ = store.recovery_reconciled_run_at_revision("rsk", target_revision)
        projection_bytes, projection_sha256, manifest_bytes = self._build_for_revision(
            store, target_revision
        )
        if projection_sha256 != target_sha256:
            raise WatcherRuntimeError("target public projection digest differs")
        return self._generations.rebuild(
            expected_current_revision=expected_current_revision,
            expected_current_sha256=expected_current_sha256,
            target_revision=target_revision,
            target_sha256=target_sha256,
            files={
                "manifest.json": manifest_bytes,
                _PROJECTION_PATH: projection_bytes,
            },
        )

    def _build_for_revision(
        self,
        store: ControlStore,
        state_revision: int,
    ) -> tuple[bytes, str, bytes]:
        history = store.load_committed_observation_history(
            "rsk",
            _PUBLIC_OBSERVATION_KIND,
            through_state_revision=state_revision,
        )
        return build_rsk_projection(history)


def validate_deployed_rsk_projection(
    public_directory: Path,
) -> dict[str, int | str]:
    current = ImmutableGenerationPublisher(public_directory).current()
    if current is None:
        return {"status": "absent"}
    revision, generation = current
    digest = generation.split("-", 1)[1]
    try:
        projection_bytes = (
            public_directory / "current" / _PROJECTION_PATH
        ).read_bytes()
    except OSError:
        raise WatcherRuntimeError(
            "deployed public RSK projection is unreadable"
        ) from None
    if (
        len(projection_bytes) < 1
        or len(projection_bytes) > MAX_PUBLIC_JSON_BYTES
        or hashlib.sha256(projection_bytes).hexdigest() != digest
    ):
        raise WatcherRuntimeError("deployed public RSK projection differs")
    try:
        projection = RskProvenanceProjection.model_validate_json(projection_bytes)
    except ValidationError:
        raise WatcherRuntimeError("deployed public RSK projection is invalid") from None
    if projection.through_state_revision != revision:
        raise WatcherRuntimeError("deployed public RSK revision differs")
    return {"revision": revision, "sha256": digest, "status": "valid"}


def build_rsk_projection(
    history: CommittedObservationHistory,
) -> tuple[bytes, str, bytes]:
    if history.source_id != "rsk" or not history.revisions:
        raise WatcherRuntimeError(
            "RSK public history is empty or belongs to another source"
        )
    if len(history.revisions) > MAX_PUBLIC_REVISIONS:
        raise WatcherRuntimeError("RSK public history revision count exceeds its bound")
    if history.revisions[-1].state_revision != history.through_state_revision:
        raise WatcherRuntimeError(
            "RSK public history does not reach its revision fence"
        )

    evidence = _verified_product_evidence(
        history.product_evidence,
        history.through_state_revision,
    )
    revisions, snapshots, work_truncated = _select_work_suffix(
        history.revisions,
        evidence,
    )
    intervals = _build_intervals(revisions, snapshots)
    entries = _entries_from_intervals(
        intervals,
        snapshots[-1],
        len(snapshots) - 1,
    )
    start_index = _select_output_suffix(
        revisions,
        entries,
        history.older_revisions_exist or work_truncated,
        history.through_state_revision,
    )
    included_revisions = revisions[start_index:]
    included_entries = [
        entry for end_index, entry in entries if end_index >= start_index
    ]
    included_entries.sort(key=public_entry_sort_key)
    projection = _projection_model(
        history.through_state_revision,
        included_revisions,
        included_entries,
        history.older_revisions_exist or work_truncated or start_index > 0,
    )
    projection_bytes = _canonical_bytes(projection.model_dump(mode="json"))
    if len(projection_bytes) > MAX_PUBLIC_JSON_BYTES:
        raise WatcherRuntimeError("RSK public projection exceeds its byte bound")
    projection_sha256 = hashlib.sha256(projection_bytes).hexdigest()
    redistribution = RedistributionManifest(schema_version=1, artifacts=[])
    manifest = PublicGenerationManifest(
        schema_version=1,
        source="RSK",
        state_revision=history.through_state_revision,
        projection_sha256=projection_sha256,
        projection_path=_PROJECTION_PATH,
        projection_size_bytes=len(projection_bytes),
        redistribution=redistribution,
    )
    return (
        projection_bytes,
        projection_sha256,
        _canonical_bytes(manifest.model_dump(mode="json")),
    )


def _verified_product_evidence(
    evidence: tuple[CommittedProductEvidence, ...],
    through_state_revision: int,
) -> dict[str, tuple[tuple[int, ...], tuple[_VerifiedProductEvidence, ...]]]:
    by_uuid: dict[str, list[_VerifiedProductEvidence]] = {}
    by_digest: dict[str, _VerifiedProductEvidence] = {}
    for item in evidence:
        if item.state_revision > through_state_revision:
            raise WatcherRuntimeError(
                "committed RSK product evidence exceeds its fence"
            )
        verified = _validate_product_evidence(item)
        previous = by_digest.get(verified.sha256)
        if previous is not None and (
            previous.size_bytes != verified.size_bytes
            or previous.media_type != verified.media_type
        ):
            raise WatcherRuntimeError("committed RSK product evidence conflicts")
        by_digest[verified.sha256] = verified
        by_uuid.setdefault(verified.file_uuid, []).append(verified)
    result: dict[str, tuple[tuple[int, ...], tuple[_VerifiedProductEvidence, ...]]] = {}
    for file_uuid, items in by_uuid.items():
        ordered = tuple(sorted(items, key=lambda item: item.state_revision))
        result[file_uuid] = (
            tuple(item.state_revision for item in ordered),
            ordered,
        )
    return result


def _validate_product_evidence(
    evidence: CommittedProductEvidence,
) -> _VerifiedProductEvidence:
    if evidence.state_revision < 1:
        raise WatcherRuntimeError("committed RSK product revision is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", evidence.response_sha256) is None:
        raise WatcherRuntimeError("committed RSK product digest is invalid")
    metadata = evidence.metadata
    if set(metadata) != {"hcd", "size_bytes"}:
        raise WatcherRuntimeError("committed RSK product metadata is not exact")
    hcd = metadata["hcd"]
    size_bytes = metadata["size_bytes"]
    if hcd != "00CD10":
        raise WatcherRuntimeError("committed RSK product HCD is not allowlisted")
    if type(size_bytes) is not int or size_bytes < 1:
        raise WatcherRuntimeError("committed RSK product size is invalid")
    if _UUID_PATTERN.fullmatch(evidence.source_identity) is None:
        raise WatcherRuntimeError("committed RSK product UUID is invalid")
    if evidence.response_size_bytes != size_bytes:
        raise WatcherRuntimeError("committed RSK product receipt size differs")
    if evidence.response_media_type not in _PRODUCT_MEDIA_TYPES:
        raise WatcherRuntimeError("committed RSK product media type is not allowlisted")
    if len(evidence.blobs) != 1:
        raise WatcherRuntimeError("committed RSK product blob evidence is incomplete")
    blob = evidence.blobs[0]
    if blob.blob_role != "product" or blob.ordinal != 0:
        raise WatcherRuntimeError("committed RSK product blob role is invalid")
    if (
        blob.sha256 != evidence.response_sha256
        or blob.size_bytes != evidence.response_size_bytes
        or blob.media_type != evidence.response_media_type
    ):
        raise WatcherRuntimeError("committed RSK product blob receipt differs")
    return _VerifiedProductEvidence(
        evidence.state_revision,
        evidence.source_identity,
        evidence.response_sha256,
        size_bytes,
        evidence.response_media_type,
    )


def _select_work_suffix(
    revisions: tuple[CommittedObservationRevision, ...],
    evidence: dict[str, tuple[tuple[int, ...], tuple[_VerifiedProductEvidence, ...]]],
) -> tuple[
    tuple[CommittedObservationRevision, ...],
    tuple[dict[str, _SnapshotVersion], ...],
    bool,
]:
    selected_revisions: list[CommittedObservationRevision] = []
    selected_snapshots: list[dict[str, _SnapshotVersion]] = []
    file_records = 0
    truncated = False
    for revision in reversed(revisions):
        try:
            snapshot = _snapshot_from_revision(
                revision,
                evidence,
                maximum_file_records=(_MAX_PUBLIC_WORK_FILE_RECORDS - file_records),
            )
        except _WorkBudgetExceeded:
            if not selected_revisions:
                raise WatcherRuntimeError(
                    "latest RSK public revision exceeds its work budget"
                ) from None
            truncated = True
            break
        file_records += len(snapshot)
        selected_revisions.append(revision)
        selected_snapshots.append(snapshot)
    selected_revisions.reverse()
    selected_snapshots.reverse()
    return tuple(selected_revisions), tuple(selected_snapshots), truncated


def _snapshot_from_revision(
    revision: CommittedObservationRevision,
    evidence: dict[str, tuple[tuple[int, ...], tuple[_VerifiedProductEvidence, ...]]],
    *,
    maximum_file_records: int,
) -> dict[str, _SnapshotVersion]:
    snapshot: dict[str, _SnapshotVersion] = {}
    seen_hcds: set[str] = set()
    for stored in revision.observations:
        if stored.observation_kind != _PUBLIC_OBSERVATION_KIND:
            raise WatcherRuntimeError("RSK public observation kind is invalid")
        if set(stored.metadata) != {"public"}:
            raise WatcherRuntimeError(
                "RSK observation public payload is not allowlisted"
            )
        try:
            observation = RskPublicObservation.model_validate(stored.metadata["public"])
        except ValidationError:
            raise WatcherRuntimeError(
                "RSK observation public payload is invalid"
            ) from None
        _validate_public_observation(
            stored.source_identity, stored.response_sha256, observation
        )
        if observation.hcd in seen_hcds:
            raise WatcherRuntimeError("RSK public revision repeats an HCD")
        seen_hcds.add(observation.hcd)
        derived_digests = _derived_product_digests(
            observation,
            stored.observed_at_ms,
            revision.state_revision,
            evidence,
        )
        for file_index, source_file in enumerate(observation.files):
            if len(snapshot) >= maximum_file_records:
                raise _WorkBudgetExceeded
            stable_identity = source_file.uuid
            if stable_identity in snapshot:
                raise WatcherRuntimeError("RSK public revision repeats a file identity")
            product_sha256 = derived_digests[file_index]
            version = _SnapshotVersion(
                observation,
                file_index,
                product_sha256,
                _snapshot_fingerprint(observation, file_index, product_sha256),
                stored.observed_at_ms,
            )
            snapshot[stable_identity] = version
    return snapshot


def _validate_public_observation(
    source_identity: str,
    response_sha256: str,
    observation: RskPublicObservation,
) -> None:
    if observation.hcd != source_identity:
        raise WatcherRuntimeError("RSK public observation HCD is inconsistent")
    if observation.metadata_observation_sha256 != response_sha256:
        raise WatcherRuntimeError(
            "RSK metadata response and public digest are inconsistent"
        )


def _derived_product_digests(
    observation: RskPublicObservation,
    observed_at_ms: int,
    state_revision: int,
    evidence: dict[str, tuple[tuple[int, ...], tuple[_VerifiedProductEvidence, ...]]],
) -> list[str | None]:
    candidates = [
        index
        for index, source_file in enumerate(observation.files)
        if _is_allowlisted_product(observation, source_file)
    ]
    derived: list[str | None] = [None] * len(observation.files)
    if observation.hcd == "00CD10" and candidates:
        if len(candidates) != 1 or not _is_active(observation, observed_at_ms):
            raise WatcherRuntimeError("RSK public acquisition allowlist is ambiguous")
        file_index = candidates[0]
        source_file = observation.files[file_index]
        verified = _latest_evidence(evidence.get(source_file.uuid), state_revision)
        if verified is not None:
            if source_file.size_bytes != verified.size_bytes:
                raise WatcherRuntimeError("RSK public product evidence size differs")
            derived[file_index] = verified.sha256
    for file_index, source_file in enumerate(observation.files):
        expected_digest = derived[file_index]
        expected_status = (
            "privately_archived_verified"
            if expected_digest is not None
            else "metadata_only"
        )
        if (
            source_file.product_sha256 != expected_digest
            or source_file.archive_status != expected_status
        ):
            raise WatcherRuntimeError(
                "RSK public product claim lacks exact committed evidence"
            )
    return derived


def _is_allowlisted_product(
    observation: RskPublicObservation,
    source_file: RskPublicObservedFile,
) -> bool:
    return (
        observation.hcd == "00CD10"
        and source_file.extension == "xml"
        and source_file.source_archived is False
        and source_file.education_file is False
        and source_file.size_bytes is not None
        and source_file.size_bytes > 0
        and source_file.file_created_at is not None
    )


def _is_active(observation: RskPublicObservation, observed_at_ms: int) -> bool:
    observed = datetime.fromtimestamp(observed_at_ms / 1000, tz=UTC)
    valid_from = datetime.fromisoformat(observation.valid_from.replace("Z", "+00:00"))
    if observed < valid_from:
        return False
    if observation.valid_to is None:
        return True
    valid_to = datetime.fromisoformat(observation.valid_to.replace("Z", "+00:00"))
    return observed <= valid_to


def _latest_evidence(
    indexed: tuple[tuple[int, ...], tuple[_VerifiedProductEvidence, ...]] | None,
    state_revision: int,
) -> _VerifiedProductEvidence | None:
    if indexed is None:
        return None
    revisions, evidence = indexed
    index = bisect_right(revisions, state_revision) - 1
    return None if index < 0 else evidence[index]


def _snapshot_fingerprint(
    observation: RskPublicObservation,
    file_index: int,
    product_sha256: str | None,
) -> bytes:
    source_file = observation.files[file_index]
    return _canonical_bytes(
        {
            "hcd": observation.hcd,
            "resource_name": observation.resource_name,
            "resource_short_name": observation.resource_short_name,
            "publisher_name": observation.publisher_name,
            "publisher_short_name": observation.publisher_short_name,
            "resource_type": observation.resource_type,
            "resource_version": observation.resource_version,
            "valid_from": observation.valid_from,
            "valid_to": observation.valid_to,
            "metadata_observation_sha256": observation.metadata_observation_sha256,
            "file_uuid": source_file.uuid,
            "file_name": source_file.file_name,
            "extension": source_file.extension,
            "type_label": source_file.type_label,
            "size_bytes": source_file.size_bytes,
            "file_created_at": source_file.file_created_at,
            "source_archived_at": source_file.source_archived_at,
            "product_sha256": product_sha256,
            "notices": observation.notices,
        }
    )


def _build_intervals(
    revisions: tuple[CommittedObservationRevision, ...],
    snapshots: tuple[dict[str, _SnapshotVersion], ...],
) -> list[_Interval]:
    active: dict[str, _Interval] = {}
    completed: list[_Interval] = []
    for revision_index, (_revision, snapshot) in enumerate(
        zip(revisions, snapshots, strict=True)
    ):
        for stable_identity in tuple(active):
            if stable_identity not in snapshot:
                completed.append(active.pop(stable_identity))
        for stable_identity, version in snapshot.items():
            observed_at_ms = version.observed_at_ms
            interval = active.get(stable_identity)
            if (
                interval is not None
                and interval.version.fingerprint == version.fingerprint
            ):
                interval.last_observed_at_ms = observed_at_ms
                interval.end_revision_index = revision_index
                continue
            if interval is not None:
                completed.append(interval)
            active[stable_identity] = _Interval(
                stable_identity,
                version,
                observed_at_ms,
                observed_at_ms,
                revision_index,
            )
    completed.extend(active.values())
    return completed


def _entries_from_intervals(
    intervals: list[_Interval],
    latest_snapshot: dict[str, _SnapshotVersion],
    latest_revision_index: int,
) -> list[tuple[int, RskProvenanceEntry]]:
    entries: list[tuple[int, RskProvenanceEntry]] = []
    for interval in intervals:
        if interval.stable_identity not in latest_snapshot:
            status: Literal["current", "historical", "missing"] = "missing"
        elif interval.end_revision_index < latest_revision_index:
            status = "historical"
        else:
            source_file = interval.version.observation.files[
                interval.version.file_index
            ]
            status = "historical" if source_file.source_archived is True else "current"
        entries.append(
            (interval.end_revision_index, _entry_from_interval(interval, status))
        )
    return entries


def _entry_from_interval(
    interval: _Interval,
    status: Literal["current", "historical", "missing"],
) -> RskProvenanceEntry:
    observation = interval.version.observation
    source_file = observation.files[interval.version.file_index]
    product_sha256 = interval.version.product_sha256
    return RskProvenanceEntry(
        hcd=observation.hcd,
        resource_name=observation.resource_name,
        resource_short_name=observation.resource_short_name,
        publisher_name=observation.publisher_name,
        publisher_short_name=observation.publisher_short_name,
        resource_type=observation.resource_type,
        resource_version=observation.resource_version,
        origin_url=(
            f"https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/{source_file.uuid}"
        ),
        valid_from=observation.valid_from,
        valid_to=observation.valid_to,
        file_uuid=source_file.uuid,
        file_name=source_file.file_name,
        extension=source_file.extension,
        type_label=source_file.type_label,
        size_bytes=source_file.size_bytes,
        file_created_at=source_file.file_created_at,
        source_archived_at=source_file.source_archived_at,
        metadata_observation_sha256=observation.metadata_observation_sha256,
        product_sha256=product_sha256,
        status=status,
        first_observed_at=public_timestamp(interval.first_observed_at_ms),
        last_observed_at=public_timestamp(interval.last_observed_at_ms),
        archive_status=(
            "privately_archived_verified"
            if product_sha256 is not None
            else "metadata_only"
        ),
        downloadable=False,
        download_url=None,
        notices=observation.notices,
    )


def _select_output_suffix(
    revisions: tuple[CommittedObservationRevision, ...],
    entries: list[tuple[int, RskProvenanceEntry]],
    previously_truncated: bool,
    through_state_revision: int,
) -> int:
    count_by_end = [0] * len(revisions)
    bytes_by_end = [0] * len(revisions)
    for end_index, entry in entries:
        count_by_end[end_index] += 1
        bytes_by_end[end_index] += len(_canonical_bytes(entry.model_dump(mode="json")))
    suffix_count = 0
    suffix_entry_bytes = 0
    counts = [0] * len(revisions)
    byte_sums = [0] * len(revisions)
    for index in range(len(revisions) - 1, -1, -1):
        suffix_count += count_by_end[index]
        suffix_entry_bytes += bytes_by_end[index]
        counts[index] = suffix_count
        byte_sums[index] = suffix_entry_bytes
    for start_index in range(len(revisions)):
        count = counts[start_index]
        if count > MAX_PUBLIC_TIMELINE_ENTRIES:
            continue
        empty = _projection_model(
            through_state_revision,
            revisions[start_index:],
            [],
            previously_truncated or start_index > 0,
        )
        empty_size = len(_canonical_bytes(empty.model_dump(mode="json")))
        projected_size = empty_size + byte_sums[start_index] + max(0, count - 1)
        if projected_size <= MAX_PUBLIC_JSON_BYTES:
            return start_index
    raise WatcherRuntimeError("latest RSK public revision exceeds projection bounds")


def _projection_model(
    through_state_revision: int,
    revisions: tuple[CommittedObservationRevision, ...],
    entries: list[RskProvenanceEntry],
    history_truncated: bool,
) -> RskProvenanceProjection:
    return RskProvenanceProjection(
        schema_version=1,
        source="RSK",
        through_state_revision=through_state_revision,
        projected_at=public_timestamp(revisions[-1].committed_at_ms),
        included_revisions=[item.state_revision for item in revisions],
        history_truncated=history_truncated,
        entries=entries,
        redistribution=RedistributionManifest(schema_version=1, artifacts=[]),
        notices=[_PUBLIC_NOTICE],
    )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
