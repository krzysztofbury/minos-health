import gzip
import json
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import ValidationError

from minos.ingest.watchers.eli.client import (
    WATCHED,
    EliAct,
    EliRetryableError,
    EliSourceError,
    WatchedDictionary,
    create_eli_client,
    fetch_act,
    findings,
)

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/eli"


def _captured(eli: str) -> dict[str, object]:
    # Live ELI answers captured on 2026-10-28.
    path = FIXTURES / (eli.replace("/", "-") + ".json")
    return cast(dict[str, object], json.loads(path.read_bytes()))


def _act(eli: str, change: dict[str, object] | None = None) -> EliAct:
    return EliAct.model_validate({**_captured(eli), **(change or {})})


def _references(eli: str, section: str, extra: str) -> dict[str, object]:
    references = cast(dict[str, list[dict[str, str]]], _captured(eli)["references"])
    references[section] = [*references.get(section, []), {"id": extra}]
    return {"references": references}


def test_reviewed_pins_are_the_manifest_sources() -> None:
    # Re-pinning a dictionary must also update what the watcher compares.
    manifest = cast(
        dict[str, dict[str, dict[str, str]]],
        json.loads(
            (ROOT / "manifests/sources/grouping-dictionaries.json").read_bytes()
        )["grouping_dictionaries"],
    )
    assert {item.name: item.pinned for item in WATCHED} == {
        name: entry["source"]["source_url"]
        .removeprefix("https://api.sejm.gov.pl/eli/acts/")
        .removesuffix("/text.pdf")
        for name, entry in manifest.items()
    }


@pytest.mark.parametrize("watched", WATCHED, ids=[item.name for item in WATCHED])
def test_captured_acts_match_the_reviewed_pins(watched: WatchedDictionary) -> None:
    assert findings(watched, _act(watched.pinned), _act(watched.base)) == []


DISCHARGE = next(item for item in WATCHED if item.name == "discharge_modes")


@pytest.mark.parametrize(
    ("pinned", "base", "expected"),
    [
        ({}, _references("DU/2019/1207", "Akty zmieniające", "DU/2026/1500"),
         ["amendment:DU/2026/1500"]),
        ({}, _references("DU/2019/1207", "Inf. o tekście jednolitym", "DU/2026/2000"),
         ["consolidated_text:DU/2026/2000"]),
        ({}, {"references": {"Inf. o tekście jednolitym": [{"id": "DU/2025/1588"}]}},
         ["reference_removed:DU/2019/2532", "reference_removed:DU/2020/1586",
          "reference_removed:DU/2022/1497", "reference_removed:DU/2022/2262",
          "reference_removed:DU/2022/434", "reference_removed:DU/2022/749",
          "reference_removed:DU/2023/1245", "reference_removed:DU/2024/1469",
          "reference_removed:DU/2024/610", "reference_removed:DU/2025/205",
          "reference_removed:DU/2025/985", "reference_removed:DU/2026/1113",
          "reference_removed:DU/2026/270", "reference_removed:DU/2026/822"]),
        ({"inForce": "NOT_IN_FORCE"}, {}, ["not_in_force:pinned:NOT_IN_FORCE"]),
        ({}, {"inForce": "NOT_IN_FORCE"}, ["not_in_force:base:NOT_IN_FORCE"]),
        # A repeal is listed before its effective date flips inForce.
        ({}, _references("DU/2019/1207", "Uchylenia wynikające z", "DU/2026/2001"),
         ["repeal:base:DU/2026/2001"]),
        (_references("DU/2025/1588", "Sprostowanie", "MP/2026/12"), {},
         ["correction:pinned:MP/2026/12"]),
        # Sections the watch does not read may name Monitor Polski acts.
        ({}, _references("DU/2019/1207", "Akty wykonawcze", "MP/2026/866"), []),
    ],
)  # fmt: skip
def test_each_difference_from_the_review_is_named(
    pinned: dict[str, object], base: dict[str, object], expected: list[str]
) -> None:
    assert (
        findings(DISCHARGE, _act("DU/2025/1588", pinned), _act("DU/2019/1207", base))
        == expected
    )


@pytest.mark.parametrize(
    ("base", "message"),
    [
        ({"references": {}}, "no longer lists the pinned text"),
        (_references("DU/2019/1207", "Akty zmieniające", "xDU/2026/15000z"),
         "identifier is invalid"),
        ({"references": {
            "Inf. o tekście jednolitym": [{"id": "DU/2025/1588"}],
            "Akty zmieniające": [{"id": f"DU/2027/{n}"} for n in range(1, 40)]}},
         "exceed their bound"),
    ],
)  # fmt: skip
def test_unusable_base_act_sections_fail_closed(
    base: dict[str, object], message: str
) -> None:
    with pytest.raises(EliSourceError, match=message):
        _ = findings(DISCHARGE, _act("DU/2025/1588"), _act("DU/2019/1207", base))


def test_act_fields_must_match_whole_values() -> None:
    changes: tuple[dict[str, object], ...] = (
        {"ELI": "xDU/2025/1588z"},
        {"inForce": "in force IN_FORCE"},
    )
    for change in changes:
        with pytest.raises(ValidationError):
            _ = _act("DU/2025/1588", change)


def _fetch(response: httpx.Response, eli: str = "DU/2025/1588") -> EliAct:
    with create_eli_client(transport=httpx.MockTransport(lambda _: response)) as client:
        return fetch_act(client, eli).act


JSON = {"content-type": "application/json"}


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        # A firewall answers HTTP 200 with an HTML page: a failure, not a quiet run.
        (httpx.Response(200, content=b"<html>Request Rejected</html>",
                        headers={"content-type": "text/html"}), EliSourceError, "not JSON"),
        (httpx.Response(200, content=json.dumps(_captured("DU/2019/1207")).encode(),
                        headers=JSON), EliSourceError, "names another act"),
        (httpx.Response(200, content=b"{" + b" " * 300_000 + b"}", headers=JSON),
         EliSourceError, "byte limit"),
        (httpx.Response(200, content=b'{"ELI": "DU/2025/1588"}', headers=JSON),
         EliSourceError, "metadata is invalid"),
        (httpx.Response(404, content=b"", headers=JSON), EliSourceError, "rejected"),
        (httpx.Response(503, content=b"", headers=JSON), EliRetryableError, "temporarily"),
        (httpx.Response(200, content=gzip.compress(b"{}"), headers={**JSON, "content-encoding": "gzip"}),
         EliSourceError, "encoding"),
    ],
)  # fmt: skip
def test_unexpected_answers_fail_closed(
    response: httpx.Response, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        _ = _fetch(response)


def test_only_the_sejm_host_is_reachable() -> None:
    with create_eli_client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    ) as client:
        with pytest.raises(EliSourceError, match="not allowlisted"):
            _ = client.get("https://example.com/eli/acts/DU/2025/1588")
