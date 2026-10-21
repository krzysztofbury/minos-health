import fcntl
import json
import os
import shutil
from pathlib import Path
from typing import NamedTuple, final

import pytest

from scripts import processor_host as host

_IMAGE = "ghcr.io/krzysztofbury/minos-health-reference-api@sha256:" + "a" * 64
_BUNDLE = "/opt/minos/reference/" + "b" * 64
_ENDPOINT = "https://0123456789abcdef0123456789abcdef.eu.r2.cloudflarestorage.com"


def _compose(
    root: Path,
    *,
    apis: tuple[str, ...] = ("reference-api-green",),
    image: str = _IMAGE,
    **watcher: object,
) -> None:
    services: dict[str, object] = {
        name: {
            "image": image,
            "volumes": [{"type": "bind", "source": _BUNDLE, "target": "/app/data"}],
        }
        for name in apis
    }
    services["watcher"] = {
        "image": "ghcr.io/krzysztofbury/minos-health-watcher@sha256:" + "c" * 64,
        "environment": {
            "MINOS_WATCHER_S3_BUCKET": "minos-archive",
            "MINOS_WATCHER_S3_ENDPOINT": _ENDPOINT,
        },
    } | watcher
    _ = (root / "compose.yaml").write_text(json.dumps({"services": services}))


def test_plan_names_the_live_bundle_its_image_and_the_bucket(tmp_path: Path) -> None:
    _compose(tmp_path)

    assert host.read_plan(tmp_path) == host.Plan(
        _BUNDLE, _IMAGE, "minos-archive", _ENDPOINT
    )


@pytest.mark.parametrize(
    "apis,image,environment",
    [
        (("reference-api-blue", "reference-api-green"), _IMAGE, None),
        ((), _IMAGE, None),
        (
            ("reference-api",),
            "ghcr.io/krzysztofbury/minos-health-reference-api:latest",
            None,
        ),
        (("reference-api",), _IMAGE, {}),
    ],
)
def test_plan_refuses_an_unreviewed_live_model(
    tmp_path: Path,
    apis: tuple[str, ...],
    image: str,
    environment: dict[str, str] | None,
) -> None:
    if environment is None:
        _compose(tmp_path, apis=apis, image=image)
    else:
        _compose(tmp_path, apis=apis, image=image, environment=environment)

    with pytest.raises(host.HostError):
        _ = host.read_plan(tmp_path)


def test_container_mounts_inputs_and_secrets_read_only() -> None:
    arguments = host.container_arguments(
        host.Plan(_BUNDLE, _IMAGE, "minos-archive", _ENDPOINT)
    )

    volumes = [
        arguments[index + 1]
        for index, item in enumerate(arguments)
        if item == "--volume"
    ]
    writable = [volume for volume in volumes if not volume.endswith(":ro")]
    assert writable == [
        "/var/lib/minos/processor/work:/var/lib/minos/work:rw",
        "/var/lib/minos/processor/state:/var/lib/minos/state:rw",
    ]
    assert f"{_BUNDLE}:/var/lib/minos/live:ro" in volumes
    assert {"--read-only", "--init"} <= set(arguments)
    assert arguments[arguments.index("--memory") + 1] == "384m"
    assert arguments[arguments.index("--cpus") + 1] == "0.5"
    # The API image's own entrypoint is the web server.
    image = arguments.index(_IMAGE)
    assert arguments[image - 2 : image + 3] == [
        "--entrypoint",
        "/build/.venv/bin/minos",
        _IMAGE,
        "processor",
        "run",
    ]


class Usage(NamedTuple):
    total: int
    used: int
    free: int


@final
class _Docker:
    def __init__(self, work: Path, failing: str | None = None, start: int = 0) -> None:
        self.work = work
        self.failing = failing
        self.start = start
        self.calls: list[str] = []

    def __call__(self, arguments: list[str], _timeout_s: float) -> int:
        command = " ".join(arguments[1:3])
        self.calls.append(command)
        if command == "start --attach":
            # The container leaves build output behind when it is killed.
            (self.work / "build").mkdir()
            return self.start
        return 1 if command == self.failing else 0


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "opt"
    work = tmp_path / "work"
    root.mkdir()
    work.mkdir()
    _compose(root)
    _ = (work / "left-over").write_bytes(b"x")
    return root, work


@pytest.mark.parametrize("start,outcome", [(0, "completed"), (1, "processor exited 1")])
def test_run_empties_work_and_keeps_only_a_failed_container(
    layout: tuple[Path, Path], start: int, outcome: str
) -> None:
    root, work = layout
    docker = _Docker(work, failing="network inspect", start=start)

    assert host.run_once(root, work, docker) == outcome

    assert docker.calls == [
        "rm --force",
        "network inspect",
        "network create",
        "create --name",
        "network connect",
        "start --attach",
        # A failed processor is kept for its logs.
        *(["rm --force"] if start == 0 else []),
    ]
    assert list(work.iterdir()) == []


def test_failed_create_still_removes_the_container(layout: tuple[Path, Path]) -> None:
    root, work = layout
    docker = _Docker(work, failing="create --name")

    with pytest.raises(host.HostError, match="create failed"):
        _ = host.run_once(root, work, docker)

    assert docker.calls[-1] == "rm --force"
    assert "start --attach" not in docker.calls


def test_busy_deploy_lock_skips_the_run_without_docker(
    layout: tuple[Path, Path],
) -> None:
    root, work = layout
    docker = _Docker(work)
    descriptor = os.open(
        root / ".validation-deploy.lock", os.O_RDWR | os.O_CREAT, 0o600
    )
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        outcome = host.run_once(root, work, docker)
    finally:
        os.close(descriptor)

    assert outcome == "skipped: deployment in progress"
    assert docker.calls == []


def test_low_free_space_refuses_before_the_lock(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, work = layout

    def disk_usage(_path: Path) -> tuple[int, int, int]:
        return Usage(0, 0, host.MIN_FREE_BYTES - 1)

    monkeypatch.setattr(shutil, "disk_usage", disk_usage)
    docker = _Docker(work)

    with pytest.raises(host.HostError, match="free-space"):
        _ = host.run_once(root, work, docker)

    assert docker.calls == []
    assert not (root / ".validation-deploy.lock").exists()
