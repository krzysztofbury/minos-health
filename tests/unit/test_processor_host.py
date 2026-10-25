import fcntl
import json
import os
import shutil
from pathlib import Path
from typing import NamedTuple, cast, final

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
    assert writable == ["/var/lib/minos/processor:/var/lib/minos/processor:rw"]
    assert f"{_BUNDLE}:/var/lib/minos/live:ro" in volumes
    assert {"--read-only", "--init"} <= set(arguments)
    assert arguments[arguments.index("--memory") + 1] == "512m"
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
    def __init__(
        self,
        work: Path,
        failing: str | None = None,
        start: int = 0,
        starts: list[int] | None = None,
    ) -> None:
        self.work = work
        self.failing = failing
        self.start = start
        # Exit codes of successive containers; the default applies after them.
        self.starts = list(starts or [])
        self.calls: list[str] = []

    def __call__(self, arguments: list[str], _timeout_s: float) -> int:
        command = " ".join(arguments[1:3])
        self.calls.append(command)
        if command == "start --attach":
            # The container leaves build output behind when it is killed.
            (self.work / "build").mkdir()
            return self.starts.pop(0) if self.starts else self.start
        return 1 if command == self.failing else 0


@final
class _Activator:
    def __init__(self, failures: list[str] | None = None, serves: bool = True) -> None:
        self.failures = list(failures or [])
        self.serves = serves
        self.calls: list[str] = []

    def stage(self, bundle: Path, database: str) -> dict[str, object]:
        self.calls.append("stage")
        assert bundle.name == database and bundle.is_dir()
        return self._step("stage", {
            "stage_key": "data-" + database,
            "deployment": {"api_image": _IMAGE, "edge_image": "edge",
                           "source_revision": "1" * 40, "database_sha256": database},
        })  # fmt: skip

    def activate(self, _staged: dict[str, object]) -> dict[str, object]:
        self.calls.append("activate")
        return self._step("activate", {"activated_at": "2026-10-21T14:00:00Z"})

    def rollback(self, stage_key: str) -> dict[str, object]:
        self.calls.append("rollback " + stage_key[:5])
        return {"status": "restored"}

    def finish(self, _staged: dict[str, object], _receipt: dict[str, object]) -> None:
        self.calls.append("finish")

    def public_dataset(self) -> str | None:
        return _DATASET if self.serves else "f" * 64

    def _step(self, name: str, value: dict[str, object]) -> dict[str, object]:
        # "activate" fails with a restored release; "activate: <text>" with text.
        if self.failures and self.failures[0].split(":")[0] == name:
            failure = self.failures.pop(0)
            raise host.HostError(
                failure.split(": ", 1)[1] if ": " in failure else failure
            )
        return value


_DATABASE, _DATASET = "d" * 64, "e" * 64


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "opt"
    processor = tmp_path / "processor"
    root.mkdir()
    for name in ("work", "state", "accepted", "activation"):
        (processor / name).mkdir(parents=True)
    _compose(root)
    _ = (processor / "work/left-over").write_bytes(b"x")
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    monkeypatch.setattr(host, "SECRETS", secrets)
    monkeypatch.setattr(host, "READY_DELAY_S", 0.0)
    return root, processor


def _accept(processor: Path, classes: list[str] | None) -> None:
    (processor / "accepted" / _DATABASE).mkdir()
    request = {"version": 1, "system": "icd9-pl", "database_sha256": _DATABASE,
               "release_id": "icd9-czs-5-90-gen-94-0a1b2c3d4e5f", "dataset_id": _DATASET}  # fmt: skip
    _ = (processor / "accepted/request.json").write_text(json.dumps(request))
    if classes is not None:
        _ = (host.SECRETS / "activation.json").write_text(
            json.dumps({"version": 1, "classes": classes})
        )


def _outcome(processor: Path) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((processor / "activation/outcome.json").read_text()),
    )


@pytest.mark.parametrize("start,outcome", [(0, "idle"), (1, "processor exited 1")])
def test_run_empties_work_and_keeps_only_a_failed_container(
    layout: tuple[Path, Path], start: int, outcome: str
) -> None:
    root, processor = layout
    work = processor / "work"
    docker = _Docker(work, failing="network inspect", start=start)

    assert host.run_once(root, processor, docker, _Activator()) == outcome

    container = [
        "rm --force",
        "network inspect",
        "network create",
        "create --name",
        "network connect",
        "start --attach",
    ]
    # A failed processor is kept for its logs and nothing is finalized.
    assert docker.calls == ([*container, "rm --force"] * 2 if start == 0 else container)
    assert list(work.iterdir()) == []


def test_failed_create_still_removes_the_container(layout: tuple[Path, Path]) -> None:
    root, processor = layout
    docker = _Docker(processor / "work", failing="create --name")

    with pytest.raises(host.HostError, match="create failed"):
        _ = host.run_once(root, processor, docker, _Activator())

    assert docker.calls[-1] == "rm --force"
    assert "start --attach" not in docker.calls


def test_busy_deploy_lock_skips_the_run_without_docker(
    layout: tuple[Path, Path],
) -> None:
    root, processor = layout
    docker = _Docker(processor / "work")
    descriptor = os.open(
        root / ".validation-deploy.lock", os.O_RDWR | os.O_CREAT, 0o600
    )
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        outcome = host.run_once(root, processor, docker, _Activator())
    finally:
        os.close(descriptor)

    assert outcome == "skipped: deployment in progress"
    assert docker.calls == []


