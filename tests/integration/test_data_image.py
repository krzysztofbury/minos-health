# Raw OCI mutations deliberately bypass production models to exercise refusal.
# pyright: reportAny=false

import hashlib
import json
import os
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from minos.ingest.processing import data_image as image_module
from minos.ingest.processing.data_image import (
    DataImagePins,
    ImageContentError,
    package_data_image,
    verify_data_image,
)
from minos.ingest.reference_artifact import build_reference_artifact
from tests.integration.test_reference_artifact import stage_source

ROOT = Path(__file__).parents[2]


def encoded(value: dict[str, object]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


@dataclass(frozen=True)
class ImageCase:
    bundle: Path
    output: Path
    manifest: bytes
    config: bytes
    pins: DataImagePins


@pytest.fixture
def image_case(tmp_path: Path) -> ImageCase:
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(stage_source(tmp_path), tmp_path, bundle)
    config = encoded(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {
                "User": "10004:10004",
                "Env": ["HOME=/nonexistent"],
                "Entrypoint": ["/app/server"],
                "WorkingDir": "/app",
                "Labels": {"approved": "application-baseline"},
            },
            "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "a" * 64]},
            "history": [{"created_by": "approved application build"}],
        }
    )
    manifest = encoded(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + hashlib.sha256(config).hexdigest(),
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "digest": "sha256:" + "b" * 64,
                    "size": 1024,
                }
            ],
        }
    )
    return ImageCase(
        bundle,
        tmp_path / "output",
        manifest,
        config,
        DataImagePins(
            application_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
            artifact_manifest_sha256=hashlib.sha256(
                (bundle / "manifest.json").read_bytes()
            ).hexdigest(),
        ),
    )


def package(case: ImageCase) -> None:
    _ = package_data_image(
        application_manifest=case.manifest,
        application_config=case.config,
        bundle=case.bundle,
        pins=case.pins,
        output=case.output,
    )


def verify(case: ImageCase) -> None:
    result = verify_data_image(
        application_manifest=case.manifest,
        application_config=case.config,
        image_manifest=(case.output / "image-manifest.json").read_bytes(),
        image_config=(case.output / "image-config.json").read_bytes(),
        data_layer=case.output / "data-layer.tar",
        bundle=case.bundle,
        pins=case.pins,
    )
    assert result.status == "data_image_content_only"
    assert (
        result.database_sha256
        == hashlib.sha256((case.bundle / "minos.db").read_bytes()).hexdigest()
    )


def test_exact_data_layer_is_deterministic_and_preserves_runtime(
    image_case: ImageCase,
) -> None:
    case = image_case
    package(case)
    first = {path.name: path.read_bytes() for path in case.output.iterdir()}
    second_output = case.output.with_name("second")
    _ = package_data_image(
        application_manifest=case.manifest,
        application_config=case.config,
        bundle=case.bundle,
        pins=case.pins,
        output=second_output,
    )
    assert {path.name: path.read_bytes() for path in second_output.iterdir()} == first
    base = json.loads(case.config)
    candidate = json.loads(first["image-config.json"])
    assert candidate["config"] == base["config"]
    assert candidate["rootfs"]["diff_ids"][:-1] == base["rootfs"]["diff_ids"]
    assert (
        json.loads(first["image-manifest.json"])["layers"][:-1]
        == json.loads(case.manifest)["layers"]
    )
    with tarfile.open(case.output / "data-layer.tar") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == [
            "app/data/manifest.json",
            "app/data/minos.db",
            "app/data/review.txt",
        ]
        for member in members:
            assert member.isfile()
            assert (member.mode, member.uid, member.gid, member.mtime) == (
                0o444,
                10004,
                10004,
                0,
            )
            stream = archive.extractfile(member)
            assert stream is not None
            with stream:
                assert (
                    stream.read() == (case.bundle / Path(member.name).name).read_bytes()
                )
    # Reading a layer may legitimately update atime. It must not look like content mutation.
    os.utime(case.output / "data-layer.tar", (1, 2))
    verify(case)


