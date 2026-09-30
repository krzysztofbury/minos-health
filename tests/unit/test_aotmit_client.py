import httpx
import pytest

from minos.ingest.watchers.aotmit.client import (
    AotmitRetryableError,
    AotmitSourceError,
    create_aotmit_client,
    fetch_listing,
    honor_retry_after,
    parse_listing,
)


def _notice(
    *, timestamp: str = "2026-07-02T12:41:34+02:00", title: str = "leczenia szpitalnego"
) -> bytes:
    return (
        '<div class="blog-item"><dd class="published"><time datetime="'
        + timestamp
        + '">date</time></dd><div class="introtext"><p><strong>'
        + title
        + '</strong></p><a href="/assets/files/taryfikacja/taryfy/obwieszczenia/2026/szpital.pdf">PDF</a>'
        + "</div></div>"
    ).encode()


def test_identical_duplicate_is_deduplicated_but_conflicting_duplicate_fails() -> None:
    identical = parse_listing(
        _notice() + _notice(timestamp="2026-07-02T12:41:40+02:00")
    )
    assert len(identical) == 1
    with pytest.raises(AotmitSourceError, match="conflicting duplicate"):
        _ = parse_listing(_notice() + _notice(title="inne leczenia szpitalnego"))


def test_retry_after_even_on_success_is_honored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with create_aotmit_client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=_notice(),
                headers={"content-type": "text/html", "Retry-After": "2"},
            )
        )
    ) as client:
        response, _ = fetch_listing(client)
    assert response.retry_after_seconds == 2
    sleeps: list[int] = []
    monkeypatch.setattr("minos.ingest.watchers.aotmit.client.time.sleep", sleeps.append)
    honor_retry_after(response, remaining_seconds=3)
    assert sleeps == [2]
    with pytest.raises(AotmitRetryableError, match="deadline"):
        honor_retry_after(response, remaining_seconds=1)


def test_bad_listing_and_unsupported_retry_after_fail_closed() -> None:
    with pytest.raises(AotmitSourceError, match="no bounded hospital notices"):
        _ = parse_listing(b"<html>new layout</html>")
    with create_aotmit_client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=_notice(),
                headers={"content-type": "text/html", "Retry-After": "301"},
            )
        )
    ) as client:
        with pytest.raises(AotmitSourceError, match="exceeds its bound"):
            _ = fetch_listing(client)
