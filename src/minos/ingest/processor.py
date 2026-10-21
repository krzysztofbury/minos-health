"""Judge newly archived source files against the live bundle (#141 phase B3).

The processor reads only the watcher's public projections, never its control
database. It fetches every input from the archive with its own read-only key,
runs the shadow candidates and activates nothing. A verdict is stored once per
input, live bundle and policy, so an unchanged rejection is not rebuilt; every
run reports the current verdicts to the data-update Kuma monitor.
"""

import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.domain.reference import ReferenceError
from minos.ingest.archive_build import ArchiveObject, source_files
from minos.ingest.candidate_evaluation import (
    CandidateReport,
    evaluate_candidate,
    rejected,
)
from minos.ingest.icd9_candidate import CzsIcd9Policy, NewWorkbook, propose_icd9_release
from minos.ingest.icd10_candidate import (
    CandidateRejected,
    RskIcd10Policy,
    propose_icd10_release,
)
from minos.ingest.reference_manifest import DatasetManifest
from minos.ingest.watchers.nfz.public_models import Companion, Czs, NfzProjection
from minos.ingest.watchers.rsk.public_models import (
    MAX_PUBLIC_JSON_BYTES,
    RskProvenanceProjection,
)
from minos.store.artifact import ARTIFACT_MANIFEST_NAME, ArtifactManifest

ICD10_POLICY = "rsk-icd10-automatic.json"
ICD9_POLICY = "czs-icd9-automatic.json"
MAX_BLOB_BYTES = 64 * 1024 * 1024
MAX_BASIC_INFO_BYTES = 1024 * 1024
MAX_STATE_BYTES = 64 * 1024
MAX_POLICY_BYTES = 64 * 1024
# Two terminologies, each with at most one new input set at a time.
MAX_VERDICTS = 8
_GENERATION = re.compile(r"generations/[1-9][0-9]*-([0-9a-f]{64})")

type Failure = Literal["projection", "live_bundle", "policy", "fetch", "build", "state"]


class ProcessorError(Exception):
    """A processor step failed; nothing is stored and the next run retries."""

    def __init__(self, failure: Failure) -> None:
        super().__init__(failure)
        self.failure: Failure = failure


class BlobReader(Protocol):
    def pin(self, sha256: str, *, max_bytes: int) -> ArchiveObject: ...

    def download(self, objects: list[ArchiveObject], archive: Path) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProcessorPaths:
    public_directory: Path
    live_bundle: Path
    policy_directory: Path
    work: Path
    state: Path


