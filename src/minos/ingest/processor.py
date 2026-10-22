"""Judge newly archived source files against the live bundle (#141 phase B3).

The processor reads only the watcher's public projections, never its control
database. It fetches every input from the archive with its own key,
runs the shadow candidates and activates nothing. A verdict is stored once per
input, live bundle and policy, so an unchanged rejection is not rebuilt; every
run reports the current verdicts to the data-update Kuma monitor. JGP catalogs
are not built here: a BAW workbook the live bundle lacks is reported as a
manual step until a reviewed release serves it.
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
from typing import Annotated, ClassVar, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.domain.jgp_ruleset import polish_date
from minos.domain.reference import ReferenceError
from minos.http.source_models import BawProjection
from minos.ingest.acceptance import AcceptanceUnavailable, accept_candidate
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
from minos.store.artifact import (
    ARTIFACT_MANIFEST_NAME,
    ArtifactManifest,
    dataset_identity,
)

ICD10_POLICY = "rsk-icd10-automatic.json"
ICD9_POLICY = "czs-icd9-automatic.json"
MAX_BLOB_BYTES = 64 * 1024 * 1024
MAX_BASIC_INFO_BYTES = 1024 * 1024
MAX_STATE_BYTES = 64 * 1024
MAX_POLICY_BYTES = 64 * 1024
# Two terminologies, each with at most one new input set at a time.
MAX_VERDICTS = 8
# Part of every verdict key: bump it when a judgement rule changes, so stored
# verdicts from older rules are judged again (2: unchanged RSK re-exports).
RULES_VERSION = 2
_GENERATION = re.compile(r"generations/[1-9][0-9]*-([0-9a-f]{64})")
# A JGP catalog is built from an original act (BAW AutomaticType 1) or a
# consolidated text (3), as CatalogManifest admits; types 4 and 5 repeat
# earlier versions' attachments and type 6 is the amending act itself.
_CATALOG_ACT_TYPES = frozenset((1, 3))
_IN_FORCE = "Obowiązujący"
MAX_MANUAL_JGP = 4
# ICD-11 PL files the bundle reads; the resource's PDFs are not used (#53).
_ICD11_FILES = re.compile(
    r"icd11_20[0-9]{2}-[0-9]{2}_pl_in|(10To11MapdoJednejKategorii|10To11MapdowieluKategorii|11To10MapdoJednejKategorii)_20[0-9]{2}-[0-9]{2}"
)
_RESOURCE_VERSION = re.compile(r"[0-9A-Za-z.-]{1,16}")

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
    # Reviewed acceptance cases (criterion 4) and the hand-over directory for
    # an accepted bundle; both are optional so the shadow mode stays possible.
    acceptance_directory: Path | None = None
    accepted: Path | None = None


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
            "rules": RULES_VERSION,
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
    _drop_stale_acceptance(paths, current)
    return tuple(current.values())


def manual_jgp(paths: ProcessorPaths) -> tuple[int, ...]:
    """BAW documents in force whose JGP workbooks the live bundle lacks.

    The answer is recomputed each run from the watcher projection and the live
    bundle, so it clears once a reviewed release serves those workbooks. The
    newest documents come first and the count is bounded for the Kuma message.
    """
    projection = _read_projection(
        paths.public_directory / "baw", "api/v1/provenance/baw.json", BawProjection
    )
    if projection is None:
        return ()
    dataset = _read_live(paths.live_bundle).dataset
    live = {item.workbook.sha256 for item in dataset.catalogs} | {
        item.workbook.sha256 for item in dataset.characteristics
    }
    pending: list[int] = []
    for document in projection.documents:
        # Without lineage the act type is unknown; guessing could hide a catalog.
        if document.lineage is None:
            raise ProcessorError("projection")
        if (
            document.legal_status != _IN_FORCE
            or document.lineage.automatic_type not in _CATALOG_ACT_TYPES
        ):
            continue
        # The watcher keeps only the act's ZIPX and its 1a and 9 workbooks, so
        # every other file is a workbook; display names vary and are not read.
        if any(
            not item.name.casefold().endswith(".zipx") and item.sha256 not in live
            for item in document.files
        ):
            pending.append(document.document_id)
    return tuple(sorted(pending, reverse=True)[:MAX_MANUAL_JGP])


def manual_icd11(paths: ProcessorPaths) -> str | None:
    """The RSK version of ICD-11 PL files no live release pins, if any.

    ICD-11 is built by hand, and the watcher keeps only its metadata, so the
    check compares RSK file identities and sizes with the live pins. It stays
    silent while the live bundle serves no ICD-11 at all.
    """
    projection = _read_projection(
        paths.public_directory, "api/v1/provenance/rsk.json", RskProvenanceProjection
    )
    if projection is None:
        return None
    dataset = _read_live(paths.live_bundle).dataset
    live = {
        (source.source_file_id, source.size_bytes)
        for source in dataset.sources
        if source.system == "icd11-pl"
    }
    if not live:
        return None
    if dataset.icd11_maps is not None:
        live |= {
            (item.source_url.rsplit("/", 1)[1], item.size_bytes)
            for item in dataset.icd11_maps.files
        }
    pending = [
        entry.resource_version
        for entry in projection.entries
        if entry.hcd == "99ICD1"
        and entry.status == "current"
        and entry.file_name is not None
        and _ICD11_FILES.fullmatch(entry.file_name)
        and (entry.file_uuid, entry.size_bytes) not in live
    ]
    if not pending:
        return None
    if not _RESOURCE_VERSION.fullmatch(pending[0]):
        raise ProcessorError("projection")
    return pending[0]


def _drop_stale_acceptance(paths: ProcessorPaths, current: dict[str, Verdict]) -> None:
    """An accepted bundle lives only while a current verdict still accepts it."""
    if paths.accepted is None:
        return
    accepted = {
        verdict.report.database_sha256
        for verdict in current.values()
        if verdict.report.decision == "would_activate"
    }
    try:
        request = cast(
            object, json.loads(_read_bounded(paths.accepted / "request.json", 4096))
        )
        database = (
            cast(dict[str, object], request).get("database_sha256")
            if isinstance(request, dict)
            else None
        )
    except FileNotFoundError:
        return
    except ValueError, OSError:
        database = None
    if database not in accepted:
        _clear(paths.accepted)


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
            report = evaluate_candidate(
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
            if report.decision == "would_activate":
                _accept(item.system, report, paths, work / "first")
            return report
        except CandidateRejected as error:
            return rejected(error)
        except ReferenceError, ValidationError, OSError, AcceptanceUnavailable:
            raise ProcessorError("build") from None
    finally:
        _clear(paths.work)


def _accept(
    system: str, report: CandidateReport, paths: ProcessorPaths, bundle: Path
) -> None:
    """Run criterion 4, then hand the first build to the host runner."""
    if paths.acceptance_directory is None or paths.accepted is None:
        return
    built = ArtifactManifest.model_validate_json(
        _read_bounded(bundle / ARTIFACT_MANIFEST_NAME, MAX_PUBLIC_JSON_BYTES)
    )
    [release] = [
        source
        for source in built.dataset.sources
        if source.release_id == report.release_id
    ]
    if release.valid_from is None or report.database_sha256 != built.database_sha256:
        raise CandidateRejected("build", "accepted_bundle_identity_differs")
    accept_candidate(
        paths.live_bundle,
        bundle,
        system=system,
        effective=polish_date(release.valid_from),
        cases=paths.acceptance_directory,
    )
    # One accepted bundle at a time; a newer verdict replaces the older one.
    _clear(paths.accepted)
    os.rename(bundle, paths.accepted / built.database_sha256)
    request = {
        "version": 1,
        "system": system,
        "release_id": report.release_id,
        "database_sha256": built.database_sha256,
        "dataset_id": dataset_identity(built.dataset),
    }
    _write_private(paths.accepted / "request.json", json.dumps(request).encode())


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
    try:
        _write_private(path, state.model_dump_json(exclude_none=True).encode())
    except OSError:
        raise ProcessorError("state") from None


def _write_private(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.partial")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        _ = target.write(content)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


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
