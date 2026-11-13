import gzip
import json
from pathlib import Path
from typing import cast

import httpx
import pytest

from minos.ingest.watchers.dane_gov.client import (
    LISTING_URL,
    DaneGovProxy,
    DaneGovProxyError,
    DaneGovRetryableError,
    DaneGovSourceError,
    ReportsResponse,
    create_dane_gov_client,
    fetch_newest_report,
    load_dane_gov_proxy,
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


# The proxied mode (#349): the Worker answers the one listing request from a
# Polish Cloudflare location.
WORKER = "https://dane-gov.worker.test"
KEY = "k" * 43
WORKER_MEDIA = {**MEDIA, "x-minos-colo": "WAW"}


def _proxied(
    response: httpx.Response, seen: list[httpx.Request] | None = None
) -> ReportsResponse:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return response

    transport = httpx.MockTransport(handler)
    with create_dane_gov_client(
        transport=transport, proxy=DaneGovProxy(WORKER, KEY)
    ) as client:
        return fetch_newest_report(client)


def test_the_proxied_listing_carries_the_key_only_to_the_worker() -> None:
    seen: list[httpx.Request] = []

    answer = _proxied(httpx.Response(200, content=CAPTURED, headers=WORKER_MEDIA), seen)

    (request,) = seen
    assert str(request.url) == (
        WORKER + "/1.4/datasets/2581/resources?per_page=20&sort=-data_date"
    )
    assert request.headers["x-minos-proxy-key"] == KEY
    assert request.headers["accept-encoding"] == "identity"
    assert "cookie" not in request.headers
    assert answer.proxy_colo == "WAW"
    assert answer.report.month == "2026-08"
    assert answer.body == CAPTURED
    assert "x-minos-colo" not in answer.headers
    assert _answer(CAPTURED).proxy_colo is None


@pytest.mark.parametrize(
    ("url", "headers"),
    [
        ("https://example.org/", {}),
        ("https://api.dane.gov.pl/1.4/datasets/2581/resources", {}),
        (WORKER + "/1.4/datasets/2581/resources?per_page=20&sort=-data_date", {}),
        (LISTING_URL.replace("https://", "http://"), {}),
        (LISTING_URL, {"cookie": "session=1"}),
        (LISTING_URL, {"x-minos-proxy-key": "caller"}),
    ],
    ids=["other host", "other query", "worker url", "plain http", "cookie", "key"],
)
def test_the_proxied_client_refuses_any_other_request(
    url: str, headers: dict[str, str]
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    with (
        create_dane_gov_client(
            transport=transport, proxy=DaneGovProxy(WORKER, KEY)
        ) as client,
        pytest.raises(DaneGovSourceError),
    ):
        _ = client.get(url, headers=headers)
    assert seen == []


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(504, headers={"x-minos-colo": "WAW"}), DaneGovRetryableError),
        (httpx.Response(503, headers={"x-minos-colo": "HEL"}), DaneGovRetryableError),
        (httpx.Response(403), DaneGovSourceError),
        (httpx.Response(404), DaneGovSourceError),
        (httpx.Response(200, content=CAPTURED, headers=MEDIA), DaneGovSourceError),
        (
            httpx.Response(
                200, content=CAPTURED, headers={**MEDIA, "x-minos-colo": "waw\n"}
            ),
            DaneGovSourceError,
        ),
        (
            httpx.Response(
                200,
                content=gzip.compress(CAPTURED),
                headers={**WORKER_MEDIA, "content-encoding": "gzip"},
            ),
            DaneGovSourceError,
        ),
        (
            httpx.Response(
                200,
                content=b"<html>",
                headers={"content-type": "text/html", "x-minos-colo": "WAW"},
            ),
            DaneGovSourceError,
        ),
    ],
    ids=[
        "upstream timeout",
        "outside poland",
        "wrong key",
        "wrong path",
        "no colo",
        "invalid colo",
        "gzip",
        "html",
    ],
)
def test_a_worker_answer_maps_to_the_existing_failure_classes(
    response: httpx.Response, error: type[Exception]
) -> None:
    with pytest.raises(error):
        _ = _proxied(response)


def _key_file(tmp_path: Path, content: bytes, mode: int = 0o440) -> Path:
    path = tmp_path / "proxy-key"
    _ = path.write_bytes(content)
    path.chmod(mode)
    return path


def test_the_proxy_key_comes_from_a_private_file(tmp_path: Path) -> None:
    proxy = load_dane_gov_proxy(WORKER + "/", _key_file(tmp_path, KEY.encode() + b"\n"))
    assert (proxy.origin, proxy.key) == (WORKER, KEY)
    assert WORKER not in repr(proxy)
    assert KEY not in repr(proxy)


@pytest.mark.parametrize(
    "origin",
    [
        "http://dane-gov.worker.test",
        "https://dane-gov.worker.test/1.4",
        "https://dane-gov.worker.test/?a=1",
        "https://user:pass@dane-gov.worker.test",
        "https://dane-gov.worker.test#x",
        "dane-gov.worker.test",
    ],
)
def test_a_proxy_origin_is_a_bare_https_origin(tmp_path: Path, origin: str) -> None:
    with pytest.raises(DaneGovProxyError) as raised:
        _ = load_dane_gov_proxy(origin, _key_file(tmp_path, KEY.encode()))
    assert "worker.test" not in str(raised.value)


@pytest.mark.parametrize(
    ("content", "mode"),
    [
        (b"short", 0o440),
        (b"k" * 40 + b" ", 0o440),
        (b"k" * 40 + b"\n\n", 0o440),
        (b"k" * 600, 0o440),
        (KEY.encode(), 0o444),
        (b"", 0o440),
    ],
    ids=["short", "space", "two newlines", "oversize", "world readable", "empty"],
)
def test_an_unsafe_or_malformed_key_file_is_refused(
    tmp_path: Path, content: bytes, mode: int
) -> None:
    path = _key_file(tmp_path, content, mode)
    with pytest.raises(DaneGovProxyError) as raised:
        _ = load_dane_gov_proxy(WORKER, path)
    message = str(raised.value)
    assert str(tmp_path) not in message
    assert "worker.test" not in message


def test_a_missing_or_linked_key_file_is_refused(tmp_path: Path) -> None:
    target = _key_file(tmp_path, KEY.encode())
    link = tmp_path / "link"
    link.symlink_to(target)
    for path in (tmp_path / "missing", link):
        with pytest.raises(DaneGovProxyError):
            _ = load_dane_gov_proxy(WORKER, path)
