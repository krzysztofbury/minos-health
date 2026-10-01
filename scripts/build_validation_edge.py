"""Build a validation-only edge artifact without changing maintenance defaults."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import final

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / "infra/ansible/files"

ROUTES = """
        location = /szukaj {
            if ($request_uri != "/szukaj") { return 404; }
            try_files /search.html =404;
        }
        location = /search.css {
            if ($request_uri != "/search.css") { return 404; }
            try_files /search.css =404;
        }
        location = /search.js {
            if ($request_uri != "/search.js") { return 404; }
            try_files /search.js =404;
        }
        location = /api/v1/releases {
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
"""


@final
class Arguments(argparse.Namespace):
    output: Path = Path()


def build(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    names = (
        "home.css",
        "legal.css",
        "plausible-init.js",
        "privacy-policy.html",
        "sources.css",
        "sources.html",
        "sources.js",
        "terms.html",
        "search.html",
        "search.css",
        "search.js",
    )
    identities: dict[str, str] = {}
    for name in names:
        source_directory = ROOT / "apps/web" if name.startswith("search.") else FILES
        content = (source_directory / name).read_bytes()
        _ = (output / name).write_bytes(content)
        identities[name] = hashlib.sha256(content).hexdigest()
    home = (FILES / "maintenance-index.html").read_text()
    marker = 'href="/zrodla"'
    if marker not in home:
        raise ValueError("validation home navigation layout changed")
    home = home.replace(
        "</nav>", '<a href="/szukaj">Wyszukiwanie ICD-9 PL (walidacja)</a></nav>', 1
    )
    home = home.replace(
        "Wyszukiwarka jest w przygotowaniu.",
        "Dostępna jest walidacja wyszukiwania ICD-9 PL.",
    )
    home = home.replace(
        "Wyszukiwanie kodów ICD-10 i ICD-9 oraz porównywanie punktów",
        "Wyszukiwanie kodów ICD-10 oraz porównywanie punktów",
    )
    if 'href="/szukaj"' not in home:
        raise ValueError("validation home navigation is missing")
    _ = (output / "index.html").write_text(home)
    identities["index.html"] = hashlib.sha256(home.encode()).hexdigest()
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
        + ROUTES,
    )
    redirect = """        location = /szukaj {
            if ($request_uri != "/szukaj") { return 404; }
            return 308 https://minoshealth.pl/szukaj;
        }
"""
    config = config.replace(
        "        server_name minoshealth.com www.minoshealth.com;",
        "        server_name minoshealth.com www.minoshealth.com;\n" + redirect,
    )
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
