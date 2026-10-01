from collections.abc import Iterator
from typing import TypedDict, cast

import pytest
from fastapi.testclient import TestClient

from minos.http.app import create_app
from minos.ingest.reference_artifact import build_reference_artifact
from tests.integration.test_canonical_reference import canonical_manifest

ICD10 = "icd10-synthetic-v1"
ICD9 = "icd9-czs-synthetic-v1"
WORKBOOK = "icd9-nfz-workbook-synthetic-v1"


class Child(TypedDict):
    ordinal: int
    code: str
    label: str
    has_children: bool
    canonical: bool


class Note(TypedDict):
    kind: str
    code: str
    text: str


class PathItem(TypedDict):
    code: str


class Node(TypedDict):
    code: str
    notes: list[Note]
    path: list[PathItem]


class Unplaced(TypedDict):
    code: str
    attributes: list[dict[str, str]]


class Tree(TypedDict):
    children: list[Child]
    node: Node | None
    hierarchy_release_id: str
    supporting_hierarchy: bool
    unplaced: list[Unplaced]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    root = tmp_path_factory.mktemp("tree")
    bundle = root / "bundle"
    _ = build_reference_artifact(canonical_manifest(root), root, bundle)
    with TestClient(create_app(bundle)) as started:
        yield started


def browse(client: TestClient, release: str, ordinal: int) -> Tree:
    response = client.get(f"/api/v1/tree/{release}/{ordinal}")
    assert response.status_code == 200
    return cast(Tree, response.json())


def child(tree: Tree, code: str) -> Child:
    return next(item for item in tree["children"] if item["code"] == code)


def test_tree_follows_source_order_through_anonymous_containers(
    client: TestClient,
) -> None:
    roots = browse(client, ICD10, 0)
    assert [item["code"] for item in roots["children"]] == ["E00-E99", "M00", "U99.9"]
    assert roots["node"] is None
    chapter = browse(client, ICD10, child(roots, "E00-E99")["ordinal"])
    # E11 sits inside an unnamed source container and is still a direct child.
    assert [item["code"] for item in chapter["children"]] == ["E11"]
    category = browse(client, ICD10, child(chapter, "E11")["ordinal"])
    assert [item["code"] for item in category["children"]] == ["E11.0", "E11.2"]
    assert not child(category, "E11.0")["has_children"]
    variant = browse(client, ICD10, child(category, "E11.0")["ordinal"])
    assert variant["node"] is not None
    assert [item["code"] for item in variant["node"]["path"]] == ["E00-E99", "E11"]
    assert [note["text"] for note in variant["node"]["notes"]] == [
        "Przykład opisowy",
        "Przykład opisowy",
    ]
    modifier = browse(client, ICD10, child(roots, "M00")["ordinal"])
    assert modifier["node"] is not None
    assert modifier["node"]["notes"] == [
        {
            "kind": "modifier",
            "code": "0",
            "text": "Próbny modyfikator",
            "source": "canonical",
        }
    ]


def test_supporting_hierarchy_keeps_canonical_titles_and_unplaced_codes(
    client: TestClient,
) -> None:
    roots = browse(client, ICD9, 0)
    assert roots["supporting_hierarchy"]
    assert roots["hierarchy_release_id"] == WORKBOOK
    assert [item["code"] for item in roots["children"]] == ["AA", "88"]
    assert not child(roots, "88")["canonical"]
    # CZS codes without a workbook position are listed, never re-parented.
    assert [item["code"] for item in roots["unplaced"]] == ["00.00001"]
    assert {"name": "Status w źródle", "key": "status", "value": "A"} in roots[
        "unplaced"
    ][0]["attributes"]
    chapter = browse(client, ICD9, child(browse(client, ICD9, 1), "A")["ordinal"])
    # The workbook's "Different synthetic title" never replaces the CZS title.
    assert child(chapter, "A01")["label"] == "  Badanie moczu  "
    assert child(chapter, "A01")["canonical"]
    assert not child(chapter, "A03")["canonical"]


@pytest.mark.parametrize(
    "path",
    [
        f"/api/v1/tree/{WORKBOOK}/0",
        "/api/v1/tree/unknown-release/0",
        f"/api/v1/tree/{ICD10}/99999",
        f"/api/v1/tree/{ICD10}/01",
        f"/api/v1/tree/{ICD10}/-1",
        f"/api/v1/tree/{ICD10}/12345678",
        f"/api/v1/tree/{ICD10.upper()}/0",
    ],
)
def test_tree_rejects_supporting_unknown_and_non_structural_nodes(
    client: TestClient, path: str
) -> None:
    assert client.get(path).status_code == 404


def test_tree_rejects_text_nodes_and_url_queries(client: TestClient) -> None:
    category = browse(client, ICD10, child(browse(client, ICD10, 1), "E11")["ordinal"])
    variant = child(category, "E11.0")["ordinal"]
    # The note under E11.0 is a text node with the next source ordinal.
    assert client.get(f"/api/v1/tree/{ICD10}/{variant + 1}").status_code == 404
    response = client.get(f"/api/v1/tree/{ICD10}/0?q=cukrzyca")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "queries_must_not_be_in_urls"
