import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

from minos.ingest.watchers.publication import (
    ImmutableGenerationPublisher,
    prepare_public_root,
)

ROOT = Path(__file__).parents[2]
_NGINX_IMAGE = (
    "docker.io/nginxinc/nginx-unprivileged@sha256:"
    "b54ac358b83fc6c965793fd271839b4ea4cdb6e99895bb19618cbc2ca152d972"
)


def test_nginx_exposes_only_the_exact_public_projection_endpoint() -> None:
    configuration = (
        ROOT / "infra" / "ansible" / "files" / "edge-nginx.conf"
    ).read_text()

    assert "location = /api/v1/provenance/rsk {" in configuration
    assert "root /var/lib/minos/public;" in configuration
    assert "try_files /current/api/v1/provenance/rsk.json =404;" in configuration
    assert "location = /api/v1/provenance/baw {" in configuration
    assert "root /var/lib/minos/public/baw;" in configuration
    assert "try_files /current/api/v1/provenance/baw.json =404;" in configuration
    assert "default_type application/json;" in configuration
    # Uncacheable unless a public page or static asset is explicitly listed.
    assert "add_header Cache-Control $minos_cache_control always;" in configuration
    cache_map = configuration.split("map $request_uri $minos_cache_route {", 1)[1]
    cache_map = cache_map.split("}", 1)[0]
    assert 'default "no-store";' in cache_map
    assert "provenance" not in cache_map
    # The edge never makes an API response cacheable; JGP and grouping-dictionary
    # API entries only pass the application's own header through (an empty
    # value adds nothing).
    api_entries = [line.strip() for line in cache_map.splitlines() if "api" in line]
    assert api_entries == [
        '~^/api/v1/jgp/ "upstream";',
        '~^/api/v1/grouping/dictionaries$ "upstream";',
    ]
    assert "img-src 'self' data:" in configuration
    assert (
        'add_header Cross-Origin-Opener-Policy "same-origin" always;' in configuration
    )
    assert 'add_header X-Content-Type-Options "nosniff" always;' in configuration
    assert "location = /api/v1/provenance/rsk.json" not in configuration


def test_nginx_artifact_namespace_is_unconditionally_denied() -> None:
    configuration = (
        ROOT / "infra" / "ansible" / "files" / "edge-nginx.conf"
    ).read_text()
    block = configuration.split("location ^~ /api/v1/artifacts/ {", 1)[1].split("}", 1)[
        0
    ]

    assert "return 404;" in block
    assert "try_files" not in block
    assert "proxy_pass" not in block
    assert "alias" not in block
    assert 'if ($request_uri ~ "%") {' in configuration
    assert 'if ($request_uri ~ "//") {' in configuration
    assert 'if ($request_uri ~ "\\.\\.") {' in configuration
    assert 'if ($request_uri ~ "(^|/)\\.(/|$)") {' in configuration
    assert 'if ($request_uri ~* "^/api/v1/artifacts") {' in configuration
    assert 'if ($request_uri != "/api/v1/provenance/rsk") {' in configuration
    assert 'if ($request_uri != "/api/v1/provenance/baw") {' in configuration


