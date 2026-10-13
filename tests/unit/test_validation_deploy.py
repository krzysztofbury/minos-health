import ast
import base64
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from scripts import deploy_validation, validation_host
from scripts.deploy_validation import (
    DeployError,
    Plan,
    Release,
    bundle_archive,
    parse_host_answer,
)
from scripts.validation_host import HostError, swap_identities

ROOT = Path(__file__).parents[2]
HOST_PROGRAM = ROOT / "scripts" / "validation_host.py"
API = "ghcr.io/krzysztofbury/minos-health-reference-api@sha256:"
EDGE = "ghcr.io/krzysztofbury/minos-health-validation-edge@sha256:"
OLD_REVISION, NEW_REVISION = "1" * 40, "2" * 40
OLD_API, OLD_EDGE = API + "a" * 64, EDGE + "b" * 64
NEW_API, NEW_EDGE = API + "c" * 64, EDGE + "d" * 64
OLDER_API, OLDER_EDGE = API + "e" * 64, EDGE + "f" * 64
OLD_DB, OLDER_DB = "3" * 64, "4" * 64
MAINTENANCE = "docker.io/nginxinc/nginx-unprivileged@sha256:" + "5" * 64
WATCHER = "ghcr.io/krzysztofbury/minos-health-watcher@sha256:" + "6" * 64
STALE_WATCHER = "ghcr.io/krzysztofbury/minos-health-watcher@sha256:" + "7" * 64

type Host = Callable[[str, dict[str, object]], dict[str, object]]


def compose(root: Path, api: str, edge: str, database: str) -> dict[str, object]:
    return {
        "name": "minos",
        "services": {
            "watcher": {"image": WATCHER},
            "cloudflared": {"image": "cloudflare/cloudflared@sha256:" + "8" * 64},
            "edge": {
                "image": edge,
                "networks": {"edge": None, "reference": None},
                "volumes": [
                    {
                        "type": "bind",
                        "source": "/var/lib/minos/public",
                        "target": "/var/lib/minos/public",
                        "read_only": True,
                    }
                ],
            },
            "reference-api": {
                "image": api,
                "healthcheck": {
                    "test": ["CMD", "python", "probe.py"],
                    "interval": "10s",
                    "timeout": "3s",
                    "retries": 3,
                    "start_period": "20s",
                },
                "environment": {"MINOS_REFERENCE_DATABASE_SHA256": database},
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(root / "reference" / database),
                        "target": "/app/data",
                        "read_only": True,
                    }
                ],
            },
        },
    }


def deployment(database: str) -> dict[str, str]:
    return {
        "api_image": NEW_API,
        "edge_image": NEW_EDGE,
        "source_revision": NEW_REVISION,
        "database_sha256": database,
    }


PREVIOUS = {"api_image": OLD_API, "edge_image": OLD_EDGE, "database_sha256": OLD_DB}


