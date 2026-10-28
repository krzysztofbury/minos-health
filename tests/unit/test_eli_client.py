import json
from pathlib import Path
from typing import cast

import httpx
import pytest

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
    ],
)  # fmt: skip
def test_each_difference_from_the_review_is_named(
    pinned: dict[str, object], base: dict[str, object], expected: list[str]
) -> None:
    assert (
        findings(DISCHARGE, _act("DU/2025/1588", pinned), _act("DU/2019/1207", base))
        == expected
    )


def test_base_act_that_no_longer_lists_the_pin_fails_closed() -> None:
    base = _act("DU/2019/1207", {"references": {}})
    with pytest.raises(EliSourceError, match="no longer lists the pinned text"):
        _ = findings(DISCHARGE, _act("DU/2025/1588"), base)


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
