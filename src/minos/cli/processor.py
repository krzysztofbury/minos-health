"""Host processor adapter (#141 phase B3); it reports and never activates."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from minos.ingest.archive_build import R2BlobReader
from minos.ingest.processor import (
    BlobReader,
    ProcessorError,
    ProcessorPaths,
    Verdict,
    run_processor,
)
from minos.ingest.watchers.uptime_kuma import (
    UptimeKumaClient,
    UptimeKumaError,
    UptimeKumaTargets,
)

KUMA_SOURCE = "data-update"


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
    )
    try:
        verdicts = run_processor(paths, reader)
    except ProcessorError as error:
        return _report(targets, kuma_factory, failure=error.failure)
    return _report(targets, kuma_factory, verdicts=verdicts)


def _r2_reader(bucket: str, endpoint: str) -> BlobReader:
    return R2BlobReader.create(bucket=bucket, endpoint=endpoint)


def _report(
    targets: UptimeKumaTargets,
    kuma_factory: Callable[[UptimeKumaTargets], UptimeKumaClient],
    *,
    failure: str | None = None,
    verdicts: tuple[Verdict, ...] = (),
) -> int:
    up, message = _message(failure, verdicts)
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


def _message(failure: str | None, verdicts: tuple[Verdict, ...]) -> tuple[bool, str]:
    if failure is not None:
        return False, f"failed: {failure}"
    if not verdicts:
        return True, "idle"
    parts = [
        f"would_activate {item.report.release_id}"
        if item.report.decision == "would_activate"
        else f"rejected {item.system} {item.report.criterion}/{item.report.code}"
        for item in verdicts
    ]
    up = all(item.report.decision == "would_activate" for item in verdicts)
    return up, "; ".join(parts)