def test_pinned_nginx_serves_only_canonical_public_route(tmp_path: Path) -> None:
    if shutil.which("docker") is None or shutil.which("curl") is None:
        pytest.skip("Docker and curl are required for real Nginx route validation")
    public_root = tmp_path / "public"
    prepare_public_root(public_root)
    projection = b'{"status":"ok"}'
    digest = hashlib.sha256(projection).hexdigest()
    manifest = json.dumps(
        {
            "projection_path": "api/v1/provenance/rsk.json",
            "projection_sha256": digest,
            "projection_size_bytes": len(projection),
            "redistribution": {"artifacts": [], "schema_version": 1},
            "schema_version": 1,
            "source": "RSK",
            "state_revision": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    _ = ImmutableGenerationPublisher(public_root).publish(
        1,
        digest,
        {
            "manifest.json": manifest,
            "api/v1/provenance/rsk.json": projection,
        },
    )
    baw_root = public_root / "baw"
    prepare_public_root(baw_root)
    baw_projection = b'{"source":"BAW"}'
    baw_digest = hashlib.sha256(baw_projection).hexdigest()
    baw_manifest = json.dumps(
        {
            "projection_path": "api/v1/provenance/baw.json",
            "projection_sha256": baw_digest,
            "projection_size_bytes": len(baw_projection),
            "redistribution": {"artifacts": [], "schema_version": 1},
            "schema_version": 1,
            "source": "BAW",
            "state_revision": 2,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    _ = ImmutableGenerationPublisher(baw_root, source="BAW").publish(
        2,
        baw_digest,
        {"manifest.json": baw_manifest, "api/v1/provenance/baw.json": baw_projection},
    )
    container = f"minos-public-nginx-test-{uuid4().hex}"
    try:
        started = subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                container,
                "--publish",
                "127.0.0.1::8080",
                "--volume",
                f"{ROOT / 'infra/ansible/files/edge-nginx.conf'}:/etc/nginx/nginx.conf:ro",
                "--volume",
                f"{public_root}:/var/lib/minos/public:ro",
                _NGINX_IMAGE,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert started.returncode == 0, started.stderr
        port_result = subprocess.run(
            ["docker", "port", container, "8080/tcp"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        port = int(port_result.stdout.strip().rsplit(":", 1)[1])
        _wait_for_nginx(port)

        canonical = _curl(port, "/api/v1/provenance/rsk", "minoshealth.pl")
        assert canonical.endswith('{"status":"ok"}\nSTATUS:200')
        lowered = canonical.lower()
        assert "content-type: application/json" in lowered
        assert "cache-control: no-store" in lowered
        assert "cross-origin-opener-policy: same-origin" in lowered
        assert "x-content-type-options: nosniff" in lowered
        icon = _curl(port, "/favicon.svg", "minoshealth.pl")
        assert icon.endswith("</svg>\nSTATUS:200")
        assert "content-type: image/svg+xml" in icon.lower()
        favicon_fallback = _curl(port, "/favicon.ico", "minoshealth.pl")
        assert favicon_fallback.endswith("STATUS:308")
        assert (
            "location: https://minoshealth.pl/favicon.svg" in favicon_fallback.lower()
        )
        assert _curl(port, "/api/v1/provenance/baw", "minoshealth.pl").endswith(
            '{"source":"BAW"}\nSTATUS:200'
        )

        rejected = (
            "/api/v1/provenance/rsk.json",
            "/api/v1/provenance/baw.json",
            "/api/v1/provenance/baw?alias=1",
            "/favicon.svg?alias=1",
            "/favicon.ico?alias=1",
            "/api/v1/./provenance/rsk",
            "/api/v1/provenance/./rsk",
            "/api/v1/provenance/rsk/.",
            "/api/v1/%2e/provenance/rsk",
            "/api/v1%2fprovenance/rsk",
            "/api//v1/provenance/rsk",
            "/api/v1/provenance/rsk?alias=1",
            "/api/v1/artifacts",
            "/api/v1/artifacts/not-a-digest",
            "/API/V1/ARTIFACTS/" + "A" * 64,
            "/api/v1/artifacts/../provenance/rsk",
            "/api/v1/artifacts/%2e%2e/provenance/rsk",
            "/api/v1/artif%61cts/../../",
        )
        for path in rejected:
            assert _curl(port, path, "minoshealth.pl").endswith("STATUS:404")
        # JGP routes pass the application's header through; the edge's own
        # refusals there have no upstream header and must still be no-store.
        for path in (
            "/jgp/2026-07-10?x=1",
            "/jgp/2026-07-10/A01/extra",
            "/jgp?x=1",
            "/api/v1/jgp/x",
        ):
            refused = _curl(port, path, "minoshealth.pl").lower()
            assert refused.endswith("status:404"), path
            assert refused.count("cache-control: no-store") == 1, path
        assert _curl(port, "/api/v1/provenance/rsk", "untrusted.invalid").endswith(
            "STATUS:404"
        )
        assert _curl(port, "/api/v1/provenance/rsk", "minoshealth.com").endswith(
            "STATUS:404"
        )
    finally:
        _ = subprocess.run(
            ["docker", "stop", container],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    remaining = subprocess.run(
        ["docker", "ps", "--filter", f"name={container}", "--format", "{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert remaining.stdout.strip() == ""


def _wait_for_nginx(port: int) -> None:
    for _attempt in range(50):
        response = _curl(port, "/healthz", "minoshealth.pl", timeout=2)
        if response.endswith("STATUS:200"):
            return
        time.sleep(0.1)
    raise AssertionError("pinned Nginx container did not become ready")


def _curl(port: int, path: str, host: str, *, timeout: int = 5) -> str:
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
            "\nSTATUS:%{http_code}",
            f"http://127.0.0.1:{port}{path}",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed.stdout
