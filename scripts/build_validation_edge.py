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
    ("/gruper", "grouper.html"),
    ("/slowniki", "dictionaries.html"),
    ("/slowniki/icd-10", "dictionary-icd-10.html"),
    ("/slowniki/icd-9", "dictionary-icd-9.html"),
    ("/slowniki/icd-11", "dictionary-icd-11.html"),
    ("/dla-agentow", "agents.html"),
)
ASSET_ROUTES = (
    "/grouper.js",
    "/datepicker.js",
    "/request.js",
    "/retry.js",
    "/copy.js",
    "/search.js",
    "/dictionary.js",
    "/jgp.js",
    "/social-preview-v2.png",
    "/icon-512.png",
)


def _routes() -> str:
    blocks = [
        """
        # Branded page for every edge-generated 404; proxied API errors pass through.
        error_page 404 /404.html;
        error_page 502 503 504 /unavailable.html;
        location = /unavailable.html {
            internal;
            try_files /unavailable.html =503;
        }
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
        location = /api/v1/grouping/dictionaries {
            if ($request_uri != "/api/v1/grouping/dictionaries") { return 404; }
            if ($request_method != GET) { return 405; }
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 5s;
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
        # Public MCP for agents (#216): stateless Streamable HTTP served by the
        # API, limited per client (Cloudflare's CF-Connecting-IP, else the peer).
        location = /mcp {
            if ($request_uri != "/mcp") { return 404; }
            # Stateless: no server stream, and a held GET would pin an API slot.
            if ($request_method != POST) { return 405; }
            limit_req zone=mcp burst=30 nodelay;
            limit_req_status 429;
            client_max_body_size 64k;
            client_body_buffer_size 64k;
            client_body_timeout 2s;
            proxy_request_buffering on;
            proxy_buffering off;
            proxy_max_temp_file_size 0;
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            # One tool may make several bounded API calls in sequence.
            proxy_read_timeout 20s;
            error_log /dev/null;
        }
        location = /api/v1/group {
            if ($request_uri != "/api/v1/group") { return 404; }
            if ($request_method != POST) { return 405; }
            client_max_body_size 256k;
            client_body_buffer_size 256k;
            client_body_timeout 2s;
            proxy_request_buffering off;
            proxy_buffering off;
            proxy_max_temp_file_size 0;
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            # Outlasts the API worst case (2 s body, 0.5 s lease wait, 3 s
            # evaluation) so callers receive its JSON error, never an edge 504.
            proxy_read_timeout 7s;
            error_log /dev/null;
        }
        # Code pages and sitemaps are server-rendered by the API for crawlers.
        location ~ "^/slowniki/icd-(10|9|11)/[A-Za-z0-9][A-Za-z0-9.-]{0,15}(/jgp/20[0-9]{2}-[0-9]{2}-[0-9]{2}(/1ae?/[1-9][0-9]{0,1})?)?$" {
            if ($request_uri ~ "\\?") { return 404; }
            if ($request_method !~ ^(GET|HEAD)$) { return 405; }
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
            error_log /dev/null;
        }
        location ~ "^/sitemap-icd-(10|9|11)\\.xml$" {
            if ($request_uri ~ "\\?") { return 404; }
            if ($request_method !~ ^(GET|HEAD)$) { return 405; }
            proxy_hide_header Cache-Control;
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
            error_log /dev/null;
        }
        # Comparisons pin public dataset/date/filter identities, never queries.
        location ~ "^/jgp/porownaj/[0-9a-f]{64}/20[0-9]{2}-[0-9]{2}-[0-9]{2}/[a-z0-9-]{1,80}/20[0-9]{2}-[0-9]{2}-[0-9]{2}/[a-z0-9-]{1,80}/(wszystkie|[0-9a-f]{64})/(wszystkie|[A-Z]{1,4}[0-9]{2,3}[A-Z]{0,3})$" {
            if ($request_uri ~ "\\?") { return 404; }
            if ($request_method != GET) { return 405; }
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
            error_log /dev/null;
        }

        # JGP pages: today-redirects, dated catalog, group and scope pages.
        # Lowercase codes pass so the API can redirect them to uppercase.
        location ~ "^/jgp(/(20[0-9]{2}-[0-9]{2}-[0-9]{2}(/([A-Za-z]{1,4}[0-9]{2,3}[A-Za-z]{0,3}|zakres/[1-9][0-9]{0,2}|listy/1ae?/[1-9][0-9]{0,3}/[1-9][0-9]{0,2}))?|[A-Za-z]{1,4}[0-9]{2,3}[A-Za-z]{0,3}))?$" {
            if ($request_uri ~ "\\?") { return 404; }
            if ($request_method != GET) { return 405; }
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
            error_log /dev/null;
        }
        # JGP paths carry a service date and public exact identifiers only.
        location ~ "^/api/v1/jgp/20[0-9]{2}-[0-9]{2}-[0-9]{2}/(groups/[A-Z]{1,4}[0-9]{2,3}[A-Z]{0,3}|sections|sections/1ae?/[1-9][0-9]{0,2}|lists/1ae?/[1-9][0-9]{0,3}/pages/[1-9][0-9]{0,2})$" {
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
        # Concept details pin dictionary releases; optional dates select JGP only.
        location ~ "^/api/v1/concepts/icd(10|9|11)-pl/[a-z0-9-]{1,80}/[A-Z0-9][A-Z0-9.-]{0,15}(/jgp/20[0-9]{2}-[0-9]{2}-[0-9]{2}(/1ae?/pages/([1-9]|1[0-9]|20))?)?$" {
            if ($request_uri ~ "\\?") { return 404; }
            if ($request_method != GET) { return 405; }
            proxy_hide_header Cache-Control;
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
    keepalive = "    keepalive_timeout 15;\n"
    if config.count(keepalive) != 1:
        raise ValueError("edge http block layout changed")
    config = config.replace(
        keepalive,
        keepalive
        + "    map $http_cf_connecting_ip $mcp_client {\n"
        + '        "" $binary_remote_addr;\n'
        + "        default $http_cf_connecting_ip;\n"
        + "    }\n"
        + "    limit_req_zone $mcp_client zone=mcp:1m rate=60r/m;\n",
    )
    # Nginx warning/error messages can include untrusted URLs and headers.
    config = config.replace("error_log /dev/stderr warn;", "error_log /dev/null;")
    sources = """        location = /zrodla {
            if ($request_uri != "/zrodla") {
                return 404;
            }
            try_files /sources.html =404;
        }"""
    if config.count(sources) != 1:
        raise ValueError("source catalog route layout changed")
    config = config.replace(
        sources,
        """        location = /zrodla {
            if ($args != "") { return 404; }
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
        }

        location ~ "^/zrodla/(rsk|baw|nfz)(/obserwacje/[1-9][0-9]{0,18}-[0-9a-f]{64})?(/([0-9A-Z]{6}|[1-9][0-9]{0,15}))?(/strona/[1-9][0-9]{0,2})?$" {
            if ($args != "") { return 404; }
            proxy_pass http://reference-api:8081;
            proxy_connect_timeout 1s;
            proxy_read_timeout 3s;
        }""",
    )
    config = config.replace(
        '~^/(|slowniki|slowniki/icd-10|slowniki/icd-9|slowniki/icd-11|zrodla|polityka-prywatnosci|regulamin)$ "no-cache";',
        '~^/(|slowniki|slowniki/icd-10|slowniki/icd-9|slowniki/icd-11|polityka-prywatnosci|regulamin)$ "no-cache";\n        ~^/zrodla(/|$|\\?) "upstream";',
    )
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
