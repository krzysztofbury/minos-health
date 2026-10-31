"""Bounded mixed load on the packaged API, locally or capped on the public site (#42).

`local` runs the release images on a pinned bundle in a disposable Compose
project at the production API limits, records startup time and cgroup memory,
then drives browser-shaped and MCP requests at rising concurrency. `public`
sends a small, capped mix to the live site and stops at the first server error
or unreadable answer. Reports hold route kinds, statuses, timings and memory,
never queries, codes or bodies.
"""

import argparse
import json
import math
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import cast, final
from urllib.parse import urlsplit

from scripts.check_reference_web import get_json
from scripts.render_validation_compose import api_service
from scripts.smoke_reference_web import docker

type Json = dict[str, object]

PUBLIC_BASE = "https://minoshealth.pl"
# Public runs stay far below one visitor-minute of edge capacity: concurrency
# at most the API's four leases, and MCP under its 60 per minute client limit.
PUBLIC_PHASES = ((1, 40), (4, 120))
PUBLIC_MCP_CALLS = 12
LOCAL_PHASES = ((1, 60), (4, 240), (8, 240), (16, 240))
DEADLINE_ANSWERS = frozenset({"query_timeout", "evaluation_timeout"})
SERVICE_DATE = "2026-10-02"
GROUP_BODY: Json = {
    "schema_version": 1,
    "service_date": SERVICE_DATE,
    "reporting_month": "2026-10-01",
    "encounter": {
        "admitted_on": "2026-09-30",
        "discharged_on": "2026-10-02",
        "age_completed_years": 51,
        "hospitalization_type": "hospitalization",
        "product_reporting": "complete",
        "principal_diagnosis": {"code": "K40.9", "hospitalization_level": True},
        "stays": [
            {
                "identifier": "surgery",
                "department": "4500",
                "admitted_on": "2026-09-30",
                "discharged_on": "2026-10-02",
            }
        ],
        "procedures": [
            {"code": "53.03", "performed_on": "2026-10-01", "stay_id": "surgery"}
        ],
    },
}
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-11-25",
}


@dataclass(frozen=True)
class Step:
    kind: str
    route: str
    body: Json | None = None
    mcp: bool = False


def workload(releases: Json) -> list[Step]:
    """One browser-shaped cycle: a search fan-out, detail pages, a grouping."""
    by_system = {
        str(item["system"]): str(item["release_id"])
        for item in cast(list[Json], releases["releases"])
    }

    def search(system: str, query: str) -> Step:
        body: Json = {"system": system, "query": query, "limit": 10}
        if system != "jgp":
            body["release_id"] = by_system[system]
        return Step("search", "/api/v1/search", body)

    steps = [
        search("icd10-pl", "zapalenie płuc"),
        search("icd9-pl", "przepuklina"),
        search("jgp", "przepuklina"),
        search("icd10-pl", "E11"),
        Step("concept", f"/api/v1/concepts/icd10-pl/{by_system['icd10-pl']}/J18.9"),
        Step("jgp_group", f"/api/v1/jgp/{SERVICE_DATE}/groups/F72"),
        Step("group", "/api/v1/group", GROUP_BODY),
    ]
    if "icd11-pl" in by_system:
        steps.append(search("icd11-pl", "cholera"))
    return steps


def mcp_steps() -> list[Step]:
    def call(name: str, arguments: Json) -> Step:
        return Step(
            "mcp_" + name,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            mcp=True,
        )

    return [
        call("search_codes", {"query": "zapalenie płuc"}),
        call("get_code", {"system": "icd10-pl", "code": "J18.9"}),
        call("group_case", {"case": GROUP_BODY["encounter"]}),
    ]


@dataclass(frozen=True)
class Sample:
    kind: str
    status: int
    ms: float
    # A closed error code from the API or a tool, never message text.
    error: str | None


def send(base: str, step: Step, client: str) -> Sample:
    # Cloudflare refuses Python's default urllib agent on the public site.
    headers = {"User-Agent": "minos-load-measurement"}
    if step.mcp:
        headers = headers | MCP_HEADERS
    if client:
        headers = headers | {"CF-Connecting-IP": client}
    began = time.perf_counter()
    try:
        status, answer, elapsed = get_json(
            base, step.route, step.body, headers=headers, timeout_s=20
        )
    except OSError, ValueError:
        # A timeout, reset or unreadable body is a failed request, not a crash.
        return Sample(step.kind, 0, (time.perf_counter() - began) * 1000, "transport")
    if step.mcp:
        result = cast(Json, answer.get("result") or {})
        failed = status != 200 or bool(result.get("isError"))
        payload = cast(Json, result.get("structuredContent") or answer)
    else:
        failed = status != 200 or not answer
        payload = answer
    if not failed:
        return Sample(step.kind, status, elapsed, None)
    code = cast(Json, payload.get("error") or {}).get("code")
    # Edge-generated refusals carry no JSON code.
    return Sample(
        step.kind,
        status,
        elapsed,
        code if isinstance(code, str) else f"http_{status}",
    )


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return round(ordered[max(math.ceil(len(ordered) * share) - 1, 0)], 1)


