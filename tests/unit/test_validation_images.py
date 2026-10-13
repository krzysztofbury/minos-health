import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts import validation_images as images


@pytest.fixture
def transfer(tmp_path: Path) -> tuple[Path, images.WorkflowContext]:
    context = images.WorkflowContext(revision="a" * 40, run_id=12, run_attempt=1)
    body = b"synthetic image archive"
    _ = (tmp_path / "images.tar").write_bytes(body)
    _ = (tmp_path / "transfer.json").write_text(
        json.dumps(
            {
                "version": 1,
                "context": {"revision": "a" * 40, "run_id": 12, "run_attempt": 1},
                "images": {"api": "sha256:" + "1" * 64, "edge": "sha256:" + "2" * 64},
                "archive_bytes": len(body),
                "archive_sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    )
    return tmp_path, context


def forbid_docker(*_arguments: str) -> bytes:
    raise AssertionError("Untrusted transfer reached Docker")


@pytest.mark.parametrize("field", ["revision", "run_id", "run_attempt"])
def test_other_commit_run_or_attempt_is_refused_before_docker(
    transfer: tuple[Path, images.WorkflowContext],
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    directory, context = transfer
    changed = context.model_copy(
        update={field: "b" * 40 if field == "revision" else 99}
    )
    monkeypatch.setattr(images, "docker", forbid_docker)

    with pytest.raises(images.ImageError, match="workflow identity differs"):
        _ = images.restore_images(directory, changed)


@pytest.mark.parametrize("change", ["content", "length", "symlink", "oversized"])
def test_archive_tampering_is_refused_before_docker(
    transfer: tuple[Path, images.WorkflowContext],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    directory, context = transfer
    archive = directory / "images.tar"
    match change:
        case "content":
            _ = archive.write_bytes(b"X" * archive.stat().st_size)
        case "length":
            _ = archive.write_bytes(archive.read_bytes() + b"extra")
        case "symlink":
            renamed = directory / "other.tar"
            _ = archive.rename(renamed)
            archive.symlink_to(renamed)
        case "oversized":
            with archive.open("wb") as output:
                _ = output.truncate(images.MAX_ARCHIVE_BYTES + 1)
        case _:
            raise AssertionError(change)
    monkeypatch.setattr(images, "docker", forbid_docker)

    with pytest.raises(images.ImageError, match="archive"):
        _ = images.restore_images(directory, context)


@pytest.mark.parametrize("body", [b"{}", b"X" * 4097, b'{"version":2}'])
def test_invalid_or_oversized_manifest_is_refused_before_docker(
    transfer: tuple[Path, images.WorkflowContext],
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    directory, context = transfer
    _ = (directory / "transfer.json").write_bytes(body)
    monkeypatch.setattr(images, "docker", forbid_docker)

    with pytest.raises((images.ImageError, ValidationError)):
        _ = images.restore_images(directory, context)


def inspection(image_id: str, revision: str) -> bytes:
    return json.dumps(
        {
            "Id": image_id,
            "Os": "linux",
            "Architecture": "amd64",
            "Size": 512,
            "Config": {"Labels": {"org.opencontainers.image.revision": revision}},
        }
    ).encode()


def test_loaded_image_must_match_tested_config_and_layers(
    transfer: tuple[Path, images.WorkflowContext], monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, context = transfer

    def docker(*arguments: str) -> bytes:
        if arguments[:2] == ("image", "load"):
            return b"loaded"
        return inspection("sha256:" + "f" * 64, context.revision)

    monkeypatch.setattr(images, "docker", docker)

    with pytest.raises(images.ImageError, match="Loaded image differs"):
        _ = images.restore_images(directory, context)


def test_registry_pull_must_match_tested_image(monkeypatch: pytest.MonkeyPatch) -> None:
    def docker(*arguments: str) -> bytes:
        if "{{json .RepoDigests}}" in arguments:
            return json.dumps(["registry.example.test/api@sha256:" + "3" * 64]).encode()
        if "{{json .}}" in arguments:
            return inspection("sha256:" + "f" * 64, "a" * 40)
        return b""

    monkeypatch.setattr(images, "docker", docker)

    with pytest.raises(images.ImageError, match="Published image differs"):
        _ = images.push_image(
            "sha256:" + "1" * 64, "registry.example.test/api", "run-12-1", "a" * 40
        )


def test_failed_transfer_never_emits_a_release_receipt(
    transfer: tuple[Path, images.WorkflowContext], monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, context = transfer
    _ = (directory / "images.tar").write_bytes(b"tampered")
    monkeypatch.setattr(images, "docker", forbid_docker)
    release = directory / "release"

    with pytest.raises(images.ImageError, match="archive identity differs"):
        images.publish_images(directory, context, release)

    assert not release.exists()
