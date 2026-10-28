"""Deterministic NFZ metadata from one recovery-reconciled evidence snapshot."""

import hashlib
import json
from datetime import UTC, datetime
from typing import cast, final

from pydantic import ValidationError

from minos.ingest.watchers.control import (
    CommittedObservation,
    CommittedObservationRevision,
    CommittedRun,
    ControlStore,
)
from minos.ingest.watchers.nfz.public_models import NfzProjection
from minos.ingest.watchers.protocols import WatcherRuntimeError
from minos.ingest.watchers.publication import ImmutableGenerationPublisher

MAX_PUBLIC_BYTES = 1024 * 1024
PROJECTION_PATH = "api/v1/provenance/nfz.json"
_IGNORED = frozenset(
    {
        "nfz_listing",
        "nfz_signal_listing",
        "nfz_jgp_listing",
        "nfz_icd9_companion_page",
        "aotmit_listing",
    }
)
_KINDS = frozenset(
    {
        "nfz_order_detail",
        "nfz_signal_detail",
        "nfz_jgp_detail",
        "nfz_jgp_file",
        "czs_dictionary",
        "czs_modtar_display",
        "nfz_icd9_companion",
        "rsk_icd9_crosscheck",
        "aotmit_tariff_notice",
        "eli_dictionary",
        "eli_base_act",
    }
)
_ELI_TITLES = {
    "specialties": "Kody specjalności komórek organizacyjnych",
    "discharge_modes": "Kody trybu wypisu ze szpitala",
}


def _require(condition: bool) -> None:
    if not condition:
        raise WatcherRuntimeError("NFZ public evidence is incomplete or inconsistent")


