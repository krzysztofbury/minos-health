"""Transfer tested validation images to a separate publisher without rebuilding."""

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, ClassVar, Literal, final

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
DIGEST_ADAPTER: TypeAdapter[str] = TypeAdapter(Digest)
Revision = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Kind = Literal["api", "edge"]
KINDS: tuple[Kind, ...] = ("api", "edge")
LOCAL_IMAGES = {
    "api": "minos-reference-api:validation-local",
    "edge": "minos-reference-edge:validation-local",
}
REPOSITORIES = {
    "api": "ghcr.io/krzysztofbury/minos-health-reference-api",
    "edge": "ghcr.io/krzysztofbury/minos-health-validation-edge",
}
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024


class ImageError(Exception):
    """A fixed-field transfer or publication refusal, without raw Docker output."""


class WorkflowContext(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    revision: Revision
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)


class Transfer(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    version: Literal[1] = 1
    context: WorkflowContext
    images: dict[Kind, Digest] = Field(min_length=2, max_length=2)
    archive_bytes: int = Field(gt=0, le=MAX_ARCHIVE_BYTES)
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ImageLabels(BaseModel):
    revision: Revision = Field(alias="org.opencontainers.image.revision")


class ImageConfig(BaseModel):
    labels: ImageLabels = Field(alias="Labels")


class ImageIdentity(BaseModel):
    image_id: Digest = Field(alias="Id")
    operating_system: Literal["linux"] = Field(alias="Os")
    architecture: Literal["amd64"] = Field(alias="Architecture")
    size: int = Field(alias="Size", gt=0, le=MAX_ARCHIVE_BYTES)
    config: ImageConfig = Field(alias="Config")


def docker(*arguments: str) -> bytes:
    with tempfile.TemporaryFile() as output:
        try:
            _ = subprocess.run(
                ["docker", *arguments],
                stdout=output,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ImageError("Docker operation failed") from error
        _ = output.seek(0)
        result = output.read(1024 * 1024 + 1)
    if len(result) > 1024 * 1024:
        raise ImageError("Docker output exceeds limit")
    return result


def identity(image: str, revision: str) -> str:
    inspected = ImageIdentity.model_validate_json(
        docker("image", "inspect", "--format", "{{json .}}", image)
    )
    if inspected.config.labels.revision != revision:
        raise ImageError("Image source revision differs")
    return inspected.image_id


def archive_identity(archive: Path) -> tuple[int, str]:
    info = archive.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ARCHIVE_BYTES:
        raise ImageError("Image archive is not a bounded regular file")
    with archive.open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    return info.st_size, checksum


def export_images(directory: Path, context: WorkflowContext) -> Transfer:
    images: dict[Kind, str] = {
        kind: identity(LOCAL_IMAGES[kind], context.revision) for kind in KINDS
    }
    directory.mkdir(mode=0o700)
    archive = directory / "images.tar"
    _ = docker("image", "save", "--output", str(archive), *LOCAL_IMAGES.values())
    size, checksum = archive_identity(archive)
    transfer = Transfer(
        context=context, images=images, archive_bytes=size, archive_sha256=checksum
    )
    _ = (directory / "transfer.json").write_text(
        transfer.model_dump_json(indent=2) + "\n"
    )
    return transfer


def restore_images(directory: Path, context: WorkflowContext) -> Transfer:
    manifest = directory / "transfer.json"
    info = manifest.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 4096:
        raise ImageError("Transfer manifest is not a bounded regular file")
    transfer = Transfer.model_validate_json(manifest.read_bytes())
    if transfer.context != context:
        raise ImageError("Transfer source or workflow identity differs")
    archive = directory / "images.tar"
    if archive_identity(archive) != (transfer.archive_bytes, transfer.archive_sha256):
        raise ImageError("Image archive identity differs")
    _ = docker("image", "load", "--input", str(archive))
    for kind in KINDS:
        if identity(LOCAL_IMAGES[kind], context.revision) != transfer.images[kind]:
            raise ImageError("Loaded image differs from tested image")
    return transfer


def push_image(image_id: str, repository: str, tag: str, revision: str) -> str:
    tagged = f"{repository}:{tag}"
    _ = docker("image", "tag", image_id, tagged)
    _ = docker("image", "push", tagged)
    references = TypeAdapter(list[str]).validate_json(
        docker("image", "inspect", "--format", "{{json .RepoDigests}}", tagged)
    )
    matching = [
        reference for reference in references if reference.startswith(repository + "@")
    ]
    if len(matching) != 1:
        raise ImageError("Published registry digest is ambiguous")
    reference = matching[0]
    _ = DIGEST_ADAPTER.validate_python(reference.removeprefix(repository + "@"))
    _ = docker("image", "pull", reference)
    if identity(reference, revision) != image_id:
        raise ImageError("Published image differs from tested image")
    return reference


def publish_images(directory: Path, context: WorkflowContext, release: Path) -> None:
    transfer = restore_images(directory, context)
    references = {
        kind: push_image(
            transfer.images[kind],
            REPOSITORIES[kind],
            f"run-{context.run_id}-{context.run_attempt}",
            context.revision,
        )
        for kind in KINDS
    }
    release.mkdir(mode=0o700)
    receipt = {
        "source_revision": context.revision,
        "run_id": context.run_id,
        "run_attempt": context.run_attempt,
        "api_image": references["api"],
        "edge_image": references["edge"],
    }
    _ = (release / "validation-release.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    )
    # Retain the small identity evidence with the release, not the image archive.
    _ = (release / "validation-build.json").write_text(
        transfer.model_dump_json(indent=2) + "\n"
    )
    print(json.dumps(receipt))


@final
class Arguments(argparse.Namespace):
    command: str = ""
    directory: Path = Path()
    release: Path | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("command", choices=("export", "publish"))
    _ = parser.add_argument("directory", type=Path)
    _ = parser.add_argument("--release", type=Path)
    arguments = Arguments()
    _ = parser.parse_args(namespace=arguments)
    try:
        context = WorkflowContext.model_validate(
            {
                "revision": os.environ.get("GITHUB_SHA"),
                "run_id": os.environ.get("GITHUB_RUN_ID"),
                "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            },
            strict=False,
        )
        if arguments.command == "export":
            print(export_images(arguments.directory, context).model_dump_json())
        elif arguments.release is not None:
            publish_images(arguments.directory, context, arguments.release)
        else:
            raise ImageError("Publication requires a release directory")
    except (ImageError, OSError, ValidationError) as error:
        print(
            json.dumps(
                {
                    "error": str(error)
                    if isinstance(error, ImageError)
                    else type(error).__name__
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
