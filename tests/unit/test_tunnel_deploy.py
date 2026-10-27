import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from scripts import deploy_tunnel, tunnel_host, validation_host
from scripts.deploy_validation import DeployError, parse_host_answer
from tests.fixtures.fake_docker import service_config
from tests.unit.test_validation_deploy import ROOT, docker_state
from tests.unit.test_validation_deploy import site as site

OLD_TUNNEL = "docker.io/cloudflare/cloudflared@sha256:" + "8" * 64
NEW_TUNNEL = "docker.io/cloudflare/cloudflared@sha256:" + "9" * 64
VERSION = "2026.10.4"


@pytest.fixture
def tunnel(site: tuple[Path, Path, Path]) -> tuple[Path, Path, Path]:
    """The validation host with all five reviewed services healthy."""
    root, _, base = site
    _, document = validation_host.load_compose(root / "compose.yaml")
    selected = tunnel_host.service(document)
    selected.update(
        image=OLD_TUNNEL,
        command=tunnel_host.COMMAND,
        user=tunnel_host.USER,
        volumes=[
            {
                "type": "bind",
                "source": "/etc/minos/cloudflared/config.yml",
                "target": tunnel_host.CONFIG_TARGET,
                "read_only": True,
            }
        ],
    )
    _ = (root / "compose.yaml").write_bytes(validation_host.canonical(document))
    state = docker_state(site)
    containers = cast(dict[str, dict[str, object]], state["containers"])
    containers["cloudflared-0"] = {
        "service": "cloudflared",
        "image": "old-tunnel",
        "health": "healthy",
        "restarts": 0,
        "config": service_config(selected),
    }
    containers["uptime-kuma-0"] = {
        "service": "uptime-kuma",
        "image": "maintenance",
        "health": "healthy",
        "restarts": 0,
    }
    images = cast(dict[str, dict[str, object]], state["images"])
    images["old-tunnel"] = {"references": [OLD_TUNNEL], "size": 60, "revision": ""}
    cast(dict[str, str], state["registry"])[NEW_TUNNEL] = ""
    state["cloudflared_version"] = VERSION
    _ = (base / "docker-state.json").write_text(json.dumps(state))
    return site


def params(site: tuple[Path, Path, Path]) -> dict[str, object]:
    root = site[0]
    return {
        "image": NEW_TUNNEL,
        "version": VERSION,
        "compose_sha256": hashlib.sha256(
            (root / "compose.yaml").read_bytes()
        ).hexdigest(),
        "root": str(root),
    }


