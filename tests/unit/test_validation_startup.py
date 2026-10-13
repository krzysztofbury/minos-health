from pathlib import Path
from typing import final

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
    return scenario


@pytest.mark.parametrize("reuse", [False, True])
def test_initial_unhealthy_can_recover_without_weakening_real_health(
    startup: Startup, tmp_path: Path, reuse: bool
) -> None:
    startup.reuse = reuse

    result = host.compose_result(host.Layout(tmp_path, "startup"))

    assert result["status"] == "completed"
    assert list(startup.launched) == ["reference-api", "edge"]
    assert startup.launched["edge"] >= 4
    assert startup.clock.now == 6
    assert "unhealthy" in startup.health_seen
    assert result["output_bytes"] == 34


def test_api_and_edge_share_one_deadline(startup: Startup, tmp_path: Path) -> None:
    startup.delays = {"reference-api": 6, "edge": 6}

    result = host.compose_result(host.Layout(tmp_path, "startup"))

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

    result = host.compose_result(host.Layout(tmp_path, "startup"))

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

    result = host.compose_result(host.Layout(tmp_path, "startup"))

    assert result["status"] != "completed"
    assert "edge" not in startup.launched
    assert startup.clock.now < 4
    assert "PRIVATE_INSPECTION_OUTPUT" not in str(result)


def test_api_must_stay_ready_while_edge_starts(
    startup: Startup, tmp_path: Path
) -> None:
    startup.fault = "lost-readiness"

    result = host.compose_result(host.Layout(tmp_path, "startup"))

    assert result["status"] != "completed"
    assert result["phase"] == "edge"
    assert startup.clock.now == 4
