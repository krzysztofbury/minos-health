import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).parents[2]
STATIC_ROOT = ROOT / "infra" / "ansible" / "files"
OUTPUT_ROOT = ROOT / "test-output"
_NGINX_IMAGE = (
    "docker.io/nginxinc/nginx-unprivileged@sha256:"
    "b54ac358b83fc6c965793fd271839b4ea4cdb6e99895bb19618cbc2ca152d972"
)
_UUIDS = (
    "00000000-0000-4000-8000-000000000011",
    "00000000-0000-4000-8000-000000000012",
    "00000000-0000-4000-8000-000000000013",
)


def _entry(index: int, status: str) -> dict[str, object]:
    file_uuid = _UUIDS[index]
    digest = str(index + 1) * 64
    archived = index == 0
    return {
        "archive_status": (
            "privately_archived_verified" if archived else "metadata_only"
        ),
        "download_url": None,
        "downloadable": False,
        "extension": "xml",
        "file_created_at": "2026-09-20T10:00:00Z",
        "file_name": f"resource-{index}.xml",
        "file_uuid": file_uuid,
        "first_observed_at": "2026-09-20T11:00:00Z",
        "hcd": "00CD10",
        "last_observed_at": f"2026-09-2{index + 1}T11:00:00Z",
        "metadata_observation_sha256": digest,
        "notices": ["Publiczny komunikat źródła."] if index == 0 else [],
        "origin_url": f"https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/{file_uuid}",
        "product_sha256": "f" * 64 if archived else None,
        "publisher_name": "Biuro WHO w Polsce",
        "publisher_short_name": "WHO PL",
        "resource_name": (
            '<img src=x onerror="alert(1)">' if index == 0 else f"Zasób {index}"
        ),
        "resource_short_name": "ICD-10",
        "resource_type": "SLOWNIK",
        "resource_version": "013",
        "size_bytes": 12_738_027,
        "source_archived_at": None,
        "status": status,
        "type_label": "XML",
        "valid_from": "2025-10-16T22:00:00Z",
        "valid_to": None,
    }


def _projection() -> dict[str, object]:
    return {
        "entries": [
            _entry(0, "current"),
            _entry(1, "historical"),
            _entry(2, "missing"),
        ],
        "history_truncated": True,
        "included_revisions": [10, 11, 12],
        "notices": [
            "Artifact redistribution is disabled pending an approved legal allowlist."
        ],
        "projected_at": "2026-09-22T08:30:00Z",
        "redistribution": {"artifacts": [], "schema_version": 1},
        "schema_version": 1,
        "source": "RSK",
        "through_state_revision": 12,
    }


