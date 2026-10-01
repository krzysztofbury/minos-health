import os
from pathlib import Path

import pytest

from minos.domain.reference import ReferenceNode, ReferenceRelation
from minos.ingest.icd9 import parse_icd9_xml
from minos.ingest.icd9_workbook import parse_icd9_workbook
from minos.ingest.reference_artifact import verified_source
from minos.ingest.reference_manifest import DatasetManifest

ROOT = Path(__file__).parents[2]


@pytest.mark.icd9_live
@pytest.mark.parametrize(
    "filename", ["icd9-czs-reviewed.json", "icd9-nfz-workbook-reviewed.json"]
)
def test_pinned_private_source_contract(filename: str, tmp_path: Path) -> None:
    archive = os.environ.get("MINOS_ICD9_PRIVATE_ARCHIVE")
    if not archive:
        pytest.skip("private ICD-9 archive opt-in is not configured")
    source = DatasetManifest.model_validate_json(
        (ROOT / "manifests/sources" / filename).read_bytes()
    ).sources[0]
    with verified_source(Path(archive), source, staging_directory=tmp_path) as snapshot:
        events = (
            parse_icd9_xml(snapshot, source)
            if source.origin == "czs"
            else parse_icd9_workbook(snapshot, source)
        )
        nodes = concepts = relations = 0
        statuses: dict[str, int] = {}
        notes = numeric = 0
        for event in events:
            if isinstance(event, ReferenceNode):
                nodes += 1
                concepts += event.lookup_code is not None
                attributes = dict(event.attributes)
                if "status" in attributes:
                    statuses[attributes["status"]] = (
                        statuses.get(attributes["status"], 0) + 1
                    )
                notes += event.kind == "text"
                numeric += attributes.get("cell_type") == "int"
            elif isinstance(event, ReferenceRelation):
                relations += 1
                assert event.parent_ordinal < event.child_ordinal
    assert (nodes, concepts, relations) == (
        source.expected.nodes,
        source.expected.concepts,
        source.expected.relations,
    )
    if source.origin == "czs":
        assert statuses == {"A": 10058, "N": 121}
        assert notes == 0
    else:
        assert statuses == {}
        assert notes == 2993
        assert numeric == 2
