"""Synthetic adapter bundle, not publisher/archive acceptance evidence."""

import hashlib
import json
import sqlite3
from io import BytesIO
from pathlib import Path
from typing import cast

from minos.grouper.szp_2026 import ALGORITHM_SHA256
from minos.ingest.komunikat_manifest import KomunikatManifest
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import ArtifactManifest, canonical_json
from minos.store.reference import read_artifact_manifest
from tests.fixtures.komunikat import stage_komunikat
from tests.integration.test_jgp_condition_consumers import consumer_bundle


def stage_dictionaries(root: Path) -> dict[str, object]:
    """Tiny synthetic legal tables; real transcriptions are checked separately."""
    directory = root / "blobs" / "sha256"
    directory.mkdir(parents=True, exist_ok=True)
    pins: list[dict[str, object]] = []
    for act in ("2019/173", "2025/1588"):
        content = f"synthetic act {act}".encode()
        digest = hashlib.sha256(content).hexdigest()
        _ = (directory / digest).write_bytes(content)
        pins.append(
            {
                "source_url": f"https://api.sejm.gov.pl/eli/acts/DU/{act}/text.pdf",
                "sha256": digest,
                "size_bytes": len(content),
                "fetched_at": "2026-10-08T12:00:00Z",
            }
        )
    common = {"publisher": "Minister Zdrowia", "license": "synthetic"}
    return {
        "specialties": {
            **common,
            "release_id": "specialty-synthetic",
            "act": "synthetic annex 2",
            "annex": "Załącznik nr 2",
            "notices": ["synthetic"],
            "source": pins[0],
            "reviewed_on": "2026-10-08",
            "children_rule": "§ 9 ust. 4",
            "entries": [
                {
                    "code": "1000",
                    "name": "Poradnia chorób wewnętrznych",
                    "section": "Poradnie specjalistyczne",
                },
                {
                    "code": "4000",
                    "name": "Oddział chorób wewnętrznych",
                    "section": "Oddziały szpitalne",
                },
                {
                    "code": "4401",
                    "name": "Oddział neonatologiczny",
                    "section": "Oddziały szpitalne",
                },
            ],
        },
        "discharge_modes": {
            **common,
            "release_id": "discharge-synthetic",
            "act": "synthetic annex 4",
            "annex": "Załącznik nr 4",
            "notices": ["synthetic"],
            "source": pins[1],
            "reviewed_on": "2026-10-08",
            "modes": [
                {
                    "point": 1,
                    "code": 1,
                    "name": "zakończenie procesu terapeutycznego lub diagnostycznego",
                },
                {"point": 2, "code": 9, "name": "zgon pacjenta"},
            ],
        },
    }


# Synthetic codes; the real catalogs are checked by opt-in public-source tests.
W_PRODUCT = "5.52.01.0001363"
NEUTRAL_PRODUCT = "5.53.01.0000001"
PRODUCT_SHEETS = {
    "1w": ("Kod produktu rozliczeniowego", {W_PRODUCT: "Synthetic excluded product"}),
    "1b": ("Kod produktu", {NEUTRAL_PRODUCT: "Synthetic separate product"}),
}


