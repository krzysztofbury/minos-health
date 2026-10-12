"""Bounded public BAW provenance from verified control-state observations."""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import cast, final

from pydantic import ValidationError

from minos.ingest.watchers.baw.models import (
    BawLineage,
    BawSourceError,
    validate_lineage,
)
from minos.ingest.watchers.control import (
    CommittedObservation,
    CommittedObservationHistory,
    CommittedProductEvidence,
    CommittedRun,
    ControlStore,
    JsonValue,
)
from minos.ingest.watchers.protocols import WatcherRuntimeError
from minos.ingest.watchers.publication import ImmutableGenerationPublisher

_PROJECTION_PATH = "api/v1/provenance/baw.json"
_MAX_DOCUMENTS = 32
_MAX_FILES = 128
_MAX_PUBLIC_BYTES = 256 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}")


@final
class BawPublicProjectionPublisher:
    def __init__(self, generations: ImmutableGenerationPublisher) -> None:
        self._generations = generations

    def publish(self, store: ControlStore, run: CommittedRun) -> None:
        self._publish(store, run, citation_only=False)

    def _publish(
        self, store: ControlStore, run: CommittedRun, *, citation_only: bool
    ) -> None:
        if run.source_id != "baw":
            return
        history = store.load_committed_observation_history(
            "baw",
            "baw_document",
            through_state_revision=run.committed_state_revision,
            product_kind="baw_file",
        )
        payload = build_baw_projection(history)
        digest = hashlib.sha256(payload).hexdigest()
        manifest = _canonical(
            {
                "schema_version": 1,
                "source": "BAW",
                "state_revision": run.committed_state_revision,
                "projection_path": _PROJECTION_PATH,
                "projection_sha256": digest,
                "projection_size_bytes": len(payload),
                "redistribution": {"schema_version": 1, "artifacts": []},
            }
        )
        publish = (
            self._generations.archive_citation
            if citation_only
            else self._generations.publish
        )
        _ = publish(
            run.committed_state_revision,
            digest,
            {"manifest.json": manifest, _PROJECTION_PATH: payload},
        )

    def restore(self, store: ControlStore) -> None:
        retained = self._generations.citation_revisions()
        if retained is not None:
            for historical in store.citation_recovery_runs("baw"):
                if historical.committed_state_revision not in retained:
                    self._publish(store, historical, citation_only=True)
        run = store.latest_recovery_reconciled_run("baw")
        if run is not None:
            self.publish(store, run)


def build_baw_projection(history: CommittedObservationHistory) -> bytes:
    if history.source_id != "baw" or not history.revisions:
        raise WatcherRuntimeError("BAW has no public observation revision")
    current = history.revisions[-1]
    if current.state_revision != history.through_state_revision:
        raise WatcherRuntimeError("BAW public observation revision is incomplete")
    products: dict[str, CommittedProductEvidence] = {}
    for product in history.product_evidence:
        products[product.source_identity] = product
    if not 1 <= len(current.observations) <= _MAX_DOCUMENTS:
        raise WatcherRuntimeError("BAW public document count is invalid")
    documents = [
        _public_document(observation, products)
        for observation in sorted(
            current.observations, key=lambda item: item.source_identity
        )
    ]
    has_lineage = _validate_public_lineage(documents)
    result = _canonical(
        {
            "schema_version": 2 if has_lineage else 1,
            "source": "BAW",
            "through_state_revision": history.through_state_revision,
            "observed_at": _timestamp(current.committed_at_ms),
            "documents": documents,
            "redistribution": {"schema_version": 1, "artifacts": []},
        }
    )
    if len(result) > _MAX_PUBLIC_BYTES:
        raise WatcherRuntimeError("BAW public projection exceeds its size bound")
    return result


def _public_document(
    observation: CommittedObservation, products: dict[str, CommittedProductEvidence]
) -> dict[str, JsonValue]:
    identity = observation.source_identity
    if not identity.isascii() or not identity.isdigit() or int(identity) < 1:
        raise WatcherRuntimeError("BAW document identity is invalid")
    metadata = observation.metadata
    _validate_document_metadata(metadata)
    document_id = int(identity)
    act_number = _text(metadata["act_number"])
    selected_ids = (
        _selected_ids(metadata["selected_file_ids"]) if "lineage" in metadata else None
    )
    # Legacy observations must restore byte-for-byte immutable generations.
    source_url = (
        f"https://baw.nfz.gov.pl/NFZ/document/{identity}"
        if "legal_act_type" not in metadata
        else _source_url(document_id, act_number, metadata["legal_act_type"])
    )
    document = cast(
        dict[str, JsonValue],
        {
            "document_id": document_id,
            "act_number": act_number,
            "act_date": _date(metadata["act_date"]),
            "published_date": _date(metadata["baw_published_date"]),
            "entry_into_force": _date(metadata["entry_into_force"]),
            "service_applicability_date": _date(metadata["service_applicability_date"]),
            "valid_to": _date(metadata["valid_to"]),
            "legal_status": _text(metadata["legal_status"]),
            "observed_at": _timestamp(observation.observed_at_ms),
            "metadata_sha256": _digest(observation.response_sha256),
            "source_url": source_url,
            "files": _document_files(document_id, products, selected_ids),
        },
    )
    if metadata["service_applicability_evidence"] is not None:
        document["service_applicability_evidence"] = _text(
            metadata["service_applicability_evidence"]
        )
    if "lineage" in metadata:
        document["lineage"] = metadata["lineage"]
    return document


