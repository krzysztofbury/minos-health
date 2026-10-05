import json
import struct
from dataclasses import replace
from html.parser import HTMLParser
from typing import override

import pytest

from scripts.build_site import PAGES, Mode, render_page, render_site


class Metadata(HTMLParser):
    def __init__(self, page: str) -> None:
        super().__init__()
        self.values: dict[str, str | None] = {}
        self.feed(page)

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            values = dict(attrs)
            key = values.get("property") or values.get("name")
            if key:
                self.values[key] = values.get("content")


@pytest.mark.parametrize("mode", ["maintenance", "validation"])
def test_social_metadata_matches_the_served_site_artifact(mode: Mode) -> None:
    files = render_site(mode)
    for name, content in files.items():
        if not name.endswith(".html"):
            continue
        metadata = Metadata(content.decode()).values
        assert metadata["twitter:title"] == metadata["og:title"]
        assert metadata["twitter:description"] == metadata["description"]
        assert metadata["og:description"] == metadata["description"]
        if mode == "maintenance":
            assert metadata["twitter:card"] == "summary"
            assert "og:image" not in metadata
        else:
            assert metadata["twitter:card"] == "summary_large_image"
            assert (
                metadata["og:image"] == "https://minoshealth.pl/social-preview-v1.png"
            )
            assert metadata["twitter:image"] == metadata["og:image"]
            assert metadata["og:image:type"] == "image/png"
            assert metadata["og:image:width"] == "1200"
            assert metadata["og:image:height"] == "630"
            assert metadata["twitter:image:alt"] == metadata["og:image:alt"]
    if mode == "validation":
        image = files["social-preview-v1.png"]
        assert image[:8] == b"\x89PNG\r\n\x1a\n"
        assert image[12:16] == b"IHDR"
        assert struct.unpack(">II", image[16:24]) == (1200, 630)
        assert len(image) < 500 * 1024


def test_home_metadata_covers_reference_jgp_without_claiming_a_grouper() -> None:
    page = render_site("validation")["index.html"].decode()
    metadata = Metadata(page).values
    assert (
        metadata["og:title"] == "Minos Health - wyszukiwarka ICD-10, ICD-9 i grup JGP"
    )
    assert metadata["description"] == (
        "Bezpłatna wyszukiwarka rozpoznań ICD-10 PL, procedur ICD-9 PL i grup JGP."
        + " Sprawdź warunki grupowania, punkty bazowe i źródła NFZ."
    )
    structured = page.split('<script type="application/ld+json">')[1].split(
        "</script>"
    )[0]
    assert json.loads(structured)["description"] == metadata["description"]


def test_social_title_and_description_escape_untrusted_metadata() -> None:
    hostile = '"><img src=x onerror="alert(1)"> & opis'
    home = next(page for page in PAGES if page.output == "index.html")
    page = render_page(replace(home, title=hostile, description=hostile), "validation")
    assert "<img" not in page
    metadata = Metadata(page).values
    assert metadata["twitter:title"] == hostile
    assert metadata["og:description"] == hostile
