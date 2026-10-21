"""Host side of the data-update processor (#141 phase B3).

Run as root by ``minos-processor.timer``. It reads the live Compose model for
the reference API bundle and image and the archive bucket, then runs
one hardened processor container outside the ``minos`` Compose project, so the
watcher and validation transitions never see it. The run holds the shared
validation deployment lock: a deploy started meanwhile fails fast, and a busy
lock skips this run. Standard library only; compatible with the host's Python
3.12. Docker stderr is never printed because it may contain paths or values.
"""

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, cast

ROOT = Path("/opt/minos")
PROCESSOR = Path("/var/lib/minos/processor")
PUBLIC = Path("/var/lib/minos/public")
SECRETS = Path("/etc/minos/processor")
# The reference API container already runs as 10004.
UID = GID = 10005
CONTAINER = "minos-processor"
EGRESS_NETWORK = "minos-processor-egress"
MONITORING_NETWORK = "minos_monitoring"
API_SERVICES = ("reference-api", "reference-api-blue", "reference-api-green")
API_IMAGE = re.compile(
    r"ghcr\.io/krzysztofbury/minos-health-reference-api@sha256:[a-f0-9]{64}"
)
BUNDLE = re.compile(r"/opt/minos/reference/[a-f0-9]{64}")
MAX_COMPOSE_BYTES = 1024 * 1024
# Two candidate builds near the 1 GiB database bound plus the fetched inputs.
MIN_FREE_BYTES = 4 * 1024 * 1024 * 1024
DOCKER_TIMEOUT_S = 60
# Two builds at 0.5 CPU on the host; the lock blocks deploys for this long.
RUN_TIMEOUT_S = 75 * 60

type Runner = Callable[[list[str], float], int]


class HostError(Exception):
    pass


class Plan(NamedTuple):
    bundle: str
    image: str
    bucket: str
    endpoint: str


def read_plan(root: Path) -> Plan:
    """The live bundle, its API image and the archive bucket from the live model.

    The API image carries the reference extras the builder needs; the Alpine
    watcher image has neither the workbook reader nor the lemmatizer.
    """
    try:
        content = (root / "compose.yaml").read_bytes()
        if len(content) > MAX_COMPOSE_BYTES:
            raise HostError("compose model exceeds its bound")
        services = cast(dict[str, dict[str, object]], json.loads(content)["services"])
    except (OSError, ValueError, KeyError, TypeError):
        raise HostError("live compose model is unreadable") from None
    apis = [services[name] for name in API_SERVICES if name in services]
    if len(apis) != 1:
        raise HostError("live compose model has no single reference API")
    binds = [
        cast(dict[str, object], volume)
        for volume in cast(list[object], apis[0].get("volumes", []))
        if isinstance(volume, dict)
        and cast(dict[str, object], volume).get("target") == "/app/data"
    ]
    watcher = services.get("watcher", {})
    environment = cast(dict[str, object], watcher.get("environment", {}))
    plan = Plan(
        bundle=str(binds[0].get("source")) if len(binds) == 1 else "",
        image=str(apis[0].get("image", "")),
        bucket=str(environment.get("MINOS_WATCHER_S3_BUCKET", "")),
        endpoint=str(environment.get("MINOS_WATCHER_S3_ENDPOINT", "")),
    )
    if BUNDLE.fullmatch(plan.bundle) is None:
        raise HostError("live reference bundle is not a reviewed path")
    if API_IMAGE.fullmatch(plan.image) is None:
        raise HostError("live reference API image is not pinned")
    if not plan.bucket or not plan.endpoint.startswith("https://"):
        raise HostError("live archive bucket is not configured")
    return plan


