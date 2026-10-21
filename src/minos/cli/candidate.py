"""Shadow candidate adapter, outside the offline entry point (#141 B)."""

from pathlib import Path
from typing import ClassVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from minos.cli.reference import reference_data_error
from minos.domain.reference import ReferenceError
from minos.ingest.candidate_evaluation import (
    CandidateReport,
    RegressionBounds,
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
from minos.store.artifact import ARTIFACT_MANIFEST_NAME, ArtifactManifest

REJECTED_EXIT_CODE = 9


class _CommonArguments(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", strict=True)
    data_command: str
    live_bundle: Path
    archive_root: Path
    fetched_at: str
    policy: Path
    work: Path


class _Icd10Arguments(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", strict=True)
    product_sha256: str
    basic_info_sha256: str


class _Icd9Arguments(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", strict=True)
    czs_sha256: str
    workbook_sha256: str | None = None
    workbook_url: str | None = None


class _FetchedAt(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    value: AwareDatetime


def execute_candidate(arguments: dict[str, object]) -> int:
    """Print one privacy-safe JSON report; exit 9 when the candidate is refused."""
    try:
        common = _CommonArguments.model_validate(arguments)
        fetched_at = _FetchedAt.model_validate({"value": common.fetched_at}).value
        live = ArtifactManifest.model_validate_json(
            (common.live_bundle / ARTIFACT_MANIFEST_NAME).read_bytes()
        )
        policy: RegressionBounds
        if common.data_command == "candidate-icd9":
            icd9 = _Icd9Arguments.model_validate(arguments)
            if (icd9.workbook_sha256 is None) != (icd9.workbook_url is None):
                raise ReferenceError("a new workbook needs its digest and URL")
            icd9_policy = CzsIcd9Policy.load(common.policy)
            workbook = (
                None
                if icd9.workbook_sha256 is None or icd9.workbook_url is None
                else NewWorkbook(icd9.workbook_sha256, icd9.workbook_url)
            )

            def propose() -> DatasetManifest:
                return propose_icd9_release(
                    live.dataset,
                    common.archive_root,
                    czs_sha256=icd9.czs_sha256,
                    fetched_at=fetched_at,
                    policy=icd9_policy,
                    workbook=workbook,
                )

            policy, terminology = icd9_policy, ("icd9-pl", "czs")
        else:
            icd10 = _Icd10Arguments.model_validate(arguments)
            icd10_policy = RskIcd10Policy.load(common.policy)

            def propose() -> DatasetManifest:
                return propose_icd10_release(
                    live.dataset,
                    common.archive_root,
                    product_sha256=icd10.product_sha256,
                    basic_info_sha256=icd10.basic_info_sha256,
                    fetched_at=fetched_at,
                    policy=icd10_policy,
                )

            policy, terminology = icd10_policy, ("icd10-pl", "rsk")
        if common.work.exists() or common.work.is_symlink():
            raise ReferenceError("candidate work directory already exists")
        common.work.mkdir(mode=0o700)
        try:
            report = evaluate_candidate(
                propose(),
                terminology=terminology,
                live_bundle=common.live_bundle,
                archive_root=common.archive_root,
                work=common.work,
                policy=policy,
            )
        except CandidateRejected as error:
            report = rejected(error)
    except ReferenceError, ValidationError, OSError:
        return reference_data_error(output_json=True)
    print(report.model_dump_json(exclude_none=True))
    return 0 if _accepted(report) else REJECTED_EXIT_CODE


def _accepted(report: CandidateReport) -> bool:
    return report.decision == "would_activate"
