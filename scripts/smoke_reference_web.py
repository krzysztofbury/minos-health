"""Exercise immutable validation images through the real edge/API/browser path."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from http.client import HTTPResponse
from pathlib import Path
from typing import cast, final

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
    manifest = manifest.model_copy(
        update={
            "sources": tuple(source for source, _ in sources),
            "komunikaty": (stage_komunikat(root, canonical.release_id),),
        }
    )
    bundle = root / "bundle"
    _ = build_reference_artifact(manifest, root, bundle)
    for path in bundle.iterdir():
        path.chmod(0o444)
    bundle.chmod(0o755)
    return bundle


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
        if not read_artifact_manifest(bundle).dataset.komunikaty:
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
