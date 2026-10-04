"""One bounded isolated API observation; compatible with host Python 3.12."""

import base64
import hashlib
import json
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from types import FrameType
from typing import BinaryIO, Required, TypedDict, cast

MAX_BYTES = 512 * 1024 * 1024
FILES = {"manifest.json", "minos.db", "review.txt"}


class Parameters(TypedDict):
    image: str
    revision: str
    database_sha256: str
    dataset_id: str


class Lookup(TypedDict, total=False):
    route: Required[str]
    status: Required[int]
    duration_ms: float
    dataset_id: str
    conditions_status: str
    failure_type: str


class Probe(TypedDict):
    lookups: list[Lookup]
    cgroup: dict[str, int]
    api_process_bytes: dict[str, int]


def run(arguments: list[str], timeout_s: float = 30) -> str:
    result = subprocess.run(arguments, capture_output=True, timeout=timeout_s)
    if result.returncode:
        raise RuntimeError("measurement subprocess failed")
    return result.stdout.decode()


def snapshot() -> dict[str, list[object]]:
    ids = run(
        ["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=minos"]
    ).split()
    if len(ids) != 5:
        raise RuntimeError("expected five live Minos services")
    rows = cast(list[dict[str, object]], json.loads(run(["docker", "inspect", *ids])))
    services: dict[str, dict[str, object]] = {}
    for row in rows:
        config = cast(dict[str, object], row["Config"])
        labels = cast(dict[str, str], config["Labels"])
        services[labels["com.docker.compose.service"]] = row
    if set(services) != {
        "reference-api",
        "edge",
        "watcher",
        "cloudflared",
        "uptime-kuma",
    }:
        raise RuntimeError("unexpected live Minos services")
    for row in rows:
        state = cast(dict[str, object], row["State"])
        health = cast(dict[str, object], state.get("Health", {}))
        if state["Running"] is not True or health.get("Status", "healthy") != "healthy":
            raise RuntimeError("live service is not healthy")
    return {
        name: [r["Id"], r["RestartCount"], r["Image"]] for name, r in services.items()
    }


def available_mib() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise RuntimeError("host memory unavailable")


def unpack(stream: BinaryIO, root: Path, digest: str) -> Path:
    archive = root / "bundle.tar.gz"
    size_bytes = 0
    with archive.open("xb") as output:
        while chunk := stream.read(1024 * 1024):
            size_bytes += len(chunk)
            if size_bytes > MAX_BYTES:
                raise RuntimeError("archive exceeds limit")
            _ = output.write(chunk)
    bundle = root / "bundle"
    bundle.mkdir(mode=0o755)
    with tarfile.open(archive, "r:gz") as source:
        # Do not allocate an unbounded member inventory from an untrusted tar.
        members: list[tarfile.TarInfo] = []
        for member in source:
            members.append(member)
            if len(members) > 3:
                raise RuntimeError("unsupported archive layout")
        if len(members) != 3 or {m.name for m in members} != FILES:
            raise RuntimeError("unsupported archive layout")
        if (
            not all(m.isfile() for m in members)
            or sum(m.size for m in members) > MAX_BYTES
        ):
            raise RuntimeError("unsupported archive members")
        for member in members:
            content = source.extractfile(member)
            if content is None:
                raise RuntimeError("unreadable archive member")
            with content, (bundle / member.name).open("xb") as output:
                shutil.copyfileobj(content, output)
            (bundle / member.name).chmod(0o444)
    with (bundle / "minos.db").open("rb") as content:
        actual = hashlib.file_digest(content, "sha256").hexdigest()
    if actual != digest:
        raise RuntimeError("database checksum differs")
    return bundle


def probe(name: str, timeout_s: float = 50) -> Probe:
    program = """
import json, time, urllib.request, urllib.error
from pathlib import Path
paths = ['/readyz', '/api/v1/jgp/2026-07-01/groups/F72', '/slowniki/icd-10/K40/jgp/2026-07-01']
rows = []
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for path in paths:
    start = time.perf_counter()
    try:
        with opener.open('http://127.0.0.1:8081' + path, timeout=15) as r:
            body = r.read(2 * 1024 * 1024)
            row = {'route': path, 'status': r.status, 'duration_ms': (time.perf_counter()-start)*1000}
            if path == '/readyz': row['dataset_id'] = json.loads(body)['dataset_id']
            if '/groups/' in path: row['conditions_status'] = json.loads(body).get('conditions', {}).get('status', 'missing_conditions')
            rows.append(row)
    except urllib.error.URLError as error:
        rows.append({'route': path, 'status': getattr(error, 'code', 0), 'failure_type': type(getattr(error, 'reason', error)).__name__})
        break
memory = {name: int((Path('/sys/fs/cgroup')/name).read_text()) for name in ('memory.current', 'memory.peak', 'memory/memory.usage_in_bytes', 'memory/memory.max_usage_in_bytes') if (Path('/sys/fs/cgroup')/name).is_file()}
process = {line.split(':')[0]: int(line.split()[1])*1024 for line in Path('/proc/1/status').read_text().splitlines() if line.startswith(('VmRSS:', 'VmHWM:'))}
print(json.dumps({'lookups': rows, 'cgroup': memory, 'api_process_bytes': process}))
"""
    return cast(
        Probe,
        json.loads(
            run(
                ["docker", "exec", name, "/build/.venv/bin/python", "-c", program],
                timeout_s,
            )
        ),
    )


