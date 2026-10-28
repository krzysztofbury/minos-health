import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from scripts import deploy_kuma, kuma_host, validation_host
from scripts.deploy_validation import DeployError, parse_host_answer
from tests.fixtures.fake_docker import service_config
from tests.unit.test_tunnel_deploy import tunnel as tunnel
from tests.unit.test_validation_deploy import ROOT, docker_state
from tests.unit.test_validation_deploy import site as site

OLD_KUMA = "docker.io/louislam/uptime-kuma@sha256:" + "a" * 64
NEW_KUMA = "docker.io/louislam/uptime-kuma@sha256:" + "b" * 64
VERSION = "2.5.8"
ORIGINAL = b"original monitors\n"
# Written by the fake on each clean Kuma stop and on each start.
CHECKPOINT = b"checkpoint\n"

type Site = tuple[Path, Path, Path]


@pytest.fixture
def kuma(tunnel: Site) -> Site:
    """The five healthy services with Kuma on its pinned data directory."""
    root, _, base = tunnel
    data = root.parent / "var-lib-minos" / "uptime-kuma"
    data.mkdir(parents=True)
    _ = (data / "kuma.db").write_bytes(ORIGINAL)
    marker = data / kuma_host.MARKER
    _ = marker.write_text(OLD_KUMA + "\n")
    marker.chmod(0o444)
    _, document = validation_host.load_compose(root / "compose.yaml")
    selected: dict[str, object] = {
        "image": OLD_KUMA,
        "user": "10003:10003",
        "volumes": [{"type": "bind", "source": str(data), "target": "/app/data"}],
    }
    cast(dict[str, object], document["services"])["uptime-kuma"] = selected
    _ = (root / "compose.yaml").write_bytes(validation_host.canonical(document))
    state = docker_state(tunnel)
    containers = cast(dict[str, dict[str, object]], state["containers"])
    containers["uptime-kuma-0"].update(
        image="old-kuma", config=service_config(selected)
    )
    images = cast(dict[str, dict[str, object]], state["images"])
    images["old-kuma"] = {"references": [OLD_KUMA], "size": 300, "revision": ""}
    cast(dict[str, str], state["registry"])[NEW_KUMA] = ""
    state["kuma_version"] = VERSION
    _ = (base / "docker-state.json").write_text(json.dumps(state))
    return tunnel


def data(site: Site) -> Path:
    return site[0].parent / "var-lib-minos" / "uptime-kuma"


def params(site: Site) -> dict[str, object]:
    return {
        "image": NEW_KUMA,
        "version": VERSION,
        "compose_sha256": hashlib.sha256(
            (site[0] / "compose.yaml").read_bytes()
        ).hexdigest(),
        "root": str(site[0]),
    }


