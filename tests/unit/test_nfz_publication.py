import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from minos.ingest.watchers.control import (
    CommittedObservation,
    CommittedObservationRevision,
    JsonValue,
)
from minos.ingest.watchers.nfz.public_models import NfzProjection, official_url
from minos.ingest.watchers.nfz.publication import build_nfz_projection
from minos.ingest.watchers.protocols import WatcherRuntimeError
from minos.ingest.watchers.publication import (
    ImmutableGenerationPublisher,
    prepare_public_root,
)

NOW = 1_800_000_000_000
ICD9 = (
    "https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=ICD9&nrGen=93"
)
MODTAR = "https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=MODTAR&nrGen=102"
JGP = "https://www.nfz.gov.pl/dla-swiadczeniodawcy/jednorodne-grupy-pacjentow/komunikaty-jgp/komunikat,54.html"
FILE = "https://www.nfz.gov.pl/download/gfx/nfz/pl/defaultaktualnosci/354/54/1/synthetic.docx"


def observation(
    kind: str, url: str, digest: str, metadata: dict[str, object]
) -> CommittedObservation:
    return CommittedObservation(
        kind + ":" + digest,
        kind,
        url,
        digest * 64,
        NOW,
        cast(
            dict[str, JsonValue],
            {
                **metadata,
                "response_headers": {"secret": "PRIVATE_SENTINEL"},
                "archive_key": "PRIVATE_SENTINEL",
            },
        ),
    )


def snapshot() -> CommittedObservationRevision:
    return CommittedObservationRevision(
        3,
        NOW + 1,
        (
            observation(
                "czs_dictionary",
                ICD9,
                "a",
                {
                    "code": "ICD9",
                    "generation": 93,
                    "generated_at": "2026-09-24T08:58:10",
                    "year": None,
                    "grouper_type": None,
                    "soap_sha256": "f" * 64,
                },
            ),
            observation(
                "rsk_icd9_crosscheck",
                "not-exported",
                "b",
                {"czs_version": "5.90", "czs_xml_sha256": "a" * 64},
            ),
            observation(
                "czs_dictionary",
                MODTAR,
                "c",
                {
                    "code": "MODTAR",
                    "generation": 102,
                    "generated_at": "2026-09-24T10:58:10",
                    "year": 2026,
                    "grouper_type": "SZP",
                    "soap_sha256": "f" * 64,
                },
            ),
            observation(
                "czs_modtar_display",
                "https://slowniki.nfz.gov.pl/WersjeSlownikow/Index/7364?kod=MODTAR",
                "d",
                {
                    "technical_generation": 102,
                    "pinned_xml_sha256": "c" * 64,
                    "display_label": "202664",
                    "published_date": "2026-09-24",
                    "effective_from": "2026-10-01",
                    "soap_sha256": "f" * 64,
                },
            ),
            observation(
                "nfz_jgp_detail",
                JGP,
                "e",
                {
                    "item_id": 54,
                    "title": "Synthetic <img src=x onerror=alert(1)>",
                    "published_date": "2026-09-01",
                    "file_urls": [FILE],
                },
            ),
            observation(
                "nfz_jgp_file", FILE, "f", {"item_id": 54, "detail_sha256": "e" * 64}
            ),
        ),
    )


def test_projection_preserves_meaning_and_excludes_private_metadata() -> None:
    source = snapshot()
    payload = build_nfz_projection(source)
    result = NfzProjection.model_validate_json(payload)
    assert result.source == "NFZ" and result.through_state_revision == 3
    assert result.coverage == "latest_reconciled_observation_only"
    assert len(result.entries) == 3
    assert b"PRIVATE_SENTINEL" not in payload and b"archive_key" not in payload
    assert b"2026-09-24T10:58:10" in payload and b"2026-09-24T10:58:10Z" not in payload
    data = cast(dict[str, object], json.loads(payload))
    dictionaries = [
        entry
        for entry in cast(list[dict[str, object]], data["entries"])
        if entry["channel"] == "czs"
    ]
    assert [item["version"] for item in dictionaries] == ["5.90", "202664"]
    assert dictionaries[1]["published_date"] == "2026-09-24"
    assert dictionaries[1]["publisher_valid_from"] == "2026-10-01"
    assert dictionaries[1]["service_applicability"] == "not_reviewed"
    assert (
        build_nfz_projection(
            replace(source, observations=tuple(reversed(source.observations)))
        )
        == payload
    )


