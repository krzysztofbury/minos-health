"""Shadow ICD-10 candidate adapter, outside the offline entry point (#141 B)."""

from pathlib import Path
from typing import ClassVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from minos.cli.reference import reference_data_error
from minos.domain.reference import ReferenceError
from minos.ingest.candidate_evaluation import (
    CandidateReport,
    evaluate_icd10_candidate,
    rejected,
)
from minos.ingest.icd10_candidate import (
    CandidateRejected,
    RskIcd10Policy,
    propose_icd10_release,
)
from minos.store.artifact import ARTIFACT_MANIFEST_NAME, ArtifactManifest

REJECTED_EXIT_CODE = 9


class _CandidateArguments(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    live_bundle: Path
    archive_root: Path
    product_sha256: str
    basic_info_sha256: str
    fetched_at: str
    policy: Path
    work: Path


class _FetchedAt(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    value: AwareDatetime


def execute_icd10_candidate(arguments: dict[str, object]) -> int:
    """Print one privacy-safe JSON report; exit 9 when the candidate is refused."""
    try:
        selected = _CandidateArguments.model_validate(
            {
                name: arguments[name]
                for name in _CandidateArguments.model_fields
                if name in arguments
            }
        )
        fetched_at = _FetchedAt.model_validate({"value": selected.fetched_at}).value
        policy = RskIcd10Policy.load(selected.policy)
        live = ArtifactManifest.model_validate_json(
            (selected.live_bundle / ARTIFACT_MANIFEST_NAME).read_bytes()
        )
        if selected.work.exists() or selected.work.is_symlink():
            raise ReferenceError("candidate work directory already exists")
        selected.work.mkdir(mode=0o700)
        try:
            candidate = propose_icd10_release(
                live.dataset,
                selected.archive_root,
                product_sha256=selected.product_sha256,
                basic_info_sha256=selected.basic_info_sha256,
                fetched_at=fetched_at,
                policy=policy,
            )
            report = evaluate_icd10_candidate(
                candidate,
                live_bundle=selected.live_bundle,
                archive_root=selected.archive_root,
                work=selected.work,
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