def call(
    site: tuple[Path, Path, Path], command: str, values: dict[str, object]
) -> dict[str, object]:
    base = site[2]
    payload = base64.urlsafe_b64encode(json.dumps(values).encode()).decode()
    result = subprocess.run(
        [sys.executable, "-", command, payload],
        input=deploy_tunnel.program(),
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


def others(site: tuple[Path, Path, Path]) -> dict[str, dict[str, object]]:
    containers = cast(dict[str, dict[str, object]], docker_state(site)["containers"])
    return {
        key: row for key, row in containers.items() if row["service"] != "cloudflared"
    }


def ups(site: tuple[Path, Path, Path]) -> list[list[str]]:
    return [
        args
        for args in cast(list[list[str]], docker_state(site)["calls"])
        if "up" in args
    ]


def test_only_the_tunnel_image_changes_and_rollback_restores_the_bytes(
    tunnel: tuple[Path, Path, Path],
) -> None:
    compose = tunnel[0] / "compose.yaml"
    original = compose.read_bytes()
    before = others(tunnel)
    values = params(tunnel)
    assert call(tunnel, "stage", values)["event"] == "tunnel_staged"
    # Resuming a completed stage is accepted against identical records.
    assert call(tunnel, "stage", values)["event"] == "tunnel_staged"
    assert compose.read_bytes() == original
    runs = [
        args
        for args in cast(list[list[str]], docker_state(tunnel)["calls"])
        if args[0] == "run"
    ]
    assert runs and all(
        {"--read-only", "--rm"} <= set(args)
        and args[args.index("--network") + 1] == "none"
        and args[args.index("--user") + 1] == tunnel_host.USER
        and NEW_TUNNEL in args
        for args in runs
    )
    activated = call(tunnel, "activate", values)
    assert activated == {
        "event": "tunnel_activated",
        "image": NEW_TUNNEL,
        "version": VERSION,
        "preserved": True,
        "healthy": True,
        "cloudflared_image_matches": True,
    }
    expected = cast(dict[str, object], json.loads(original))
    tunnel_host.service(expected)["image"] = NEW_TUNNEL
    assert json.loads(compose.read_bytes()) == expected
    assert others(tunnel) == before
    assert all(
        args[-1] == "cloudflared" and "--no-deps" in args and "--wait" in args
        for args in ups(tunnel)
    )
    assert call(tunnel, "rollback", values)["event"] == "tunnel_rolled_back"
    assert compose.read_bytes() == original
    assert others(tunnel) == before


@pytest.mark.parametrize(
    "change", ["version", "platform", "ingress", "compose", "unhealthy"]
)
def test_staging_refuses_before_any_live_change(
    tunnel: tuple[Path, Path, Path], change: str
) -> None:
    values = params(tunnel)
    state = docker_state(tunnel)
    containers = cast(dict[str, dict[str, object]], state["containers"])
    if change == "version":
        state["cloudflared_version"] = "2026.9.1"
    elif change == "platform":
        state["platform"] = "linux/arm64"
    elif change == "ingress":
        state["invalid_ingress"] = True
    elif change == "compose":
        values["compose_sha256"] = "0" * 64
    else:
        containers["edge-0"]["health"] = "unhealthy"
    _ = (tunnel[2] / "docker-state.json").write_text(json.dumps(state))
    original = (tunnel[0] / "compose.yaml").read_bytes()
    with pytest.raises(DeployError):
        _ = call(tunnel, "stage", values)
    assert (tunnel[0] / "compose.yaml").read_bytes() == original
    stage = tunnel_host.directory(
        validation_host.Layout(tunnel[0], "minos"),
        NEW_TUNNEL,
        cast(str, values["compose_sha256"]),
    )
    assert not (stage / "prepared.json").exists()
    assert not ups(tunnel)
    # Activation cannot proceed without staged evidence.
    with pytest.raises(DeployError):
        _ = call(tunnel, "activate", values)
    assert not ups(tunnel)


def test_failed_activation_restores_the_previous_tunnel(
    tunnel: tuple[Path, Path, Path],
) -> None:
    original = (tunnel[0] / "compose.yaml").read_bytes()
    values = params(tunnel)
    _ = call(tunnel, "stage", values)
    state = docker_state(tunnel)
    state["fail_after_cloudflared"] = 1
    _ = (tunnel[2] / "docker-state.json").write_text(json.dumps(state))
    with pytest.raises(DeployError, match="tunnel activation failed") as failure:
        _ = call(tunnel, "activate", values)
    assert failure.value.details["recovery"] == {
        "preserved": True,
        "healthy": True,
        "cloudflared_image_matches": True,
    }
    assert (tunnel[0] / "compose.yaml").read_bytes() == original


def test_failed_automatic_rollback_leaves_explicit_rollback_possible(
    tunnel: tuple[Path, Path, Path],
) -> None:
    original = (tunnel[0] / "compose.yaml").read_bytes()
    values = params(tunnel)
    _ = call(tunnel, "stage", values)
    state = docker_state(tunnel)
    state["fail_after_cloudflared"] = 2
    _ = (tunnel[2] / "docker-state.json").write_text(json.dumps(state))
    with pytest.raises(DeployError, match="tunnel activation failed") as failure:
        _ = call(tunnel, "activate", values)
    assert failure.value.details["recovery"] == {"restored": False}
    assert (tunnel[0] / "compose.yaml").read_bytes() == original
    assert call(tunnel, "rollback", values)["cloudflared_image_matches"] is True


def test_rollback_is_judged_against_services_as_they_run_now(
    tunnel: tuple[Path, Path, Path],
) -> None:
    original = (tunnel[0] / "compose.yaml").read_bytes()
    values = params(tunnel)
    _ = call(tunnel, "stage", values)
    _ = call(tunnel, "activate", values)
    state = docker_state(tunnel)
    cast(dict[str, dict[str, object]], state["containers"])["watcher-0"]["restarts"] = 3
    _ = (tunnel[2] / "docker-state.json").write_text(json.dumps(state))
    assert call(tunnel, "rollback", values)["preserved"] is True
    assert (tunnel[0] / "compose.yaml").read_bytes() == original


def test_activation_refuses_a_changed_preserved_service_before_up(
    tunnel: tuple[Path, Path, Path],
) -> None:
    values = params(tunnel)
    _ = call(tunnel, "stage", values)
    state = docker_state(tunnel)
    cast(dict[str, dict[str, object]], state["containers"])["edge-0"]["restarts"] = 1
    _ = (tunnel[2] / "docker-state.json").write_text(json.dumps(state))
    with pytest.raises(DeployError, match="preserved services changed"):
        _ = call(tunnel, "activate", values)
    assert not ups(tunnel)


@pytest.mark.parametrize("changed", ["none", "push", "main", "digest", "ci", "pr"])
def test_plan_binds_the_digest_to_the_pin_on_remote_main(
    monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    template = (ROOT / deploy_tunnel.TEMPLATE).read_text()
    pinned = re.search(
        r"docker\.io/cloudflare/cloudflared@sha256:[0-9a-f]{64}", template
    )
    assert pinned is not None
    head = "a" * 40

    def run(arguments: list[str]) -> str:
        if arguments[1] == "ls-remote":
            return ("b" * 40 if changed == "main" else head) + "\trefs/heads/main\n"
        if arguments[1] == "show":
            assert arguments[2] == f"{head}:{deploy_tunnel.TEMPLATE}"
            return template
        if arguments[:2] == ["gh", "run"]:
            assert arguments[arguments.index("--commit") + 1] == head
            event = {"push": "push", "pr": "pull_request"}.get(
                changed, "workflow_dispatch"
            )
            failed = {"conclusion": "failure", "event": "workflow_dispatch"}
            passed = {"conclusion": "success", "event": event}
            return json.dumps([failed] if changed == "ci" else [failed, passed])
        return ""

    monkeypatch.setattr(deploy_tunnel, "run", run)
    plan = deploy_tunnel.TunnelPlan(
        revision=head,
        image=NEW_TUNNEL if changed == "digest" else pinned.group(0),
        version=VERSION,
        compose_sha256="c" * 64,
    )
    if changed in ("none", "push"):
        deploy_tunnel.reviewed(plan)
    else:
        with pytest.raises(DeployError):
            deploy_tunnel.reviewed(plan)


def test_every_cloudflared_pin_is_the_same_digest() -> None:
    # Dependabot reads none of these files, so a bump must change all of them.
    pins: set[str] = set()
    for path in (
        deploy_tunnel.TEMPLATE,
        "infra/ansible/site.yml",
        ".github/workflows/ci.yml",
    ):
        found = re.findall(
            r"docker\.io/cloudflare/cloudflared@sha256:([0-9a-f]{64})",
            (ROOT / path).read_text(),
        )
        assert found, path
        pins.update(found)
    assert len(pins) == 1


def test_plan_takes_revision_and_image_from_remote_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    head = "a" * 40
    pinned = "docker.io/cloudflare/cloudflared@sha256:" + "7" * 64

    def run(arguments: list[str]) -> str:
        if arguments[1] == "ls-remote":
            return head + "\trefs/heads/main\n"
        if arguments[1] == "show":
            assert arguments[2] == f"{head}:{deploy_tunnel.TEMPLATE}"
            return f"  cloudflared:\n    image: {pinned}\n"
        if arguments[:2] == ["gh", "run"]:
            return json.dumps([{"conclusion": "success", "event": "push"}])
        return ""

    class Inspecting:
        def call(self, command: str, _params: dict[str, object]) -> dict[str, object]:
            assert command == "inspect"
            return {"compose_sha256": "c" * 64}

    monkeypatch.setattr(deploy_tunnel, "run", run)
    monkeypatch.setattr(deploy_tunnel, "Host", Inspecting)
    path = tmp_path / "plan.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["deploy_tunnel", "plan", "--plan", str(path), "--version", VERSION],
    )
    deploy_tunnel.main()
    plan = deploy_tunnel.TunnelPlan.model_validate_json(path.read_bytes())
    assert (plan.revision, plan.image) == (head, pinned)
    printed = cast(dict[str, object], json.loads(capsys.readouterr().out))
    assert printed["revision"] == head and printed["image"] == pinned
