"""Synthetic CLI acceptance in a non-root, no-network container without Python."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import cast, final

RUNTIME_IMAGE = (
    "debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251"
)
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
        assert json.loads(built.stdout)["row_counts"]["concept"] == 6
        _verify_searches(docker, cast(str, json.loads(built.stdout)["database_sha256"]))
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
    _ = (work / "source.json").write_bytes(manifest_bytes)


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
