import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from scripts import deploy_watcher, validation_host, watcher_host
from scripts.deploy_validation import DeployError, parse_host_answer
from tests.unit.test_validation_deploy import (
    NEW_REVISION,
    STALE_WATCHER,
    WATCHER,
    docker_state,
)
from tests.unit.test_validation_deploy import (
    site as site,
)


@pytest.fixture
def staged(site: tuple[Path, Path, Path]) -> tuple[Path, Path, Path]:
    root, _, base = site
    content, document = validation_host.load_compose(root / "compose.yaml")
    original = watcher_host.service(document)
    original.update(
        command=["watcher", "run"],
        environment={
            "MINOS_WATCHER_SCHEDULER_PAUSED": "false",
            "MINOS_WATCHER_IMAGE_DIGEST": WATCHER.split("@")[1],
            "MINOS_WATCHER_RUNTIME_IMAGE_DIGEST": WATCHER.split("@")[1],
        },
    )
    content = validation_host.canonical(document)
    _ = (root / "compose.yaml").write_bytes(content)
    state = docker_state(site)
    containers = cast(dict[str, dict[str, object]], state["containers"])
    containers["watcher-0"]["restarts"] = 0
    for service in ("cloudflared", "uptime-kuma"):
        containers[service + "-0"] = {
            "service": service,
            "image": "maintenance",
            "health": "healthy",
            "restarts": 0,
        }
    _ = (base / "docker-state.json").write_text(json.dumps(state))
    directory = root / "watcher-stage" / NEW_REVISION
    directory.mkdir(parents=True)
    _ = (directory / "previous.json").write_bytes(content)
    _ = (directory / "candidate.json").write_bytes(
        validation_host.canonical(watcher_host.render(document, STALE_WATCHER))
    )
    expected = sorted(
        [
            [cast(str, row["service"]), identifier, str(row["restarts"])]
            for identifier, row in containers.items()
            if row["service"] != "watcher"
        ]
    )
    _ = (directory / "baseline.json").write_text(json.dumps(expected))
    _ = (directory / "prepared.json").write_text(
        json.dumps({"revision": NEW_REVISION, "root": str(root)})
    )
    return site


def call(site: tuple[Path, Path, Path], command: str) -> dict[str, object]:
    root, _, base = site
    payload = base64.urlsafe_b64encode(
        json.dumps({"revision": NEW_REVISION, "root": str(root)}).encode()
    ).decode()
    result = subprocess.run(
        [sys.executable, "-", command, payload],
        input=deploy_watcher.program(),
        capture_output=True,
        timeout=30,
        check=False,
        env={
            **os.environ,
            "PATH": f"{base / 'bin'}:{os.environ['PATH']}",
            "FAKE_DOCKER_STATE": str(base / "docker-state.json"),
        },
    )
    return parse_host_answer(result.stdout.decode(), command, result.returncode)


def test_only_watcher_changes_and_explicit_rollback_preserves_original_bytes(
    staged: tuple[Path, Path, Path],
) -> None:
    original = (staged[0] / "compose.yaml").read_bytes()
    before = docker_state(staged)["containers"]
    assert call(staged, "activate")["event"] == "watcher_activated"
    after = cast(dict[str, dict[str, object]], docker_state(staged)["containers"])
    assert "watcher-0" not in after
    assert {key: row for key, row in after.items() if row["service"] != "watcher"} == {
        key: row
        for key, row in cast(dict[str, dict[str, object]], before).items()
        if row["service"] != "watcher"
    }
    assert call(staged, "rollback")["event"] == "watcher_rolled_back"
    assert (staged[0] / "compose.yaml").read_bytes() == original
    calls = cast(list[list[str]], docker_state(staged)["calls"])
    ups = [args for args in calls if "up" in args]
    assert all(args[-1] == "watcher" and "--no-deps" in args for args in ups)


def test_partial_watcher_replacement_restores_prior_image(
    staged: tuple[Path, Path, Path],
) -> None:
    original = (staged[0] / "compose.yaml").read_bytes()
    state = docker_state(staged)
    state["fail_after_watcher"] = True
    _ = (staged[2] / "docker-state.json").write_text(json.dumps(state))
    with pytest.raises(DeployError, match="activation failed") as failure:
        _ = call(staged, "activate")
    assert failure.value.details["recovery"] == {
        "preserved": True,
        "healthy": True,
        "watcher_image_matches": True,
    }
    assert (staged[0] / "compose.yaml").read_bytes() == original


@pytest.mark.parametrize("change", ["compose", "preserved"])
def test_activation_refuses_changed_baseline_before_up(
    staged: tuple[Path, Path, Path], change: str
) -> None:
    if change == "compose":
        with (staged[0] / "compose.yaml").open("ab") as target:
            _ = target.write(b" ")
    else:
        state = docker_state(staged)
        cast(dict[str, dict[str, object]], state["containers"])["edge-0"][
            "restarts"
        ] = 1
        _ = (staged[2] / "docker-state.json").write_text(json.dumps(state))
    with pytest.raises(DeployError):
        _ = call(staged, "activate")
    assert not any(
        "up" in args for args in cast(list[list[str]], docker_state(staged)["calls"])
    )


def test_unrelated_compose_and_changed_preserved_service_cannot_claim_recovery(
    staged: tuple[Path, Path, Path],
) -> None:
    state = docker_state(staged)
    state["restart_edge_on_up"] = True
    _ = (staged[2] / "docker-state.json").write_text(json.dumps(state))
    with pytest.raises(DeployError) as failure:
        _ = call(staged, "activate")
    assert failure.value.details["recovery"] == {"restored": False}
    with pytest.raises(DeployError):
        _ = call(staged, "rollback")
    with (staged[0] / "compose.yaml").open("ab") as target:
        _ = target.write(b" ")
    with pytest.raises(DeployError, match="unrelated live compose"):
        _ = call(staged, "rollback")


@pytest.mark.parametrize("changed", ["none", "main", "publication", "ci", "digest"])
def test_release_gate_binds_successful_main_build_to_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    plan = deploy_watcher.WatcherPlan(
        revision=NEW_REVISION,
        image=STALE_WATCHER,
        compose_sha256="a" * 64,
        publish_run=123,
    )
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    def run(arguments: list[str]) -> str:
        if arguments[:2] == ["git", "ls-remote"]:
            return (
                "3" * 40 if changed == "main" else NEW_REVISION
            ) + "\trefs/heads/main"
        if arguments[:3] == ["gh", "run", "view"]:
            return json.dumps(
                {
                    "headSha": NEW_REVISION,
                    "conclusion": "failure" if changed == "publication" else "success",
                    "workflowName": "Publish watcher image",
                    "headBranch": "main",
                    "event": "workflow_dispatch",
                }
            )
        if arguments[:3] == ["gh", "run", "list"]:
            return json.dumps(
                [
                    {
                        "conclusion": "failure" if changed == "ci" else "success",
                        "event": "push",
                    }
                ]
            )
        assert arguments[:3] == ["gh", "run", "download"]
        _ = (Path(arguments[-1]) / "watcher-release.json").write_text(
            json.dumps(
                {
                    "revision": NEW_REVISION,
                    "image": WATCHER if changed == "digest" else STALE_WATCHER,
                    "publish_run": 123,
                    "run_attempt": 1,
                }
            )
        )
        return ""

    monkeypatch.setattr(deploy_watcher, "run", run)
    if changed == "none":
        deploy_watcher.reviewed(plan)
    else:
        with pytest.raises(DeployError):
            deploy_watcher.reviewed(plan)
    assert list(tmp_path.iterdir()) == []
