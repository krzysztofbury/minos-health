import json
from pathlib import Path
from typing import cast, final

import pytest

from scripts import validation_host as host


@final
class Clock:
    now: float = 0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@final
class Startup:
    def __init__(self) -> None:
        self.clock = Clock()
        self.launched: dict[str, float] = {}
        self.delays = {"reference-api": 4, "edge": 2}
        self.probe_cost = 0.0
        self.launch_cost = 0.0
        self.deadlines: list[float] = []
        self.health_seen: list[str] = []
        self.fault = ""
        self.reuse = False
        self.retired: list[float] = []
        self.retire_bounds: list[float] = []
        self.retire_cost = 0.0
        self.log_reads: list[list[str]] = []
        self.log_read_cost = 0.0

    def run(self, arguments: list[str], timeout_s: float) -> str:
        # Only the failed-candidate log read reaches run(); inspection is faked.
        assert timeout_s <= host.DIAGNOSTIC_TIMEOUT_S
        self.log_reads.append(arguments)
        self.clock.sleep(self.log_read_cost)
        return "\n".join(
            [
                '{"event": "comparison_preload", "status": "completed"}',
                "Traceback (most recent call last): PRIVATE_PATH=/opt/minos/reference",
                '{"event": "api_startup_failed", "error_class": "MemoryError"}',
            ]
        )

    def retire(self, _layout: host.Layout, api: str, deadline: float) -> list[str]:
        assert api == "reference-api"
        assert self.clock.now < deadline
        self.retired.append(self.clock.now)
        self.retire_bounds.append(deadline)
        self.clock.sleep(self.retire_cost)
        return ["reference-api-blue"]

    def command(
        self, arguments: list[str], *, timeout_s: float | None = None
    ) -> dict[str, object]:
        assert timeout_s is not None
        assert self.clock.now + timeout_s <= 10
        self.clock.sleep(min(self.launch_cost, timeout_s))
        self.launched[arguments[-1]] = self.clock.now
        return {
            "status": "completed" if self.launch_cost <= timeout_s else "timeout",
            "exit_code": 0,
            "output_bytes": 17,
            "failure_classes": [],
        }

    def inspect(
        self, _layout: host.Layout, service: str, deadline: float
    ) -> host.StartupState | None:
        self.deadlines.append(deadline)
        self.clock.sleep(min(self.probe_cost, max(0, deadline - self.clock.now)))
        if self.clock.now >= deadline:
            raise host.HostError("inspection deadline exhausted")
        if service not in self.launched:
            return (
                host.StartupState(service + "-old", 2, True, False, "healthy")
                if self.reuse
                else None
            )
        age = self.clock.now - self.launched[service]
        health = "healthy" if age >= self.delays[service] else "unhealthy"
        self.health_seen.append(health)
        state = host.StartupState(
            service + ("-old" if self.reuse else "-new"),
            2 if self.reuse else 0,
            True,
            False,
            health,
        )
        if service == "reference-api":
            match self.fault:
                case "exit":
                    return state._replace(running=False)
                case "oom":
                    return state._replace(oom_killed=True)
                case "restart":
                    return state._replace(restarts=state.restarts + 1)
                case "missing":
                    return None
                case "no-healthcheck":
                    return state._replace(health="none")
                case "inspection":
                    raise host.HostError("PRIVATE_INSPECTION_OUTPUT")
                case "replacement" if age > 0:
                    return state._replace(identifier="replacement")
                case "lost-readiness" if "edge" in self.launched:
                    return state._replace(health="unhealthy")
                case _:
                    pass
        return state


@pytest.fixture
def startup(monkeypatch: pytest.MonkeyPatch) -> Startup:
    scenario = Startup()
    monkeypatch.setattr(host, "UP_WAIT_TIMEOUT_S", 10)
    monkeypatch.setattr(host, "time", scenario.clock)
    monkeypatch.setattr(host, "bounded_command", scenario.command)
    monkeypatch.setattr(host, "startup_state", scenario.inspect)
    monkeypatch.setattr(host, "retire_superseded", scenario.retire)
    monkeypatch.setattr(host, "run", scenario.run)
    return scenario


def layout(root: Path) -> host.Layout:
    services: dict[str, dict[str, object]] = {"reference-api": {}, "edge": {}}
    _ = (root / "compose.yaml").write_text(json.dumps({"services": services}))
    return host.Layout(root, "startup")


@pytest.mark.parametrize("reuse", [False, True])
def test_initial_unhealthy_can_recover_without_weakening_real_health(
    startup: Startup, tmp_path: Path, reuse: bool
) -> None:
    startup.reuse = reuse

    result = host.compose_result(layout(tmp_path))

    assert result["status"] == "completed"
    assert list(startup.launched) == ["reference-api", "edge"]
    assert startup.launched["edge"] >= 4
    assert startup.clock.now == 6
    assert "unhealthy" in startup.health_seen
    assert result["output_bytes"] == 34


def test_api_and_edge_share_one_deadline(startup: Startup, tmp_path: Path) -> None:
    startup.delays = {"reference-api": 6, "edge": 6}

    result = host.compose_result(layout(tmp_path))

    assert result["status"] == "startup_deadline"
    assert result["phase"] == "edge"
    assert result["failure_classes"] == ["timeout"]
    assert startup.clock.now == 10
    assert set(startup.deadlines) == {10}


