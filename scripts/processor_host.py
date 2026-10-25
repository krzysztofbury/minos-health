"""Host side of the data-update processor (#141 phases B3 and C).

Run as root by ``minos-processor.timer``. It reads the live Compose model for
the reference API bundle and image and the archive bucket, then runs one
hardened processor container outside the ``minos`` Compose project, so the
watcher and validation transitions never see it. The run holds the shared
validation deployment lock for its whole length: a deploy started meanwhile
fails fast, and a busy lock skips this run.

A bundle the processor accepted is activated here, data only, through the
same ``validation_host`` stage, activate and rollback used for reviewed
releases, only for source classes the owner allowlisted, with at most two
attempts per database. A second container records the R2 receipt and reports
the outcome. Standard library only; compatible with the host's Python 3.12.
Docker stderr is never printed because it may contain paths or values.
"""

import fcntl
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, Protocol, cast, final

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
RELEASE = re.compile(r"[a-z0-9][a-z0-9-]{0,95}")
DIGEST = re.compile(r"[a-f0-9]{64}")
MAX_COMPOSE_BYTES = 1024 * 1024
MAX_STATE_BYTES = 64 * 1024
# The processor directory as the container sees it.
MOUNT = "/var/lib/minos/processor"
# Two tries: every observed failed API start (#198) succeeded on retry.
MAX_ATTEMPTS = 2
PUBLIC_READY_URL = "https://minoshealth.pl/readyz"
READY_ATTEMPTS = 6
READY_DELAY_S = 10.0
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


def container_arguments(plan: Plan, command: str = "run") -> list[str]:
    """Mirror the watcher's hardening; the archive key and Kuma token are files.

    The whole processor directory is one mount, so an accepted build moves
    into ``accepted`` by rename; ``activation`` stays root-owned and read-only.
    """
    arguments = [
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
        # The API's own limit: acceptance loads the reference pool (#141 C).
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
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
        f"TMPDIR={MOUNT}/work",
        "--env",
        "AWS_SHARED_CREDENTIALS_FILE=/run/secrets/r2-credentials",
        "--env",
        "AWS_CONFIG_FILE=/nonexistent",
        "--volume",
        f"{PUBLIC}:/var/lib/minos/public:ro",
        "--volume",
        f"{plan.bundle}:/var/lib/minos/live:ro",
        "--volume",
        f"{PROCESSOR}:{MOUNT}:rw",
        "--volume",
        f"{SECRETS / 'r2/credentials'}:/run/secrets/r2-credentials:ro",
        "--volume",
        f"{SECRETS / 'uptime-kuma/targets.json'}:/run/secrets/uptime-kuma-targets.json:ro",
        "--entrypoint",
        "/build/.venv/bin/minos",
        plan.image,
        "processor",
        command,
    ]
    if command == "run":
        arguments += [
            "--public-directory",
            "/var/lib/minos/public",
            "--live-bundle",
            "/var/lib/minos/live",
            "--policy-directory",
            "/app/policy",
            "--acceptance-directory",
            "/app/acceptance",
            "--work",
            f"{MOUNT}/work",
            "--state",
            f"{MOUNT}/state/verdicts.json",
            "--accepted",
            f"{MOUNT}/accepted",
        ]
    else:
        arguments += ["--outcome", f"{MOUNT}/activation/outcome.json"]
    return arguments + [
        "--uptime-kuma-targets",
        "/run/secrets/uptime-kuma-targets.json",
        "--archive-bucket",
        plan.bucket,
        "--archive-endpoint",
        plan.endpoint,
    ]


class Activator(Protocol):
    """The data-only transition; ``validation_host`` in production."""

    def stage(self, bundle: Path, database: str, /) -> dict[str, object]: ...

    def activate(self, staged: dict[str, object], /) -> dict[str, object]: ...

    def rollback(self, stage_key: str, /) -> dict[str, object]: ...

    def finish(
        self, staged: dict[str, object], receipt: dict[str, object], /
    ) -> None: ...

    def public_dataset(self) -> str | None: ...


def run_once(root: Path, processor: Path, runner: Runner, activator: Activator) -> str:
    """Return the run outcome; the containers' own lines go to the journal."""
    if shutil.disk_usage(processor).free < MIN_FREE_BYTES:
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
        plan = read_plan(root)
        code = _run_container(plan, processor / "work", runner, "run")
        if code != 0:
            return f"processor exited {code}"
        # The processor container has exited, so two API colours fit in memory.
        outcome = activate_accepted(processor, activator, plan)
        _write_outcome(processor / "activation", outcome)
        code = _run_container(plan, processor / "work", runner, "finalize")
        status = cast(str, outcome["status"])
        pending = processor / "activation" / "pending.json"
        if status == "activated" and code != 0:
            # Criterion 7: keep it until finalize records the R2 receipt.
            _write_private(pending, json.dumps(outcome, sort_keys=True).encode())
        elif status == "activated":
            pending.unlink(missing_ok=True)
        return status if code == 0 or status == "failed" else f"finalize exited {code}"
    finally:
        os.close(descriptor)


