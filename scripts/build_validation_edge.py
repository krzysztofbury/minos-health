"""Build a validation-only edge artifact without changing maintenance defaults."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import final

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / "infra/ansible/files"
sys.path.insert(0, str(ROOT))

from scripts.build_site import FONTS, render_site  # noqa: E402

PAGE_ROUTES = (
    ("/slowniki", "dictionaries.html"),
    ("/slowniki/icd-10", "dictionary-icd-10.html"),
    ("/slowniki/icd-9", "dictionary-icd-9.html"),
)
ASSET_ROUTES = ("/search.js", "/dictionary.js")


def _routes() -> str:
    blocks = [
        """
        # Branded page for every edge-generated 404; proxied API errors pass through.
        error_page 404 /404.html;
        location = /404.html {
            internal;
            try_files /404.html =404;
        }"""
    ]
    for path, name in PAGE_ROUTES:
        blocks.append(
            f"""        location = {path} {{
            if ($request_uri != "{path}") {{ return 404; }}
            try_files /{name} =404;
        }}"""
        )
    for path in ASSET_ROUTES:
        blocks.append(
            f"""        location = {path} {{
            if ($request_uri != "{path}") {{ return 404; }}
            try_files {path} =404;
        }}"""
        )
    for name in FONTS:
        blocks.append(
            f"""        location = /fonts/{name} {{
            if ($request_uri != "/fonts/{name}") {{ return 404; }}
            types {{ }}
            default_type font/woff2;
            try_files /fonts/{name} =404;
        }}"""
        )
    for path, media in (
        ("/robots.txt", "text/plain"),
        ("/llms.txt", "text/markdown"),
        ("/sitemap.xml", "application/xml"),
        ("/sitemap-pages.xml", "application/xml"),
    ):
        blocks.append(
            f"""        location = {path} {{
            if ($request_uri != "{path}") {{ return 404; }}
            types {{ }}
            default_type "{media}; charset=utf-8";
            try_files {path} =404;
        }}"""
        )
    blocks.append(
        """        location = /fonts/OFL.txt {
            if ($request_uri != "/fonts/OFL.txt") { return 404; }
            types { }
            default_type "text/plain; charset=utf-8";
            try_files /fonts/OFL.txt =404;
        }"""
    )
    blocks.append(
        """        location = /api/v1/releases {
            if ($request_uri != "/api/v1/releases") { return 404; }
            if ($request_method != GET) { return 405; }
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
            error_log /dev/null;
        }
        location = /api/v1/search {
            if ($request_uri != "/api/v1/search") { return 404; }
            if ($request_method != POST) { return 405; }
            client_max_body_size 4k;
            client_body_buffer_size 4k;
            client_body_timeout 2s;
            proxy_request_buffering on;
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
            error_log /dev/null;
        }
        # Code pages and sitemaps are server-rendered by the API for crawlers.
        location ~ "^/slowniki/icd-(10|9)/[A-Za-z0-9][A-Za-z0-9.-]{0,15}$" {
            if ($request_uri ~ "\\?") { return 404; }
            if ($request_method !~ ^(GET|HEAD)$) { return 405; }
            proxy_hide_header Cache-Control;
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
            error_log /dev/null;
        }
        location ~ "^/sitemap-icd-(10|9)\\.xml$" {
            if ($request_uri ~ "\\?") { return 404; }
            if ($request_method !~ ^(GET|HEAD)$) { return 405; }
            proxy_hide_header Cache-Control;
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
            error_log /dev/null;
        }
        # JGP paths carry a service date and public exact identifiers only.
        location ~ "^/api/v1/jgp/20[0-9]{2}-[0-9]{2}-[0-9]{2}/(groups/[A-Z]{1,4}[0-9]{2,3}[A-Z]{0,3}|sections|sections/1ae?/[1-9][0-9]{0,2})$" {
            if ($request_uri ~ "\\?") { return 404; }
            if ($request_method != GET) { return 405; }
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
            error_log /dev/null;
        }
        # Rank paths carry public exact codes, never free-text medical queries.
        location ~ "^/api/v1/procedure-ranks/[a-z0-9-]{1,80}/[A-Za-z0-9][A-Za-z0-9.-]{0,15}$" {
            if ($request_uri ~ "\\?") { return 404; }
            if ($request_method != GET) { return 405; }
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
            error_log /dev/null;
        }
        # Tree paths carry only a release id and a source node number.
        location ~ "^/api/v1/tree/[a-z0-9-]{1,80}/(0|[1-9][0-9]{0,6})$" {
            if ($request_uri ~ "\\?") { return 404; }
            if ($request_method != GET) { return 405; }
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
            error_log /dev/null;
        }"""
    )
    return "\n".join(blocks) + "\n"


def _com_redirects() -> str:
    return "".join(
        f"""        location = {path} {{
            if ($request_uri != "{path}") {{ return 404; }}
            return 308 https://minoshealth.pl{path};
        }}
"""
        for path, _name in PAGE_ROUTES
    )


@final
class Arguments(argparse.Namespace):
    output: Path = Path()


def build(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    files = render_site("validation")
    for name in ("plausible-init.js", "sources.js"):
        files[name] = (FILES / name).read_bytes()
    identities: dict[str, str] = {}
    for name, content in sorted(files.items()):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_bytes(content)
        identities[name] = hashlib.sha256(content).hexdigest()
    config = (FILES / "edge-nginx.conf").read_text()
    # Nginx warning/error messages can include untrusted URLs and headers.
    config = config.replace("error_log /dev/stderr warn;", "error_log /dev/null;")
    health = """        location = /healthz {
            default_type application/json;
            return 200 '{"status":"ok","mode":"maintenance"}';
        }"""
    if config.count(health) != 1:
        raise ValueError("maintenance liveness layout changed")
    config = config.replace(
        health,
        """        location = /healthz {
            if ($request_uri != "/healthz") { return 404; }
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
        }""",
    )
    original = """        location = /readyz {
            default_type application/json;
            return 503 '{"status":"not_ready","mode":"maintenance"}';
        }"""
    if config.count(original) != 1:
        raise ValueError("maintenance readiness layout changed")
    config = config.replace(
        original,
        """        location = /readyz {
            if ($request_uri != "/readyz") { return 404; }
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
            error_log /dev/null;
        }
"""
        + _routes(),
    )
    marker = "        server_name minoshealth.com www.minoshealth.com;"
    if config.count(marker) != 1:
        raise ValueError("canonical redirect layout changed")
    config = config.replace(marker, marker + "\n" + _com_redirects())
    _ = (output / "nginx.conf").write_text(config)
    identities["nginx.conf"] = hashlib.sha256(config.encode()).hexdigest()
    _ = (output / "checksums.json").write_text(
        json.dumps(identities, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("output", type=Path)
    arguments = Arguments()
    _parsed_arguments = parser.parse_args(namespace=arguments)
    build(arguments.output)