@pytest.mark.parametrize(
    "damage",
    [
        "entrypoint",
        "environment",
        "user",
        "label",
        "base-layer",
        "extra-layer",
        "architecture",
    ],
)
def test_self_consistent_candidate_cannot_change_application(
    image_case: ImageCase, damage: str
) -> None:
    case = image_case
    package(case)
    config = json.loads((case.output / "image-config.json").read_bytes())
    manifest = json.loads((case.output / "image-manifest.json").read_bytes())
    if damage == "entrypoint":
        config["config"]["Entrypoint"] = ["/app/data/server"]
    elif damage == "environment":
        config["config"]["Env"] = ["PYTHONPATH=/app/data"]
    elif damage == "user":
        config["config"]["User"] = "0"
    elif damage == "label":
        config["config"]["Labels"]["approved"] = "different"
    elif damage == "architecture":
        config["architecture"] = "arm64"
    elif damage == "base-layer":
        manifest["layers"][0]["digest"] = "sha256:" + "c" * 64
    else:
        manifest["layers"].append(manifest["layers"][-1])
        config["rootfs"]["diff_ids"].append(config["rootfs"]["diff_ids"][-1])
    content = encoded(config)
    manifest["config"]["digest"] = "sha256:" + hashlib.sha256(content).hexdigest()
    manifest["config"]["size"] = len(content)
    _ = (case.output / "image-config.json").write_bytes(content)
    _ = (case.output / "image-manifest.json").write_bytes(encoded(manifest))
    with pytest.raises(ImageContentError, match="application|unsupported"):
        verify(case)


@pytest.mark.parametrize(
    "damage",
    [
        "traversal",
        "symlink",
        "hardlink",
        "executable",
        "whiteout",
        "owner",
        "extra-file",
        "payload",
        "padding",
        "truncated",
    ],
)
def test_layer_refuses_non_data_semantics(image_case: ImageCase, damage: str) -> None:
    case = image_case
    package(case)
    path = case.output / "data-layer.tar"
    content = bytearray(path.read_bytes())
    if damage in ("payload", "padding", "truncated"):
        if damage == "payload":
            content[512] ^= 1
        elif damage == "padding":
            content[-1] = 1
        else:
            content = content[:-1]
    else:
        with tarfile.open(path) as archive:
            header = archive.getmembers()[0]
        if damage == "traversal":
            header.name = "app/data/../../server"
        elif damage == "symlink":
            header.type = tarfile.SYMTYPE
            header.linkname = "/app/server"
        elif damage == "hardlink":
            header.type = tarfile.LNKTYPE
            header.linkname = "app/server"
        elif damage == "executable":
            header.mode = 0o555
        elif damage == "whiteout":
            header.name = "app/data/.wh..wh..opq"
        elif damage == "owner":
            header.uid = 0
        else:
            header.name = "app/data/extra.json"
        content[:512] = header.tobuf(format=tarfile.USTAR_FORMAT)
    _ = path.write_bytes(content)
    # Recompute all claimed layer identities: a digest alone cannot authorize unsafe content.
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    config = json.loads((case.output / "image-config.json").read_bytes())
    config["rootfs"]["diff_ids"][-1] = digest
    config_bytes = encoded(config)
    manifest = json.loads((case.output / "image-manifest.json").read_bytes())
    manifest["layers"][-1].update(digest=digest, size=len(content))
    manifest["config"].update(
        digest="sha256:" + hashlib.sha256(config_bytes).hexdigest(),
        size=len(config_bytes),
    )
    _ = (case.output / "image-config.json").write_bytes(config_bytes)
    _ = (case.output / "image-manifest.json").write_bytes(encoded(manifest))
    with pytest.raises(ImageContentError, match="layer"):
        verify(case)


@pytest.mark.parametrize(
    "damage",
    ["database", "review", "manifest", "symlink", "fifo", "base-pin", "config-pin"],
)
def test_packaging_failure_never_publishes_output(
    image_case: ImageCase, damage: str
) -> None:
    case = image_case
    if damage in ("database", "review", "manifest"):
        name = {
            "database": "minos.db",
            "review": "review.txt",
            "manifest": "manifest.json",
        }[damage]
        path = case.bundle / name
        _ = path.write_bytes(b"X" * path.stat().st_size)
    elif damage in ("symlink", "fifo"):
        path = case.bundle / "review.txt"
        saved = path.with_name("saved")
        _ = path.rename(saved)
        if damage == "symlink":
            path.symlink_to(saved)
        else:
            os.mkfifo(path)
    elif damage == "base-pin":
        case = ImageCase(
            case.bundle, case.output, case.manifest + b" ", case.config, case.pins
        )
    else:
        case = ImageCase(
            case.bundle, case.output, case.manifest, case.config + b" ", case.pins
        )
    with pytest.raises(ImageContentError):
        package(case)
    assert not case.output.exists()
    assert not list(case.output.parent.glob(".minos-data-image-*"))


