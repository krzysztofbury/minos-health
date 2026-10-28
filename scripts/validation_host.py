"""Host side of validation deployment updates (#109).

Run as root over SSH by ``scripts/deploy_validation.py`` with
``python3 - <command> <base64-json>``. It uses only the standard library and
must stay compatible with the host's Python 3.12. Every mutating command holds
one exclusive lock, refuses any starting state other than the reviewed plan, and
changes only the reference API, ``edge`` and their bundle; watcher, tunnel and
monitoring containers must keep their identities and restart counts.

The API alternates between blue and green services (#192). Each colour is
alone on its own internal network under the alias ``reference-api``, so the
edge image is unchanged and follows the colour of the network it joins. A
transition starts the target API beside the live one, moves the edge only after
real API health, and then retires the superseded API: API startup no longer
interrupts public traffic. The legacy ``reference-api`` service is accepted as
the initial live colour.
"""

import base64
import copy
import fcntl
import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple, cast, final

PRODUCTION_ROOT = Path("/opt/minos")
LEGACY_API_SERVICE = "reference-api"
API_ALIAS = "reference-api"
API_NETWORKS = {
    LEGACY_API_SERVICE: "reference",
    "reference-api-blue": "reference-blue",
    "reference-api-green": "reference-green",
}
API_SERVICES = tuple(API_NETWORKS)
EDGE_SERVICE = "edge"
VALIDATION_SERVICES = (*API_SERVICES, EDGE_SERVICE)
DATABASE_VARIABLE = "MINOS_REFERENCE_DATABASE_SHA256"
BUNDLE_FILES = ("manifest.json", "minos.db", "review.txt")
API_IMAGE = re.compile(
    r"ghcr\.io/krzysztofbury/minos-health-reference-api@sha256:[a-f0-9]{64}"
)
EDGE_IMAGE = re.compile(
    r"ghcr\.io/krzysztofbury/minos-health-validation-edge@sha256:[a-f0-9]{64}"
)
REVISION = re.compile(r"[a-f0-9]{40}")
# A code release stages under its source revision; a data-only activation
# (#141 phase C) keeps the live images and stages under its database digest.
STAGE_KEY = re.compile(r"[a-f0-9]{40}|data-[a-f0-9]{64}")
DIGEST = re.compile(r"[a-f0-9]{64}")
PROJECT = re.compile(r"[a-z][a-z0-9_-]{0,31}")
MAX_PARAMS_BYTES = 16 * 1024
MAX_COMPOSE_BYTES = 1024 * 1024
# The 1 GiB database bound of src/minos/store/artifact.py plus its two small files.
MAX_BUNDLE_BYTES = 1024 * 1024 * 1024 + 2 * 1024 * 1024
MIN_AVAILABLE_MIB = 300
MEMORY_LIMIT = re.compile(r"([0-9]+)([bkmg]?)")
RETIRE_GRACE_S = 10
# Retirement is cleanup after real health, so it has its own bound and never
# consumes the owner-approved startup deadline (#181).
RETIRE_TIMEOUT_S = 60
MAX_KEEP = 5
INSPECT_FORMAT = '{{.Id}} {{.RestartCount}} {{if .State.Running}}{{if index .State "Health"}}{{.State.Health.Status}}{{else}}none{{end}}{{else}}{{.State.Status}}{{end}}'
PULL_TIMEOUT_S = 900
UP_WAIT_TIMEOUT_S = 300
COMPOSE_TIMEOUT_S = 300
DOCKER_TIMEOUT_S = 60
DIAGNOSTIC_TIMEOUT_S = 10
ERROR_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}")
MEMORY_EVENTS = ("max", "oom", "oom_kill")
MAX_COMMAND_OUTPUT = 256 * 1024
# Compose files owned by the watcher and maintenance procedures; their images
# and bundles are never pruned by validation updates.
FOREIGN_COMPOSE_GLOBS = (
    "rollback/compose.yaml",
    "candidate/compose.yaml",
    "catalog-deploy/*/*/compose.yaml",
    "watcher-stage/*/previous.json",
    "watcher-stage/*/candidate.json",
    "tunnel-stage/*/previous.json",
    "tunnel-stage/*/candidate.json",
    "kuma-stage/*/previous.json",
    "kuma-stage/*/candidate.json",
    "*.compose.yml",
)