def container_arguments(plan: Plan) -> list[str]:
    """Mirror the watcher's hardening; the archive key and Kuma token are files."""
    return [
        "docker",
        "create",
        "--name",
        CONTAINER,
        "--label",
        "minos.processor=1",
        "--user",
        f"{UID}:{GID}",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--init",
        "--cpus",
        "0.5",
        "--memory",
        "384m",
        "--memory-swap",
        "384m",
        "--pids-limit",
        "64",
        "--network",
        EGRESS_NETWORK,
        "--log-driver",
        "local",
        "--log-opt",
        "max-size=10m",
        "--log-opt",
        "max-file=3",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size=16m,uid={UID},gid={GID},mode=0700",
        # Builds spill SQLite and Python temporary files beyond the 16 MiB /tmp.
        "--env",
        "TMPDIR=/var/lib/minos/work",
        "--env",
        "AWS_SHARED_CREDENTIALS_FILE=/run/secrets/r2-credentials",
        "--env",
        "AWS_CONFIG_FILE=/nonexistent",
        "--volume",
        f"{PUBLIC}:/var/lib/minos/public:ro",
        "--volume",
        f"{plan.bundle}:/var/lib/minos/live:ro",
        "--volume",
        f"{PROCESSOR / 'work'}:/var/lib/minos/work:rw",
        "--volume",
        f"{PROCESSOR / 'state'}:/var/lib/minos/state:rw",
        "--volume",
        f"{SECRETS / 'r2/credentials'}:/run/secrets/r2-credentials:ro",
        "--volume",
        f"{SECRETS / 'uptime-kuma/targets.json'}:/run/secrets/uptime-kuma-targets.json:ro",
        "--entrypoint",
        "/build/.venv/bin/minos",
        plan.image,
        "processor",
        "run",
        "--public-directory",
        "/var/lib/minos/public",
        "--live-bundle",
        "/var/lib/minos/live",
        "--policy-directory",
        "/app/policy",
        "--work",
        "/var/lib/minos/work",
        "--state",
        "/var/lib/minos/state/verdicts.json",
        "--uptime-kuma-targets",
        "/run/secrets/uptime-kuma-targets.json",
        "--archive-bucket",
        plan.bucket,
        "--archive-endpoint",
        plan.endpoint,
    ]


def run_once(root: Path, work: Path, runner: Runner) -> str:
    """Return the run outcome; the processor's own line goes to the journal."""
    if shutil.disk_usage(work).free < MIN_FREE_BYTES:
        raise HostError("processor work disk is below its free-space bound")
    descriptor = os.open(
        root / ".validation-deploy.lock", os.O_RDWR | os.O_CREAT, 0o600
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "skipped: deployment in progress"
        # Read under the lock, so no transition switches the colour between
        # choosing the bundle and running against it.
        return _run_container(read_plan(root), work, runner)
    finally:
        os.close(descriptor)


def _run_container(plan: Plan, work: Path, runner: Runner) -> str:
    _ = runner(["docker", "rm", "--force", CONTAINER], DOCKER_TIMEOUT_S)
    _clear(work)
    failed = False
    try:
        if runner(["docker", "network", "inspect", EGRESS_NETWORK], DOCKER_TIMEOUT_S):
            _require(
                runner(
                    [
                        "docker",
                        "network",
                        "create",
                        "--label",
                        "minos.processor=1",
                        EGRESS_NETWORK,
                    ],
                    DOCKER_TIMEOUT_S,
                ),
                "processor egress network could not be created",
            )
        _require(
            runner(container_arguments(plan), DOCKER_TIMEOUT_S),
            "processor create failed",
        )
        _require(
            runner(
                ["docker", "network", "connect", MONITORING_NETWORK, CONTAINER],
                DOCKER_TIMEOUT_S,
            ),
            "processor could not join the monitoring network",
        )
        code = runner(["docker", "start", "--attach", CONTAINER], RUN_TIMEOUT_S)
        failed = code != 0
        return "completed" if code == 0 else f"processor exited {code}"
    finally:
        # A failed container stays for `docker logs minos-processor` until the
        # next run removes it; its work files never do.
        if not failed:
            _ = runner(["docker", "rm", "--force", CONTAINER], DOCKER_TIMEOUT_S)
        _clear(work)


def _require(code: int, message: str) -> None:
    if code != 0:
        raise HostError(message)


def _clear(work: Path) -> None:
    for child in work.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def docker(arguments: list[str], timeout_s: float) -> int:
    """Pass container stdout to the journal; a timeout kills the processor."""
    try:
        completed = subprocess.run(
            arguments,
            stdout=None if arguments[1] == "start" else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _ = subprocess.run(
            ["docker", "kill", CONTAINER],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=DOCKER_TIMEOUT_S,
            check=False,
        )
        return 124
    return completed.returncode


def main() -> int:
    try:
        outcome = run_once(ROOT, PROCESSOR / "work", docker)
    except (HostError, OSError) as error:
        message = (
            str(error) if isinstance(error, HostError) else "host file access failed"
        )
        print(json.dumps({"processor_host": "failed", "reason": message}), flush=True)
        return 1
    print(json.dumps({"processor_host": outcome}), flush=True)
    return 0 if outcome in ("completed", "skipped: deployment in progress") else 1


if __name__ == "__main__":
    sys.exit(main())
