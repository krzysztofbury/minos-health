import ast
import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from scripts.measure_reference_host import Parameters, Probe, unpack, validate_probe


@pytest.mark.parametrize("name", ["../minos.db", "/minos.db", "other.db"])
def test_measurement_refuses_foreign_archive_paths(tmp_path: Path, name: str) -> None:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for filename in (name, "manifest.json", "review.txt"):
            info = tarfile.TarInfo(filename)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    _ = stream.seek(0)
    with pytest.raises(RuntimeError, match="layout"):
        _ = unpack(stream, tmp_path, "a" * 64)
    assert not (tmp_path.parent / "minos.db").exists()


def test_measurement_verifies_database_before_running_any_container(
    tmp_path: Path,
) -> None:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for filename in ("minos.db", "manifest.json", "review.txt"):
            info = tarfile.TarInfo(filename)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    _ = stream.seek(0)
    with pytest.raises(RuntimeError, match="checksum"):
        _ = unpack(stream, tmp_path, hashlib.sha256(b"different").hexdigest())


def test_host_measurement_program_has_python_312_compatible_grammar() -> None:
    source = Path("scripts/measure_reference_host.py").read_text()
    _ = ast.parse(source, feature_version=(3, 12))


@pytest.mark.parametrize("failure", ["http", "dataset", "conditions"])
def test_measurement_refuses_failed_or_foreign_warm_probe(failure: str) -> None:
    parameters: Parameters = {
        "image": "unused",
        "revision": "a" * 40,
        "database_sha256": "b" * 64,
        "dataset_id": "c" * 64,
    }
    result: Probe = {
        "lookups": [
            {"route": "/readyz", "status": 200, "dataset_id": "c" * 64},
            {
                "route": "/groups/F72",
                "status": 200,
                "conditions_status": "fully_interpreted",
            },
            {"route": "/slowniki/icd-10/K40", "status": 200},
        ],
        "cgroup": {},
        "api_process_bytes": {},
    }
    validate_probe(result, parameters)
    if failure == "http":
        result["lookups"][2]["status"] = 503
    elif failure == "dataset":
        result["lookups"][0]["dataset_id"] = "d" * 64
    else:
        result["lookups"][1]["conditions_status"] = "missing_conditions"
    with pytest.raises(RuntimeError):
        validate_probe(result, parameters)