def _product_workbook(header: str, rows: dict[str, str]) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Arkusz1"
    sheet.append(["Załącznik nr 1 (synthetic)"])
    sheet.append(["Lp.", "Zakres", header, "Nazwa produktu rozliczeniowego"])
    sheet.append([1, 2, 3, 4])
    for ordinal, (code, name) in enumerate(rows.items(), 1):
        sheet.append([ordinal, "synthetic", code, name])
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def stage_product_catalogs(root: Path, document: int) -> list[dict[str, object]]:
    """One act's catalogs, bound to the synthetic JGP catalog release."""
    directory = root / "blobs" / "sha256"
    directory.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, object]] = []
    for number, (sheet, (header, rows)) in enumerate(PRODUCT_SHEETS.items(), 1):
        content = _product_workbook(header, rows)
        digest = hashlib.sha256(content).hexdigest()
        _ = (directory / digest).write_bytes(content)
        files.append(
            {
                "sheet": sheet,
                "file_name": f"Zalacznik{sheet}.xlsx",
                "source_url": f"https://baw.nfz.gov.pl/api/file/4/{number}",
                "sha256": digest,
                "size_bytes": len(content),
                "fetched_at": "2026-10-09T07:00:00Z",
                "parsed": True,
                "worksheet": "Arkusz1",
                "code_header": header,
                "expected_rows": len(rows),
                "expected_codes": len(rows),
            }
        )
    return [
        {
            "release_id": "products-synthetic",
            "catalog_release_id": "jgp-catalog-synthetic",
            "baw_document_id": document,
            "attachments": ["Zalacznik1a.xlsx", "Zalacznik1b.xlsx", "Zalacznik1w.xlsx"],
            "notices": ["synthetic"],
            "files": files,
        }
    ]


def grouping_bundle(root: Path, *, products: bool = False) -> Path:
    source = consumer_bundle(root)
    manifest = read_artifact_manifest(source).dataset
    notice = stage_komunikat(root, "icd9-czs-synthetic-v1")
    manifest = DatasetManifest.model_validate_json(
        json.dumps(
            {
                **manifest.model_dump(mode="json"),
                "komunikaty": [notice.model_dump(mode="json")],
                "search_profile": "polish-folded-1",
                "grouping_dictionaries": stage_dictionaries(root),
                "product_catalogs": stage_product_catalogs(
                    root, manifest.catalogs[0].baw_document_id
                )
                if products
                else [],
            }
        )
    )
    output = root / "grouping"
    _ = build_reference_artifact(manifest, root, output)
    database = output / "minos.db"
    database.chmod(0o600)
    # The adapter tests trust a constructed domain snapshot, like the existing
    # pipeline fixtures. Only opt-in real-source tests prove publisher provenance.
    pins = notice.model_dump(mode="json")
    cast(dict[str, object], pins["algorithm"])["sha256"] = ALGORITHM_SHA256
    with sqlite3.connect(database) as connection:
        _ = connection.execute(
            "UPDATE algorithm_release SET manifest_json=?",
            (
                canonical_json(
                    KomunikatManifest.model_validate_json(json.dumps(pins))
                ).decode(),
            ),
        )
        _ = connection.execute(
            "UPDATE procedure_rank SET code='100.01', rank=3 WHERE grouper_type='SZP' AND source_row=4"
        )
    artifact_path = output / "manifest.json"
    artifact = cast(dict[str, object], json.loads(artifact_path.read_bytes()))
    cast(dict[str, object], artifact["dataset"])["komunikaty"] = [pins]
    artifact["database_sha256"] = hashlib.sha256(database.read_bytes()).hexdigest()
    artifact["database_size_bytes"] = database.stat().st_size
    _ = artifact_path.write_bytes(
        canonical_json(ArtifactManifest.model_validate_json(json.dumps(artifact)))
    )
    database.chmod(0o444)
    return output


def grouping_body() -> dict[str, object]:
    return {
        "schema_version": 1,
        "service_date": "2026-10-05",
        "reporting_month": "2026-10-01",
        "declared_modifiers": [],
        "encounter": {
            "admitted_on": "2026-10-01",
            "discharged_on": "2026-10-05",
            "hospitalization_type": "hospitalization",
            "age_completed_years": 40,
            "principal_diagnosis": {"code": "G60.4", "hospitalization_level": True},
            "stays": [
                {
                    "identifier": "ward",
                    "department": "4000",
                    "admitted_on": "2026-10-01",
                    "discharged_on": "2026-10-05",
                }
            ],
            "procedures": [
                {"code": "100.01", "performed_on": "2026-10-02", "stay_id": "ward"}
            ],
            "product_reporting": "complete",
            "settlement_products": [],
        },
    }
