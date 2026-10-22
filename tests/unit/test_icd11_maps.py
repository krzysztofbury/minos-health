import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from minos.domain.reference import SourceError
from minos.ingest.icd11_maps import parse_icd11_map
from minos.ingest.icd11_maps_manifest import Icd11MapFile, Icd11Maps
from tests.fixtures.icd11 import MAP_UUIDS, map_workbook, maps


def _pin(content: bytes, *, rows: int = 2) -> Icd11MapFile:
    return Icd11MapFile.model_validate_json(
        json.dumps(
            {
                "kind": "10to11_one",
                "file_name": "10To11MapdoJednejKategorii_2026-01.xlsx",
                "source_url": "https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/"
                + MAP_UUIDS["10to11_one"],
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "fetched_at": "2026-10-22T10:50:56Z",
                "expected_rows": rows,
            }
        )
    )


@pytest.mark.parametrize(
    "header,rows,reason",
    [
        (
            ("10ClassKind", "10DepthInKind", "icd10Title", "icd10Code"),
            2,
            "header differs",
        ),
        (None, 3, "row count differs"),
    ],
    ids=["moved_column", "row_count"],
)
def test_a_layout_or_count_change_fails_instead_of_shifting_columns(
    header: tuple[str, ...] | None, rows: int, reason: str
) -> None:
    content = map_workbook("10to11_one", header=header)

    with pytest.raises(SourceError, match=reason):
        _ = parse_icd11_map(BytesIO(content), _pin(content, rows=rows))


def test_maps_need_each_who_table_once_and_one_version(tmp_path: Path) -> None:
    valid = maps(tmp_path, "icd11-rsk-2026-01-000000000000")
    files = cast(list[dict[str, object]], valid["files"])
    newer = {**files[0], "file_name": "10To11MapdoJednejKategorii_2027-01.xlsx"}

    _ = Icd11Maps.model_validate_json(json.dumps(valid))
    with pytest.raises(ValidationError, match="each WHO table"):
        _ = Icd11Maps.model_validate_json(
            json.dumps({**valid, "files": [files[0]] * 3})
        )
    with pytest.raises(ValidationError, match="different WHO versions"):
        _ = Icd11Maps.model_validate_json(
            json.dumps({**valid, "files": [newer, *files[1:]]})
        )
    with pytest.raises(ValidationError, match="does not match its kind"):
        _ = Icd11MapFile.model_validate_json(
            json.dumps(
                {**files[0], "file_name": "11To10MapdoJednejKategorii_2026-01.xlsx"}
            )
        )