def validate_probe(result: Probe, params: Parameters) -> None:
    rows = result["lookups"]
    if len(rows) != 3 or any(row["status"] != 200 for row in rows):
        raise RuntimeError("isolated reference probes failed")
    if rows[0].get("dataset_id") != params["dataset_id"]:
        raise RuntimeError("isolated dataset identity differs")
    if rows[1].get("conditions_status") not in (
        "fully_interpreted",
        "not_yet_interpreted",
    ):
        raise RuntimeError("isolated conditions are not available")


def await_ready(name: str, params: Parameters) -> Probe:
    deadline = time.monotonic() + 120
    result: Probe = {"lookups": [], "cgroup": {}, "api_process_bytes": {}}
    for _ in range(120):
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0:
            break
        if available_mib() < 500:
            raise RuntimeError("host headroom fell below observation floor")
        try:
            result = probe(name, min(50, remaining_s))
        except (RuntimeError, subprocess.TimeoutExpired):
            time.sleep(0.5)
            continue
        if len(result["lookups"]) == 3 and all(
            row["status"] == 200 for row in result["lookups"]
        ):
            validate_probe(result, params)
            return result
        time.sleep(0.5)
    rows = cast(list[dict[str, object]], json.loads(run(["docker", "inspect", name])))
    state = cast(dict[str, object], rows[0]["State"])
    detail = {
        "running": state["Running"],
        "oom_killed": state["OOMKilled"],
        "exit_code": state["ExitCode"],
        "last_probe": result["lookups"],
        "observed_memory": result["api_process_bytes"],
    }
    raise RuntimeError(
        "isolated API failed bounded readiness/lookups: " + json.dumps(detail)
    )


def start_container(name: str, bundle: Path, params: Parameters) -> None:
    _ = run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "64",
            "--mount",
            f"type=bind,source={bundle},target=/app/data,readonly",
            "-e",
            "MINOS_REFERENCE_BUNDLE=/app/data",
            "-e",
            "MINOS_REFERENCE_DATABASE_SHA256=" + params["database_sha256"],
            params["image"],
        ]
    )


def measure(params: Parameters, stream: BinaryIO) -> dict[str, object]:
    if not re.fullmatch(
        r"ghcr.io/krzysztofbury/minos-health-reference-api@sha256:[a-f0-9]{64}",
        params["image"],
    ):
        raise RuntimeError("image must be an exact API digest")
    for value, length in (
        (params["revision"], 40),
        (params["database_sha256"], 64),
        (params["dataset_id"], 64),
    ):
        if not re.fullmatch(r"[a-f0-9]{" + str(length) + r"}", value):
            raise RuntimeError("invalid measurement identity")
    before = snapshot()
    compose_before = Path("/opt/minos/compose.yaml").read_bytes()
    memory_before = available_mib()
    if memory_before < 800:
        raise RuntimeError("insufficient memory for isolated observation")
    images = cast(
        list[dict[str, object]],
        json.loads(run(["docker", "image", "inspect", params["image"]])),
    )
    config = cast(dict[str, object], images[0]["Config"])
    labels = cast(dict[str, str], config["Labels"])
    if labels["org.opencontainers.image.revision"] != params["revision"]:
        raise RuntimeError("installed image revision differs")
    root = Path(tempfile.mkdtemp(prefix="minos-reference-measure-"))
    root.chmod(0o755)
    name = "minos-measure-" + root.name.removeprefix("minos-reference-measure-")
    try:
        bundle = unpack(stream, root, params["database_sha256"])
        start = time.perf_counter()
        start_container(name, bundle, params)
        result = await_ready(name, params)
        startup_ms = (time.perf_counter() - start) * 1000
        warmed = probe(name)
        validate_probe(warmed, params)
        return {
            **params,
            "startup_and_first_probes_ms": startup_ms,
            "first": result,
            "warm": warmed,
            "available_before_mib": memory_before,
            "available_during_mib": available_mib(),
            "cpu_limit": 1,
            "memory_limit_mib": 512,
            "scope": "one isolated startup and sequential reference probes, not load suitability",
        }
    finally:
        # Stop the exact owned container before removing its mounted files.
        ids = run(["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"]).split()
        if ids:
            _ = run(["docker", "rm", "--force", name])
        shutil.rmtree(root)
        if (
            snapshot() != before
            or Path("/opt/minos/compose.yaml").read_bytes() != compose_before
        ):
            raise RuntimeError(
                "live service identities or Compose changed during observation"
            )


def interrupted(_signum: int, _frame: FrameType | None) -> None:
    raise RuntimeError("measurement interrupted")


if __name__ == "__main__":
    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        _ = signal.signal(signum, interrupted)
    try:
        parameters = cast(Parameters, json.loads(base64.urlsafe_b64decode(sys.argv[1])))
        print(json.dumps(measure(parameters, sys.stdin.buffer), sort_keys=True))
    except Exception as error:
        print(
            json.dumps(
                {
                    "error": str(error)
                    if isinstance(error, RuntimeError)
                    else "isolated measurement failed; activation not attempted"
                }
            )
        )
        sys.exit(1)
