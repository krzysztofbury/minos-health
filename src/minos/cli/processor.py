"""Host processor adapter (#141 phase B3); it reports and never activates."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.domain.reference import ReferenceError
from minos.ingest.archive_build import R2BlobReader, create_r2_client
from minos.ingest.object_storage import validate_r2_endpoint
from minos.ingest.processor import (
    BlobReader,
    ProcessorError,
    ProcessorPaths,
    Verdict,
    manual_eli,
    manual_icd11,
    manual_jgp,
    run_processor,
)
from minos.ingest.receipts import ReceiptClient, append_receipt
from minos.ingest.watchers.uptime_kuma import (
    UptimeKumaClient,
    UptimeKumaError,
    UptimeKumaTargets,
)

KUMA_SOURCE = "data-update"
ACTIVATION_SOURCE = "data-activation"


class _Arguments(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", strict=True)
    public_directory: Path
    live_bundle: Path
    policy_directory: Path
    work: Path
    state: Path
    uptime_kuma_targets: Path
    archive_bucket: str
    archive_endpoint: str
    acceptance_directory: Path | None = None
    accepted: Path | None = None


def execute_processor(
    arguments: dict[str, object],
    *,
    reader_factory: Callable[[str, str], BlobReader] | None = None,
    kuma_factory: Callable[[UptimeKumaTargets], UptimeKumaClient] = UptimeKumaClient,
) -> int:
    """Print one privacy-safe JSON line; exit 1 when the run itself failed."""
    try:
        selected = _Arguments.model_validate(arguments)
        targets = UptimeKumaTargets.load(selected.uptime_kuma_targets)
        targets.require_sources((KUMA_SOURCE,))
    except ValidationError, UptimeKumaError:
        print(json.dumps({"version": 1, "failed": "config"}))
        return 1
    create = reader_factory or _r2_reader

    def reader() -> BlobReader:
        return create(selected.archive_bucket, selected.archive_endpoint)

    paths = ProcessorPaths(
        public_directory=selected.public_directory,
        live_bundle=selected.live_bundle,
        policy_directory=selected.policy_directory,
        work=selected.work,
        state=selected.state,
        acceptance_directory=selected.acceptance_directory,
        accepted=selected.accepted,
    )
    try:
        verdicts = run_processor(paths, reader)
        manual = manual_jgp(paths)
        icd11 = manual_icd11(paths)
        eli = manual_eli(paths)
    except ProcessorError as error:
        return _report(targets, kuma_factory, failure=error.failure)
    return _report(
        targets,
        kuma_factory,
        verdicts=verdicts,
        manual=manual,
        icd11=icd11,
        eli=eli,
    )


class Outcome(BaseModel):
    """What the host runner did with the accepted bundle; written by root."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    status: Literal["idle", "disabled", "activated", "failed"]
    stage: Annotated[str, Field(pattern=r"^[a-z_]{1,32}$")] | None = None
    release_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,95}$")] | None = (
        None
    )
    activation: dict[str, object] | None = None