def call(site: Site, command: str, values: dict[str, object]) -> dict[str, object]:
    base = site[2]
    payload = base64.urlsafe_b64encode(json.dumps(values).encode()).decode()
    result = subprocess.run(
        [sys.executable, "-", command, payload],
        input=deploy_kuma.program(),
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


def update(site: Site, **values: object) -> None:
    state = docker_state(site)
    state.update(values)
    _ = (site[2] / "docker-state.json").write_text(json.dumps(state))


def others(site: Site) -> dict[str, dict[str, object]]:
    containers = cast(dict[str, dict[str, object]], docker_state(site)["containers"])
    return {k: row for k, row in containers.items() if row["service"] != "uptime-kuma"}


def compose_calls(site: Site, verb: str) -> list[list[str]]:
    return [
        args
        for args in cast(list[list[str]], docker_state(site)["calls"])
        if args[0] == "compose" and verb in args
    ]


def marker(site: Site) -> str:
    return (data(site) / kuma_host.MARKER).read_text().strip()


def test_release_replaces_only_kuma_and_rollback_restores_its_data(
    kuma: Site,
) -> None:
    compose = kuma[0] / "compose.yaml"
    original = compose.read_bytes()
    before = others(kuma)
    values = params(kuma)
    assert call(kuma, "stage", values)["event"] == "kuma_staged"
    # Resuming a completed stage is accepted against identical records.
    assert call(kuma, "stage", values)["event"] == "kuma_staged"
    assert compose.read_bytes() == original
    assert not compose_calls(kuma, "stop") and not compose_calls(kuma, "up")
    runs = [
        args
        for args in cast(list[list[str]], docker_state(kuma)["calls"])
        if args[0] == "run"
    ]
    assert any(NEW_KUMA in args for args in runs)
    assert all(
        {"--read-only", "--rm"} <= set(args)
        and args[args.index("--network") + 1] == "none"
        and args[args.index("--user") + 1] == "10003:10003"
        for args in runs
        if NEW_KUMA in args
    )
    activated = call(kuma, "activate", values)
    assert activated == {
        "event": "kuma_activated",
        "image": NEW_KUMA,
        "version": VERSION,
        "preserved": True,
        "healthy": True,
        "image_matches": True,
    }
    expected = cast(dict[str, object], json.loads(original))
    kuma_host.service(expected)["image"] = NEW_KUMA
    assert json.loads(compose.read_bytes()) == expected
    assert marker(kuma) == NEW_KUMA
    # The copy is taken after the stop checkpoint, so the backup holds it.
    copied = ORIGINAL + CHECKPOINT
    assert (data(kuma) / "kuma.db").read_bytes() == copied + f"{NEW_KUMA}\n".encode()
    assert (data(kuma) / kuma_host.MARKER).stat().st_mode & 0o777 == 0o444
    assert others(kuma) == before
    assert all(
        args[-1] == "uptime-kuma" and "--no-deps" in args and "--wait" in args
        for args in compose_calls(kuma, "up")
    )
    rolled_back = call(kuma, "rollback", values)
    assert rolled_back["data_restored"] is True and rolled_back["image"] == OLD_KUMA
    assert compose.read_bytes() == original
    assert marker(kuma) == OLD_KUMA
    # The data is the stopped copy again: only the restarted old image wrote.
    assert (data(kuma) / "kuma.db").read_bytes() == copied + f"{OLD_KUMA}\n".encode()
    assert others(kuma) == before
    stage = kuma_host.directory(
        validation_host.Layout(kuma[0], "minos"),
        NEW_KUMA,
        cast(str, values["compose_sha256"]),
    )
    (replaced,) = stage.glob("replaced-rollback-*")
    assert NEW_KUMA.encode() in (replaced / "kuma.db").read_bytes()


@pytest.mark.parametrize(
    "change",
    ["version", "platform", "image-user", "compose", "unhealthy", "marker", "root"],
)
def test_staging_refuses_before_any_live_change(kuma: Site, change: str) -> None:
    values = params(kuma)
    compose = kuma[0] / "compose.yaml"
    if change == "version":
        update(kuma, kuma_version="2.5.7")
    elif change == "platform":
        update(kuma, platform="linux/arm64")
    elif change == "image-user":
        update(kuma, kuma_user="root")
    elif change == "compose":
        values["compose_sha256"] = "0" * 64
    elif change == "unhealthy":
        state = docker_state(kuma)
        containers = cast(dict[str, dict[str, object]], state["containers"])
        containers["watcher-0"]["health"] = "unhealthy"
        update(kuma, containers=containers)
    elif change == "marker":
        path = data(kuma) / kuma_host.MARKER
        path.chmod(0o644)
        _ = path.write_text(NEW_KUMA + "\n")
    else:
        _, document = validation_host.load_compose(compose)
        kuma_host.service(document)["user"] = "0:0"
        _ = compose.write_bytes(validation_host.canonical(document))
        values = params(kuma)
    original = compose.read_bytes()
    with pytest.raises(DeployError):
        _ = call(kuma, "stage", values)
    assert compose.read_bytes() == original
    # Activation cannot proceed without staged evidence.
    with pytest.raises(DeployError):
        _ = call(kuma, "activate", values)
    assert not compose_calls(kuma, "stop") and not compose_calls(kuma, "up")
    assert (data(kuma) / "kuma.db").read_bytes() == ORIGINAL


def test_failed_activation_restores_the_data_and_a_retry_backs_up_again(
    kuma: Site,
) -> None:
    original = (kuma[0] / "compose.yaml").read_bytes()
    values = params(kuma)
    _ = call(kuma, "stage", values)
    update(kuma, **{"fail_after_uptime-kuma": 1})
    with pytest.raises(DeployError, match="Uptime Kuma activation failed") as failure:
        _ = call(kuma, "activate", values)
    assert failure.value.details["recovery"] == {
        "preserved": True,
        "healthy": True,
        "image_matches": True,
        "data_restored": True,
    }
    assert (kuma[0] / "compose.yaml").read_bytes() == original
    assert marker(kuma) == OLD_KUMA
    recovered = ORIGINAL + CHECKPOINT + f"{OLD_KUMA}\n".encode()
    assert (data(kuma) / "kuma.db").read_bytes() == recovered
    # The retry backs up the data the old image wrote after recovery, so a
    # rollback keeps it instead of rewinding to the first attempt.
    assert call(kuma, "activate", values)["image_matches"] is True
    assert call(kuma, "rollback", values)["data_restored"] is True
    assert (data(kuma) / "kuma.db").read_bytes() == (
        recovered + CHECKPOINT + f"{OLD_KUMA}\n".encode()
    )


@pytest.mark.parametrize("step", ["stop", "marker"])
def test_a_failure_before_kuma_starts_keeps_its_data_and_a_retry_backs_up_again(
    kuma: Site, step: str
) -> None:
    values = params(kuma)
    _ = call(kuma, "stage", values)
    if step == "stop":
        update(kuma, fail_kuma_stop=True)
    else:
        # The copy is taken, then the marker cannot be replaced.
        (data(kuma) / f".{kuma_host.MARKER}.partial").mkdir()
    with pytest.raises(DeployError, match="Uptime Kuma activation failed") as error:
        _ = call(kuma, "activate", values)
    if step == "marker":
        (data(kuma) / f".{kuma_host.MARKER}.partial").rmdir()
    assert error.value.details["recovery"] == {
        "preserved": True,
        "healthy": True,
        "image_matches": True,
        "data_restored": False,
    }
    assert marker(kuma) == OLD_KUMA
    database = (data(kuma) / "kuma.db").read_bytes()
    assert NEW_KUMA.encode() not in database
    # The retry replaces any copy the failed attempt left with the data the
    # old image has written since, and rollback returns exactly that.
    assert call(kuma, "activate", values)["image_matches"] is True
    assert call(kuma, "rollback", values)["data_restored"] is True
    assert (data(kuma) / "kuma.db").read_bytes() == (
        database + CHECKPOINT + f"{OLD_KUMA}\n".encode()
    )


def test_failed_recovery_leaves_explicit_rollback_possible(kuma: Site) -> None:
    original = (kuma[0] / "compose.yaml").read_bytes()
    values = params(kuma)
    _ = call(kuma, "stage", values)
    update(kuma, **{"fail_after_uptime-kuma": 2})
    with pytest.raises(DeployError, match="Uptime Kuma activation failed") as failure:
        _ = call(kuma, "activate", values)
    assert failure.value.details["recovery"] == {"restored": False}
    assert (kuma[0] / "compose.yaml").read_bytes() == original
    # Recovery already put the backup in place; rollback only recreates Kuma.
    rolled_back = call(kuma, "rollback", values)
    assert rolled_back["data_restored"] is False
    assert rolled_back["image_matches"] is True
    assert marker(kuma) == OLD_KUMA
    assert NEW_KUMA.encode() not in (data(kuma) / "kuma.db").read_bytes()


def test_activation_refuses_a_changed_preserved_service_before_stopping(
    kuma: Site,
) -> None:
    values = params(kuma)
    _ = call(kuma, "stage", values)
    state = docker_state(kuma)
    cast(dict[str, dict[str, object]], state["containers"])["edge-0"]["restarts"] = 1
    _ = (kuma[2] / "docker-state.json").write_text(json.dumps(state))
    with pytest.raises(DeployError, match="preserved services changed"):
        _ = call(kuma, "activate", values)
    assert not compose_calls(kuma, "stop") and not compose_calls(kuma, "up")


@pytest.mark.parametrize("pinned", [True, False])
def test_plan_binds_the_digest_to_the_site_pin_on_remote_main(
    monkeypatch: pytest.MonkeyPatch, pinned: bool
) -> None:
    site_yml = (ROOT / deploy_kuma.SITE).read_text()
    found = set(deploy_kuma.PIN.findall(site_yml))
    assert len(found) == 1
    head = "a" * 40

    def run(arguments: list[str]) -> str:
        if arguments[1] == "ls-remote":
            return head + "\trefs/heads/main\n"
        if arguments[1] == "show":
            assert arguments[2] == f"{head}:{deploy_kuma.SITE}"
            return site_yml
        if arguments[:2] == ["gh", "run"]:
            return json.dumps([{"conclusion": "success", "event": "push"}])
        return ""

    monkeypatch.setattr("scripts.deploy_tunnel.run", run)
    plan = deploy_kuma.KumaPlan(
        revision=head,
        image=found.pop() if pinned else NEW_KUMA,
        version=VERSION,
        compose_sha256="c" * 64,
    )
    if pinned:
        deploy_kuma.check(plan)
    else:
        with pytest.raises(DeployError, match="not the pin"):
            deploy_kuma.check(plan)