def _timestamp(value: int) -> str:
    return (
        datetime.fromtimestamp(value / 1000, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")


def _evidence(observation: CommittedObservation) -> dict[str, object]:
    return {
        "source_url": observation.source_identity,
        "sha256": observation.response_sha256,
        "observed_at": _timestamp(observation.observed_at_ms),
    }


def build_nfz_projection(snapshot: CommittedObservationRevision) -> bytes:
    try:
        return _build(snapshot)
    except ValidationError, KeyError, TypeError, ValueError, OverflowError:
        # Pydantic and arbitrary stored metadata must not enter operational logs.
        raise WatcherRuntimeError("NFZ public projection validation failed") from None


def _build(snapshot: CommittedObservationRevision) -> bytes:
    _require(len(snapshot.observations) <= 512)
    by_kind: dict[str, list[CommittedObservation]] = {}
    identities: set[tuple[str, str]] = set()
    for observation in snapshot.observations:
        _require(observation.observation_kind in _KINDS | _IGNORED)
        key = (observation.observation_kind, observation.source_identity)
        _require(
            key not in identities
            and observation.observed_at_ms <= snapshot.committed_at_ms
        )
        identities.add(key)
        by_kind.setdefault(observation.observation_kind, []).append(observation)
    dictionaries = by_kind.get("czs_dictionary", [])
    _require(len(dictionaries) <= 2)
    codes = [item.metadata["code"] for item in dictionaries]
    _require(len(set(cast(list[str], codes))) == len(codes))
    displays = by_kind.get("czs_modtar_display", [])
    companions = by_kind.get("nfz_icd9_companion", [])
    crosschecks = by_kind.get("rsk_icd9_crosscheck", [])
    _require(len(displays) <= 1 and len(companions) <= 1 and len(crosschecks) <= 1)
    icd9 = next(
        (item for item in dictionaries if item.metadata["code"] == "ICD9"), None
    )
    modtar = next(
        (item for item in dictionaries if item.metadata["code"] == "MODTAR"), None
    )
    versions: set[str] = set()
    for item in companions + crosschecks:
        _require(
            icd9 is not None and item.metadata["czs_xml_sha256"] == icd9.response_sha256
        )
        version = item.metadata[
            "version"
            if item.observation_kind == "nfz_icd9_companion"
            else "czs_version"
        ]
        _require(isinstance(version, str))
        versions.add(cast(str, version))
    _require(len(versions) <= 1)
    if displays:
        matched_display = displays[0]
        _require(
            modtar is not None
            and matched_display.metadata["pinned_xml_sha256"] == modtar.response_sha256
            and matched_display.metadata["technical_generation"]
            == modtar.metadata["generation"]
            and matched_display.metadata["soap_sha256"]
            == modtar.metadata["soap_sha256"]
        )
    files = by_kind.get("nfz_jgp_file", [])
    used_files: set[str] = set()
    entries: list[dict[str, object]] = []
    bases = by_kind.get("eli_base_act", [])
    used_bases: set[str] = set()
    for kind in sorted(
        _KINDS
        - {"nfz_jgp_file", "czs_modtar_display", "rsk_icd9_crosscheck", "eli_base_act"}
    ):
        for observation in by_kind.get(kind, []):
            metadata = observation.metadata
            entry = _evidence(observation)
            if kind == "nfz_order_detail":
                entry.update(
                    channel="order",
                    title="Zarządzenie " + cast(str, metadata["act_number"]),
                    act_number=metadata["act_number"],
                    baw_document_id=metadata["baw_document_id"],
                    published_date=metadata["published_date"],
                )
            elif kind in ("nfz_signal_detail", "nfz_jgp_detail"):
                channel = "jgp" if kind == "nfz_jgp_detail" else metadata["channel"]
                _require(
                    channel in ("news", "project", "jgp")
                    and (kind == "nfz_jgp_detail" or channel != "jgp")
                )
                selected: list[dict[str, object]] = []
                if kind == "nfz_jgp_detail":
                    urls = metadata["file_urls"]
                    _require(isinstance(urls, list) and 1 <= len(urls) <= 8)
                    matched = [
                        file
                        for file in files
                        if file.metadata["item_id"] == metadata["item_id"]
                    ]
                    _require(
                        len(matched) == len(cast(list[object], urls))
                        and {file.source_identity for file in matched}
                        == set(cast(list[str], urls))
                    )
                    for file in sorted(matched, key=lambda item: item.source_identity):
                        _require(
                            file.metadata["detail_sha256"]
                            == observation.response_sha256
                            and file.source_identity not in used_files
                        )
                        used_files.add(file.source_identity)
                        selected.append(_evidence(file))
                entry.update(
                    channel=channel,
                    title=metadata["title"],
                    item_id=metadata["item_id"],
                    published_date=metadata["published_date"],
                    files=selected,
                )
            elif kind == "czs_dictionary":
                code = metadata["code"]
                display = displays[0] if code == "MODTAR" and displays else None
                entry.update(
                    channel="czs",
                    title="Słownik CZS " + cast(str, code),
                    code=code,
                    generation=metadata["generation"],
                    generated_at=metadata["generated_at"],
                    year=metadata["year"],
                    grouper_type=metadata["grouper_type"],
                    version=next(iter(versions), None)
                    if code == "ICD9"
                    else display.metadata["display_label"]
                    if display
                    else None,
                    published_date=display.metadata["published_date"]
                    if display
                    else None,
                    publisher_valid_from=display.metadata["effective_from"]
                    if display
                    else None,
                    display_evidence=_evidence(display) if display else None,
                )
            elif kind == "nfz_icd9_companion":
                entry.update(
                    channel="icd9_companion",
                    title="Plik ICD-9 PL NFZ",
                    version=metadata["version"],
                    czs_xml_sha256=metadata["czs_xml_sha256"],
                )
            elif kind == "eli_dictionary":
                matched = [
                    item
                    for item in bases
                    if item.metadata["dictionary"] == metadata["dictionary"]
                    and item.response_sha256 == metadata["base_sha256"]
                    and item.source_identity not in used_bases
                ]
                _require(len(matched) == 1)
                used_bases.add(matched[0].source_identity)
                entry.update(
                    channel="eli",
                    title=_ELI_TITLES[cast(str, metadata["dictionary"])],
                    dictionary=metadata["dictionary"],
                    eli=metadata["eli"],
                    base_eli=metadata["base_eli"],
                    in_force=metadata["in_force"],
                    findings=metadata["findings"],
                    base_evidence=_evidence(matched[0]),
                )
            else:
                entry.update(
                    channel="aotmit",
                    title=metadata["title"],
                    published_date=metadata["published_date"],
                )
            entries.append(entry)
    _require(len(used_files) == len(files) and len(used_bases) == len(bases))
    projection = NfzProjection.model_validate_json(
        _canonical(
            {
                "through_state_revision": snapshot.state_revision,
                "observed_at": _timestamp(snapshot.committed_at_ms),
                "entries": sorted(
                    entries,
                    key=lambda entry: (
                        cast(str, entry["channel"]),
                        cast(str, entry["source_url"]),
                    ),
                ),
            }
        )
    )
    payload = _canonical(projection.model_dump(mode="json"))
    _require(len(payload) <= MAX_PUBLIC_BYTES)
    return payload


@final
class NfzPublicProjectionPublisher:
    def __init__(self, generations: ImmutableGenerationPublisher) -> None:
        self._generations = generations

    def publish(self, store: ControlStore, run: CommittedRun) -> None:
        if run.source_id != "nfz":
            return
        payload = build_nfz_projection(store.load_reconciled_observations(run))
        digest = hashlib.sha256(payload).hexdigest()
        manifest = _canonical(
            {
                "schema_version": 1,
                "source": "NFZ",
                "state_revision": run.committed_state_revision,
                "projection_path": PROJECTION_PATH,
                "projection_sha256": digest,
                "projection_size_bytes": len(payload),
                "redistribution": {"schema_version": 1, "artifacts": []},
            }
        )
        _ = self._generations.publish(
            run.committed_state_revision,
            digest,
            {"manifest.json": manifest, PROJECTION_PATH: payload},
        )

    def restore(self, store: ControlStore) -> None:
        run = store.latest_recovery_reconciled_run("nfz")
        if run is not None:
            self.publish(store, run)