def summarise(samples: list[Sample]) -> Json:
    kinds: Json = {}
    for kind in sorted({item.kind for item in samples}):
        rows = [item for item in samples if item.kind == kind]
        statuses: dict[str, int] = {}
        for item in rows:
            statuses[str(item.status)] = statuses.get(str(item.status), 0) + 1
        errors: dict[str, int] = {}
        for item in rows:
            if item.error is not None:
                errors[item.error] = errors.get(item.error, 0) + 1
        served = [item.ms for item in rows if item.status == 200 and not item.error]
        kinds[kind] = {
            "count": len(rows),
            "statuses": statuses,
            "errors": errors,
            "p50_ms": percentile(served, 0.5) if served else None,
            "p95_ms": percentile(served, 0.95) if served else None,
            "max_ms": round(max(served), 1) if served else None,
        }
    return kinds


def phase(
    base: str,
    steps: list[Step],
    concurrency: int,
    count: int,
    *,
    stop_on_error: bool,
    distinct_clients: bool,
) -> Json:
    stop = Event()

    def one(index: int) -> Sample | None:
        if stop.is_set():
            return None
        # Local runs emulate distinct agents behind Cloudflare; the edge keys
        # the MCP limit on this header. Public runs never set it.
        client = f"198.51.100.{index % concurrency + 1}" if distinct_clients else ""
        sample = send(base, steps[index % len(steps)], client)
        # The API's closed deadline answers are designed shedding (#42); any
        # other 5xx, such as the edge's own 504, or a transport failure stops.
        if stop_on_error and (
            sample.status == 0
            or (sample.status >= 500 and sample.error not in DEADLINE_ANSWERS)
        ):
            stop.set()
        return sample

    began = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        samples = [item for item in executor.map(one, range(count)) if item]
    return {
        "concurrency": concurrency,
        "requests": len(samples),
        "stopped_early": stop.is_set(),
        "seconds": round(time.perf_counter() - began, 1),
        "kinds": summarise(samples),
    }


def cgroup(container: str) -> Json:
    script = (
        "import json;r=lambda f:open('/sys/fs/cgroup/'+f).read();"
        "s=dict(l.split() for l in r('memory.stat').splitlines());"
        "e=dict(l.split() for l in r('memory.events').splitlines());"
        "print(json.dumps({'peak_mib':int(r('memory.peak'))>>20,"
        "'current_mib':int(r('memory.current'))>>20,'anon_mib':int(s['anon'])>>20,"
        "'file_mib':int(s['file'])>>20,'max_events':int(e['max']),"
        "'oom_kill':int(e['oom_kill'])}))"
    )
    return cast(
        Json,
        json.loads(docker("exec", container, "/build/.venv/bin/python", "-c", script)),
    )


def startup_event(container: str) -> Json:
    for line in docker("logs", container).splitlines():
        if line.startswith('{"event": "api_startup"'):
            return cast(Json, json.loads(line))
    raise ValueError("API startup event missing")


def grouping_phases(container: str, seen: int) -> tuple[Json, int]:
    """Percentiles of the API's per-request grouping phases logged after `seen`."""
    lines = [
        line
        for line in docker("logs", container).splitlines()
        if line.startswith("{") and '"route": "/api/v1/group"' in line
    ]
    values: dict[str, list[float]] = {}
    for line in lines[seen:]:
        logged = cast(Json, json.loads(line)).get("phases")
        if isinstance(logged, dict):
            for name, ms in cast(dict[str, int], logged).items():
                values.setdefault(name, []).append(ms)
    return {
        name: {"p50_ms": percentile(rows, 0.5), "p95_ms": percentile(rows, 0.95)}
        for name, rows in sorted(values.items())
    }, len(lines)