class _FinalizeArguments(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", strict=True)
    outcome: Path
    uptime_kuma_targets: Path
    archive_bucket: str
    archive_endpoint: str


def execute_finalize(
    arguments: dict[str, object],
    *,
    client_factory: Callable[[str], ReceiptClient] | None = None,
    kuma_factory: Callable[[UptimeKumaTargets], UptimeKumaClient] = UptimeKumaClient,
) -> int:
    """Record an activation receipt in R2 and report the activation outcome."""
    try:
        selected = _FinalizeArguments.model_validate(arguments)
        targets = UptimeKumaTargets.load(selected.uptime_kuma_targets)
        with selected.outcome.open("rb") as source:
            outcome = Outcome.model_validate_json(source.read(64 * 1024))
    except ValidationError, UptimeKumaError, OSError:
        print(json.dumps({"version": 1, "failed": "config"}))
        return 1
    up, message, summary = _outcome_message(outcome)
    if outcome.status == "activated":
        try:
            if outcome.activation is None:
                raise ReferenceError("activation record is missing")
            client = (client_factory or _r2_client)(selected.archive_endpoint)
            summary["receipt"] = append_receipt(
                client, selected.archive_bucket, outcome.activation
            )
        except ReferenceError:
            # The new data is live; only its R2 record is missing.
            up, message = False, "activation failed: receipt"
            summary["receipt"] = "failed"
    # Every outcome is pushed to the data-activation monitor; a failed push is
    # recorded in the summary and fails the run.
    try:
        with kuma_factory(targets) as kuma:
            kuma.report(ACTIVATION_SOURCE, up=up, message=message)
    except UptimeKumaError:
        summary["report"] = "failed"
    print(json.dumps(summary, sort_keys=True))
    return 0 if up and "report" not in summary else 1


def _outcome_message(outcome: Outcome) -> tuple[bool, str, dict[str, object]]:
    summary: dict[str, object] = {"version": 1, "status": outcome.status}
    if outcome.status == "failed":
        return False, f"activation failed: {outcome.stage or 'unknown'}", summary
    if outcome.status == "idle" or outcome.release_id is None:
        return True, "idle", summary
    summary["release_id"] = outcome.release_id
    return True, f"{outcome.status} {outcome.release_id}", summary


def _r2_client(endpoint: str) -> ReceiptClient:
    client = cast(object, create_r2_client(validate_r2_endpoint(endpoint)))
    return cast(ReceiptClient, client)


def _r2_reader(bucket: str, endpoint: str) -> BlobReader:
    return R2BlobReader.create(bucket=bucket, endpoint=endpoint)


def _report(
    targets: UptimeKumaTargets,
    kuma_factory: Callable[[UptimeKumaTargets], UptimeKumaClient],
    *,
    failure: str | None = None,
    verdicts: tuple[Verdict, ...] = (),
    manual: tuple[int, ...] = (),
    icd11: str | None = None,
    eli: tuple[str, ...] = (),
) -> int:
    up, message = _message(failure, verdicts, manual, icd11, eli)
    summary: dict[str, object] = {"version": 1}
    if failure is None:
        summary["verdicts"] = [
            {"system": item.system}
            | item.report.model_dump(
                include={"decision", "release_id", "criterion", "code"},
                exclude_none=True,
            )
            for item in verdicts
        ]
        if manual:
            summary["manual_jgp"] = list(manual)
        if icd11 is not None:
            summary["manual_icd11"] = icd11
        if eli:
            summary["manual_eli"] = list(eli)
    else:
        summary["failed"] = failure
    try:
        with kuma_factory(targets) as client:
            client.report(KUMA_SOURCE, up=up, message=message)
    except UptimeKumaError:
        summary["report"] = "failed"
        print(json.dumps(summary, sort_keys=True))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0 if failure is None else 1


def _message(
    failure: str | None,
    verdicts: tuple[Verdict, ...],
    manual: tuple[int, ...],
    icd11: str | None,
    eli: tuple[str, ...] = (),
) -> tuple[bool, str]:
    if failure is not None:
        return False, f"failed: {failure}"
    # A republication without content change is nothing to do; reporting it
    # down would hold the monitor red and hide the next real alert.
    reported = [
        item for item in verdicts if item.report.code != "release_content_unchanged"
    ]
    if not reported and not manual and icd11 is None and not eli:
        return True, "idle"
    parts = [
        f"would_activate {item.report.release_id}"
        if item.report.decision == "would_activate"
        else f"rejected {item.system} {item.report.criterion}/{item.report.code}"
        for item in reported
    ]
    # A JGP workbook waiting for review holds the monitor down, so the owner
    # is told once; a reviewed release that serves it brings the monitor up.
    parts += [f"manual jgp {document}" for document in manual]
    if icd11 is not None:
        parts.append(f"manual icd11 {icd11}")
    # A Dziennik Ustaw amendment of a grouping dictionary waits for review.
    parts += [f"manual eli {name}" for name in eli]
    up = (
        not manual
        and icd11 is None
        and not eli
        and all(item.report.decision == "would_activate" for item in reported)
    )
    return up, "; ".join(parts)
