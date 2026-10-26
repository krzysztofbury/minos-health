"""Synthetic CLI acceptance in a non-root, no-network container without Python."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast, final

RUNTIME_IMAGE = "debian:bookworm-slim@sha256:7ff6ba31527b9e336162fa061746c366c4b2032f4d2cff2078bea43f78efe3f8"
ROOT = Path(__file__).resolve().parents[1]


@final
class _Arguments(argparse.Namespace):
    application: Path = Path()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "application", type=Path, help="Standalone application directory."
    )
    arguments = _Arguments()
    _ = parser.parse_args(namespace=arguments)
    application = Path(arguments.application).resolve()
    with tempfile.TemporaryDirectory(prefix="minos-offline-") as temporary:
        work = Path(temporary)
        _prepare_fixture(work)
        docker = _docker_command(application, work)
        _ = subprocess.run(
            docker
            + [
                "/bin/sh",
                "-c",
                "! command -v python && ! command -v python3 && test ! -e /usr/local/bin/python",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        built = _run(
            docker,
            [
                "data",
                "build",
                "--manifest",
                "/work/source.json",
                "--archive-root",
                "/work",
                "--output",
                "/work/bundle",
                "--json",
            ],
        )
        assert json.loads(built.stdout)["row_counts"]["concept"] == 15
        _verify_searches(docker, cast(str, json.loads(built.stdout)["database_sha256"]))
        _verify_icd9_searches(docker)
        missing = _run(
            docker,
            [
                "search",
                "--system",
                "icd10-pl",
                "--query",
                "E99.9",
                "--data",
                "/work/bundle",
                "--json",
            ],
            expected_code=1,
        )
        assert json.loads(missing.stdout)["status"] == "no_results"
        print(
            "Standalone offline build/search passed: no installed Python; both indexes; deterministic output."
        )


def _prepare_fixture(work: Path) -> None:
    manifest_bytes = (ROOT / "manifests/sources/icd10-synthetic.json").read_bytes()
    manifest = cast(dict[str, object], json.loads(manifest_bytes))
    sources = cast(list[dict[str, object]], manifest["sources"])
    blob = work / "blobs/sha256" / cast(str, sources[0]["sha256"])
    blob.parent.mkdir(parents=True)
    _ = blob.write_bytes((ROOT / "tests/fixtures/icd10-pl/minimal.xml").read_bytes())
    # Host-only fixture helpers are not mounted into the frozen runtime.
    sys.path.insert(0, str(ROOT))
    from tests.fixtures.icd9_workbook import archive_bytes, workbook_manifest
    from tests.fixtures.icd9_xml import XML, source_manifest

    content = archive_bytes()
    for source, body in (
        (source_manifest(), XML),
        (workbook_manifest(content), content),
    ):
        _ = (blob.parent / source.sha256).write_bytes(body)
        sources.append(source.model_dump(mode="json"))
    _ = (work / "source.json").write_text(json.dumps(manifest))


def _docker_command(application: Path, work: Path) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=64m",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--volume",
        f"{application}:/app:ro",
        "--volume",
        f"{work}:/work",
        RUNTIME_IMAGE,
    ]


def _verify_searches(docker: list[str], database_sha256: str) -> None:
    for index in ("auto", "portable"):
        command = [
            "search",
            "--system",
            "icd10-pl",
            "--query",
            "E11.0",
            "--data",
            "/work/bundle",
            "--index",
            index,
            "--json",
        ]
        first = _run(docker, command)
        assert first.stdout == _run(docker, command).stdout
        answer = cast(dict[str, object], json.loads(first.stdout))
        results = cast(list[dict[str, object]], answer["results"])
        assert results[0]["code"] == "E11.0"
        assert results[0]["match_type"] == "exact_code"
        assert answer["database_sha256"] == database_sha256
        assert cast(dict[str, object], results[0]["context"])["edges"]
        command[command.index("E11.0")] = "etykietą"
        inflected = cast(dict[str, object], json.loads(_run(docker, command).stdout))
        assert inflected["morphology_version"] == "morfeusz2-1.99.15/sgjp-2026.06.01"
        assert (
            cast(list[dict[str, object]], inflected["results"])[0]["match_type"]
            == "morphology_tokens"
        )
        assert "etykieta" in cast(
            list[str],
            cast(list[dict[str, object]], inflected["morphology"])[0]["alternatives"],
        )


def _verify_icd9_searches(docker: list[str]) -> None:
    for index in ("auto", "portable"):
        for release in ("icd9-czs-synthetic-v1", "icd9-nfz-workbook-synthetic-v1"):
            command = [
                "search",
                "--system",
                "icd9-pl",
                "--release",
                release,
                "--query",
                "A01",
                "--data",
                "/work/bundle",
                "--index",
                index,
                "--json",
            ]
            first = _run(docker, command)
            assert first.stdout == _run(docker, command).stdout
            answer = cast(dict[str, object], json.loads(first.stdout))
            hit = cast(list[dict[str, object]], answer["results"])[0]
            attributes = cast(dict[str, str], hit["source_attributes"])
            context = cast(dict[str, object], hit["context"])
            assert hit["code"] == "A01"
            if release == "icd9-czs-synthetic-v1":
                assert attributes["status"] == "N"
                assert attributes["data_do"] == "2026-09-24"
                assert not context["edges"]
            else:
                assert hit["source_code"] == "A01 "
                assert len(cast(list[object], context["edges"])) == 2
                command[command.index("A01")] = "Different synthetic title"
                evidence_answer = cast(
                    dict[str, object], json.loads(_run(docker, command).stdout)
                )
                evidence = cast(list[dict[str, object]], evidence_answer["results"])[0]
                match = cast(dict[str, object], evidence["match"])
                assert evidence["code"] == "A01"
                assert match["description_type"] == "context"
                assert (
                    cast(dict[str, str], match["source_attributes"])["evidence_kind"]
                    == "conflicting_title_or_code_form"
                )


def _run(
    docker: list[str], arguments: list[str], *, expected_code: int = 0
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        docker + ["/app/minos", *arguments],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != expected_code:
        # Do not print captured source text, queries, local paths or tracebacks.
        raise RuntimeError(
            f"standalone acceptance failed with exit {result.returncode}"
        )
    return result


if __name__ == "__main__":
    main()