@pytest.fixture
def site(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A host root with a live release, its rollback and a new local bundle."""
    root = tmp_path / "opt-minos"
    for database in (OLD_DB, OLDER_DB):
        (root / "reference" / database).mkdir(parents=True)
    live = compose(root, OLD_API, OLD_EDGE, OLD_DB)
    _ = (root / "compose.yaml").write_text(json.dumps(live, indent=2) + "\n")
    stage = root / "validation-stage" / OLD_REVISION
    stage.mkdir(parents=True)
    _ = (stage / "deployment.json").write_text(
        json.dumps({"api_image": OLD_API, "edge_image": OLD_EDGE,
                    "source_revision": OLD_REVISION, "database_sha256": OLD_DB})
    )  # fmt: skip
    _ = (stage / "bundle.tar.gz").write_bytes(b"stale staged archive")
    rollback = root / "validation-rollback" / OLD_REVISION / "compose.yaml"
    rollback.parent.mkdir(parents=True)
    _ = rollback.write_text(json.dumps(compose(root, OLDER_API, OLDER_EDGE, OLDER_DB)))
    (root / "rollback").mkdir()
    _ = (root / "rollback" / "compose.yaml").write_text(
        json.dumps({"image": MAINTENANCE})
    )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name, content in (
        ("manifest.json", b"{}"),
        ("minos.db", b"new database"),
        ("review.txt", b"review"),
    ):
        _ = (bundle / name).write_bytes(content)
    images: dict[str, dict[str, object]] = {
        "live-api": {"references": [OLD_API], "size": 300, "revision": OLD_REVISION},
        "live-edge": {"references": [OLD_EDGE], "size": 80, "revision": OLD_REVISION},
        "older-api": {"references": [OLDER_API], "size": 300, "revision": "9" * 40},
        "older-edge": {"references": [OLDER_EDGE], "size": 80, "revision": "9" * 40},
        "stale-api": {
            "references": [API + "0" * 64],
            "size": 300,
            "revision": "0" * 40,
        },
        "watcher": {"references": [WATCHER], "size": 139, "revision": ""},
        "stale-watcher": {"references": [STALE_WATCHER], "size": 139, "revision": ""},
        "maintenance": {"references": [MAINTENANCE], "size": 90, "revision": ""},
        "local-build": {"references": [], "size": 5, "revision": ""},
    }
    state: dict[str, object] = {
        "calls": [],
        "fail_up": 0,
        "sequence": 0,
        "registry": {NEW_API: NEW_REVISION, NEW_EDGE: NEW_REVISION},
        "images": images,
        "containers": {
            "api-0": {"service": "reference-api", "image": "live-api", "restarts": 0,
                      "health": "healthy"},
            "edge-0": {"service": "edge", "image": "live-edge", "restarts": 0,
                       "health": "healthy"},
            "watcher-0": {"service": "watcher", "image": "watcher", "restarts": 2,
                          "health": "healthy"},
        },
    }  # fmt: skip
    _ = (tmp_path / "docker-state.json").write_text(json.dumps(state))
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    docker = bin_directory / "docker"
    _ = docker.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tests/fixtures/fake_docker.py"}" "$@"\n'
    )
    docker.chmod(0o755)
    return root, bundle, tmp_path


def host_for(site: tuple[Path, Path, Path]) -> Host:
    root, _, base = site

    def call(command: str, params: dict[str, object]) -> dict[str, object]:
        encoded = base64.urlsafe_b64encode(
            json.dumps({**params, "root": str(root)}).encode()
        ).decode()
        completed = subprocess.run(
            [sys.executable, "-", command, encoded],
            input=(
                "import sys, types\nmodule = types.ModuleType('host_fixture')\n"
                f"exec({HOST_PROGRAM.read_text()!r}, module.__dict__)\n"
                # Keep real subprocess/CLI/receipt coverage without five-minute
                # waits for deliberately unhealthy synthetic containers.
                "module.UP_WAIT_TIMEOUT_S = 5\nsys.exit(module.main(sys.argv))\n"
            ).encode(),
            capture_output=True,
            timeout=60,
            check=False,
            env={
                **os.environ,
                "PATH": f"{base / 'bin'}:{os.environ['PATH']}",
                "FAKE_DOCKER_STATE": str(base / "docker-state.json"),
            },
        )
        return parse_host_answer(completed.stdout.decode(), command)

    return call


def docker_state(site: tuple[Path, Path, Path]) -> dict[str, object]:
    return cast(
        dict[str, object], json.loads((site[2] / "docker-state.json").read_text())
    )


def staged(site: tuple[Path, Path, Path], host: Host) -> dict[str, object]:
    root, bundle, base = site
    plan = host("inspect", {})
    database = hashlib.sha256((bundle / "minos.db").read_bytes()).hexdigest()
    params: dict[str, object] = {
        "deployment": deployment(database),
        "previous": PREVIOUS,
        "compose_sha256": plan["compose_sha256"],
    }
    prepared = host("prepare", params)
    archive_sha256 = bundle_archive(bundle, base / "upload.tar.gz")
    _ = Path(cast(str, prepared["archive"])).write_bytes(
        (base / "upload.tar.gz").read_bytes()
    )
    _ = host("stage", {**params, "archive_sha256": archive_sha256})
    assert root.is_dir()
    return params


@pytest.mark.parametrize("start_period", ["20s", "120s"])
def test_update_changes_only_validation_services_and_is_resumable(
    site: tuple[Path, Path, Path],
    start_period: str,
) -> None:
    root, _, _ = site
    live = cast(dict[str, object], json.loads((root / "compose.yaml").read_bytes()))
    services = cast(dict[str, dict[str, object]], live["services"])
    cast(dict[str, object], services["reference-api"]["healthcheck"])[
        "start_period"
    ] = start_period
    _ = (root / "compose.yaml").write_text(json.dumps(live))
    host = host_for(site)
    before = (root / "compose.yaml").read_bytes()
    params = staged(site, host)
    database = cast(dict[str, str], params["deployment"])["database_sha256"]
    installed = root / "reference" / database
    assert stat.S_IMODE(installed.stat().st_mode) == 0o755
    assert {stat.S_IMODE(path.stat().st_mode) for path in installed.iterdir()} == {
        0o444
    }
    stage = root / "validation-stage" / NEW_REVISION
    assert not (stage / "bundle.tar.gz").exists()
    assert (
        root / "validation-rollback" / NEW_REVISION / "compose.yaml"
    ).read_bytes() == before
    rendered = cast(dict[str, object], json.loads((stage / "compose.json").read_text()))
    expected = cast(dict[str, object], json.loads(before))
    services = cast(dict[str, dict[str, object]], expected["services"])
    services["reference-api"]["image"] = NEW_API
    services["edge"]["image"] = NEW_EDGE
    api = services["reference-api"]
    cast(dict[str, object], api["healthcheck"])["start_period"] = "120s"
    cast(dict[str, str], api["environment"])["MINOS_REFERENCE_DATABASE_SHA256"] = (
        database
    )
    cast(list[dict[str, object]], api["volumes"])[0]["source"] = str(installed)
    cast(list[dict[str, object]], api["volumes"]).append(
        {
            "type": "bind",
            "source": "/var/lib/minos/public",
            "target": "/var/lib/minos/public",
            "read_only": True,
        }
    )
    assert rendered == expected
    assert (root / "compose.yaml").read_bytes() == before  # staging is not activation

    assert host("prepare", params)["bundle_installed"] is True
    _ = host("stage", {**params, "archive_sha256": "0" * 64})  # resumed: bundle exists
    activated = host("activate", params)
    assert activated["status"] == "activated"
    assert (root / "compose.yaml").read_bytes() == (stage / "compose.json").read_bytes()
    assert (stage / "ob").read_bytes() == (stage / "oa").read_bytes()
    assert host("activate", params)["status"] == "already_active"
    stored = host("receipt", {"deployment": params["deployment"], "receipt": {"x": 1}})
    content = cast(dict[str, object], stored["content"])
    assert content["activated_at"] == (stage / "activated-at.txt").read_text().strip()
    inspected = host("inspect", {})
    assert inspected["live_revision"] == NEW_REVISION
    assert inspected["operational"] == [["watcher", "watcher-0", "2"]]


def test_failed_activation_restores_previous_compose(
    site: tuple[Path, Path, Path],
) -> None:
    root, _, base = site
    host = host_for(site)
    before = (root / "compose.yaml").read_bytes()
    params = staged(site, host)
    state = docker_state(site)
    state["fail_up"] = 1
    _ = (base / "docker-state.json").write_text(json.dumps(state))
    with pytest.raises(DeployError, match="previous release was restored"):
        _ = host("activate", params)
    assert (root / "compose.yaml").read_bytes() == before
    assert not (root / "validation-stage" / NEW_REVISION / "activated-at.txt").exists()


def test_operational_restart_during_activation_fails(
    site: tuple[Path, Path, Path],
) -> None:
    _, _, base = site
    host = host_for(site)
    params = staged(site, host)
    state = docker_state(site)
    state["restart_watcher_on_up"] = True
    _ = (base / "docker-state.json").write_text(json.dumps(state))
    with pytest.raises(DeployError, match="rollback incomplete") as failed:
        _ = host("activate", params)
    assert (
        cast(dict[str, object], failed.value.details["rollback"])["operational"]
        == "changed"
    )


def test_changed_live_compose_and_wrong_image_build_are_refused(
    site: tuple[Path, Path, Path],
) -> None:
    root, bundle, base = site
    host = host_for(site)
    plan = host("inspect", {})
    database = hashlib.sha256((bundle / "minos.db").read_bytes()).hexdigest()
    params: dict[str, object] = {
        "deployment": deployment(database),
        "previous": PREVIOUS,
        "compose_sha256": plan["compose_sha256"],
    }
    _ = (root / "compose.yaml").write_text((root / "compose.yaml").read_text() + " ")
    with pytest.raises(DeployError, match="changed since plan"):
        _ = host("prepare", params)
    _ = (root / "compose.yaml").write_text((root / "compose.yaml").read_text()[:-1])
    state = docker_state(site)
    cast(dict[str, str], state["registry"])[NEW_API] = "9" * 40
    _ = (base / "docker-state.json").write_text(json.dumps(state))
    prepared = host("prepare", params)
    archive_sha256 = bundle_archive(bundle, base / "upload.tar.gz")
    _ = Path(cast(str, prepared["archive"])).write_bytes(
        (base / "upload.tar.gz").read_bytes()
    )
    with pytest.raises(DeployError, match="not the reviewed build"):
        _ = host("stage", {**params, "archive_sha256": archive_sha256})
    with pytest.raises(DeployError, match="not exact"):
        _ = host("prepare", {**params, "deployment": {**deployment(database),
                                                      "api_image": API[:-8] + ":latest"}})  # fmt: skip


@pytest.mark.parametrize("watcher_record", [None, "previous.json", "candidate.json"])
def test_prune_keeps_live_rollback_chain_and_foreign_images(
    site: tuple[Path, Path, Path],
    watcher_record: str | None,
) -> None:
    root, _, _ = site
    if watcher_record is not None:
        record = root / "watcher-stage" / NEW_REVISION / watcher_record
        record.parent.mkdir(parents=True)
        _ = record.write_text(
            json.dumps({"services": {"watcher": {"image": STALE_WATCHER}}})
        )
    host = host_for(site)
    dry = host("prune", {"keep": 1})
    assert dry["images"] == [API + "0" * 64] + (
        [] if watcher_record else [STALE_WATCHER]
    )
    assert dry["bundles"] == []  # live and its rollback target only
    assert dry["archives"] == [f"validation-stage/{OLD_REVISION}/bundle.tar.gz"]
    assert dry["applied"] is False
    assert "stale-api" in cast(dict[str, object], docker_state(site)["images"])
    applied = host("prune", {"keep": 1, "apply": True})
    assert applied["applied"] is True
    remaining = set(cast(dict[str, object], docker_state(site)["images"]))
    assert remaining == {"live-api", "live-edge", "older-api", "older-edge", "watcher",
                         "maintenance", "local-build"} | ({"stale-watcher"} if watcher_record else set())  # fmt: skip
    assert host("prune", {"keep": 1})["images"] == []
    assert (root / "reference" / OLDER_DB).is_dir()
    with pytest.raises(DeployError, match="keep must be"):
        _ = host("prune", {"keep": True})


def test_prune_drops_bundles_outside_the_retained_chain(
    site: tuple[Path, Path, Path],
) -> None:
    root, _, _ = site
    stale = "9" * 64
    (root / "reference" / stale).mkdir()
    (root / "reference" / f".{stale}.partial").mkdir()
    dry = host_for(site)("prune", {"keep": 1})
    assert dry["bundles"] == [f".{stale}.partial", stale]


def test_mutating_commands_are_exclusive(site: tuple[Path, Path, Path]) -> None:
    root, _, _ = site
    with (root / ".validation-deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(DeployError, match="another validation deployment"):
            _ = host_for(site)("prune", {"keep": 1})


def test_rollback_restores_retained_compose(site: tuple[Path, Path, Path]) -> None:
    root, _, _ = site
    host = host_for(site)
    params = staged(site, host)
    before = (root / "validation-rollback" / NEW_REVISION / "compose.yaml").read_bytes()
    _ = host("activate", params)
    result = host("rollback", {"revision": NEW_REVISION})
    assert result["status"] == "restored"
    assert result["validation_health"] == {
        "reference-api": "healthy",
        "edge": "healthy",
    }
    assert result["public_ingress"] == "not_checked"
    assert (root / "compose.yaml").read_bytes() == before


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("api_image", API + "9" * 64, "live API image"),
        ("edge_image", EDGE + "9" * 64, "live edge image"),
        ("database_sha256", "9" * 64, "live database digest"),
    ],
)
def test_identity_swap_refuses_unplanned_live_state(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    live = compose(tmp_path, OLD_API, OLD_EDGE, OLD_DB)
    with pytest.raises(HostError, match=message):
        _ = swap_identities(
            live, {**PREVIOUS, field: value}, deployment("2" * 64), tmp_path
        )


def _writable(volumes: list[dict[str, object]]) -> None:
    volumes[0]["read_only"] = False


def _duplicated(volumes: list[dict[str, object]]) -> None:
    volumes.append(deepcopy(volumes[0]))


def _moved(volumes: list[dict[str, object]]) -> None:
    volumes[0]["source"] = "/elsewhere"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_writable, "read-only"),
        (_duplicated, "exactly one data bind"),
        (_moved, "bundle bind"),
    ],
)
def test_identity_swap_requires_one_read_only_planned_bind(
    tmp_path: Path,
    mutate: Callable[[list[dict[str, object]]], None],
    message: str,
) -> None:
    live = compose(tmp_path, OLD_API, OLD_EDGE, OLD_DB)
    services = cast(dict[str, dict[str, object]], live["services"])
    mutate(cast(list[dict[str, object]], services["reference-api"]["volumes"]))
    with pytest.raises(HostError, match=message):
        _ = swap_identities(live, PREVIOUS, deployment("2" * 64), tmp_path)


def test_identity_swap_refuses_unknown_startup_policy(tmp_path: Path) -> None:
    live = compose(tmp_path, OLD_API, OLD_EDGE, OLD_DB)
    services = cast(dict[str, dict[str, object]], live["services"])
    cast(dict[str, object], services["reference-api"]["healthcheck"])[
        "start_period"
    ] = "5m"
    with pytest.raises(HostError, match="startup grace is unexpected"):
        _ = swap_identities(
            live, PREVIOUS, deployment("2" * 64), tmp_path / "reference"
        )


@pytest.mark.parametrize("mutation", ["writable", "private", "missing", "api_conflict"])
def test_source_pages_mount_only_the_exact_public_metadata_boundary(
    tmp_path: Path, mutation: str
) -> None:
    live = compose(tmp_path, OLD_API, OLD_EDGE, OLD_DB)
    services = cast(dict[str, dict[str, object]], live["services"])
    public = cast(list[dict[str, object]], services["edge"]["volumes"])
    if mutation == "writable":
        public[0]["read_only"] = False
    elif mutation == "private":
        public[0]["source"] = "/var/lib/minos/control"
    elif mutation == "missing":
        public.clear()
    else:
        cast(list[dict[str, object]], services["reference-api"]["volumes"]).append(
            {**public[0], "source": "/var/lib/minos/archive"}
        )
    with pytest.raises(HostError, match="public metadata bind differs"):
        _ = swap_identities(
            live, PREVIOUS, deployment("2" * 64), tmp_path / "reference"
        )


def test_bundle_archive_is_byte_reproducible(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in ("manifest.json", "minos.db", "review.txt"):
        _ = (bundle / name).write_bytes(name.encode())
    first = bundle_archive(bundle, tmp_path / "first.tar.gz")
    os.utime(bundle / "minos.db", (1, 1))
    assert bundle_archive(bundle, tmp_path / "second.tar.gz") == first


def test_release_and_plan_accept_only_exact_identities() -> None:
    release = {
        "source_revision": NEW_REVISION,
        "run_id": 7,
        "run_attempt": 1,
        "api_image": NEW_API,
        "edge_image": NEW_EDGE,
    }
    _ = Release.model_validate_json(json.dumps(release))
    for field, value in (
        ("api_image", API[:-8] + ":latest"),
        ("edge_image", "ghcr.io/other/edge@sha256:" + "d" * 64),
        ("source_revision", "main"),
    ):
        with pytest.raises(ValidationError):
            _ = Release.model_validate_json(json.dumps({**release, field: value}))
    plan = {
        "deployment": deployment("2" * 64),
        "previous": {**PREVIOUS, "source_revision": None},
        "compose_sha256": "0" * 64,
        "run_id": 7,
        "dataset_id": "1" * 64,
        "bundle": "/tmp/bundle",
        "rank_sample": ["89.02", 4],
        "planned_at": "2026-10-02T00:00:00Z",
    }
    assert Plan.model_validate_json(json.dumps(plan)).rank_sample == ("89.02", 4)


def test_host_refusal_and_empty_answers_surface_as_errors() -> None:
    with pytest.raises(DeployError, match="refused: HostError: nope"):
        _ = parse_host_answer('{"ok": false, "error": "HostError: nope"}\n', "stage")
    with pytest.raises(DeployError, match="returned nothing"):
        _ = parse_host_answer("\n", "stage")


@pytest.mark.parametrize("program", [HOST_PROGRAM, ROOT / "scripts/watcher_host.py"])
def test_host_program_parses_as_host_python(program: Path) -> None:
    # The production host runs Ubuntu 24.04's Python 3.12.
    _ = ast.parse(program.read_text(), feature_version=(3, 12))


@pytest.mark.parametrize("change_operational", [False, True])
def test_partial_rollback_retains_diagnostics_and_original_recovery_baseline(
    site: tuple[Path, Path, Path],
    change_operational: bool,
) -> None:
    root, _, base = site
    host = host_for(site)
    params = staged(site, host)
    _ = host("activate", params)
    state = docker_state(site)
    state["fail_after_api"] = True
    _ = (base / "docker-state.json").write_text(json.dumps(state))
    with pytest.raises(DeployError, match="rollback incomplete") as failed:
        _ = host("rollback", {"revision": NEW_REVISION})
    assert failed.value.details["validation_health"] == {
        "reference-api": "healthy",
        "edge": "created",
    }
    assert failed.value.details["operational"] == "unchanged"
    assert failed.value.details["recovery"] == "rollback --revision " + NEW_REVISION
    assert (root / "compose.yaml").read_bytes() == (
        root / "validation-rollback" / NEW_REVISION / "compose.yaml"
    ).read_bytes()
    stage = root / "validation-stage" / NEW_REVISION
    record = cast(
        dict[str, object], json.loads((stage / "rollback-operations.log").read_bytes())
    )
    assert record["status"] == "command_failed" and record["exit_code"] == 1
    assert record["failure_classes"] == ["unhealthy"]
    assert cast(int, record["output_bytes"]) > 0
    assert stat.S_IMODE((stage / "rollback-operations.log").stat().st_mode) == 0o600
    assert (
        "PRIVATE_TOKEN"
        not in str(failed.value)
        + json.dumps(failed.value.details)
        + (stage / "rollback-operations.log").read_text()
    )
    baseline = (stage / "rollback-ob.json").read_bytes()
    state = docker_state(site)
    state["fail_after_api"] = False
    if change_operational:
        cast(dict[str, dict[str, object]], state["containers"])["watcher-0"][
            "restarts"
        ] = 3
    _ = (base / "docker-state.json").write_text(json.dumps(state))
    if change_operational:
        with pytest.raises(DeployError, match="rollback incomplete") as retry:
            _ = host("rollback", {"revision": NEW_REVISION})
        assert retry.value.details["operational"] == "changed"
    else:
        result = host("rollback", {"revision": NEW_REVISION})
        assert result["status"] == "restored" and result["identities_match"] is True
        assert result["validation_health"] == {
            "reference-api": "healthy",
            "edge": "healthy",
        }
    assert (stage / "rollback-ob.json").read_bytes() == baseline
    assert (
        json.loads((stage / "rollback-operations.previous.log").read_bytes()) == record
    )


@pytest.mark.parametrize("failure", ["omit_edge", "unhealthy_edge", "wrong_edge_image"])
def test_successful_compose_exit_does_not_override_runtime_state(
    site: tuple[Path, Path, Path],
    failure: str,
) -> None:
    root, _, base = site
    host = host_for(site)
    params = staged(site, host)
    _ = host("activate", params)
    state = docker_state(site)
    state[failure] = True
    _ = (base / "docker-state.json").write_text(json.dumps(state))
    with pytest.raises(DeployError, match="rollback incomplete") as failed:
        _ = host("rollback", {"revision": NEW_REVISION})
    expected_health = {
        "omit_edge": "missing",
        "unhealthy_edge": "unhealthy",
        "wrong_edge_image": "healthy",
    }[failure]
    assert failed.value.details["validation_health"] == {
        "reference-api": "healthy",
        "edge": expected_health,
    }
    assert failed.value.details["identities_match"] is (failure == "unhealthy_edge")
    assert (
        json.loads(
            (
                root / "validation-stage" / NEW_REVISION / "rollback-result.json"
            ).read_bytes()
        )["status"]
        == "incomplete"
    )


def test_rollback_refuses_unrelated_live_compose(site: tuple[Path, Path, Path]) -> None:
    root, _, _ = site
    host = host_for(site)
    _ = staged(site, host)
    current = (root / "compose.yaml").read_bytes() + b" "
    _ = (root / "compose.yaml").write_bytes(current)
    with pytest.raises(DeployError, match="neither the selected candidate"):
        _ = host("rollback", {"revision": NEW_REVISION})
    assert (root / "compose.yaml").read_bytes() == current


def test_remote_nonzero_exit_preserves_host_failure_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variables = tmp_path / "host.yml"
    _ = variables.write_text(
        "ansible_host: localhost\nansible_port: 22\nansible_user: root\n"
    )
    monkeypatch.setattr(deploy_validation, "HOST_VARS", variables)
    details = {"validation_health": {"reference-api": "healthy", "edge": "created"}}

    def refused(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            [],
            2,
            json.dumps(
                {"ok": False, "error": "rollback incomplete", "details": details}
            ).encode(),
            b"PRIVATE_SSH_PATH",
        )

    monkeypatch.setattr(subprocess, "run", refused)
    with pytest.raises(DeployError, match="rollback incomplete") as failed:
        _ = deploy_validation.Host().call("rollback", {"revision": NEW_REVISION})
    assert failed.value.details == details
    assert "PRIVATE_SSH_PATH" not in str(failed.value)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ('{"ok":true,"result":{}}', 2),
        ('{"ok":true}', 0),
        ('["PRIVATE_PAYLOAD"]', 2),
        ("bad PRIVATE_PAYLOAD", 2),
    ],
)
def test_invalid_or_conflicting_host_answers_never_report_success(
    body: str, code: int
) -> None:
    with pytest.raises(DeployError) as failed:
        _ = parse_host_answer(body, "rollback", code)
    assert "PRIVATE_PAYLOAD" not in str(failed.value)


@pytest.mark.parametrize("mode", ["timeout", "output_limit"])
def test_operational_command_capture_is_bounded_and_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    docker = tmp_path / "docker"
    code = (
        'import time; print("PRIVATE_PAYLOAD", flush=True); time.sleep(60)'
        if mode == "timeout"
        else 'import sys; sys.stderr.write("PRIVATE_PAYLOAD" * 25000)'
    )
    _ = docker.write_text(f"#!{sys.executable}\n{code}\n")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(validation_host, "COMPOSE_TIMEOUT_S", 0.2)
    record = validation_host.bounded_command(["docker", "compose", "up"])
    assert record["status"] == mode
    assert (
        cast(int, record["output_bytes"]) <= validation_host.MAX_COMMAND_OUTPUT + 4096
    )
    encoded = json.dumps(record)
    assert len(encoded) < 1024 and "PRIVATE_PAYLOAD" not in encoded


@pytest.mark.parametrize("healthy", [False, True])
def test_rollback_success_event_requires_public_ingress(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], healthy: bool
) -> None:
    def restored_call(
        _self: deploy_validation.Host, _command: str, _params: dict[str, object]
    ) -> dict[str, object]:
        return {"status": "restored", "public_ingress": "not_checked"}

    monkeypatch.setattr(deploy_validation.Host, "call", restored_call)

    def initialize(_self: deploy_validation.Host) -> None:
        pass

    def get(_url: str) -> tuple[int, bytes]:
        return (200, b'{"status":"ready"}') if healthy else (503, b"")

    monkeypatch.setattr(deploy_validation.Host, "__init__", initialize)
    monkeypatch.setattr(
        deploy_validation,
        "get",
        get,
    )
    arguments = deploy_validation.Arguments()
    arguments.command, arguments.revision = "rollback", NEW_REVISION
    if healthy:
        deploy_validation.main(arguments)
        event = cast(dict[str, object], json.loads(capsys.readouterr().out))
        assert event["event"] == "rolled_back" and event["public_ingress"] == "healthy"
    else:
        with pytest.raises(DeployError, match="public ingress"):
            deploy_validation.main(arguments)
        assert "rolled_back" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (200, b"<html><a href='mailto:kontakt@minoshealth.pl'>", None),
        (
            200,
            b'<script src="/cdn-cgi/scripts/x/cloudflare-static/email-decode.min.js">',
            "rewritten by the edge proxy",
        ),
        (503, b"", "not served"),
    ],
)
def test_verify_home_refuses_a_proxy_rewritten_homepage(
    monkeypatch: pytest.MonkeyPatch, status: int, body: bytes, error: str | None
) -> None:
    def get(_url: str) -> tuple[int, bytes]:
        return status, body

    monkeypatch.setattr(deploy_validation, "get", get)
    if error is None:
        assert deploy_validation.verify_home("https://example.test") == (
            "served without edge rewriting"
        )
    else:
        with pytest.raises(DeployError, match=error):
            _ = deploy_validation.verify_home("https://example.test")