def local(
    bundle: Path, api_image: str, edge_image: str, output: Path, *, api_cpus: float
) -> Json:
    from minos.store.reference import read_artifact_manifest

    manifest = read_artifact_manifest(bundle)
    output.mkdir(parents=True, exist_ok=False)
    compose = output / "compose.json"
    _ = compose.write_text(
        json.dumps(
            {
                "services": {
                    "reference-api": api_service(
                        api_image, bundle.resolve(), manifest.database_sha256
                    )
                    | {"memswap_limit": "512m", "cpus": f"{api_cpus:.2f}"},
                    "edge": {
                        "image": edge_image,
                        "user": "101:101",
                        "read_only": True,
                        "cap_drop": ["ALL"],
                        "security_opt": ["no-new-privileges:true"],
                        "tmpfs": [
                            "/tmp:rw,noexec,nosuid,nodev,size=8m,uid=101,gid=101,mode=0700"
                        ],
                        "mem_limit": "64m",
                        "cpus": "0.50",
                        "pids_limit": 64,
                        "ports": ["127.0.0.1::8080"],
                        "networks": {"reference": None, "client": None},
                        "depends_on": {
                            "reference-api": {"condition": "service_healthy"}
                        },
                    },
                },
                "networks": {"reference": {"internal": True}, "client": {}},
            }
        )
    )
    prefix = (
        "compose",
        "--project-name",
        "minos-load-" + uuid.uuid4().hex[:12],
        "--file",
        str(compose),
    )
    began = time.perf_counter()
    try:
        _ = docker(*prefix, "up", "--wait", "--wait-timeout", "300")
        healthy_s = round(time.perf_counter() - began, 1)
        api = docker(*prefix, "ps", "--quiet", "reference-api").strip()
        edge = docker(*prefix, "ps", "--quiet", "edge").strip()
        inspected = cast(list[Json], json.loads(docker("inspect", edge)))
        ports = cast(
            dict[str, list[dict[str, str]]],
            cast(Json, inspected[0]["NetworkSettings"])["Ports"],
        )
        base = f"http://127.0.0.1:{ports['8080/tcp'][0]['HostPort']}"
        startup = {
            "healthy_s": healthy_s,
            "event": startup_event(api),
            "memory": cgroup(api),
        }
        status, releases, _ = get_json(base, "/api/v1/releases")
        if status != 200:
            raise ValueError("releases unavailable")
        steps = workload(releases) + mcp_steps()
        phases: list[Json] = []
        seen = grouping_phases(api, 0)[1]
        for concurrency, count in LOCAL_PHASES:
            measured = phase(
                base,
                steps,
                concurrency,
                count,
                stop_on_error=False,
                distinct_clients=True,
            )
            measured["grouping_phases"], seen = grouping_phases(api, seen)
            phases.append(measured)
        ready = get_json(base, "/readyz")[0]
        return {
            "mode": "local",
            "dataset_id": releases.get("dataset_id"),
            "api_limits": {"cpus": api_cpus, "memory_mib": 512, "swap": False},
            "startup": startup,
            "phases": phases,
            "after": {"readyz": ready, "memory": cgroup(api)},
        }
    finally:
        _ = docker(*prefix, "down", "--volumes", "--remove-orphans")


def public(base: str) -> Json:
    if urlsplit(base).scheme != "https":
        raise ValueError("public runs use the HTTPS site address")
    status, releases, _ = get_json(
        base, "/api/v1/releases", headers={"User-Agent": "minos-load-measurement"}
    )
    if status != 200:
        raise ValueError(f"releases unavailable: {status}")
    steps = workload(releases)
    phases = [
        phase(
            base,
            steps,
            concurrency,
            count,
            stop_on_error=True,
            distinct_clients=False,
        )
        for concurrency, count in PUBLIC_PHASES
    ]
    mcp = phase(
        base,
        mcp_steps(),
        1,
        PUBLIC_MCP_CALLS,
        stop_on_error=True,
        distinct_clients=False,
    )
    return {
        "mode": "public",
        "dataset_id": releases.get("dataset_id"),
        "phases": [*phases, mcp],
        "after": {
            "readyz": get_json(
                base, "/readyz", headers={"User-Agent": "minos-load-measurement"}
            )[0]
        },
    }


@final
class Arguments(argparse.Namespace):
    command: str = ""
    bundle: Path = Path()
    api_image: str = ""
    edge_image: str = ""
    output: Path = Path()
    api_cpus: float = 1.0
    base: str = PUBLIC_BASE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("local")
    _ = command.add_argument("--bundle", type=Path, required=True)
    _ = command.add_argument("--api-image", required=True)
    _ = command.add_argument("--edge-image", required=True)
    _ = command.add_argument("--output", type=Path, required=True)
    # The host CPU is several times slower than a workstation core; a smaller
    # share approximates it (#222). Production limits stay one CPU.
    _ = command.add_argument("--api-cpus", type=float, default=1.0)
    command = commands.add_parser("public")
    _ = command.add_argument("--base", default=PUBLIC_BASE)
    _ = command.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(namespace=Arguments())
    run: Callable[[], Json] = (
        (
            lambda: local(
                arguments.bundle,
                arguments.api_image,
                arguments.edge_image,
                arguments.output,
                api_cpus=arguments.api_cpus,
            )
        )
        if arguments.command == "local"
        else (lambda: public(arguments.base))
    )
    report = run()
    target = (
        arguments.output / "report.json"
        if arguments.command == "local"
        else arguments.output
    )
    with target.open("x") as stream:
        _ = stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
