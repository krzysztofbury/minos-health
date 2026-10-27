"""Exercise watcher-only preflight, partial activation and rollback with Docker."""

import argparse
import base64
import copy
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import cast

from minos.ingest.watchers.config import WATCHER_SOURCE_CONFIGURATION_CODECS
from minos.ingest.watchers.control import (
    BlobReceipt,
    ControlStore,
    ObservationCommit,
    ObservationDraft,
    RecoveryReceipt,
    SourceConfiguration,
    SourceSchedule,
)
from scripts import deploy_watcher, validation_host, watcher_host


def terminate(_number: int, _frame: FrameType | None) -> None:
    raise SystemExit("watcher deployment smoke interrupted")


def main(output: Path, image: str) -> None:
    output.mkdir(parents=True, exist_ok=False)
    root = output.resolve()
    for child in ("control", "archive", "staging", "public"):
        (root / child).mkdir(mode=0o700)
    # Future-only scheduling supplies durable evidence without upstream work.
    with ControlStore.open(
        root / "control/control.sqlite3",
        source_configuration_codecs=WATCHER_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        _ = store.configure_source(SourceConfiguration("nfz", "nfz", {}, True))
        claim = store.claim_manual("nfz")
        blob = BlobReceipt(
            "a" * 64, 2, "application/json", "blobs/sha256/" + "a" * 64, "a" * 64, 2
        )
        observation = ObservationDraft(
            "synthetic-observation",
            "nfz_listing",
            "https://www.nfz.gov.pl/",
            "a" * 64,
            int(time.time() * 1000),
            {},
        )
        run = store.commit_observations(
            claim.identity, ObservationCommit((blob,), (observation,), {})
        )
        digest = "b" * 64
        receipt = RecoveryReceipt(
            run.run_id,
            run.committed_state_revision,
            run.committed_state_revision,
            digest,
            4096,
            f"control-recovery/run/{run.committed_state_revision:020d}/{digest}.sqlite3",
            digest,
            4096,
        )
        _ = store.mark_heartbeat_pending(
            run.run_id,
            committed_state_revision=run.committed_state_revision,
            recovery_receipt=receipt,
        )
        _ = store.mark_successful(
            run.run_id, committed_state_revision=run.committed_state_revision
        )
        future = datetime.now(UTC) + timedelta(days=2)
        _ = store.set_schedule(
            SourceSchedule(
                "nfz", f"0 0 {future.day} {future.month} *", 3600, 3, 1.0, 30.0
            )
        )
    uid, gid = os.getuid(), os.getgid()
    common: dict[str, object] = {
        "image": image,
        "init": True,
        "user": f"{uid}:{gid}",
        "read_only": True,
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": "512m",
        "pids_limit": 64,
    }
    watcher = {
        **common,
        "command": ["watcher", "run"],
        "environment": {
            "MINOS_WATCHER_SCHEDULER_PAUSED": "false",
            "MINOS_WATCHER_DATABASE": "/var/lib/minos/control/control.sqlite3",
            "MINOS_WATCHER_PROCESS_LOCK": "/var/lib/minos/control/watcher.lock",
            "MINOS_WATCHER_STAGING_DIRECTORY": "/var/lib/minos/staging",
            "MINOS_WATCHER_ARCHIVE_DIRECTORY": "/var/lib/minos/archive",
            "MINOS_WATCHER_PUBLIC_DIRECTORY": "/var/lib/minos/public",
        },
        "volumes": [
            {
                "type": "bind",
                "source": str(root / name),
                "target": "/var/lib/minos/" + name,
            }
            for name in ("control", "archive", "staging", "public")
        ],
        "tmpfs": [
            f"/tmp:rw,noexec,nosuid,nodev,size=16m,uid={uid},gid={gid},mode=0700"
        ],
        "healthcheck": {
            "test": ["CMD", "/app/.venv/bin/minos", "watcher", "health"],
            "interval": "2s",
            "timeout": "10s",
            "retries": 3,
        },
        "stop_grace_period": "30s",
    }
    sentinel = {
        **common,
        "entrypoint": [watcher_host.PYTHON, "-c", "import time; time.sleep(900)"],
        "healthcheck": {
            "test": ["CMD", watcher_host.PYTHON, "-c", "import os; os.kill(1, 0)"],
            "interval": "2s",
            "timeout": "5s",
            "retries": 3,
        },
    }
    previous: dict[str, object] = {
        "services": {
            "watcher": watcher,
            # A colour API proves the preserved set accepts blue/green (#192).
            **{
                name: sentinel
                for name in (*sorted(watcher_host.PRESERVED), "reference-api-blue")
            },
        },
        "networks": {},
    }
    layout = validation_host.Layout(
        root, "minos-watcher-smoke-" + uuid.uuid4().hex[:10]
    )
    validation_host.write_private(layout.compose, validation_host.canonical(previous))
    prefix = [
        "docker",
        "compose",
        "--project-name",
        layout.project,
        "--file",
        str(layout.compose),
    ]
    revision = "2" * 40
    params: dict[str, object] = {
        "revision": revision,
        "root": str(root),
        "project": layout.project,
    }
    directory = watcher_host.directory(layout, params)
    directory.mkdir(parents=True)
    encoded = base64.urlsafe_b64encode(json.dumps(params).encode()).decode()

    def call(command: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-", command, encoded],
            input=deploy_watcher.program(),
            capture_output=True,
            timeout=360,
            check=False,
        )

    try:
        boot = subprocess.run(
            [*prefix, "up", "--detach", "--wait", "--wait-timeout", "60"],
            check=False,
            timeout=90,
            capture_output=True,
        )
        if boot.returncode:
            raise RuntimeError("synthetic watcher did not become healthy")
        baseline = watcher_host.baseline(layout)
        started = time.monotonic()
        watcher_host.isolated_preflight(layout, previous, directory)
        validation_host.write_once(
            directory / "previous.json", layout.compose.read_bytes()
        )
        validation_host.write_once(
            directory / "baseline.json", validation_host.canonical(baseline)
        )
        validation_host.write_once(
            directory / "prepared.json", validation_host.canonical(params)
        )
        candidate = copy.deepcopy(previous)
        selected = watcher_host.service(candidate)
        # Real process failure occurs after Compose removes the healthy watcher.
        selected["entrypoint"] = [watcher_host.PYTHON, "-c", "raise SystemExit(7)"]
        validation_host.write_private(
            directory / "candidate.json", validation_host.canonical(candidate)
        )
        failed = call("activate")
        answer = cast(dict[str, object], json.loads(failed.stdout))
        assert failed.returncode == 2 and answer["ok"] is False, answer
        assert cast(dict[str, object], answer["details"])["recovery"] == {
            "preserved": True,
            "healthy": True,
            "watcher_image_matches": True,
        }, answer
        assert watcher_host.baseline(layout) == baseline
        # A valid update then exercises success and explicit rollback boundaries.
        _ = selected.pop("entrypoint")
        selected["labels"] = {"minos.smoke.revision": revision}
        validation_host.write_private(
            directory / "candidate.json", validation_host.canonical(candidate)
        )
        activated = call("activate")
        assert activated.returncode == 0, activated.stdout
        rolled_back = call("rollback")
        assert rolled_back.returncode == 0, rolled_back.stdout
        assert layout.compose.read_bytes() == (directory / "previous.json").read_bytes()
        assert watcher_host.baseline(layout) == baseline
        projection = cast(
            dict[str, object],
            json.loads(
                (root / "public/nfz/current/api/v1/provenance/nfz.json").read_bytes()
            ),
        )
        assert projection["entries"] == [] and projection["source"] == "NFZ"
        print(
            json.dumps(
                {
                    "isolated_preflight": "passed",
                    "partial_activation_recovery": "passed",
                    "activation_and_rollback": "passed",
                    "preserved_healthy_sentinels": 4,
                    "installed_nfz_projection": "passed",
                    "seconds": round(time.monotonic() - started, 2),
                }
            )
        )
    finally:
        _ = subprocess.run(
            [*prefix, "down", "--volumes", "--remove-orphans"],
            check=True,
            timeout=90,
            capture_output=True,
        )


def cli() -> None:
    _ = signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("output", type=Path)
    _ = parser.add_argument("--image", default="minos-health-watcher:local")
    arguments = parser.parse_args()
    main(cast(Path, arguments.output), cast(str, arguments.image))


if __name__ == "__main__":
    cli()