@dataclass(frozen=True, slots=True)
class _Input:
    system: Literal["icd10-pl", "icd9-pl"]
    # Archived digest to its HEAD byte bound; the candidate needs all of them.
    blobs: tuple[tuple[str, int], ...]
    fetched_at: datetime
    workbook: NewWorkbook | None = None

    def key(self, live_database: str, policy: str) -> str:
        identity = {
            "system": self.system,
            "blobs": [digest for digest, _ in self.blobs],
            "fetched_at": self.fetched_at.isoformat(),
            "live_database_sha256": live_database,
            "policy_sha256": policy,
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


class Verdict(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    system: Literal["icd10-pl", "icd9-pl"]
    report: CandidateReport


class _State(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    verdicts: Annotated[dict[str, Verdict], Field(max_length=MAX_VERDICTS)] = {}


def run_processor(
    paths: ProcessorPaths, reader_factory: Callable[[], BlobReader]
) -> tuple[Verdict, ...]:
    """Return the verdict for each new input, judging only unseen ones."""
    live = _read_live(paths.live_bundle)
    inputs = (
        *_icd10_inputs(paths.public_directory, live.dataset),
        *_icd9_inputs(paths.public_directory, live.dataset),
    )
    state = _read_state(paths.state)
    current: dict[str, Verdict] = {}
    for item in inputs:
        policy_path, policy = _policy(paths.policy_directory, item.system)
        key = item.key(live.database_sha256, _sha256_file(policy_path))
        verdict = state.verdicts.get(key)
        if verdict is None:
            verdict = Verdict(
                system=item.system,
                report=_judge(item, live.dataset, paths, policy, reader_factory),
            )
        current[key] = verdict
    # Only verdicts for the current inputs are kept, so the file stays bounded.
    if current != state.verdicts:
        _write_state(paths.state, _State(verdicts=current))
    return tuple(current.values())


def _judge(
    item: _Input,
    live: DatasetManifest,
    paths: ProcessorPaths,
    policy: RskIcd10Policy | CzsIcd9Policy,
    reader_factory: Callable[[], BlobReader],
) -> CandidateReport:
    _clear(paths.work)
    try:
        archive = paths.work / "archive"
        _fetch(item, live, archive, reader_factory)
        work = paths.work / "build"
        work.mkdir(mode=0o700)
        try:
            candidate = _propose(item, live, archive, policy)
            return evaluate_candidate(
                candidate,
                terminology=(
                    item.system,
                    "rsk" if item.system == "icd10-pl" else "czs",
                ),
                live_bundle=paths.live_bundle,
                archive_root=archive,
                work=work,
                policy=policy,
            )
        except CandidateRejected as error:
            return rejected(error)
        except ReferenceError, ValidationError, OSError:
            raise ProcessorError("build") from None
    finally:
        _clear(paths.work)


def _propose(
    item: _Input,
    live: DatasetManifest,
    archive: Path,
    policy: RskIcd10Policy | CzsIcd9Policy,
) -> DatasetManifest:
    if isinstance(policy, RskIcd10Policy):
        (product, _), (basic_info, _) = item.blobs
        return propose_icd10_release(
            live,
            archive,
            product_sha256=product,
            basic_info_sha256=basic_info,
            fetched_at=item.fetched_at,
            policy=policy,
        )
    return propose_icd9_release(
        live,
        archive,
        czs_sha256=item.blobs[0][0],
        fetched_at=item.fetched_at,
        policy=policy,
        workbook=item.workbook,
    )


def _fetch(
    item: _Input,
    live: DatasetManifest,
    archive: Path,
    reader_factory: Callable[[], BlobReader],
) -> None:
    pinned = {blob.sha256: blob.size_bytes for blob in source_files(live)}
    try:
        reader = reader_factory()
        try:
            new = [
                reader.pin(digest, max_bytes=bound)
                for digest, bound in item.blobs
                if digest not in pinned
            ]
            objects = [
                ArchiveObject(sha256=digest, size_bytes=size)
                for digest, size in pinned.items()
            ]
            reader.download([*objects, *new], archive)
        finally:
            reader.close()
    except ReferenceError, ValidationError, OSError:
        raise ProcessorError("fetch") from None


def _icd10_inputs(public: Path, live: DatasetManifest) -> tuple[_Input, ...]:
    projection = _read_projection(
        public, "api/v1/provenance/rsk.json", RskProvenanceProjection
    )
    if projection is None:
        return ()
    current = [
        entry
        for entry in projection.entries
        if entry.hcd == "00CD10"
        and entry.status == "current"
        and entry.product_sha256 is not None
    ]
    if len(current) > 1:
        raise ProcessorError("projection")
    if not current or _live_has(live, current[0].product_sha256):
        return ()
    entry = current[0]
    product = entry.product_sha256
    if product is None:
        raise ProcessorError("projection")
    return (
        _Input(
            system="icd10-pl",
            blobs=(
                (product, MAX_BLOB_BYTES),
                (entry.metadata_observation_sha256, MAX_BASIC_INFO_BYTES),
            ),
            fetched_at=datetime.fromisoformat(entry.first_observed_at),
        ),
    )


def _icd9_inputs(public: Path, live: DatasetManifest) -> tuple[_Input, ...]:
    projection = _read_projection(
        public / "nfz", "api/v1/provenance/nfz.json", NfzProjection
    )
    if projection is None:
        return ()
    czs = [
        entry
        for entry in projection.entries
        if isinstance(entry, Czs) and entry.code == "ICD9"
    ]
    if not czs or _live_has(live, czs[0].sha256):
        return ()
    companions = [entry for entry in projection.entries if isinstance(entry, Companion)]
    # The projection pairs a companion only with this CZS version; the live
    # workbook bytes need no new pin and are reused by the proposal.
    workbook = (
        NewWorkbook(companions[0].sha256, companions[0].source_url)
        if companions and not _live_has(live, companions[0].sha256)
        else None
    )
    blobs: tuple[tuple[str, int], ...] = ((czs[0].sha256, MAX_BLOB_BYTES),)
    if workbook is not None:
        blobs = (*blobs, (workbook.sha256, MAX_BLOB_BYTES))
    return (
        _Input(
            system="icd9-pl",
            blobs=blobs,
            fetched_at=datetime.fromisoformat(czs[0].observed_at),
            workbook=workbook,
        ),
    )


def _live_has(live: DatasetManifest, digest: str | None) -> bool:
    return any(source.sha256 == digest for source in live.sources)


def _read_projection[T: BaseModel](
    root: Path, relative: str, model: type[T]
) -> T | None:
    """Read the generation ``current`` names, verified against its digest.

    The watcher owns these files, so the publisher's owner checks do not apply
    here. Resolving ``current`` once keeps a concurrent switch from mixing two
    generations; superseded generations stay readable for a while.
    """
    try:
        target = os.readlink(root / "current")
    except FileNotFoundError:
        return None
    except OSError:
        raise ProcessorError("projection") from None
    match = _GENERATION.fullmatch(target)
    if match is None:
        raise ProcessorError("projection")
    try:
        content = _read_bounded(root / target / relative, MAX_PUBLIC_JSON_BYTES)
        if hashlib.sha256(content).hexdigest() != match.group(1):
            raise ProcessorError("projection")
        return model.model_validate_json(content)
    except ValidationError, OSError:
        raise ProcessorError("projection") from None


def _read_live(bundle: Path) -> ArtifactManifest:
    try:
        return ArtifactManifest.model_validate_json(
            _read_bounded(bundle / ARTIFACT_MANIFEST_NAME, MAX_PUBLIC_JSON_BYTES)
        )
    except ValidationError, OSError:
        raise ProcessorError("live_bundle") from None


def _policy(
    directory: Path, system: Literal["icd10-pl", "icd9-pl"]
) -> tuple[Path, RskIcd10Policy | CzsIcd9Policy]:
    try:
        if system == "icd10-pl":
            path = directory / ICD10_POLICY
            return path, RskIcd10Policy.load(path)
        path = directory / ICD9_POLICY
        return path, CzsIcd9Policy.load(path)
    except ValueError, OSError:
        raise ProcessorError("policy") from None


def _read_state(path: Path) -> _State:
    try:
        return _State.model_validate_json(_read_bounded(path, MAX_STATE_BYTES))
    except FileNotFoundError:
        return _State()
    except ValidationError, OSError:
        raise ProcessorError("state") from None


def _write_state(path: Path, state: _State) -> None:
    temporary = path.with_name(f".{path.name}.partial")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            _ = target.write(state.model_dump_json(exclude_none=True).encode())
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    except OSError:
        raise ProcessorError("state") from None


def _read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        raise OSError("file exceeds its byte bound")
    return content


def _sha256_file(path: Path) -> str:
    try:
        content = _read_bounded(path, MAX_POLICY_BYTES)
    except OSError:
        raise ProcessorError("policy") from None
    return hashlib.sha256(content).hexdigest()


def _clear(work: Path) -> None:
    """Empty the processor's own work directory, keeping the mount point."""
    try:
        for child in work.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    except OSError:
        raise ProcessorError("build") from None
