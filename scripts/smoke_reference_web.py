"""Exercise immutable validation images through the real edge/API/browser path."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import date
from email.message import Message
from http.client import HTTPResponse
from pathlib import Path
from typing import IO, cast, final, override

ROOT = Path(__file__).resolve().parents[1]


@final
class Arguments(argparse.Namespace):
    output: Path = Path()
    bundle: Path | None = None
    api_image: str = "minos-reference-api:validation-local"
    edge_image: str = "minos-reference-edge:validation-local"
    chromium: str = "chromium"


def fixture(root: Path) -> Path:
    sys.path.insert(0, str(ROOT))
    from minos.ingest.reference_artifact import build_reference_artifact
    from minos.ingest.reference_manifest import DatasetManifest
    from tests.fixtures.icd9_workbook import archive_bytes, workbook_manifest
    from tests.fixtures.icd9_xml import XML, source_manifest
    from tests.fixtures.jgp_catalog import (
        catalog_workbook,
        default_sheets,
        stage_catalog,
    )
    from tests.fixtures.jgp_conditions import stage as stage_conditions
    from tests.fixtures.komunikat import stage_komunikat

    manifest = DatasetManifest.model_validate_json(
        (ROOT / "manifests/sources/icd10-synthetic.json").read_bytes()
    )
    body = archive_bytes()
    canonical = source_manifest()
    sources = (
        (canonical, XML),
        (
            workbook_manifest(body).model_copy(
                update={"context_for": canonical.release_id}
            ),
            body,
        ),
        (
            manifest.sources[0],
            (ROOT / "tests/fixtures/icd10-pl/minimal.xml").read_bytes(),
        ),
    )
    archive = root / "blobs/sha256"
    archive.mkdir(parents=True)
    for source, content in sources:
        _ = (archive / source.sha256).write_bytes(content)
    catalog = stage_catalog(
        root,
        catalog_workbook(default_sheets()),
        release={
            "baw_document_id": 44042,
            "document_url": "https://baw.nfz.gov.pl/NFZ/document/44042/Synthetic",
        },
    )
    characteristics = stage_conditions(root).model_copy(
        update={"catalog_release_id": catalog.release_id}
    )
    manifest = DatasetManifest.model_validate(
        {
            **manifest.model_dump(),
            "sources": tuple(source for source, _ in sources),
            "search_profile": "polish-folded-1",
            "search_synonyms": (
                {
                    "id": "synthetic-browser-alias",
                    "version": 1,
                    "system": "icd10-pl",
                    "term": "syntetyczna nazwa",
                    "source_wording": "Próbna etykieta Łódź",
                    "source_url": "https://example.org/synthetic-dictionary",
                    "evidence": "Synthetic fixture only, not a medical synonym.",
                    "status": "reviewed",
                    "reviewer": "Synthetic fixture reviewer",
                    "reviewed_on": date(2026, 10, 4),
                },
            ),
            "komunikaty": (stage_komunikat(root, canonical.release_id),),
            "catalogs": (catalog,),
            "characteristics": (characteristics,),
            "characteristics_knowledge_as_of": date(2026, 10, 2),
            "characteristics_knowledge_evidence": "Synthetic reviewed attachment 9 lineage.",
            "catalog_knowledge_as_of": date(2026, 10, 2),
            "catalog_knowledge_evidence": "Synthetic lineage review on 2026-10-02.",
        },
        strict=False,
    )
    bundle = root / "bundle"
    _ = build_reference_artifact(manifest, root, bundle)
    for path in bundle.iterdir():
        path.chmod(0o444)
    bundle.chmod(0o755)
    return bundle


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    @override
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        return None  # the redirect itself is the answer under test


def mcp_call(
    base: str, method: str, params: dict[str, object], *, client: str | None = None
) -> dict[str, object]:
    request = urllib.request.Request(
        base + "/mcp",
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).encode(),
        headers={
            "Host": "minoshealth.pl",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
            **({"CF-Connecting-IP": client} if client else {}),
        },
        method="POST",
    )
    with cast(HTTPResponse, urllib.request.urlopen(request, timeout=20)) as response:
        answer = cast(dict[str, object], json.loads(response.read(1_048_576)))
    return cast(dict[str, object], answer["result"])


def get_page(base: str, route: str) -> tuple[int, list[str]]:
    """Status and every Cache-Control header, without following redirects."""
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(base + route, headers={"Host": "minoshealth.pl"})
    try:
        with cast(HTTPResponse, opener.open(request, timeout=5)) as response:
            _ = response.read(1_048_577)
            return response.status, response.headers.get_all("Cache-Control") or []
    except urllib.error.HTTPError as error:
        headers = error.headers.get_all("Cache-Control") or []
        error.close()
        return error.code, headers


def docker(*arguments: str) -> str:
    return subprocess.check_output(
        ["docker", *arguments], text=True, timeout=120
    ).strip()


def browser_checks(arguments: Arguments, bundle: Path, port: int, output: Path) -> None:
    from minos.store.reference import open_reference_bundle, read_artifact_manifest

    # Chromium adds its own socket-directory suffix; deep artifact paths exceed
    # the Unix socket path limit. Honour the caller's short owned scratch root.
    parent = os.environ.get(
        "TMPDIR", "/tmp/opencode" if Path("/tmp/opencode").is_dir() else "/tmp"
    )
    with tempfile.TemporaryDirectory(prefix="cb-", dir=parent) as browser_temp:
        environment = dict(os.environ, TMPDIR=browser_temp)
        _ = subprocess.run(
            [
                "node",
                str(ROOT / "tests/browser/search-browser.mjs"),
                arguments.chromium,
                f"http://minoshealth.pl:{port}/",
                str(output),
            ],
            timeout=90,
            check=True,
            env=environment,
        )
        _ = subprocess.run(
            [
                "node",
                str(ROOT / "tests/browser/filter-preferences-browser.mjs"),
                arguments.chromium,
                f"http://minoshealth.pl:{port}/",
                str(output),
            ],
            timeout=180,
            check=True,
            env=environment,
        )
        dataset = read_artifact_manifest(bundle).dataset
        if dataset.search_profile is not None:
            _ = subprocess.run(
                [
                    "node",
                    str(ROOT / "tests/browser/search-fallback-browser.mjs"),
                    arguments.chromium,
                    f"http://minoshealth.pl:{port}/",
                    str(output),
                ],
                timeout=90,
                check=True,
                env=environment,
            )
        if dataset.characteristics:
            _ = subprocess.run(
                [
                    "node",
                    str(ROOT / "tests/browser/jgp-conditions-browser.mjs"),
                    arguments.chromium,
                    f"http://minoshealth.pl:{port}/",
                    str(output),
                ],
                timeout=90,
                check=True,
                env=environment,
            )
        if dataset.catalogs:
            _ = subprocess.run(
                [
                    "node",
                    str(ROOT / "tests/browser/jgp-search-browser.mjs"),
                    arguments.chromium,
                    f"http://minoshealth.pl:{port}/",
                    str(output),
                ],
                timeout=90,
                check=True,
                env=environment,
            )
            _ = subprocess.run(
                [
                    "node",
                    str(ROOT / "tests/browser/jgp-browser.mjs"),
                    arguments.chromium,
                    f"http://minoshealth.pl:{port}/",
                    str(output),
                ],
                timeout=90,
                check=True,
                env=environment,
            )
        if not dataset.komunikaty:
            return
        with open_reference_bundle(bundle) as (connection, _):
            sample = cast(
                tuple[str, int] | None,
                connection.execute(
                    """SELECT rank.code, rank.rank FROM procedure_rank AS rank
                JOIN algorithm_release AS release ON release.id=rank.release_id
                JOIN concept ON concept.source_release_id=release.icd9_release_id
                AND concept.lookup_code=rank.code WHERE rank.grouper_type='SZP'
                ORDER BY rank.code LIMIT 1"""
                ).fetchone(),
            )
        if sample is None:
            raise RuntimeError("rank browser fixture has no canonical SZP rank")
        _ = subprocess.run(
            [
                "node",
                str(ROOT / "tests/browser/procedure-rank-browser.mjs"),
                arguments.chromium,
                f"http://minoshealth.pl:{port}/",
                str(output),
                sample[0],
                str(sample[1]),
            ],
            timeout=90,
            check=True,
            env=environment,
        )


def main(arguments: Arguments) -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.check_reference_web import check, get_json
    from scripts.render_validation_compose import api_service

    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    bundle = arguments.bundle.resolve() if arguments.bundle else fixture(output)
    manifest = cast(
        dict[str, object], json.loads((bundle / "manifest.json").read_text())
    )
    digest = cast(str, manifest["database_sha256"])
    network = "minos-validation-" + uuid.uuid4().hex[:12]
    compose = output / "compose.json"
    configuration = {
        "services": {
            "reference-api": api_service(arguments.api_image, bundle, digest),
            "edge": {
                "image": arguments.edge_image,
                "user": "101:101",
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "tmpfs": [
                    "/tmp:rw,noexec,nosuid,nodev,size=8m,uid=101,gid=101,mode=0700"
                ],
                "mem_limit": "64m",
                "cpus": "0.50",
                "pids_limit": 64,
                "networks": {"reference": None, "browser": None},
                "ports": ["127.0.0.1::8080"],
                "depends_on": {"reference-api": {"condition": "service_healthy"}},
            },
        },
        "networks": {"reference": {"internal": True}, "browser": {}},
    }
    _ = compose.write_text(json.dumps(configuration))
    prefix = ("compose", "--project-name", network, "--file", str(compose))
    try:
        _ = docker(*prefix, "up", "--wait", "--wait-timeout", "45")
        api = docker(*prefix, "ps", "--quiet", "reference-api")
        edge = docker(*prefix, "ps", "--quiet", "edge")
        inspected = cast(list[dict[str, object]], json.loads(docker("inspect", edge)))
        settings = cast(dict[str, object], inspected[0]["NetworkSettings"])
        ports = cast(dict[str, list[dict[str, str]]], settings["Ports"])
        port = int(ports["8080/tcp"][0]["HostPort"])
        base = f"http://127.0.0.1:{port}"
        # A fresh process must serve text lookup before any browser/code warm-up.
        _status, metadata, _duration = get_json(base, "/api/v1/releases")
        assert _status == 200
        sources = cast(list[dict[str, str]], metadata["releases"])
        cold_status, cold_answer, cold_ms = get_json(
            base,
            "/api/v1/search",
            {
                "system": "icd9-pl",
                "release_id": sources[0]["release_id"],
                "query": "badania moczu",
            },
        )
        assert cold_status == 200
        assert cold_answer["results"]
        assert cold_ms < 500
        for _ in range(100):
            try:
                with cast(
                    HTTPResponse,
                    urllib.request.urlopen(
                        urllib.request.Request(
                            base + "/readyz", headers={"Host": "minoshealth.pl"}
                        ),
                        timeout=2,
                    ),
                ) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("validation runtime did not become ready")
        browser_checks(arguments, bundle, port, output)
        canonical = next(source for source in sources if source["origin"] == "czs")
        rank_route = f"/api/v1/procedure-ranks/{canonical['release_id']}/A01"
        assert get_json(base, rank_route)[0] == 200
        assert get_json(base, rank_route + "?q=PRIVATE_QUERY_SENTINEL")[0] == 404
        assert get_json(base, rank_route, {})[0] == 405
        concept_route = f"/api/v1/concepts/icd9-pl/{canonical['release_id']}/A01"
        status, concept, _ = get_json(base, concept_route)
        assert status == 200 and concept["api"] == 1
        assert (
            cast(dict[str, object], concept["release"])["release_id"]
            == canonical["release_id"]
        )
        assert cast(dict[str, object], concept["concept"])["parents"] == []
        assert concept["jgp"] is None
        # The agent endpoint answers through the same edge (#216).
        listed = mcp_call(base, "tools/list", {})
        assert len(cast(list[object], listed["tools"])) == 12
        searched = mcp_call(
            base,
            "tools/call",
            {
                "name": "search_codes",
                "arguments": {"query": "badania moczu", "systems": ["icd9-pl"]},
            },
        )
        assert not searched.get("isError")
        # 60 per minute with a burst of 30 per client; a held GET is refused.
        limited = 0
        for _ in range(40):
            try:
                _ = mcp_call(base, "tools/list", {}, client="203.0.113.9")
            except urllib.error.HTTPError as error:
                error.close()
                limited += error.code == 429
        assert limited >= 5
        assert get_json(base, "/mcp")[0] == 405
        dated_status, dated, _ = get_json(base, concept_route + "/jgp/2026-07-10")
        assert dated_status == 200 and dated["concept"] == concept["concept"]
        assert get_json(base, concept_route + "/jgp/2026-07-10/1a/pages/1")[0] == 200
        assert get_json(base, concept_route + "/jgp/2026-07-10/1a/pages/21")[0] == 404
        assert get_json(base, concept_route + "?date=2026-07-10")[0] == 404
        assert get_json(base, concept_route, {})[0] == 405
        assert get_page(base, concept_route)[1] == ["no-store"]
        # JGP routes: one call per group page, path-only dates, explicit gaps.
        group_route = "/api/v1/jgp/2026-07-10/groups/PZD02"
        status, group, _ = get_json(base, group_route)
        assert status == 200
        assert group["points_kind"] == "catalog_base_points_without_modifiers"
        ruleset = cast(dict[str, dict[str, object]], group["ruleset"])
        assert ruleset["modifiers"]["binding"] == "unbound"
        assert get_json(base, "/api/v1/jgp/2026-07-10/sections")[0] == 200
        assert get_json(base, "/api/v1/jgp/2026-07-10/sections/1a/1")[0] == 200
        status, gap, _ = get_json(base, "/api/v1/jgp/2026-06-30/groups/PZD02")
        # The API's explicit gap, not an edge 404 for an unrouted path.
        assert (status, cast(dict[str, str], gap["error"])["code"]) == (
            404,
            "no_ruleset_for_date",
        )
        status, refused, _ = get_json(base, group_route + "?date=2026-07-10")
        assert (status, refused) == (404, {})  # nginx refuses queries before the API
        assert get_json(base, group_route, {})[0] == 405
        # JGP pages: dated pages cache like other pages, "today" never does, and
        # the edge adds no second Cache-Control to the API's own header.
        for route, status, cache in (
            ("/jgp/2026-07-10", 200, "public"),
            ("/jgp/2026-07-10/PZD02", 200, "public"),
            ("/jgp/2026-07-10/zakres/15", 200, "public"),
            ("/jgp", 302, "no-store"),
            ("/jgp/2026-06-30/PZD02", 404, "no-store"),
            ("/jgp/2026-07-10/zakres/69", 404, "no-store"),
        ):
            answer = get_page(base, route)
            assert answer[0] == status, (route, answer)
            assert len(answer[1]) == 1 and answer[1][0].startswith(cache), (
                route,
                answer,
            )
        assert get_page(base, "/jgp/2026-07-10?x=1")[0] == 404
        assert get_page(base, "/jgp/2026-07-10/zakres/1000")[0] == 404
        assert len(get_page(base, group_route)[1]) == 1  # was doubled before #26
        if cast(dict[str, object], manifest["dataset"]).get("characteristics"):
            for route, status, cache in (
                # Today's JGP data: cached until midnight in Poland, at most 1 h.
                ("/slowniki/icd-10/E11.0", 200, "public, max-age="),
                ("/slowniki/icd-10/E11.0/jgp/2026-07-10", 200, "public"),
                ("/slowniki/icd-10/E11.0/jgp/2026-06-30", 404, "no-store"),
                ("/jgp/2026-07-10/listy/1a/2/1", 200, "public"),
                ("/jgp/2026-07-10/listy/1a/2/2", 404, "no-store"),
            ):
                answer = get_page(base, route)
                assert (
                    answer[0] == status
                    and len(answer[1]) == 1
                    and answer[1][0].startswith(cache)
                ), (route, answer)
            assert get_json(base, "/api/v1/jgp/2026-07-10/lists/1a/2/pages/1")[0] == 200
            assert (
                get_page(
                    base,
                    "/slowniki/icd-10/E11.0/jgp/2026-07-10?x=PRIVATE_QUERY_SENTINEL",
                )[0]
                == 404
            )
        logs = docker("logs", api)
        assert "badania moczu" not in logs and '"query"' not in logs
        assert '"request_id"' in logs
        load = check(base, expected_digest=digest)
        peak_bytes = int(
            docker(
                "exec",
                api,
                "/build/.venv/bin/python",
                "-c",
                "from pathlib import Path; print(Path('/sys/fs/cgroup/memory.peak').read_text().strip())",
            )
        )
        assert peak_bytes < 512 * 1024 * 1024
        for container in (api, edge):
            logs = docker("logs", container)
            assert "PRIVATE_QUERY_SENTINEL" not in logs
            assert "badania moczu" not in logs
        _ = docker(*prefix, "stop", "reference-api")
        stopped_status = get_json(base, "/readyz")[0]
        assert stopped_status in (502, 504), stopped_status
        _ = docker(*prefix, "up", "--wait", "--wait-timeout", "45", "reference-api")
        assert get_json(base, "/readyz")[0] == 200
        _ = (output / "acceptance.json").write_text(
            json.dumps(
                {
                    "database_sha256": digest,
                    "browser": "passed",
                    "log_privacy": "passed",
                    "load": load,
                    "first_text_request_ms": round(cold_ms, 3),
                    "api_peak_memory_bytes": peak_bytes,
                    "stopped_api_readiness": "failed_closed_and_recovered",
                }
            )
            + "\n"
        )
    finally:
        _ = docker(*prefix, "down", "--volumes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("output", type=Path)
    _ = parser.add_argument("--bundle", type=Path)
    _ = parser.add_argument(
        "--api-image", default="minos-reference-api:validation-local"
    )
    _ = parser.add_argument(
        "--edge-image", default="minos-reference-edge:validation-local"
    )
    _ = parser.add_argument("--chromium", default="chromium")
    arguments = Arguments()
    _parsed_arguments = parser.parse_args(namespace=arguments)
    main(arguments)