class HostError(Exception):
    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details: dict[str, object] = details or {}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise HostError(message)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def run(arguments: list[str], timeout_s: float) -> str:
    try:
        completed = subprocess.run(
            arguments, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired:
        raise HostError(f"{' '.join(arguments[:3])} timed out") from None
    if completed.returncode != 0:
        # Docker stderr may contain credentials, environment values or paths.
        raise HostError(
            f"{arguments[0]} {arguments[1]} failed (exit {completed.returncode})"
        )
    return completed.stdout


def write_private(path: Path, content: bytes) -> None:
    """Atomically replace a root-only file; partial writes never become live."""
    temporary = path.with_name("." + path.name + ".partial")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        _ = target.write(content)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


def write_once(path: Path, content: bytes) -> None:
    """Create a record, or accept an identical one when a step is resumed."""
    if path.exists():
        require(path.read_bytes() == content, f"{path.name} differs from the plan")
        return
    write_private(path, content)


@final
class Layout:
    def __init__(self, root: Path, project: str) -> None:
        self.root = root
        self.project = project
        self.compose = root / "compose.yaml"
        self.reference = root / "reference"

    def stage(self, revision: str) -> Path:
        return self.root / "validation-stage" / revision

    def rollback(self, revision: str) -> Path:
        return self.root / "validation-rollback" / revision / "compose.yaml"

    def bundle(self, database_sha256: str) -> Path:
        return self.reference / database_sha256


@contextmanager
def exclusive(layout: Layout) -> Generator[None]:
    descriptor = os.open(
        layout.root / ".validation-deploy.lock", os.O_RDWR | os.O_CREAT, 0o600
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HostError(
                "another validation deployment command is running"
            ) from None
        yield
    finally:
        os.close(descriptor)


def load_compose(path: Path) -> tuple[bytes, dict[str, object]]:
    content = path.read_bytes()
    require(len(content) <= MAX_COMPOSE_BYTES, "compose file exceeds limit")
    return content, cast(dict[str, object], json.loads(content))


def _api_bind(api: dict[str, object]) -> dict[str, object]:
    binds = [
        cast(dict[str, object], volume)
        for volume in cast(list[object], api.get("volumes", []))
        if isinstance(volume, dict)
        and cast(dict[str, object], volume).get("target") == "/app/data"
    ]
    require(len(binds) == 1, "reference API must have exactly one data bind")
    require(binds[0].get("read_only") is True, "reference data bind must be read-only")
    return binds[0]


def api_service(compose: dict[str, object]) -> str:
    services = cast(dict[str, object], compose["services"])
    present = [name for name in API_SERVICES if name in services]
    require(len(present) == 1, "compose must define exactly one reference API")
    return present[0]


def successor(api: str) -> str:
    return (
        "reference-api-green" if api == "reference-api-blue" else "reference-api-blue"
    )


def api_networks(api: str) -> dict[str, object]:
    if api == LEGACY_API_SERVICE:
        return {API_NETWORKS[api]: None}
    return {API_NETWORKS[api]: {"aliases": [API_ALIAS]}}


def api_memory_mib(compose: dict[str, object]) -> int:
    services = cast(dict[str, dict[str, object]], compose["services"])
    match = MEMORY_LIMIT.fullmatch(
        str(services[api_service(compose)].get("mem_limit", "")).lower()
    )
    require(match is not None, "reference API memory limit is not exact")
    assert match is not None
    scale = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match[2]]
    return -(-int(match[1]) * scale // 1024**2)


def live_identities(compose: dict[str, object]) -> dict[str, str]:
    services = cast(dict[str, dict[str, object]], compose["services"])
    api, edge = services[api_service(compose)], services[EDGE_SERVICE]
    environment = cast(dict[str, str], api["environment"])
    return {
        "api_image": cast(str, api["image"]),
        "edge_image": cast(str, edge["image"]),
        "database_sha256": environment[DATABASE_VARIABLE],
        "bundle_source": cast(str, _api_bind(api)["source"]),
    }


def swap_identities(
    compose: dict[str, object],
    previous: dict[str, str],
    deployment: dict[str, str],
    reference_root: Path,
) -> dict[str, object]:
    """Replace validation identities and migrate the known API startup grace."""
    current = live_identities(compose)
    require(
        current["api_image"] == previous["api_image"],
        "live API image differs from plan",
    )
    require(
        current["edge_image"] == previous["edge_image"],
        "live edge image differs from plan",
    )
    require(
        current["database_sha256"] == previous["database_sha256"],
        "live database digest differs from plan",
    )
    require(
        current["bundle_source"] == str(reference_root / previous["database_sha256"]),
        "live bundle bind differs from plan",
    )
    result = copy.deepcopy(compose)
    services = cast(dict[str, dict[str, object]], result["services"])
    live_api = api_service(compose)
    api = services[live_api]
    api["image"] = deployment["api_image"]
    services["edge"]["image"] = deployment["edge_image"]
    health = api.get("healthcheck")
    require(isinstance(health, dict), "API healthcheck is missing")
    health = cast(dict[str, object], health)
    require(
        health.get("start_period") in ("20s", "120s"), "API startup grace is unexpected"
    )
    # Existing hosts retain their old Compose configuration during an image
    # swap. Apply the measured conditions grace here, not only on first install.
    health["start_period"] = "120s"
    cast(dict[str, str], api["environment"])[DATABASE_VARIABLE] = deployment[
        "database_sha256"
    ]
    _api_bind(api)["source"] = str(reference_root / deployment["database_sha256"])
    # Source pages read only the already-public metadata mounted by the edge.
    # Never infer a wider watcher/control/archive mount from a host path.
    public = [
        volume
        for volume in cast(list[dict[str, object]], services["edge"].get("volumes", []))
        if volume.get("target") == "/var/lib/minos/public"
    ]
    require(
        len(public) == 1
        and public[0].get("type") == "bind"
        and public[0].get("read_only") is True
        and public[0].get("source") == "/var/lib/minos/public",
        "edge public metadata bind differs",
    )
    volumes = cast(list[dict[str, object]], api["volumes"])
    mounted = [
        volume for volume in volumes if volume.get("target") == "/var/lib/minos/public"
    ]
    require(not mounted or mounted == public, "API public metadata bind differs")
    if not mounted:
        volumes.append(copy.deepcopy(public[0]))
    recolour(result, live_api)
    return result


def recolour(compose: dict[str, object], live_api: str) -> None:
    """Move the API to the other colour and the edge to that colour's network.

    The live service, its isolated network and the edge's exact attachment
    must match the reviewed topology, so no other route can reach either API.
    """
    services = cast(dict[str, dict[str, object]], compose["services"])
    networks = cast(dict[str, dict[str, object]], compose["networks"])
    target = successor(live_api)
    live_network, target_network = API_NETWORKS[live_api], API_NETWORKS[target]
    api = services.pop(live_api)
    edge = services[EDGE_SERVICE]
    require(api.get("networks") == api_networks(live_api), "API network differs")
    require(
        edge.get("networks") == {EDGE_SERVICE: None, live_network: None},
        "edge network differs",
    )
    dependency = edge.get("depends_on")
    require(
        dependency is None
        or (
            isinstance(dependency, dict)
            and list(cast(dict[str, object], dependency)) == [live_api]
        ),
        "edge dependency differs",
    )
    require(
        networks.get(live_network, {}).get("internal") is True,
        "API network is not internal",
    )
    require(
        networks.get(target_network, {"internal": True}).get("internal") is True,
        "target API network is not internal",
    )
    _ = networks.setdefault(target_network, {"internal": True})
    api["networks"] = api_networks(target)
    services[target] = api
    edge["networks"] = {EDGE_SERVICE: None, target_network: None}
    if dependency is not None:
        edge["depends_on"] = {target: cast(dict[str, object], dependency)[live_api]}


def validate_deployment(value: object) -> dict[str, str]:
    item = cast(dict[str, str], value)
    require(
        set(item) == {"api_image", "edge_image", "source_revision", "database_sha256"},
        "deployment fields are incomplete",
    )
    require(bool(API_IMAGE.fullmatch(item["api_image"])), "API image is not exact")
    require(bool(EDGE_IMAGE.fullmatch(item["edge_image"])), "edge image is not exact")
    require(bool(REVISION.fullmatch(item["source_revision"])), "revision is not exact")
    require(
        bool(DIGEST.fullmatch(item["database_sha256"])), "database digest is invalid"
    )
    return item


def validate_previous(value: object) -> dict[str, str]:
    item = cast(dict[str, str], value)
    require(bool(API_IMAGE.fullmatch(item["api_image"])), "previous API is not exact")
    require(
        bool(EDGE_IMAGE.fullmatch(item["edge_image"])), "previous edge is not exact"
    )
    require(
        bool(DIGEST.fullmatch(item["database_sha256"])), "previous digest is invalid"
    )
    return item


def stage_key(params: dict[str, object], deployment: dict[str, str]) -> str:
    key = cast(str, params.get("stage_key", deployment["source_revision"]))
    require(bool(STAGE_KEY.fullmatch(key)), "stage key is not exact")
    require(
        key in (deployment["source_revision"], "data-" + deployment["database_sha256"]),
        "stage key does not name this deployment",
    )
    return key


def memory_available_mib() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise HostError("available memory is unknown")


def containers(layout: Layout) -> list[tuple[str, str, str, str]]:
    """Return (service, id, restart count, health) for the Compose project."""
    listing = run(
        [
            "docker",
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={layout.project}",
            "--format",
            '{{.ID}} {{.Label "com.docker.compose.service"}}',
        ],
        DOCKER_TIMEOUT_S,
    ).split("\n")
    services = dict(
        cast(tuple[str, str], tuple(line.split(" ", 1)))
        for line in listing
        if line.strip()
    )
    if not services:
        return []
    details = run(
        [
            "docker",
            "inspect",
            "--format",
            INSPECT_FORMAT,
            *sorted(services),
        ],
        DOCKER_TIMEOUT_S,
    ).split("\n")
    result: list[tuple[str, str, str, str]] = []
    for line in details:
        if line.strip():
            identifier, restarts, health = line.split()
            result.append((services[identifier], identifier, restarts, health))
    return sorted(result)


def operational(layout: Layout) -> list[list[str]]:
    records = containers(layout)
    require(
        all(
            health in ("healthy", "starting", "unhealthy", "none")
            for service, _, _, health in records
            if service not in VALIDATION_SERVICES
        ),
        "operational services are not running",
    )
    return [
        [service, identifier, restarts]
        for service, identifier, restarts, _ in records
        if service not in VALIDATION_SERVICES
    ]


def validation_health(layout: Layout) -> dict[str, str]:
    records = [
        record for record in containers(layout) if record[0] in VALIDATION_SERVICES
    ]
    require(
        len({record[0] for record in records}) == len(records),
        "duplicate validation services",
    )
    return {
        service: health
        for service, _, _, health in records
        if service in VALIDATION_SERVICES
    }


class StartupState(NamedTuple):
    identifier: str
    restarts: int
    running: bool
    oom_killed: bool
    health: str


def remaining(deadline: float) -> float:
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise HostError(
            "startup deadline exhausted",
            {"status": "startup_deadline", "failure_classes": ["timeout"]},
        )
    return min(DOCKER_TIMEOUT_S, budget)


def startup_state(layout: Layout, service: str, deadline: float) -> StartupState | None:
    identifiers = run(
        [
            "docker",
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={layout.project}",
            "--filter",
            f"label=com.docker.compose.service={service}",
            "--format",
            "{{.ID}}",
        ],
        remaining(deadline),
    ).split()
    require(len(identifiers) <= 1, "duplicate startup service")
    if not identifiers:
        return None
    identifier = identifiers[0]
    require(
        bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", identifier)),
        "invalid container ID",
    )
    values = run(
        [
            "docker",
            "inspect",
            "--format",
            '{{.Id}} {{.RestartCount}} {{.State.Running}} {{.State.OOMKilled}} {{if index .State "Health"}}{{or .State.Health.Status "none"}}{{else}}none{{end}}',
            identifier,
        ],
        remaining(deadline),
    ).split()
    require(
        len(values) == 5
        and values[0] == identifier
        and values[1].isdigit()
        and values[2] in ("true", "false")
        and values[3] in ("true", "false")
        and values[4] in ("none", "starting", "unhealthy", "healthy"),
        "invalid startup state",
    )
    return StartupState(
        identifier, int(values[1]), values[2] == "true", values[3] == "true", values[4]
    )


def check_startup(
    state: StartupState | None, baseline: StartupState | None
) -> StartupState:
    require(state is not None, "startup service is missing")
    assert state is not None
    expected_restarts = (
        baseline.restarts
        if baseline is not None and baseline.identifier == state.identifier
        else 0
    )
    if not state.running or state.oom_killed or state.restarts != expected_restarts:
        raise HostError(
            "startup process failed",
            {
                "status": "startup_process_failed",
                "failure_classes": ["process_failure"],
                "candidate": {
                    "running": state.running,
                    "oom_killed": state.oom_killed,
                    "restarts": state.restarts,
                },
                # Consumed before the record is written; see candidate_diagnostics.
                "candidate_id": state.identifier,
            },
        )
    require(state.health != "none", "startup healthcheck is missing")
    return state


def startup_error_class(logs: str) -> str | None:
    """The last `api_startup_failed` class in fixed-field JSON log lines."""
    found: str | None = None
    for line in logs.splitlines():
        try:
            record = cast(object, json.loads(line))
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        fields = cast(dict[str, object], record)
        if fields.get("event") == "api_startup_failed":
            value = fields.get("error_class")
            found = (
                value
                if isinstance(value, str) and ERROR_CLASS.fullmatch(value)
                else None
            )
    return found


def memory_events(text: str) -> dict[str, int] | None:
    """The cgroup v2 limit and OOM counters, or None for an unexpected file."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, count = line.partition(" ")
        if key in MEMORY_EVENTS:
            if not count.isdigit():
                return None
            values[key] = int(count)
    return values if set(values) == set(MEMORY_EVENTS) else None


def candidate_diagnostics(identifier: str) -> dict[str, object]:
    """Evidence about a failed start, read before rollback removes it (#198).

    Best effort with its own bound: a missing log or cgroup file leaves None
    and never changes the failure outcome. Only a validated exception class
    name leaves the logs, never messages or tracebacks.
    """
    diagnostics: dict[str, object] = {"error_class": None, "memory_events": None}
    try:
        logs = run(
            ["docker", "logs", "--tail", "200", identifier], DIAGNOSTIC_TIMEOUT_S
        )
        diagnostics["error_class"] = startup_error_class(logs)
    except HostError:
        pass
    events = Path(
        f"/sys/fs/cgroup/system.slice/docker-{identifier}.scope/memory.events"
    )
    try:
        diagnostics["memory_events"] = memory_events(events.read_text())
    except (OSError, ValueError):
        pass
    return diagnostics


def retire_superseded(layout: Layout, api: str, deadline: float) -> list[str]:
    """Stop and remove API containers that the live Compose no longer defines."""
    listing = run(
        [
            "docker",
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={layout.project}",
            "--format",
            '{{.ID}} {{.Label "com.docker.compose.service"}}',
        ],
        remaining(deadline),
    ).split("\n")
    retired: list[str] = []
    for line in listing:
        if not line.strip():
            continue
        identifier, service = line.split(" ", 1)
        if service not in API_SERVICES or service == api:
            continue
        require(
            bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", identifier)),
            "invalid container ID",
        )
        _ = run(
            ["docker", "stop", "--time", str(RETIRE_GRACE_S), identifier],
            remaining(deadline),
        )
        _ = run(["docker", "rm", identifier], remaining(deadline))
        retired.append(service)
    return sorted(retired)


def compose_result(layout: Layout, *, overlap: bool = True) -> dict[str, object]:
    """One real-health deadline for API, edge and retirement, also for rollback.

    Compose's unhealthy abort can precede --wait-timeout. Poll health explicitly
    while a process is still in its initial bounded startup, preserving the
    image's health policy and the byte-identical prior Compose on rollback.
    With ``overlap`` the superseded API keeps serving through the old edge until
    the target API and the new edge are healthy; without it (insufficient
    memory) the superseded API is retired first, accepting downtime.
    """
    began = time.monotonic()
    deadline = began + UP_WAIT_TIMEOUT_S
    phase = "inspection"
    output_bytes = 0
    ready: dict[str, StartupState] = {}
    unhealthy: set[str] = set()
    retired: list[str] = []

    def result(details: dict[str, object]) -> dict[str, object]:
        return {
            **details,
            "phase": phase,
            "startup_wait_seconds": UP_WAIT_TIMEOUT_S,
            "duration_ms": round((time.monotonic() - began) * 1000),
            "output_bytes": output_bytes,
            "startup_unhealthy_seen": sorted(unhealthy),
            "overlap": overlap,
            "retired": retired,
        }

    def check_ready(bound: float) -> None:
        for completed, expected in ready.items():
            current = check_startup(startup_state(layout, completed, bound), expected)
            if current != expected:
                raise HostError(
                    "ready service changed during startup",
                    {
                        "status": "startup_readiness_lost",
                        "failure_classes": ["state_changed"],
                    },
                )

    try:
        _, compose = load_compose(layout.compose)
        api = api_service(compose)
        order = (api, EDGE_SERVICE)
        if not overlap:
            phase = "retire"
            retired = retire_superseded(layout, api, began + RETIRE_TIMEOUT_S)
            deadline = time.monotonic() + UP_WAIT_TIMEOUT_S
        before = {
            service: startup_state(layout, service, deadline) for service in order
        }
        for service in order:
            phase = service
            launched = bounded_command(
                [
                    "docker",
                    "compose",
                    "--project-name",
                    layout.project,
                    "--file",
                    str(layout.compose),
                    "up",
                    "--detach",
                    "--no-deps",
                    service,
                ],
                timeout_s=remaining(deadline),
            )
            output_bytes += cast(int, launched["output_bytes"])
            if launched["status"] != "completed":
                return result(launched)
            observed: str | None = None
            while True:
                check_ready(deadline)
                current = check_startup(
                    startup_state(layout, service, deadline), before[service]
                )
                require(
                    observed in (None, current.identifier), "startup container changed"
                )
                observed = current.identifier
                _ = remaining(deadline)
                if current.health == "healthy":
                    ready[service] = current
                    break
                if current.health == "unhealthy":
                    unhealthy.add(service)
                time.sleep(min(2.0, remaining(deadline)))
        check_ready(deadline)
        _ = remaining(deadline)
        if overlap:
            phase = "retire"
            bound = time.monotonic() + RETIRE_TIMEOUT_S
            retired = retire_superseded(layout, api, bound)
            check_ready(bound)
        return result({"status": "completed", "exit_code": 0, "failure_classes": []})
    except (HostError, OSError, ValueError) as error:
        details: dict[str, object] = (
            dict(error.details) if isinstance(error, HostError) else {}
        )
        identifier = details.pop("candidate_id", None)
        if isinstance(identifier, str):
            # A process or OOM failure keeps its own status and evidence even
            # when reading the evidence outlasts the deadline (#232).
            details["candidate"] = {
                **cast(dict[str, object], details["candidate"]),
                **candidate_diagnostics(identifier),
            }
        elif time.monotonic() >= deadline and phase != "retire":
            details = {"status": "startup_deadline", "failure_classes": ["timeout"]}
        return result(
            details or {"status": "startup_inspection_failed", "failure_classes": []}
        )


def bounded_command(
    arguments: list[str], *, timeout_s: float | None = None
) -> dict[str, object]:
    """Execute an operational command with the same private bounded capture."""
    began = time.monotonic()
    limit = (
        COMPOSE_TIMEOUT_S if timeout_s is None else min(COMPOSE_TIMEOUT_S, timeout_s)
    )
    outcome = "completed"
    size = 0
    classifications: set[str] = set()
    tail = b""
    with subprocess.Popen(
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    ) as process:
        assert process.stdout is not None
        try:
            with selectors.DefaultSelector() as selector:
                _ = selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() - began >= limit:
                        outcome = "timeout"
                        break
                    for key, _ in selector.select(timeout=0.1):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            _ = selector.unregister(key.fd)
                            continue
                        size += len(chunk)
                        tail = (tail + chunk)[-8192:]
                        for marker, code in (
                            (b"unhealthy", "unhealthy"),
                            (b"timed out", "timeout"),
                            (b"timeout", "timeout"),
                            (b"permission denied", "permission_denied"),
                            (b"no space left", "disk_full"),
                        ):
                            if marker in tail.lower():
                                classifications.add(code)
                        if size > MAX_COMMAND_OUTPUT:
                            outcome = "output_limit"
                            break
                    if outcome != "completed":
                        break
            if outcome == "completed":
                try:
                    _ = process.wait(
                        timeout=max(0.01, limit - (time.monotonic() - began))
                    )
                except subprocess.TimeoutExpired:
                    outcome = "timeout"
        finally:
            if outcome != "completed" or process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            _ = process.wait()
    if outcome == "completed" and process.returncode != 0:
        outcome = "command_failed"
    return {
        "status": outcome,
        "exit_code": process.returncode,
        "duration_ms": round((time.monotonic() - began) * 1000),
        "output_bytes": size,
        "failure_classes": sorted(classifications),
    }


def compose_up(layout: Layout, log: Path, *, overlap: bool = True) -> None:
    details: dict[str, object]
    try:
        details = compose_result(layout, overlap=overlap)
    except OSError:
        details = {"status": "os_error"}
    # Keep the last attempt and its predecessor, bounded across explicit retries.
    if log.exists():
        os.replace(log, log.with_suffix(".previous.log"))
    write_private(log, canonical(details))
    if details["status"] != "completed":
        raise HostError("compose up failed", details)


def transition_state(layout: Layout, before: list[list[str]]) -> dict[str, object]:
    """Best-effort fixed-field state; inspection failures must not mask recovery."""
    state: dict[str, object] = {
        "validation_health": {"api": "unknown", EDGE_SERVICE: "unknown"},
        "operational": "unknown",
        "identities_match": False,
        "public_ingress": "not_checked",
    }
    try:
        _, compose = load_compose(layout.compose)
        expected_services = {api_service(compose), EDGE_SERVICE}
        health = validation_health(layout)
        # Superseded colours stay visible: a retained API is not a restored state.
        state["validation_health"] = {
            name: health.get(name, "missing")
            for name in sorted(expected_services | set(health))
        }
    except (HostError, OSError, ValueError, KeyError):
        pass
    try:
        state["operational"] = (
            "unchanged" if before and operational(layout) == before else "changed"
        )
    except (HostError, OSError, ValueError, KeyError):
        pass
    try:
        _, compose = load_compose(layout.compose)
        expected = live_identities(compose)
        api = api_service(compose)
        records = [
            record for record in containers(layout) if record[0] in VALIDATION_SERVICES
        ]
        matches: dict[str, bool] = {}
        for service, identifier, _, _ in records:
            image = run(
                ["docker", "inspect", "--format", "{{.Config.Image}}", identifier],
                DOCKER_TIMEOUT_S,
            ).strip()
            matches[service] = image == expected.get(
                {api: "api_image", EDGE_SERVICE: "edge_image"}.get(service, ""), ""
            )
        state["identities_match"] = matches == {api: True, EDGE_SERVICE: True}
    except (HostError, OSError, ValueError, KeyError):
        pass
    return state


def restored(state: dict[str, object]) -> bool:
    health = cast(dict[str, str], state["validation_health"])
    # identities_match pins the single API to the live Compose colour.
    return (
        len(health) == 2
        and EDGE_SERVICE in health
        and set(health.values()) == {"healthy"}
        and state["operational"] == "unchanged"
        and state["identities_match"] is True
    )


def overlap_fits(compose: dict[str, object]) -> bool:
    """Recovery outranks availability: without room for two APIs, or without an
    exact limit to reserve, rollback retires the superseded API first."""
    try:
        return memory_available_mib() >= MIN_AVAILABLE_MIB + api_memory_mib(compose)
    except (HostError, OSError, ValueError, KeyError):
        return False


def restore(
    layout: Layout, revision: str, before: list[list[str]]
) -> dict[str, object]:
    directory = layout.stage(revision)
    write_once(directory / "rollback-ob.json", canonical(before))
    content = layout.rollback(revision).read_bytes()
    write_private(layout.compose, content)
    failure: dict[str, object] | None = None
    try:
        compose_up(
            layout,
            directory / "rollback-operations.log",
            overlap=overlap_fits(cast(dict[str, object], json.loads(content))),
        )
    except (HostError, OSError) as error:
        failure = (
            error.details if isinstance(error, HostError) else {"status": "os_error"}
        )
    state = transition_state(layout, before)
    state["status"] = (
        "restored" if failure is None and restored(state) else "incomplete"
    )
    state["command_failure"] = failure
    state["recovery"] = "rollback --revision " + revision
    write_private(directory / "rollback-result.json", canonical(state))
    if state["status"] != "restored":
        raise HostError(
            "rollback incomplete; inspect diagnostics and explicitly retry rollback",
            state,
        )
    return state


def live_revision(layout: Layout, identities: dict[str, str]) -> str | None:
    matches: list[str] = []
    for record in sorted((layout.root / "validation-stage").glob("*/deployment.json")):
        deployment = cast(dict[str, str], json.loads(record.read_text()))
        if all(
            deployment.get(key) == identities[key]
            for key in ("api_image", "edge_image", "database_sha256")
        ):
            matches.append(record.parent.name)
    require(len(matches) <= 1, "live release matches several staged revisions")
    return matches[0] if matches else None


def live_sources(layout: Layout, database: str) -> list[str] | None:
    """Release identities of the live bundle, which may hold activated data."""
    path = layout.bundle(database) / "manifest.json"
    if not path.is_file():
        return None
    content = path.read_bytes()
    if len(content) > MAX_COMPOSE_BYTES:
        return None
    # Unreadable is reported, never guessed; the workstation plan then refuses.
    try:
        dataset = cast(dict[str, object], json.loads(content)["dataset"])
        return sorted(
            cast(str, cast(dict[str, object], source)["release_id"])
            for source in cast(list[object], dataset["sources"])
        )
    except (KeyError, TypeError, ValueError):
        return None


def stage_revision(layout: Layout, stage: str) -> str:
    record = cast(
        dict[str, str],
        json.loads((layout.stage(stage) / "deployment.json").read_text()),
    )
    return record["source_revision"]


def inspect(layout: Layout, _: dict[str, object]) -> dict[str, object]:
    content, compose = load_compose(layout.compose)
    identities = live_identities(compose)
    stage = live_revision(layout, identities)
    return {
        "compose_sha256": sha256_bytes(content),
        "live": identities,
        # The code revision, also for a data-only activation's stage.
        "live_revision": None if stage is None else stage_revision(layout, stage),
        "live_stage": stage,
        "live_sources": live_sources(layout, identities["database_sha256"]),
        "available_mib": memory_available_mib(),
        "free_bytes": shutil.disk_usage(layout.root).free,
        "operational": operational(layout),
        "validation_health": validation_health(layout),
    }


def prepare(layout: Layout, params: dict[str, object]) -> dict[str, object]:
    deployment = validate_deployment(params["deployment"])
    content, _ = load_compose(layout.compose)
    require(
        sha256_bytes(content) == params["compose_sha256"],
        "live compose changed since plan",
    )
    stage = layout.stage(deployment["source_revision"])
    stage.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {
        "archive": str(stage / "bundle.tar.gz"),
        # Stage re-verifies an installed bundle's checksum before trusting it.
        "bundle_installed": layout.bundle(deployment["database_sha256"]).is_dir(),
    }


def _install_bundle(
    layout: Layout, stage: Path, database: str, archive_sha: str
) -> None:
    target = layout.bundle(database)
    archive = stage / "bundle.tar.gz"
    if target.exists():
        require(
            sha256_file(target / "minos.db") == database, "installed bundle differs"
        )
        archive.unlink(missing_ok=True)
        return
    require(archive.is_file(), "bundle archive was not uploaded")
    require(sha256_file(archive) == archive_sha, "bundle archive checksum differs")
    partial = _partial(layout, database)
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        require(
            sorted(member.name for member in members) == sorted(BUNDLE_FILES)
            and all(member.isfile() for member in members)
            and sum(member.size for member in members) <= MAX_BUNDLE_BYTES,
            "bundle archive layout is unsupported",
        )
        for member in members:
            source = bundle.extractfile(member)
            if source is None:
                raise HostError("bundle member is unreadable")
            with (partial / member.name).open("xb") as output:
                shutil.copyfileobj(source, output)
    _finish_install(layout, partial, database)
    archive.unlink()


def _install_bundle_directory(layout: Layout, source: Path, database: str) -> None:
    """Copy a processor-built bundle; its owner never gains the reference tree."""
    target = layout.bundle(database)
    if target.exists():
        require(
            sha256_file(target / "minos.db") == database, "installed bundle differs"
        )
        return
    require(source.is_dir() and not source.is_symlink(), "bundle directory is missing")
    entries = sorted(source.iterdir())
    require(
        [entry.name for entry in entries] == sorted(BUNDLE_FILES)
        and all(entry.is_file() and not entry.is_symlink() for entry in entries)
        and sum(entry.stat().st_size for entry in entries) <= MAX_BUNDLE_BYTES,
        "bundle directory layout is unsupported",
    )
    partial = _partial(layout, database)
    for entry in entries:
        with entry.open("rb") as reader, (partial / entry.name).open("xb") as output:
            shutil.copyfileobj(reader, output)
    _finish_install(layout, partial, database)


def _partial(layout: Layout, database: str) -> Path:
    partial = layout.reference / ("." + database + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(mode=0o755, parents=True)
    return partial


def _finish_install(layout: Layout, partial: Path, database: str) -> None:
    target = layout.bundle(database)
    require(
        sha256_file(partial / "minos.db") == database,
        "bundle database checksum differs",
    )
    for name in BUNDLE_FILES:
        os.chmod(partial / name, 0o444)
    if os.geteuid() == 0:
        for path in (partial, *(partial / name for name in BUNDLE_FILES)):
            os.chown(path, 0, 0)
    os.chmod(partial, 0o755)
    os.rename(partial, target)


def _verify_image(reference: str, revision: str) -> str:
    _ = run(["docker", "pull", "-q", reference], PULL_TIMEOUT_S)
    identity = run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            '{{.Os}}/{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}',
            reference,
        ],
        DOCKER_TIMEOUT_S,
    ).strip()
    require(
        identity == "linux/amd64 " + revision, f"{reference} is not the reviewed build"
    )
    return f"{reference} {identity}\n"


def stage(layout: Layout, params: dict[str, object]) -> dict[str, object]:
    deployment = validate_deployment(params["deployment"])
    previous = validate_previous(params["previous"])
    content, compose = load_compose(layout.compose)
    require(
        sha256_bytes(content) == params["compose_sha256"],
        "live compose changed since plan",
    )
    revision = deployment["source_revision"]
    directory = layout.stage(revision)
    require(directory.is_dir(), "stage directory was not prepared")
    _install_bundle(
        layout,
        directory,
        deployment["database_sha256"],
        cast(str, params["archive_sha256"]),
    )
    write_once(directory / "deployment.json", canonical(deployment))
    rollback = layout.rollback(revision)
    rollback.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_once(rollback, content)
    rendered = swap_identities(compose, previous, deployment, layout.reference)
    staged = (json.dumps(rendered, indent=2) + "\n").encode()
    write_once(directory / "compose.json", staged)
    pulls = "".join(
        _verify_image(image, revision)
        for image in (deployment["api_image"], deployment["edge_image"])
    )
    write_private(directory / "image-pull.log", pulls.encode())
    return {"stage": str(directory), "compose_json_sha256": sha256_bytes(staged)}


def stage_data(layout: Layout, params: dict[str, object]) -> dict[str, object]:
    """Stage a new database under the live images (#141 phase C).

    Only the database changes: the images, Compose topology and code revision
    stay the live ones, so no image is pulled. The returned deployment and
    stage key drive the ordinary activate, receipt and rollback commands.
    """
    database = cast(str, params["database_sha256"])
    require(bool(DIGEST.fullmatch(database)), "database digest is invalid")
    content, compose = load_compose(layout.compose)
    require(
        sha256_bytes(content) == params["compose_sha256"],
        "live compose changed since plan",
    )
    identities = live_identities(compose)
    require(identities["database_sha256"] != database, "database is already live")
    live = live_revision(layout, identities)
    require(live is not None, "live release has no staged revision record")
    deployment = validate_deployment(
        {
            "api_image": identities["api_image"],
            "edge_image": identities["edge_image"],
            "source_revision": stage_revision(layout, cast(str, live)),
            "database_sha256": database,
        }
    )
    previous = {
        name: identities[name]
        for name in ("api_image", "edge_image", "database_sha256")
    }
    _install_bundle_directory(layout, Path(cast(str, params["bundle"])), database)
    key = "data-" + database
    directory = layout.stage(key)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_once(directory / "deployment.json", canonical(deployment))
    rollback = layout.rollback(key)
    rollback.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_once(rollback, content)
    rendered = swap_identities(compose, previous, deployment, layout.reference)
    staged = (json.dumps(rendered, indent=2) + "\n").encode()
    write_once(directory / "compose.json", staged)
    return {
        "stage_key": key,
        "deployment": deployment,
        "compose_json_sha256": sha256_bytes(staged),
    }


def activate(layout: Layout, params: dict[str, object]) -> dict[str, object]:
    deployment = validate_deployment(params["deployment"])
    revision = stage_key(params, deployment)
    directory = layout.stage(revision)
    staged = (directory / "compose.json").read_bytes()
    live, _ = load_compose(layout.compose)
    if live == staged:
        before = cast(list[list[str]], json.loads((directory / "ob").read_bytes()))
        state = transition_state(layout, before)
        require(
            restored(state),
            "active release is not healthy or operational services changed",
        )
        require(
            (directory / "activated-at.txt").is_file(),
            "activation has no completed record; rollback required",
        )
        return {"status": "already_active", **state}
    require(
        sha256_bytes(live) == params["compose_sha256"],
        "live compose changed since plan",
    )
    available = memory_available_mib()
    # The candidate API starts beside the live one before the live one retires.
    require(
        available
        >= MIN_AVAILABLE_MIB
        + api_memory_mib(cast(dict[str, object], json.loads(staged))),
        "host memory headroom is below the floor",
    )
    before = operational(layout)
    require(bool(before), "operational services are not running")
    write_private(directory / "ob", canonical(before))
    write_private(layout.compose, staged)
    log = directory / "compose-operations.log"
    try:
        compose_up(layout, log)
        state = transition_state(layout, before)
        if not restored(state):
            raise HostError(
                "new release is not healthy or operational services changed", state
            )
    except (HostError, OSError) as error:
        original: dict[str, object] = (
            error.details if isinstance(error, HostError) else {"status": "os_error"}
        )
        original["state"] = transition_state(layout, before)
        # Preserve the original failure even if recovery also fails.
        try:
            recovery = restore(layout, revision, before)
        except (HostError, OSError) as recovery_error:
            raise HostError(
                "activation failed; rollback incomplete",
                {
                    "activation_failure": original,
                    "rollback": recovery_error.details
                    if isinstance(recovery_error, HostError)
                    else {"status": "os_error"},
                },
            ) from None
        raise HostError(
            "activation failed and the previous release was restored",
            {"activation_failure": original, "rollback": recovery},
        ) from None
    after = operational(layout)
    write_private(directory / "oa", canonical(after))
    require(after == before, "operational services changed during activation")
    require(restored(transition_state(layout, before)), "new release is not healthy")
    activated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_private(directory / "activated-at.txt", (activated_at + "\n").encode())
    return {
        "status": "activated",
        "activated_at": activated_at,
        "available_mib": memory_available_mib(),
        "operational": "unchanged",
    }


def rollback(layout: Layout, params: dict[str, object]) -> dict[str, object]:
    revision = cast(str, params["revision"])
    require(bool(STAGE_KEY.fullmatch(revision)), "revision is not exact")
    source = layout.rollback(revision)
    require(source.is_file(), "no retained rollback for that revision")
    content, compose = load_compose(source)
    current, _ = load_compose(layout.compose)
    directory = layout.stage(revision)
    require(
        current in (content, (directory / "compose.json").read_bytes()),
        "live compose is neither the selected candidate nor its rollback",
    )
    identities = live_identities(compose)
    require(
        layout.bundle(identities["database_sha256"]).is_dir(),
        "rollback bundle is missing",
    )
    baseline = directory / "rollback-ob.json"
    if baseline.exists():
        before = cast(list[list[str]], json.loads(baseline.read_bytes()))
    else:
        before = operational(layout)
        require(bool(before), "operational services are not running")
        write_private(baseline, canonical(before))
    state = restore(layout, revision, before)
    return {**state, "live": identities}


def _rollback_chain(layout: Layout, keep: int) -> list[Path]:
    """Follow live -> previous releases through retained rollback files."""
    _, compose = load_compose(layout.compose)
    revision = live_revision(layout, live_identities(compose))
    require(revision is not None, "live release has no staged revision record")
    chain: list[Path] = []
    for _ in range(keep):
        source = layout.rollback(cast(str, revision))
        if not source.is_file():
            break
        chain.append(source)
        _, previous = load_compose(source)
        revision = live_revision(layout, live_identities(previous))
        if revision is None:
            break
    return chain


def _image_records() -> list[tuple[str, list[str], int]]:
    identifiers = sorted(
        set(
            run(["docker", "image", "ls", "-q", "--no-trunc"], DOCKER_TIMEOUT_S).split()
        )
    )
    if not identifiers:
        return []
    details = cast(
        list[dict[str, object]],
        json.loads(run(["docker", "image", "inspect", *identifiers], DOCKER_TIMEOUT_S)),
    )
    return [
        (
            cast(str, item["Id"]),
            cast(list[str], item.get("RepoDigests") or []),
            cast(int, item.get("Size", 0)),
        )
        for item in details
    ]


def _images_in_use() -> set[str]:
    containers_all = run(
        ["docker", "ps", "-aq", "--no-trunc"], DOCKER_TIMEOUT_S
    ).split()
    if not containers_all:
        return set()
    return set(
        run(
            ["docker", "inspect", "--format", "{{.Image}}", *containers_all],
            DOCKER_TIMEOUT_S,
        ).split()
    )


def prune(layout: Layout, params: dict[str, object]) -> dict[str, object]:
    keep = params["keep"]
    # bool is an int subclass; reject it explicitly.
    if type(keep) is not int or not 1 <= keep <= MAX_KEEP:
        raise HostError("keep must be 1 to 5")
    retained = [layout.compose, *_rollback_chain(layout, keep)]
    for pattern in FOREIGN_COMPOSE_GLOBS:
        retained.extend(sorted(layout.root.glob(pattern)))
    referenced: set[str] = set()
    for path in retained:
        # Image digests and bundle database digests are both 64 hex digits.
        referenced.update(DIGEST.findall(path.read_text()))
    in_use = _images_in_use()
    images = [
        (identifier, references, size)
        for identifier, references, size in _image_records()
        # Images without registry digests are not ours to judge; keep them.
        if references
        and identifier not in in_use
        and not any(
            reference.split("@", 1)[1][7:] in referenced for reference in references
        )
    ]
    bundles = [
        path
        for path in sorted(layout.reference.iterdir())
        if path.is_dir() and path.name.lstrip(".").split(".")[0] not in referenced
    ]
    archives = sorted(layout.root.glob("validation-stage/*/bundle.tar.gz")) + sorted(
        path for path in layout.root.glob("validation-stage/*/bundle") if path.is_dir()
    )
    result: dict[str, object] = {
        "retained_compose": [str(path.relative_to(layout.root)) for path in retained],
        "images": [references[0] for _, references, _ in images],
        "image_bytes": sum(size for _, _, size in images),
        "bundles": [path.name for path in bundles],
        "archives": [str(path.relative_to(layout.root)) for path in archives],
        "applied": False,
    }
    if params.get("apply") is True:
        for _, references, _ in images:
            _ = run(["docker", "image", "rm", *references], DOCKER_TIMEOUT_S)
        for path in bundles:
            shutil.rmtree(path)
        for path in archives:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        result["applied"] = True
    return result


def receipt(layout: Layout, params: dict[str, object]) -> dict[str, object]:
    deployment = validate_deployment(params["deployment"])
    directory = layout.stage(stage_key(params, deployment))
    activated = directory / "activated-at.txt"
    require(activated.is_file(), "release was not activated by this tool")
    # The host's own activation record is authoritative, not the caller's.
    content = {
        **cast(dict[str, object], params["receipt"]),
        "activated_at": activated.read_text().strip(),
    }
    path = directory / "activation-receipt.json"
    write_once(path, canonical(content))
    return {"receipt": str(path), "content": content}


READ_ONLY: dict[str, Callable[[Layout, dict[str, object]], dict[str, object]]] = {
    "inspect": inspect,
}
MUTATING: dict[str, Callable[[Layout, dict[str, object]], dict[str, object]]] = {
    "prepare": prepare,
    "stage": stage,
    "stage-data": stage_data,
    "activate": activate,
    "rollback": rollback,
    "prune": prune,
    "receipt": receipt,
}


def main(arguments: list[str]) -> int:
    try:
        require(len(arguments) == 3, "usage: python3 - <command> <base64-json>")
        command, encoded = arguments[1], arguments[2]
        require(len(encoded) <= MAX_PARAMS_BYTES, "parameters exceed limit")
        params = cast(dict[str, object], json.loads(base64.urlsafe_b64decode(encoded)))
        root = Path(cast(str, params.get("root", str(PRODUCTION_ROOT))))
        project = cast(str, params.get("project", "minos"))
        require(root.is_absolute() and root.is_dir(), "deployment root is invalid")
        require(bool(PROJECT.fullmatch(project)), "project name is invalid")
        if root == PRODUCTION_ROOT:
            require(os.geteuid() == 0, "production updates must run as root")
        layout = Layout(root, project)
        if command in READ_ONLY:
            result = READ_ONLY[command](layout, params)
        else:
            require(command in MUTATING, "unknown command")
            with exclusive(layout):
                # Prune is a dry run unless explicitly applied; it still takes
                # the lock so it never races a stage or activation.
                result = MUTATING[command](layout, params)
    except (HostError, OSError, KeyError, TypeError, ValueError) as error:
        message = str(error) if isinstance(error, HostError) else type(error).__name__
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": message,
                    "details": error.details if isinstance(error, HostError) else {},
                }
            )
        )
        return 2
    print(json.dumps({"ok": True, "result": result}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