def test_sources_javascript_schema_suite() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the Sources JavaScript schema suite")

    completed = subprocess.run(
        [node, "--test", "tests/js/sources.test.cjs"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_sources_page_uses_only_reviewed_dom_and_network_boundaries() -> None:
    javascript = (STATIC_ROOT / "sources.js").read_text()
    html = (STATIC_ROOT / "sources.html").read_text()
    home = (STATIC_ROOT / "maintenance-index.html").read_text()

    for forbidden in ("innerHTML", "document.write", "eval(", "insertAdjacentHTML"):
        assert forbidden not in javascript
    assert "createElement" in javascript
    assert "textContent" in javascript
    assert "metadane ICD-9, ICD-10 i ICD-11" in html
    assert "jawnie dozwolony plik XML ICD-10" in html
    assert 'var PROVENANCE_PATH = "/api/v1/provenance/rsk";' in javascript
    assert "/api/v1/artifacts" not in javascript
    assert "amazonaws" not in javascript.lower()
    plausible = (STATIC_ROOT / "plausible-init.js").read_text()
    assert (
        'var plausiblePagePaths = ["/", "/polityka-prywatnosci", '
        '"/regulamin", "/zrodla"];'
    ) in plausible
    assert "/api/" not in plausible
    assert "/artifacts" not in plausible
    assert "<style" not in html
    assert "<script>" not in html
    assert 'src="/sources.js"' in html
    assert 'href="/sources.css"' in html
    assert "<style" not in home
    assert 'href="/home.css"' in home
    assert "'unsafe-inline'" not in (STATIC_ROOT / "edge-nginx.conf").read_text()


def test_normal_text_color_tokens_meet_wcag_aa_contrast() -> None:
    sources_css = (STATIC_ROOT / "sources.css").read_text()
    legal_css = (STATIC_ROOT / "legal.css").read_text()
    paper = _css_color_token(sources_css, "paper")
    red_text = _css_color_token(sources_css, "red-text")
    muted = _css_color_token(sources_css, "muted")

    assert _contrast_ratio(red_text, paper) >= 4.5
    assert _contrast_ratio(muted, paper) >= 4.5
    assert _css_color_token(legal_css, "red-text") == red_text
    assert _css_color_token(legal_css, "paper") == paper
    assert ".note-number {\n  margin: 0;\n  color: var(--red-text);" in sources_css
    assert (
        ".search-field input::placeholder {\n  color: var(--muted);\n  opacity: 1;"
    ) in sources_css
    assert "a:hover {\n  color: var(--red-text);" in sources_css
    assert "a:hover {\n  color: var(--red-text);" in legal_css
    assert "background: var(--red);" in sources_css
    assert "background: var(--red);" in legal_css


def test_pinned_nginx_and_installed_browser_render_sources_page(
    tmp_path: Path,
) -> None:
    required = {
        "chromium": shutil.which("chromium"),
        "docker": shutil.which("docker"),
        "node": shutil.which("node"),
    }
    missing = [name for name, executable in required.items() if executable is None]
    if missing:
        pytest.skip(f"Real browser test requires: {', '.join(missing)}")

    public_root = tmp_path / "public"
    projection_path = (
        public_root / "generations" / "fixture" / "api" / "v1" / "provenance"
    )
    projection_path.mkdir(parents=True)
    fixture_file = projection_path / "rsk.json"
    _ = fixture_file.write_text(json.dumps(_projection(), ensure_ascii=False))
    (public_root / "current").symlink_to("generations/fixture")
    OUTPUT_ROOT.mkdir(exist_ok=True)

    container = f"minos-sources-browser-{uuid4().hex}"
    volumes = [
        f"{STATIC_ROOT / 'edge-nginx.conf'}:/etc/nginx/nginx.conf:ro",
        f"{STATIC_ROOT / 'home.css'}:/usr/share/nginx/html/home.css:ro",
        f"{STATIC_ROOT / 'sources.html'}:/usr/share/nginx/html/sources.html:ro",
        f"{STATIC_ROOT / 'sources.css'}:/usr/share/nginx/html/sources.css:ro",
        f"{STATIC_ROOT / 'sources.js'}:/usr/share/nginx/html/sources.js:ro",
        f"{STATIC_ROOT / 'plausible-init.js'}:/usr/share/nginx/html/plausible-init.js:ro",
        f"{public_root}:/var/lib/minos/public:ro",
    ]
    command = [
        str(required["docker"]),
        "run",
        "--detach",
        "--rm",
        "--name",
        container,
        "--publish",
        "127.0.0.1::8080",
    ]
    for volume in volumes:
        command.extend(("--volume", volume))
    command.append(_NGINX_IMAGE)

    try:
        started = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert started.returncode == 0, started.stderr
        port_result = subprocess.run(
            [str(required["docker"]), "port", container, "8080/tcp"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        port = int(port_result.stdout.strip().rsplit(":", 1)[1])
        _wait_for_nginx(port)

        browser = subprocess.run(
            [
                str(required["node"]),
                "tests/browser/sources-browser.mjs",
                str(required["chromium"]),
                f"http://minoshealth.pl:{port}/zrodla",
                str(fixture_file),
                str(OUTPUT_ROOT),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert browser.returncode == 0, browser.stdout + browser.stderr

        _assert_exact_routes(port)
        assert (OUTPUT_ROOT / "sources-desktop.png").stat().st_size > 10_000
        assert (OUTPUT_ROOT / "sources-mobile.png").stat().st_size > 10_000
        assert (OUTPUT_ROOT / "sources-mobile-320.png").stat().st_size > 10_000
    finally:
        _ = subprocess.run(
            [str(required["docker"]), "stop", container],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )


def _wait_for_nginx(port: int) -> None:
    for _attempt in range(50):
        response = _curl(port, "/healthz", "minoshealth.pl")
        if response.endswith("STATUS:200"):
            return
        time.sleep(0.1)
    raise AssertionError("pinned Nginx container did not become ready")


def _css_color_token(stylesheet: str, name: str) -> tuple[int, int, int]:
    match = re.search(rf"--{re.escape(name)}:\s*#([0-9a-fA-F]{{6}});", stylesheet)
    assert match is not None
    value = match.group(1)
    return (
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
    )


def _contrast_ratio(
    foreground: tuple[int, int, int],
    background: tuple[int, int, int],
) -> float:
    def luminance(color: tuple[int, int, int]) -> float:
        channels = [
            component / 3294.6
            if component <= 10
            else ((component / 255 + 0.055) / 1.055) ** 2.4
            for component in color
        ]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    lighter, darker = sorted(
        (luminance(foreground), luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _assert_exact_routes(port: int) -> None:
    sources = _curl(port, "/zrodla", "minoshealth.pl")
    assert sources.endswith("STATUS:200")
    lowered = sources.lower()
    assert "content-type: text/html" in lowered
    assert "connect-src 'self' https://plausible.io" in lowered
    assert "style-src 'self'" in lowered
    assert "unsafe-inline" not in lowered
    assert "x-content-type-options: nosniff" in lowered
    stylesheet = _curl(port, "/sources.css", "minoshealth.pl")
    home_stylesheet = _curl(port, "/home.css", "minoshealth.pl")
    javascript = _curl(port, "/sources.js", "minoshealth.pl")
    redirect = _curl(port, "/zrodla", "minoshealth.com")
    assert stylesheet.endswith("STATUS:200")
    assert "content-type: text/css" in stylesheet.lower()
    assert home_stylesheet.endswith("STATUS:200")
    assert "content-type: text/css" in home_stylesheet.lower()
    assert javascript.endswith("STATUS:200")
    assert "content-type: application/javascript" in javascript.lower()
    assert redirect.endswith("STATUS:308")
    assert "location: https://minoshealth.pl/zrodla" in redirect.lower()
    for path in (
        "/zrodla/",
        "/zrodla?alias=1",
        "/%7arodla",
        "/sources.css?alias=1",
        "/home.css?alias=1",
        "/sources.js/",
    ):
        assert _curl(port, path, "minoshealth.pl").endswith("STATUS:404")
    assert _curl(port, "/zrodla?alias=1", "minoshealth.com").endswith("STATUS:404")


def _curl(port: int, path: str, host: str) -> str:
    completed = subprocess.run(
        [
            "curl",
            "--path-as-is",
            "--silent",
            "--show-error",
            "--dump-header",
            "-",
            "--header",
            f"Host: {host}",
            "--write-out",
            "STATUS:%{http_code}",
            f"http://127.0.0.1:{port}{path}",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return completed.stdout