def activate_accepted(
    processor: Path, activator: Activator, plan: Plan
) -> dict[str, object]:
    """Activate the accepted bundle, if allowed, within the attempt budget."""
    accepted = processor / "accepted"
    request = _read_request(accepted)
    if request is None:
        # An activation whose receipt was not recorded is reported again.
        return _read_json(processor / "activation" / "pending.json") or {
            "version": 1,
            "status": "idle",
        }
    system, release_id, database = (
        request["system"],
        request["release_id"],
        request["database_sha256"],
    )
    if system not in _allowed_classes():
        return {"version": 1, "status": "disabled", "release_id": release_id}
    attempts_path = processor / "activation" / "attempts.json"
    attempts = _read_json(attempts_path) or {}
    used = attempts.get(database, 0)
    failure = "retries_exhausted"
    while isinstance(used, int) and used < MAX_ATTEMPTS:
        used += 1
        # Counted before the attempt, so a crash mid-transition is not free.
        # Other databases keep their counts, or a second one resets the cap.
        attempts[database] = used
        _write_private(attempts_path, json.dumps(attempts, sort_keys=True).encode())
        started = time.monotonic()
        try:
            staged = activator.stage(accepted / database, database)
        except (HostError, OSError, ValueError, KeyError):
            failure = "stage"
            continue
        try:
            activated = activator.activate(staged)
        except (HostError, OSError, ValueError, KeyError) as error:
            if "rollback incomplete" in str(error):
                # The staged compose may still be live: explicit operator
                # recovery only, never another attempt.
                failure = "rollback"
                break
            # validation_host has already restored the previous release.
            failure = "activate"
            continue
        if not _serves(activator, request["dataset_id"]):
            try:
                _ = activator.rollback(cast(str, staged["stage_key"]))
                failure = "verify"
            except (HostError, OSError, ValueError, KeyError):
                failure = "rollback"
                break
            continue
        deployment = cast(dict[str, str], staged["deployment"])
        activation: dict[str, object] = {
            "system": system,
            "release_id": release_id,
            "database_sha256": database,
            "dataset_id": request["dataset_id"],
            "previous_database_sha256": plan.bundle.rsplit("/", 1)[1],
            "source_revision": deployment["source_revision"],
            "api_image": deployment["api_image"],
            "edge_image": deployment["edge_image"],
            "stage_key": staged["stage_key"],
            "activated_at": activated["activated_at"],
            "transition_seconds": round(time.monotonic() - started, 1),
            "attempt": used,
            "operator": "minos-processor",
        }
        try:
            activator.finish(staged, activation)
        except (HostError, OSError, ValueError, KeyError):
            # The new data serves; the outcome still records the activation.
            pass
        _clear(accepted)
        _ = attempts.pop(database, None)
        if attempts:
            _write_private(attempts_path, json.dumps(attempts, sort_keys=True).encode())
        else:
            attempts_path.unlink(missing_ok=True)
        return {
            "version": 1,
            "status": "activated",
            "release_id": release_id,
            "activation": activation,
        }
    return {
        "version": 1,
        "status": "failed",
        "stage": failure,
        "release_id": release_id,
    }


def _serves(activator: Activator, dataset: str) -> bool:
    for attempt in range(READY_ATTEMPTS):
        if activator.public_dataset() == dataset:
            return True
        if attempt + 1 < READY_ATTEMPTS:
            time.sleep(READY_DELAY_S)
    return False


def _read_request(accepted: Path) -> dict[str, str] | None:
    value = _read_json(accepted / "request.json")
    if value is None:
        return None
    if value.get("version") != 1:
        raise HostError("accepted bundle request is invalid")
    request = {key: str(item) for key, item in value.items() if key != "version"}
    if (
        set(request) != {"system", "release_id", "database_sha256", "dataset_id"}
        or request.get("system") not in ("icd10-pl", "icd9-pl")
        or RELEASE.fullmatch(str(request.get("release_id"))) is None
        or DIGEST.fullmatch(str(request.get("database_sha256"))) is None
        or DIGEST.fullmatch(str(request.get("dataset_id"))) is None
        or not (accepted / request["database_sha256"]).is_dir()
    ):
        raise HostError("accepted bundle request is invalid")
    return request


def _allowed_classes() -> frozenset[str]:
    """Owner-managed through processor.yml; an absent file allows nothing."""
    value = _read_json(SECRETS / "activation.json")
    if value is None:
        return frozenset()
    classes = value.get("classes")
    if not isinstance(classes, list) or not all(
        item in ("icd10-pl", "icd9-pl") for item in cast(list[object], classes)
    ):
        raise HostError("activation allowlist is invalid")
    return frozenset(cast(list[str], classes))


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return None
    if len(content) > MAX_STATE_BYTES:
        raise HostError("processor state file exceeds its bound")
    try:
        value = cast(object, json.loads(content))
    except ValueError:
        raise HostError("processor state file is invalid") from None
    if not isinstance(value, dict):
        raise HostError("processor state file is invalid")
    return cast(dict[str, object], value)


