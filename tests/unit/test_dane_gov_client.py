import json
from pathlib import Path
from typing import cast

import httpx
import pytest

from minos.ingest.watchers.dane_gov.client import (
    DaneGovRetryableError,
    DaneGovSourceError,
    ReportsResponse,
    create_dane_gov_client,
    fetch_newest_report,
)

# The live answer captured on 2026-11-01.
CAPTURED = (
    Path(__file__).parents[1] / "fixtures/dane_gov/resources-2581.json"
).read_bytes()
MEDIA = {"content-type": "application/vnd.api+json"}
RENAMED = "https://api.dane.gov.pl/media/resources/20261120/raport_JGP_2026_9.xml"

type Resources = list[dict[str, dict[str, object]]]


def _fetch(response: httpx.Response) -> ReportsResponse:
    transport = httpx.MockTransport(lambda _request: response)
    with create_dane_gov_client(transport=transport) as client:
        return fetch_newest_report(client)


def _answer(body: bytes) -> ReportsResponse:
    return _fetch(httpx.Response(200, content=body, headers=MEDIA))


def _listing() -> dict[str, Resources]:
    return cast(dict[str, Resources], json.loads(CAPTURED))


def test_the_newest_report_comes_from_the_listing() -> None:
    answer = _answer(CAPTURED)
    assert (answer.report.month, answer.report.resource_id) == ("2026-08", 2834329)
    assert answer.report.published_date == "2026-10-20"
    assert answer.report.size_bytes == 57567445
    assert answer.report.source_url.endswith("raport_SZP_i_kardiologia_2026_8.xml")
    assert answer.unrecognized == ()
    # The stored evidence is the response itself.
    assert answer.body == CAPTURED


def test_republished_old_reports_do_not_hide_the_newest_month() -> None:
    listing = _listing()
    listing["data"].reverse()
    for resource in listing["data"][:-1]:
        resource["attributes"]["data_date"] = "2026-10-31"
    answer = _answer(json.dumps(listing).encode())
    assert answer.report.month == "2026-08"


def test_a_renamed_newest_resource_is_reported_without_a_failure() -> None:
    listing = _listing()
    listing["data"][0]["attributes"]["link"] = RENAMED
    answer = _answer(json.dumps(listing).encode())
    assert answer.report.month == "2026-07"
    assert answer.unrecognized == (2834329,)


def test_older_resources_with_other_names_are_history() -> None:
    listing = _listing()
    listing["data"][-1]["attributes"]["link"] = RENAMED
    assert _answer(json.dumps(listing).encode()).unrecognized == ()


def _invalid(edit: str, value: object) -> bytes:
    listing = _listing()
    listing["data"][0]["attributes"][edit] = value
    return json.dumps(listing).encode()


def _no_reports() -> bytes:
    listing = _listing()
    for resource in listing["data"]:
        resource["attributes"]["link"] = RENAMED
    return json.dumps(listing).encode()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            200, content=b"<html>Rejected</html>", headers={"content-type": "text/html"}
        ),
        httpx.Response(200, content=b"{}", headers=MEDIA),
        httpx.Response(200, content=b"not json", headers=MEDIA),
        httpx.Response(200, content=_no_reports(), headers=MEDIA),
        httpx.Response(200, content=_invalid("title", "a\u00adb"), headers=MEDIA),
        httpx.Response(200, content=_invalid("data_date", "2026-02-31"), headers=MEDIA),
        httpx.Response(200, content=b"x" * (256 * 1024 + 1), headers=MEDIA),
        httpx.Response(404),
    ],
    ids=[
        "html",
        "empty",
        "malformed",
        "no report",
        "control title",
        "impossible date",
        "oversize",
        "missing",
    ],
)
def test_a_listing_that_is_not_the_reviewed_shape_is_refused(
    response: httpx.Response,
) -> None:
    with pytest.raises(DaneGovSourceError):
        _ = _fetch(response)


def test_a_busy_api_is_retried_later() -> None:
    with pytest.raises(DaneGovRetryableError):
        _ = _fetch(httpx.Response(503))


def test_only_the_dane_gov_host_is_reached() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200))
    with (
        create_dane_gov_client(transport=transport) as client,
        pytest.raises(DaneGovSourceError),
    ):
        _ = client.get("https://example.org/")