def test_low_free_space_refuses_before_the_lock(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, processor = layout

    def disk_usage(_path: Path) -> tuple[int, int, int]:
        return Usage(0, 0, host.MIN_FREE_BYTES - 1)

    monkeypatch.setattr(shutil, "disk_usage", disk_usage)
    docker = _Docker(processor / "work")

    with pytest.raises(host.HostError, match="free-space"):
        _ = host.run_once(root, processor, docker, _Activator())

    assert docker.calls == []
    assert not (root / ".validation-deploy.lock").exists()


@pytest.mark.parametrize("classes", [None, [], ["icd10-pl"]])
def test_an_unlisted_class_is_reported_disabled_and_kept(
    layout: tuple[Path, Path], classes: list[str] | None
) -> None:
    root, processor = layout
    _accept(processor, classes)
    activator = _Activator()

    assert host.run_once(root, processor, _Docker(processor / "work"), activator) == (
        "disabled"
    )

    assert activator.calls == []
    assert _outcome(processor)["release_id"] == "icd9-czs-5-90-gen-94-0a1b2c3d4e5f"
    assert (processor / "accepted" / _DATABASE).is_dir()


def test_one_failed_start_is_retried_and_then_recorded(
    layout: tuple[Path, Path],
) -> None:
    root, processor = layout
    _accept(processor, ["icd9-pl", "icd10-pl"])
    activator = _Activator(failures=["activate"])

    status = host.run_once(root, processor, _Docker(processor / "work"), activator)

    assert status == "activated"
    assert activator.calls == ["stage", "activate", "stage", "activate", "finish"]
    activation = cast(dict[str, object], _outcome(processor)["activation"])
    assert (activation["attempt"], activation["previous_database_sha256"]) == (
        2,
        "b" * 64,
    )
    assert list((processor / "accepted").iterdir()) == []
    assert not (processor / "activation/attempts.json").exists()
    mode = (processor / "activation/outcome.json").stat().st_mode & 0o777
    assert mode == 0o640


def test_two_failed_starts_exhaust_the_budget_for_that_database(
    layout: tuple[Path, Path],
) -> None:
    root, processor = layout
    _accept(processor, ["icd9-pl"])
    first = _Activator(failures=["activate", "activate"])
    later = _Activator()

    statuses = [
        host.run_once(root, processor, _Docker(processor / "work"), activator)
        for activator in (first, later)
    ]

    assert statuses == ["failed", "failed"]
    assert first.calls == ["stage", "activate", "stage", "activate"]
    assert later.calls == []
    assert _outcome(processor)["stage"] == "retries_exhausted"
    assert (processor / "accepted" / _DATABASE).is_dir()


def test_another_database_never_resets_an_exhausted_budget(
    layout: tuple[Path, Path],
) -> None:
    # The cap is per database: activating a second one in between, even
    # successfully, must not give the first two fresh attempts (#230).
    root, processor = layout
    _accept(processor, ["icd9-pl"])
    first = _Activator(failures=["activate", "activate"])
    assert host.run_once(root, processor, _Docker(processor / "work"), first) == (
        "failed"
    )

    shutil.rmtree(processor / "accepted")
    (processor / "accepted").mkdir()
    other = "c" * 64
    (processor / "accepted" / other).mkdir()
    request = {"version": 1, "system": "icd9-pl", "database_sha256": other,
               "release_id": "icd9-czs-5-90-gen-95-0a1b2c3d4e5f", "dataset_id": _DATASET}  # fmt: skip
    _ = (processor / "accepted/request.json").write_text(json.dumps(request))
    second = _Activator()
    assert host.run_once(root, processor, _Docker(processor / "work"), second) == (
        "activated"
    )

    _accept(processor, None)
    again = _Activator()
    assert host.run_once(root, processor, _Docker(processor / "work"), again) == (
        "failed"
    )
    assert again.calls == []
    assert _outcome(processor)["stage"] == "retries_exhausted"
    assert json.loads((processor / "activation/attempts.json").read_text()) == {
        _DATABASE: 2
    }


def test_a_release_the_public_site_does_not_serve_is_rolled_back(
    layout: tuple[Path, Path],
) -> None:
    root, processor = layout
    _accept(processor, ["icd9-pl"])
    activator = _Activator(serves=False)

    status = host.run_once(root, processor, _Docker(processor / "work"), activator)

    assert status == "failed"
    assert activator.calls == ["stage", "activate", "rollback data-"] * 2
    assert _outcome(processor)["stage"] == "verify"


def test_an_incomplete_rollback_stops_without_another_attempt(
    layout: tuple[Path, Path],
) -> None:
    root, processor = layout
    _accept(processor, ["icd9-pl"])
    activator = _Activator(
        failures=["activate: activation failed; rollback incomplete"]
    )

    status = host.run_once(root, processor, _Docker(processor / "work"), activator)

    assert status == "failed"
    assert activator.calls == ["stage", "activate"]
    assert _outcome(processor)["stage"] == "rollback"


def test_an_unrecorded_receipt_is_reported_again(layout: tuple[Path, Path]) -> None:
    root, processor = layout
    _accept(processor, ["icd9-pl"])
    # Judge, finalize (fails), then the next run's judge and finalize.
    docker = _Docker(processor / "work", starts=[0, 1, 0, 0])

    first = host.run_once(root, processor, docker, _Activator())
    pending = (processor / "activation/pending.json").exists()
    second = host.run_once(root, processor, docker, _Activator())

    assert (first, pending, second) == ("finalize exited 1", True, "activated")
    assert _outcome(processor)["release_id"] == "icd9-czs-5-90-gen-94-0a1b2c3d4e5f"
    assert not (processor / "activation/pending.json").exists()