def test_command_and_inspection_time_counts_toward_deadline(
    startup: Startup, tmp_path: Path
) -> None:
    startup.launch_cost = 2
    startup.probe_cost = 0.5

    result = host.compose_result(layout(tmp_path))

    assert result["status"] in ("timeout", "startup_deadline")
    assert startup.clock.now == 10
    assert set(startup.deadlines) == {10}


@pytest.mark.parametrize(
    "fault",
    [
        "exit",
        "oom",
        "restart",
        "missing",
        "no-healthcheck",
        "inspection",
        "replacement",
    ],
)
def test_process_or_ambiguous_state_fails_without_waiting_out_grace(
    startup: Startup, tmp_path: Path, fault: str
) -> None:
    startup.fault = fault

    result = host.compose_result(layout(tmp_path))

    assert result["status"] != "completed"
    assert "edge" not in startup.launched
    assert startup.retired == []
    assert startup.clock.now < 4
    assert "PRIVATE_INSPECTION_OUTPUT" not in str(result)


@pytest.mark.parametrize(
    ("fault", "running", "oom_killed", "restarts"),
    [("exit", False, False, 0), ("oom", True, True, 0), ("restart", True, False, 1)],
)
def test_failed_start_keeps_candidate_evidence_without_raw_logs(
    startup: Startup,
    tmp_path: Path,
    fault: str,
    running: bool,
    oom_killed: bool,
    restarts: int,
) -> None:
    startup.fault = fault

    result = host.compose_result(layout(tmp_path))

    assert result["status"] == "startup_process_failed"
    # The cgroup file of a synthetic container does not exist: None, not failure.
    assert result["candidate"] == {
        "running": running,
        "oom_killed": oom_killed,
        "restarts": restarts,
        "error_class": "MemoryError",
        "memory_events": None,
    }
    assert startup.log_reads == [
        ["docker", "logs", "--tail", "200", "reference-api-new"]
    ]
    assert "candidate_id" not in result
    assert "PRIVATE_PATH" not in json.dumps(result)


def test_a_slow_evidence_read_never_turns_a_failed_start_into_a_timeout(
    startup: Startup, tmp_path: Path
) -> None:
    # The log read can outlast the grace window; the process failure and its
    # evidence are what #198 needs, not a generic deadline (#232).
    startup.fault = "oom"
    startup.log_read_cost = host.DIAGNOSTIC_TIMEOUT_S

    result = host.compose_result(layout(tmp_path))

    assert startup.clock.now >= 10
    assert result["status"] == "startup_process_failed"
    assert result["failure_classes"] == ["process_failure"]
    candidate = cast(dict[str, object], result["candidate"])
    assert (candidate["oom_killed"], candidate["error_class"]) == (True, "MemoryError")


def test_startup_error_class_takes_only_a_valid_class_name() -> None:
    assert host.startup_error_class("") is None
    assert (
        host.startup_error_class(
            "\n".join(
                [
                    '{"event": "api_startup_failed", "error_class": "ArtifactError"}',
                    "not json",
                    '{"event": "api_startup_failed", "error_class": "OSError"}',
                ]
            )
        )
        == "OSError"
    )
    assert (
        host.startup_error_class(
            '{"event": "api_startup_failed", "error_class": "/opt/minos/x y"}'
        )
        is None
    )


def test_memory_events_requires_the_counters() -> None:
    text = "low 0\nhigh 0\nmax 102\noom 1\noom_kill 1\n"
    assert host.memory_events(text) == {"max": 102, "oom": 1, "oom_kill": 1}
    assert host.memory_events("max 102\n") is None
    assert host.memory_events("max x\noom 0\noom_kill 0\n") is None


def test_api_must_stay_ready_while_edge_starts(
    startup: Startup, tmp_path: Path
) -> None:
    startup.fault = "lost-readiness"

    result = host.compose_result(layout(tmp_path))

    assert result["status"] != "completed"
    assert result["phase"] == "edge"
    assert startup.clock.now == 4


def test_superseded_api_retires_only_after_the_new_edge_is_ready(
    startup: Startup, tmp_path: Path
) -> None:
    result = host.compose_result(layout(tmp_path))

    assert result["status"] == "completed"
    assert result["overlap"] is True
    assert result["retired"] == ["reference-api-blue"]
    assert startup.retired == [6]


def test_without_overlap_superseded_api_retires_before_the_target_starts(
    startup: Startup, tmp_path: Path
) -> None:
    startup.launch_cost = 1

    result = host.compose_result(layout(tmp_path), overlap=False)

    assert result["status"] == "completed"
    assert result["overlap"] is False
    assert startup.retired == [0]
    assert startup.launched["reference-api"] == 1


def test_retirement_has_its_own_bound_after_a_slow_startup(
    startup: Startup, tmp_path: Path
) -> None:
    startup.delays = {"reference-api": 4, "edge": 4}
    startup.retire_cost = 5

    result = host.compose_result(layout(tmp_path))

    assert result["status"] == "completed"
    assert startup.retired == [8]
    assert startup.retire_bounds == [8 + host.RETIRE_TIMEOUT_S]
    assert startup.clock.now > host.UP_WAIT_TIMEOUT_S
