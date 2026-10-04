import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from scripts.fetch_search_evaluation_sources import fetch_pin


@dataclass(frozen=True)
class Pin:
    source_url: str
    sha256: str
    size_bytes: int


@pytest.mark.parametrize(
    "status,content,accepted",
    [
        (200, b"pinned", True),
        (200, b"changed", False),
        (200, b"pin", False),
        (200, b"wrong!", False),
        (302, b"", False),
        (503, b"", False),
    ],
)
def test_source_fetch_accepts_only_exact_pinned_bytes(
    tmp_path: Path,
    status: int,
    content: bytes,
    accepted: bool,
) -> None:
    pin = Pin(
        "https://baw.nfz.gov.pl/api/file/4/123",
        hashlib.sha256(b"pinned").hexdigest(),
        6,
    )

    def response(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == pin.source_url
        assert request.method == "GET"
        return httpx.Response(
            status,
            content=content,
            headers={"Location": "https://example.org/unreviewed"},
        )

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        if accepted:
            fetch_pin(client, pin, tmp_path)
            assert (tmp_path / pin.sha256).read_bytes() == b"pinned"
        else:
            with pytest.raises(ValueError):
                fetch_pin(client, pin, tmp_path)
            assert list(tmp_path.iterdir()) == []


def test_fetch_rejects_unknown_endpoint_without_request_or_file(tmp_path: Path) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Unreviewed endpoint must not be contacted")

    pin = Pin("https://example.org/file", "a" * 64, 1)
    with httpx.Client(transport=httpx.MockTransport(refuse)) as client:
        with pytest.raises(ValueError, match="not allowed"):
            fetch_pin(client, pin, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("symlink", [False, True])
def test_fetch_never_overwrites_or_removes_an_existing_path(
    tmp_path: Path, symlink: bool
) -> None:
    pin = Pin("https://baw.nfz.gov.pl/api/file/4/123", "a" * 64, 1)
    preserved = tmp_path / "preserved"
    _ = preserved.write_bytes(b"existing")
    destination = tmp_path / pin.sha256
    if symlink:
        destination.symlink_to(preserved)
    else:
        _ = destination.write_bytes(b"existing")
    with httpx.Client() as client:
        with pytest.raises(FileExistsError):
            fetch_pin(client, pin, tmp_path)
    assert destination.read_bytes() == preserved.read_bytes() == b"existing"
    assert destination.is_symlink() == symlink
