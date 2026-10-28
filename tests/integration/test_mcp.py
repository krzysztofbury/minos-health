import json
import logging
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import jsonschema  # type: ignore[import-untyped]
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from minos.http import mcp
from minos.http.app import create_app
from minos.http.mcp_docs import EXAMPLES, catalog
from tests.fixtures.grouping_interface import grouping_body, grouping_bundle
from tests.integration.test_icd11_reference import icd11_bundle
from tests.integration.test_source_pages import publish, rsk

type Json = dict[str, object]
_SNAPSHOT = Path(__file__).parents[1] / "golden/mcp-tools.json"
_TOOL_VERBS = ("get_", "search_", "browse_", "map_", "compare_", "group_")
_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-11-25",
}


def _rpc(client: TestClient, method: str, params: Json | None = None) -> Json:
    response = client.post(
        "/mcp",
        headers=_HEADERS,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )
    assert response.status_code == 200
    return cast(Json, cast(Json, response.json())["result"])


def _call(client: TestClient, name: str, arguments: Json) -> tuple[bool, Json]:
    result = _rpc(client, "tools/call", {"name": name, "arguments": arguments})
    structured = cast(Json, result["structuredContent"])
    text = cast(list[Json], result["content"])[0]["text"]
    # The text block is the same JSON value as the structured content.
    assert json.loads(cast(str, text)) == structured
    return bool(result.get("isError")), structured


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    root = tmp_path_factory.mktemp("mcp")
    public = root / "public"
    _ = publish(public, "RSK", rsk())
    app = create_app(icd11_bundle(root), public_directory=public)
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def grouping(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    app = create_app(grouping_bundle(tmp_path_factory.mktemp("mcp-grouping")))
    with TestClient(app) as client:
        yield client


def test_catalog_is_read_only_typed_compact_and_documented(
    reference: TestClient,
) -> None:
    tools = cast(list[Json], _rpc(reference, "tools/list")["tools"])

    names = sorted(cast(str, tool["name"]) for tool in tools)
    assert names == sorted(EXAMPLES)
    # 17.5 KiB on 2026-10-22, 20.1 KiB with parameter descriptions (#266),
    # 19.9 KiB after the description rewrite (#268);
    # a doubling fails here.
    assert len(json.dumps(tools).encode()) < 32 * 1024
    for tool in tools:
        annotations = cast(Json, tool["annotations"])
        assert annotations["readOnlyHint"] is True
        assert annotations["destructiveHint"] is False
        assert tool["outputSchema"]
        assert tool["title"]
        properties = cast(Json, cast(Json, tool["inputSchema"]).get("properties", {}))
        for name, argument in properties.items():
            assert cast(Json, argument).get("description"), (tool["name"], name)
        assert '"title"' not in json.dumps(tool["inputSchema"])
        # The page's example call is valid against the published schema.
        jsonschema.validate(
            EXAMPLES[cast(str, tool["name"])], cast(Json, tool["inputSchema"])
        )
    assert sorted(tool.name for tool in catalog()) == names
    # Every description routes to a sibling, and only to tools that exist.
    for tool in tools:
        description = cast(str, tool["description"])
        mentioned: set[str] = set(re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", description))
        siblings = {name for name in mentioned if name.startswith(_TOOL_VERBS)}
        assert siblings - {tool["name"]}, tool["name"]
        assert siblings <= set(names), (tool["name"], siblings - set(names))


def test_served_tool_definitions_match_the_reviewed_snapshot(
    reference: TestClient,
) -> None:
    # Names, descriptions, schemas and annotations are what agents and
    # directories such as Glama score (#268); every change is reviewed here.
    # After a deliberate change: MINOS_UPDATE_MCP_SNAPSHOT=1 uv run pytest -k snapshot
    tools = sorted(
        cast(list[Json], _rpc(reference, "tools/list")["tools"]),
        key=lambda tool: cast(str, tool["name"]),
    )
    served = json.dumps(tools, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if os.environ.get("MINOS_UPDATE_MCP_SNAPSHOT") == "1":
        _ = _SNAPSHOT.write_text(served, encoding="utf-8")
    assert served == _SNAPSHOT.read_text(encoding="utf-8")


def test_the_registry_listing_names_this_server(reference: TestClient) -> None:
    listing = cast(Json, json.loads(Path("server.json").read_text()))
    info = cast(
        Json,
        _rpc(
            reference,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        )["serverInfo"],
    )

    assert listing["remotes"] == [
        {"type": "streamable-http", "url": mcp.SITE + mcp.MCP_PATH}
    ]
    assert listing["websiteUrl"] == info["websiteUrl"] == mcp.SITE + mcp.DOCS_PATH
    assert listing["title"] == info["title"]
    assert listing["version"] == info["version"]
    assert listing["icons"] == info["icons"]
    # /dla-agentow names the listing that registry clients search for.
    page = Path("apps/web/site/pages/agents.html").read_text()
    assert cast(str, listing["name"]) in page


def test_terminology_tools_answer_like_the_pages(reference: TestClient) -> None:
    _, found = _call(
        reference,
        "search_codes",
        {"query": "termin indeksu", "systems": ["icd11-pl", "icd10-pl"]},
    )
    _, icd11 = _call(reference, "get_code", {"system": "icd11-pl", "code": "1A00"})
    _, icd10 = _call(reference, "get_code", {"system": "icd10-pl", "code": "E11.0"})
    _, mapped = _call(
        reference, "map_icd10_icd11", {"code": "E11.0", "direction": "to_icd11"}
    )
    _, top = _call(reference, "browse_codes", {"system": "icd11-pl"})
    _, chapter = _call(
        reference, "browse_codes", {"system": "icd11-pl", "parent": "01"}
    )

    systems = cast(list[Json], found["systems"])
    assert [cast(list[Json], item["results"])[0]["code"] for item in systems[:1]] == [
        "1A00"
    ]
    assert cast(dict[str, str], icd11["attributes"])["uri"].endswith("/mms/3")
    assert cast(Json, icd11["notes"])["term"] == {
        "count": 1,
        "items": ["syntetyczny termin indeksu"],
    }
    assert icd11["jgp_lists"] is None
    assert icd10["jgp_lists"] is not None
    assert [row["icd11_code"] for row in cast(list[Json], mapped["rows"])] == [
        "1A00&XN0",
        "1A00&XN0",
    ]
    assert [child["code"] for child in cast(list[Json], top["children"])] == ["01"]
    # An ICD-11 block is a level with a node id but no code.
    block = cast(list[Json], chapter["children"])[0]
    assert (block["code"], block["title"], block["has_children"]) == (
        None,
        "Syntetyczny blok",
        True,
    )
    _, inside = _call(
        reference, "browse_codes", {"system": "icd11-pl", "node": block["node"]}
    )
    assert [child["code"] for child in cast(list[Json], inside["children"])] == [
        "1A00",
        "1A01",
    ]


def test_jgp_tools_cover_groups_lists_sections_and_dates(reference: TestClient) -> None:
    error, group = _call(
        reference, "get_jgp_group", {"code": "A01", "service_date": "2026-07-15"}
    )
    _, sections = _call(reference, "browse_jgp_catalog", {"service_date": "2026-07-15"})
    _, same = _call(
        reference,
        "compare_jgp_group",
        {"code": "A01", "before_date": "2026-07-15", "after_date": "2026-08-15"},
    )
    uncovered, before_catalogs = _call(
        reference,
        "compare_jgp_group",
        {"code": "A01", "before_date": "2026-05-15", "after_date": "2026-07-15"},
    )

    assert not error
    assert cast(Json, group["group"])["code"] == "A01"
    assert sections["sections"]
    assert same["changed"] == []
    assert cast(Json, same["before"])["release_id"] == "jgp-catalog-synthetic"
    assert (uncovered, before_catalogs) == (
        True,
        {"error": {"code": "NO_RELEASE_FOR_DATE"}},
    )


def test_group_case_summary_drops_traces_that_detail_keeps(
    grouping: TestClient,
) -> None:
    body = grouping_body()
    case = cast(Json, body["encounter"])

    _, short = _call(grouping, "group_case", {"case": case})
    _, full = _call(grouping, "group_case", {"case": case, "detail": "trace"})
    _, dictionaries = _call(grouping, "get_case_dictionaries", {})

    assert short["status"] == full["status"] == "evaluated"
    assert short["service_date"] == "2026-10-05"
    assert all("trace" not in group for group in cast(list[Json], short["groups"]))
    assert any("trace" in group for group in cast(list[Json], full["groups"]))
    assert dictionaries["hospitalization_types"] == [
        "hospitalization",
        "planned",
        "one_day",
    ]
    ward_codes = [
        str(item["code"])
        for item in cast(list[Json], cast(Json, dictionaries["specialties"])["entries"])
    ]
    assert ward_codes and all(code.startswith("4") for code in ward_codes)
    assert [
        cast(Json, dictionaries[name])["eli"]
        for name in ("specialties", "discharge_modes")
    ] == ["DU/2019/173", "DU/2025/1588"]


def test_source_status_summarises_the_published_projection(
    reference: TestClient,
) -> None:
    error, status = _call(reference, "get_source_status", {"source": "rsk"})
    missing, unavailable = _call(reference, "get_source_status", {"source": "baw"})

    assert not error
    assert status["items"]
    assert (missing, unavailable) == (True, {"error": {"code": "UNAVAILABLE"}})


@pytest.mark.parametrize(
    "name,arguments,code",
    [
        ("get_code", {"system": "icd10-pl", "code": "ZZZ.9"}, "NOT_FOUND"),
        ("get_code", {"system": "icd10-pl", "code": "bad code"}, "INVALID_INPUT"),
        ("search_codes", {"query": ""}, "INVALID_INPUT"),
        (
            "browse_codes",
            {"system": "icd11-pl", "parent": "01", "node": 1},
            "INVALID_INPUT",
        ),
        ("group_case", {"case": {"admitted_on": "x"}}, "INVALID_INPUT"),
    ],
)
def test_failures_are_closed_codes_without_input(
    reference: TestClient, name: str, arguments: Json, code: str
) -> None:
    error, payload = _call(reference, name, arguments)

    assert (error, payload) == (True, {"error": {"code": code}})


def test_a_result_over_the_bound_is_limit_not_a_cut(
    reference: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp, "MAX_RESULT_BYTES", 512)

    error, payload = _call(reference, "get_service_info", {})

    assert (error, payload) == (True, {"error": {"code": "LIMIT"}})


def test_logs_never_carry_tool_arguments(
    grouping: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    case = cast(Json, grouping_body()["encounter"])
    with caplog.at_level(logging.DEBUG):
        _ = _call(grouping, "group_case", {"case": case})
        _ = _call(grouping, "search_codes", {"query": "neuropatia unikalna"})
        # A code argument travels in the in-process request URL (#220).
        _ = _call(grouping, "get_code", {"system": "icd10-pl", "code": "Y35.7"})

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "G60.4" not in logged
    assert "neuropatia" not in logged
    assert "Y35.7" not in logged


def test_the_server_leaves_root_logging_alone() -> None:
    # The SDK's basicConfig is a no-op while pytest's handlers sit on the root.
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    root.handlers.clear()
    try:
        _ = catalog()

        assert (root.handlers, root.level) == ([], level)
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


def test_resources_and_prompts_are_listed(reference: TestClient) -> None:
    resources = cast(list[Json], _rpc(reference, "resources/list")["resources"])
    prompts = cast(list[Json], _rpc(reference, "prompts/list")["prompts"])
    about = cast(
        list[Json],
        _rpc(reference, "resources/read", {"uri": "minos://about"})["contents"],
    )

    assert {item["uri"] for item in resources} == {"minos://about", "minos://releases"}
    assert {item["name"] for item in prompts} == {
        "code-hospital-stay",
        "explain-jgp-group",
    }
    assert "Nothing a caller sends is stored" in cast(str, about[0]["text"])


def test_icd9_codes_resolve_in_the_hierarchy_the_tree_browses(
    reference: TestClient,
) -> None:
    # ICD-9 trees browse the NFZ workbook's hierarchy, numbered apart from the
    # CZS concepts; get_code answered with another code's node (#242).
    from minos.http.app import Tree
    from minos.http.reference import ReferencePool
    from minos.store.tree import all_codes

    app = cast(FastAPI, reference.app)
    pool = cast(ReferencePool, app.state.pool)
    trees = cast(dict[str, Tree], app.state.trees)
    icd9 = next(tree for tree in trees.values() if tree.system == "icd9-pl")
    assert icd9.hierarchy.supporting
    with pool.borrow() as lease:
        codes = all_codes(lease.connection, icd9.hierarchy)
    assert {"A", "AA"} <= set(codes)  # their concept and tree numbers differ
    release = next(key for key, tree in trees.items() if tree is icd9)
    # get_code serves codes with a CZS concept; workbook-only rows are not codes.
    served = [
        code
        for code in codes
        if reference.get(f"/api/v1/concepts/icd9-pl/{release}/{code}").status_code
        == 200
    ]
    assert {"A", "AA"} <= set(served)
    for code in served:
        failed, detail = _call(
            reference, "get_code", {"system": "icd9-pl", "code": code}
        )
        assert not failed, code
        assert detail["code"] == code
    failed, listing = _call(
        reference, "browse_codes", {"system": "icd9-pl", "parent": "A"}
    )
    assert not failed
    assert cast(Json, listing["parent"])["code"] == "A"
    children = [child["code"] for child in cast(list[Json], listing["children"])]
    assert children and all(str(child).startswith("A") for child in children)
