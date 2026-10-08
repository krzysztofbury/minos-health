"""Reference bundle format and bounded file identities, independent of builders."""

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from minos.domain.reference import ArtifactError
from minos.ingest.reference_manifest import DatasetManifest, Digest

DATABASE_NAME = "minos.db"
ARTIFACT_MANIFEST_NAME = "manifest.json"
REVIEW_SUMMARY_NAME = "review.txt"
MAX_MANIFEST_BYTES = 1024 * 1024


class ArtifactManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    schema_version: Literal[2, 3, 4, 5, 6, 7, 8, 9]
    database_sha256: Digest
    database_size_bytes: int = Field(ge=1, le=512 * 1024 * 1024)
    schema_sha256: Digest
    review_summary_sha256: Digest
    dataset: DatasetManifest
    row_counts: dict[str, Annotated[int, Field(ge=0, le=3_200_000)]]
    python_version: str = Field(min_length=1, max_length=64)
    sqlite_version: str = Field(min_length=1, max_length=64)
    sqlite_compile_options: Annotated[tuple[str, ...], Field(max_length=256)]
    built_at: AwareDatetime


def canonical_json(value: BaseModel | dict[str, object]) -> bytes:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return (
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def dataset_identity(manifest: DatasetManifest) -> str:
    return hashlib.sha256(canonical_json(manifest)).hexdigest()


def read_regular_file(path: Path, *, max_bytes: int = MAX_MANIFEST_BYTES) -> bytes:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    )
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ArtifactError("reference input is not a regular file")
        content = source.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ArtifactError("reference input exceeds size limit")
    return content


def file_identity(path: Path, *, max_bytes: int = 512 * 1024 * 1024) -> tuple[str, int]:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    )
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ArtifactError("reference artifact is not a regular file")
        digest = hashlib.sha256()
        size_bytes = 0
        while chunk := source.read(64 * 1024):
            size_bytes += len(chunk)
            if size_bytes > max_bytes:
                raise ArtifactError("reference artifact exceeds size limit")
            digest.update(chunk)
    return digest.hexdigest(), size_bytes
