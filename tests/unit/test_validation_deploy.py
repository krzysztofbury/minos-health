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
            "edge": {"image": edge, "networks": {"edge": None, "reference": None}},
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
            input=HOST_PROGRAM.read_bytes(),
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
    with pytest.raises(DeployError, match="operational services changed"):
        _ = host("activate", params)


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


def test_prune_keeps_live_rollback_chain_and_foreign_images(
    site: tuple[Path, Path, Path],
) -> None:
    root, _, _ = site
    host = host_for(site)
    dry = host("prune", {"keep": 1})
    assert dry["images"] == [API + "0" * 64, STALE_WATCHER]
    assert dry["bundles"] == []  # live and its rollback target only
    assert dry["archives"] == [f"validation-stage/{OLD_REVISION}/bundle.tar.gz"]
    assert dry["applied"] is False
    assert "stale-api" in cast(dict[str, object], docker_state(site)["images"])
    applied = host("prune", {"keep": 1, "apply": True})
    assert applied["applied"] is True
    remaining = set(cast(dict[str, object], docker_state(site)["images"]))
    assert remaining == {"live-api", "live-edge", "older-api", "older-edge", "watcher",
                         "maintenance", "local-build"}  # fmt: skip
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
    assert result["status"] == "rolled_back"
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


def test_host_program_parses_as_host_python() -> None:
    # The production host runs Ubuntu 24.04's Python 3.12.
    _ = ast.parse(HOST_PROGRAM.read_text(), feature_version=(3, 12))
