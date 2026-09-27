"""Bounded public BAW provenance from verified control-state observations."""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import cast, final

from minos.ingest.watchers.control import (
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
        _ = self._generations.publish(
            run.committed_state_revision,
            digest,
            {"manifest.json": manifest, _PROJECTION_PATH: payload},
        )

    def restore(self, store: ControlStore) -> None:
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
    documents: list[dict[str, JsonValue]] = []
    if not 1 <= len(current.observations) <= _MAX_DOCUMENTS:
        raise WatcherRuntimeError("BAW public document count is invalid")
    for observation in sorted(
        current.observations, key=lambda item: item.source_identity
    ):
        document_id = observation.source_identity
        if (
            not document_id.isascii()
            or not document_id.isdigit()
            or int(document_id) < 1
        ):
            raise WatcherRuntimeError("BAW document identity is invalid")
        metadata = observation.metadata
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
        if set(metadata) != expected or not isinstance(metadata["act_number"], str):
            raise WatcherRuntimeError("BAW public document metadata is invalid")
        files = _document_files(int(document_id), products)
        documents.append(
            cast(
                dict[str, JsonValue],
                {
                    "document_id": int(document_id),
                    "act_number": _text(metadata["act_number"]),
                    "act_date": _date(metadata["act_date"]),
                    "published_date": _date(metadata["baw_published_date"]),
                    "entry_into_force": _date(metadata["entry_into_force"]),
                    "service_applicability_date": _date(
                        metadata["service_applicability_date"]
                    ),
                    "valid_to": _date(metadata["valid_to"]),
                    "legal_status": _text(metadata["legal_status"]),
                    "observed_at": _timestamp(observation.observed_at_ms),
                    "metadata_sha256": _digest(observation.response_sha256),
                    "source_url": f"https://baw.nfz.gov.pl/NFZ/document/{document_id}",
                    "files": files,
                },
            )
        )
    result = _canonical(
        {
            "schema_version": 1,
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


def _document_files(
    document_id: int, products: dict[str, CommittedProductEvidence]
) -> list[dict[str, JsonValue]]:
    result: list[dict[str, JsonValue]] = []
    for identity, product in sorted(products.items()):
        if not identity.startswith(f"{document_id}:"):
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
    return result


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