def test_cli_packages_and_refuses_overwriting_existing_output(
    image_case: ImageCase,
) -> None:
    case = image_case
    manifest_path = case.bundle.parent / "application.json"
    config_path = case.bundle.parent / "config.json"
    _ = manifest_path.write_bytes(case.manifest)
    _ = config_path.write_bytes(case.config)
    command = [
        sys.executable,
        str(ROOT / "scripts/package_reference_image.py"),
        "--application-manifest",
        str(manifest_path),
        "--application-config",
        str(config_path),
        "--application-sha256",
        case.pins.application_manifest_sha256,
        "--artifact-manifest-sha256",
        case.pins.artifact_manifest_sha256,
        "--bundle",
        str(case.bundle),
        "--output",
        str(case.output),
    ]
    success = subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=30
    )
    assert json.loads(success.stdout)["status"] == "data_image_content_only"
    verify(case)
    before = {path.name: path.read_bytes() for path in case.output.iterdir()}
    failure = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=30
    )
    assert failure.returncode == 2
    assert failure.stderr == "Data-only image content could not be verified.\n"
    assert failure.stdout == ""
    assert str(case.bundle) not in failure.stderr
    assert {path.name: path.read_bytes() for path in case.output.iterdir()} == before


def test_duplicate_metadata_fields_are_refused(image_case: ImageCase) -> None:
    case = image_case
    package(case)
    path = case.output / "image-config.json"
    content = path.read_bytes().replace(b'"os":"linux"', b'"os":"linux","os":"linux"')
    _ = path.write_bytes(content)
    with pytest.raises(ImageContentError, match="duplicate"):
        verify(case)


@pytest.mark.parametrize(
    "damage", ["index", "unknown-field", "layer-inventory", "history", "metadata-bound"]
)
def test_unsupported_base_is_refused_even_with_matching_pin(
    image_case: ImageCase, damage: str
) -> None:
    case = image_case
    manifest = json.loads(case.manifest)
    config = json.loads(case.config)
    if damage == "index":
        manifest["mediaType"] = "application/vnd.oci.image.index.v1+json"
    elif damage == "unknown-field":
        config["unreviewed-extension"] = True
    elif damage == "layer-inventory":
        config["rootfs"]["diff_ids"] = []
    elif damage == "history":
        config.pop("history")
    else:
        config["config"]["Labels"] = {"oversized": "x" * (1024 * 1024)}
    config_bytes = encoded(config)
    manifest["config"].update(
        digest="sha256:" + hashlib.sha256(config_bytes).hexdigest(),
        size=len(config_bytes),
    )
    manifest_bytes = encoded(manifest)
    pins = DataImagePins(
        application_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        artifact_manifest_sha256=case.pins.artifact_manifest_sha256,
    )
    case = ImageCase(case.bundle, case.output, manifest_bytes, config_bytes, pins)
    with pytest.raises(ImageContentError):
        package(case)
    assert not case.output.exists()


@pytest.mark.parametrize("damage", ["symlink", "fifo"])
def test_verifier_refuses_non_regular_layer(image_case: ImageCase, damage: str) -> None:
    case = image_case
    package(case)
    path = case.output / "data-layer.tar"
    saved = path.with_name("saved.tar")
    _ = path.rename(saved)
    if damage == "symlink":
        path.symlink_to(saved)
    else:
        os.mkfifo(path)
    with pytest.raises(ImageContentError, match="layer"):
        verify(case)


def test_failed_flush_removes_incomplete_delta(
    image_case: ImageCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_flush(_descriptor: int) -> None:
        raise OSError("private path and payload must not escape")

    monkeypatch.setattr(os, "fsync", fail_flush)
    with pytest.raises(ImageContentError, match="packaging or cleanup failed"):
        package(image_case)
    assert not image_case.output.exists()
    assert not list(image_case.output.parent.glob(".minos-data-image-*"))


def test_expired_deadline_removes_incomplete_delta(
    image_case: ImageCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(image_module, "IMAGE_CONTENT_TIMEOUT_SECONDS", 0)
    with pytest.raises(ImageContentError, match="deadline"):
        package(image_case)
    assert not image_case.output.exists()
    assert not list(image_case.output.parent.glob(".minos-data-image-*"))