def _validate_document_metadata(metadata: dict[str, JsonValue]) -> None:
    expected = {
        "act_number",
        "act_date",
        "baw_published_date",
        "entry_into_force",
        "service_applicability_date",
        "service_applicability_evidence",
        "valid_to",
        "legal_status",
    }
    if set(metadata) not in (
        expected,
        expected | {"legal_act_type"},
        expected | {"legal_act_type", "lineage", "selected_file_ids"},
    ):
        raise WatcherRuntimeError("BAW public document metadata is invalid")
    if (metadata["service_applicability_date"] is None) != (
        metadata["service_applicability_evidence"] is None
    ):
        raise WatcherRuntimeError("BAW applicability evidence is incomplete")


def _validate_public_lineage(documents: list[dict[str, JsonValue]]) -> bool:
    lineage: dict[int, BawLineage] = {}
    act_numbers: dict[int, str] = {}
    for document in documents:
        if "lineage" not in document:
            continue
        document_id = cast(int, document["document_id"])
        try:
            lineage[document_id] = BawLineage.model_validate(document["lineage"])
        except ValidationError:
            raise WatcherRuntimeError("BAW public lineage is invalid") from None
        act_numbers[document_id] = cast(str, document["act_number"])
    if not lineage:
        return False
    if len(lineage) != len(documents):
        raise WatcherRuntimeError("BAW public lineage revision is incomplete")
    try:
        validate_lineage(lineage, act_numbers)
    except BawSourceError:
        raise WatcherRuntimeError("BAW public lineage graph is invalid") from None
    return True


def _source_url(
    document_id: int, act_number: str, legal_act_type: object
) -> str | None:
    # BAW's BuildDocumentUrl uses the legal-act type and slash-separated number.
    # Publish no link for legacy observations or types outside this reviewed form.
    if (
        legal_act_type != "Zarządzenie"
        or re.fullmatch(r"[1-9]\d{0,3}/20\d{2}/DSOZ", act_number) is None
    ):
        return None
    return f"https://baw.nfz.gov.pl/NFZ/document/{document_id}/Zarzadzenie-{act_number.replace('/', '_')}"


def _document_files(
    document_id: int,
    products: dict[str, CommittedProductEvidence],
    selected_ids: set[int] | None = None,
) -> list[dict[str, JsonValue]]:
    result: list[dict[str, JsonValue]] = []
    for identity, product in sorted(products.items()):
        if not identity.startswith(f"{document_id}:"):
            continue
        if selected_ids is not None:
            file_id = product.metadata.get("file_id")
            if type(file_id) is not int:
                raise WatcherRuntimeError("BAW public file identity is invalid")
            if file_id not in selected_ids:
                continue
        metadata = product.metadata
        if (
            set(metadata)
            != {
                "document_id",
                "file_id",
                "parent_id",
                "name",
                "display_name",
                "size_bytes",
            }
            or metadata["document_id"] != document_id
            or str(metadata["file_id"]) != identity.split(":", 1)[1]
            or len(product.blobs) != 1
            or product.blobs[0].blob_role != "product"
            or product.blobs[0].sha256 != product.response_sha256
            or product.blobs[0].size_bytes != product.response_size_bytes
        ):
            raise WatcherRuntimeError("BAW public file evidence is invalid")
        name = _text(metadata["display_name"] or metadata["name"])
        result.append(
            {
                "file_id": metadata["file_id"],
                "name": name,
                "size_bytes": product.response_size_bytes,
                "sha256": _digest(product.response_sha256),
            }
        )
        if len(result) > _MAX_FILES:
            raise WatcherRuntimeError("BAW public file count exceeds its bound")
    if (
        selected_ids is not None
        and {cast(int, item["file_id"]) for item in result} != selected_ids
    ):
        raise WatcherRuntimeError("BAW selected public file evidence is incomplete")
    return result


def _selected_ids(value: JsonValue) -> set[int]:
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise WatcherRuntimeError("BAW public selected file set is invalid")
    if any(type(item) is not int or item < 1 for item in value):
        raise WatcherRuntimeError("BAW public selected file identity is invalid")
    ids = cast(list[int], value)
    if len(set(ids)) != len(ids):
        raise WatcherRuntimeError("BAW public selected files are duplicated")
    return set(ids)


def _text(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 512
        or any(ord(character) < 32 for character in value)
    ):
        raise WatcherRuntimeError("BAW public text is invalid")
    return value


def _date(value: object) -> str | None:
    if value is None:
        return None
    text = _text(value)
    if len(text) < 10 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]):
        raise WatcherRuntimeError("BAW public date is invalid")
    try:
        _ = datetime.fromisoformat(text)
    except ValueError:
        raise WatcherRuntimeError("BAW public date is invalid") from None
    return text[:10]


def _timestamp(value: int) -> str:
    return (
        datetime.fromtimestamp(value / 1000, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _digest(value: str) -> str:
    if _DIGEST.fullmatch(value) is None:
        raise WatcherRuntimeError("BAW public checksum is invalid")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")
