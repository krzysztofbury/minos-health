"""Host side of validation deployment updates (#109).

Run as root over SSH by ``scripts/deploy_validation.py`` with
``python3 - <command> <base64-json>``. It uses only the standard library and
must stay compatible with the host's Python 3.12. Every mutating command holds
one exclusive lock, refuses any starting state other than the reviewed plan, and
changes only ``reference-api``, ``edge`` and their bundle; watcher, tunnel and
monitoring containers must keep their identities and restart counts.
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
from typing import cast, final

PRODUCTION_ROOT = Path("/opt/minos")
VALIDATION_SERVICES = ("reference-api", "edge")
DATABASE_VARIABLE = "MINOS_REFERENCE_DATABASE_SHA256"
BUNDLE_FILES = ("manifest.json", "minos.db", "review.txt")
API_IMAGE = re.compile(
    r"ghcr\.io/krzysztofbury/minos-health-reference-api@sha256:[a-f0-9]{64}"
)
EDGE_IMAGE = re.compile(
    r"ghcr\.io/krzysztofbury/minos-health-validation-edge@sha256:[a-f0-9]{64}"
)
REVISION = re.compile(r"[a-f0-9]{40}")
DIGEST = re.compile(r"[a-f0-9]{64}")
PROJECT = re.compile(r"[a-z][a-z0-9_-]{0,31}")
MAX_PARAMS_BYTES = 16 * 1024
MAX_COMPOSE_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MIN_AVAILABLE_MIB = 300
MAX_KEEP = 5
INSPECT_FORMAT = '{{.Id}} {{.RestartCount}} {{if .State.Running}}{{if index .State "Health"}}{{.State.Health.Status}}{{else}}none{{end}}{{else}}{{.State.Status}}{{end}}'
PULL_TIMEOUT_S = 900
UP_WAIT_TIMEOUT_S = 180
COMPOSE_TIMEOUT_S = UP_WAIT_TIMEOUT_S + 120
DOCKER_TIMEOUT_S = 60
MAX_COMMAND_OUTPUT = 256 * 1024
# Compose files owned by the watcher and maintenance procedures; their images
# and bundles are never pruned by validation updates.
FOREIGN_COMPOSE_GLOBS = (
    "rollback/compose.yaml",
    "candidate/compose.yaml",
    "catalog-deploy/*/*/compose.yaml",
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


def run(arguments: list[str], timeout_s: int) -> str:
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


def live_identities(compose: dict[str, object]) -> dict[str, str]:
    services = cast(dict[str, dict[str, object]], compose["services"])
    api, edge = services["reference-api"], services["edge"]
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
    services["reference-api"]["image"] = deployment["api_image"]
    services["edge"]["image"] = deployment["edge_image"]
    health = services["reference-api"].get("healthcheck")
    require(isinstance(health, dict), "API healthcheck is missing")
    health = cast(dict[str, object], health)
    require(
        health.get("start_period") in ("20s", "120s"), "API startup grace is unexpected"
    )
    # Existing hosts retain their old Compose configuration during an image
    # swap. Apply the measured conditions grace here, not only on first install.
    health["start_period"] = "120s"
    cast(dict[str, str], services["reference-api"]["environment"])[
        DATABASE_VARIABLE
    ] = deployment["database_sha256"]
    _api_bind(services["reference-api"])["source"] = str(
        reference_root / deployment["database_sha256"]
    )
    return result


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


def compose_result(layout: Layout) -> dict[str, object]:
    """Drain both streams within fixed byte/time bounds; persist only safe facts.

    Compose lifecycle messages normally go to stderr, even on success. Raw
    output is never retained: known failure classes plus exit/elapsed/byte
    counts are sufficient to distinguish timeout, unhealthy and process failure.
    Service state is captured independently by the calling transition.
    """
    arguments = [
        "docker",
        "compose",
        "--project-name",
        layout.project,
        "--file",
        str(layout.compose),
        "up",
        "--detach",
        "--no-deps",
        "--wait",
        "--wait-timeout",
        str(UP_WAIT_TIMEOUT_S),
        *VALIDATION_SERVICES,
    ]
    began = time.monotonic()
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
                    if time.monotonic() - began >= COMPOSE_TIMEOUT_S:
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
                        timeout=max(
                            0.01, COMPOSE_TIMEOUT_S - (time.monotonic() - began)
                        )
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


def compose_up(layout: Layout, log: Path) -> None:
    details: dict[str, object]
    try:
        details = compose_result(layout)
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
        "validation_health": {name: "unknown" for name in VALIDATION_SERVICES},
        "operational": "unknown",
        "identities_match": False,
        "public_ingress": "not_checked",
    }
    try:
        health = validation_health(layout)
        state["validation_health"] = {
            name: health.get(name, "missing") for name in VALIDATION_SERVICES
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
        records = [
            record for record in containers(layout) if record[0] in VALIDATION_SERVICES
        ]
        matches: dict[str, bool] = {}
        for service, identifier, _, _ in records:
            image = run(
                ["docker", "inspect", "--format", "{{.Config.Image}}", identifier],
                DOCKER_TIMEOUT_S,
            ).strip()
            matches[service] = (
                image
                == expected["api_image" if service == "reference-api" else "edge_image"]
            )
        state["identities_match"] = matches == {
            name: True for name in VALIDATION_SERVICES
        }
    except (HostError, OSError, ValueError, KeyError):
        pass
    return state


def restored(state: dict[str, object]) -> bool:
    return (
        state["validation_health"] == {name: "healthy" for name in VALIDATION_SERVICES}
        and state["operational"] == "unchanged"
        and state["identities_match"] is True
    )


def restore(
    layout: Layout, revision: str, before: list[list[str]]
) -> dict[str, object]:
    directory = layout.stage(revision)
    write_once(directory / "rollback-ob.json", canonical(before))
    write_private(layout.compose, layout.rollback(revision).read_bytes())
    failure: dict[str, object] | None = None
    try:
        compose_up(layout, directory / "rollback-operations.log")
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


def inspect(layout: Layout, _: dict[str, object]) -> dict[str, object]:
    content, compose = load_compose(layout.compose)
    identities = live_identities(compose)
    return {
        "compose_sha256": sha256_bytes(content),
        "live": identities,
        "live_revision": live_revision(layout, identities),
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
    partial = layout.reference / ("." + database + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(mode=0o755, parents=True)
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
    archive.unlink()


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


def activate(layout: Layout, params: dict[str, object]) -> dict[str, object]:
    deployment = validate_deployment(params["deployment"])
    revision = deployment["source_revision"]
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
    require(available >= MIN_AVAILABLE_MIB, "host memory headroom is below the floor")
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
    health = validation_health(layout)
    require(
        health == {name: "healthy" for name in VALIDATION_SERVICES},
        "new release is not healthy",
    )
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
    require(bool(REVISION.fullmatch(revision)), "revision is not exact")
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
    directory = layout.stage(deployment["source_revision"])
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