def test_legacy_unknown_version_and_empty_snapshot_are_explicit() -> None:
    source = snapshot()
    payload = build_nfz_projection(
        replace(source, observations=(source.observations[0],))
    )
    assert b'"version":null' in payload
    result = NfzProjection.model_validate_json(
        build_nfz_projection(replace(source, observations=()))
    )
    assert result.entries == () and result.redistribution.artifacts == ()


@pytest.mark.parametrize(
    ("index", "key", "value"),
    [
        (1, "czs_xml_sha256", "0" * 64),
        (3, "technical_generation", 101),
        (3, "pinned_xml_sha256", "0" * 64),
        (3, "soap_sha256", "0" * 64),
        (5, "detail_sha256", "0" * 64),
        (5, "item_id", 55),
        (4, "file_urls", []),
        (4, "published_date", "2026-02-30"),
        (0, "generated_at", "not-a-date"),
        (0, "generated_at", "2026-09-24T10:00:00+01:99"),
        (0, "generation", True),
    ],
)
def test_bad_joins_and_dates_cannot_be_published(
    index: int, key: str, value: JsonValue
) -> None:
    source = snapshot()
    items = list(source.observations)
    items[index] = replace(items[index], metadata={**items[index].metadata, key: value})
    with pytest.raises(WatcherRuntimeError):
        _ = build_nfz_projection(replace(source, observations=tuple(items)))


@pytest.mark.parametrize(
    "suffix", ["#private", "&token=secret", "&nrGen=94", "%00", " "]
)
def test_official_host_does_not_authorize_extra_query_or_fragment(suffix: str) -> None:
    with pytest.raises(ValueError):
        _ = official_url(ICD9 + suffix, "czs")


@pytest.mark.parametrize(
    "url",
    [
        "http://www.nfz.gov.pl/download/gfx/nfz/pl/defaultaktualnosci/354/54/1/synthetic.docx",
        FILE.replace("www.nfz.gov.pl", "www.nfz.gov.pl:443"),
        FILE.replace("www.nfz.gov.pl", "user@www.nfz.gov.pl"),
        FILE.replace("synthetic.docx", "../synthetic.docx"),
    ],
)
def test_unsafe_official_links_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        _ = official_url(url, "jgp_file")


def test_unknown_output_fields_and_duplicate_evidence_are_refused() -> None:
    source = snapshot()
    data = cast(dict[str, object], json.loads(build_nfz_projection(source)))
    data["archive_key"] = "PRIVATE_SENTINEL"
    with pytest.raises(ValidationError):
        _ = NfzProjection.model_validate_json(json.dumps(data))
    with pytest.raises(WatcherRuntimeError):
        _ = build_nfz_projection(
            replace(
                source, observations=source.observations + (source.observations[0],)
            )
        )
    with pytest.raises(WatcherRuntimeError):
        _ = build_nfz_projection(replace(source, observations=source.observations * 86))


def test_shared_browser_contract_and_interrupted_nfz_generation(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures/nfz_projection.json"
    result = NfzProjection.model_validate_json(fixture.read_bytes())
    assert len(result.entries) == 8
    prepare_public_root(tmp_path)

    def files(revision: int) -> tuple[str, dict[str, bytes]]:
        payload = build_nfz_projection(replace(snapshot(), state_revision=revision))
        digest = hashlib.sha256(payload).hexdigest()
        return digest, {
            "api/v1/provenance/nfz.json": payload,
            "manifest.json": json.dumps(
                {
                    "schema_version": 1,
                    "source": "NFZ",
                    "state_revision": revision,
                    "projection_path": "api/v1/provenance/nfz.json",
                    "projection_sha256": digest,
                    "projection_size_bytes": len(payload),
                    "redistribution": {"schema_version": 1, "artifacts": []},
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        }

    publisher = ImmutableGenerationPublisher(tmp_path, source="NFZ")
    digest, first = files(1)
    _ = publisher.publish(1, digest, first)
    before = (tmp_path / "current/api/v1/provenance/nfz.json").read_bytes()

    def interrupt(phase: str) -> None:
        if phase == "generation_fsynced":
            raise RuntimeError("interrupted before switching current")

    digest, second = files(2)
    with pytest.raises(RuntimeError, match="interrupted"):
        _ = ImmutableGenerationPublisher(
            tmp_path, source="NFZ", fault=interrupt
        ).publish(2, digest, second)
    assert (tmp_path / "current/api/v1/provenance/nfz.json").read_bytes() == before
    _ = publisher.publish(2, digest, second)
    assert (tmp_path / "current/api/v1/provenance/nfz.json").read_bytes() == second[
        "api/v1/provenance/nfz.json"
    ]