def _write_outcome(directory: Path, outcome: dict[str, object]) -> None:
    path = directory / "outcome.json"
    _write_private(path, json.dumps(outcome, sort_keys=True).encode())
    # Readable by the finalize container's group, never writable by it.
    os.chmod(path, 0o640)
    if os.geteuid() == 0:
        os.chown(path, 0, GID)


def _write_private(path: Path, content: bytes) -> None:
    temporary = path.with_name("." + path.name + ".partial")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        _ = target.write(content)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


def _run_container(plan: Plan, work: Path, runner: Runner, command: str) -> int:
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
            runner(container_arguments(plan, command), DOCKER_TIMEOUT_S),
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
        return code
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


class _Layout(Protocol):
    @property
    def compose(self) -> Path: ...


class _Step(Protocol):
    def __call__(
        self, layout: object, params: dict[str, object], /
    ) -> dict[str, object]: ...


class _ValidationHost(Protocol):
    """The parts of ``validation_host`` the activator uses."""

    HostError: type[Exception]

    def Layout(self, root: Path, project: str) -> object: ...  # noqa: N802

    def load_compose(self, path: Path) -> tuple[bytes, dict[str, object]]: ...

    def sha256_bytes(self, content: bytes) -> str: ...


class _Body(Protocol):
    def read(self, amount: int, /) -> bytes: ...

    def __enter__(self) -> "_Body": ...

    def __exit__(self, *arguments: object) -> None: ...


@final
class ValidationActivator:
    """The reviewed transition of ``validation_host``, under this run's lock.

    The functions are called in-process: their command entry point would take
    the deployment lock again, which this run already holds.
    """

    def __init__(self, root: Path) -> None:
        # Installed beside this runner, outside any package.
        path = Path(__file__).resolve().parent / "validation_host.py"
        spec = importlib.util.spec_from_file_location("validation_host", path)
        if spec is None or spec.loader is None:
            raise HostError("validation host program is missing")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._host: _ValidationHost = cast(_ValidationHost, cast(object, module))
        self._layout: object = self._host.Layout(root, "minos")

    def stage(self, bundle: Path, database: str) -> dict[str, object]:
        return self._call(
            "stage_data",
            {
                "bundle": str(bundle),
                "database_sha256": database,
                "compose_sha256": self._compose_sha256(),
            },
        )

    def activate(self, staged: dict[str, object]) -> dict[str, object]:
        return self._call(
            "activate",
            {
                "deployment": staged["deployment"],
                "compose_sha256": self._compose_sha256(),
                "stage_key": staged["stage_key"],
            },
        )

    def rollback(self, stage_key: str) -> dict[str, object]:
        return self._call("rollback", {"revision": stage_key})

    def finish(self, staged: dict[str, object], receipt: dict[str, object]) -> None:
        _ = self._call(
            "receipt",
            {
                "deployment": staged["deployment"],
                "stage_key": staged["stage_key"],
                "receipt": receipt,
            },
        )
        _ = self._call("prune", {"keep": 1, "apply": True})

    def public_dataset(self) -> str | None:
        request = urllib.request.Request(
            PUBLIC_READY_URL, headers={"User-Agent": "minos-processor"}
        )
        try:
            with cast(_Body, urllib.request.urlopen(request, timeout=10)) as response:
                value = cast(object, json.loads(response.read(4096)))
        except (OSError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        ready = cast(dict[str, object], value)
        dataset = ready.get("dataset_id")
        return (
            dataset
            if ready.get("status") == "ready" and isinstance(dataset, str)
            else None
        )

    def _compose_sha256(self) -> str:
        content, _ = self._host.load_compose(cast(_Layout, self._layout).compose)
        return self._host.sha256_bytes(content)

    def _call(self, name: str, params: dict[str, object]) -> dict[str, object]:
        step = cast(_Step, getattr(self._host, name))
        try:
            return step(self._layout, params)
        except self._host.HostError as error:
            raise HostError(str(error)) from None


def main() -> int:
    try:
        outcome = run_once(ROOT, PROCESSOR, docker, ValidationActivator(ROOT))
    except (HostError, OSError) as error:
        message = (
            str(error) if isinstance(error, HostError) else "host file access failed"
        )
        print(json.dumps({"processor_host": "failed", "reason": message}), flush=True)
        return 1
    print(json.dumps({"processor_host": outcome}), flush=True)
    return 0 if outcome != "failed" and " exited " not in outcome else 1


if __name__ == "__main__":
    sys.exit(main())
