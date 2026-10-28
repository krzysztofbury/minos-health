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


def call(
    site: tuple[Path, Path, Path],
    command: str,
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    root, _, base = site
    payload = base64.urlsafe_b64encode(
        json.dumps(params or {"revision": NEW_REVISION, "root": str(root)}).encode()
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
    state["fail_after_watcher"] = 1
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
    # The failed check is named, not an empty envelope.
    assert failure.value.details["failure"] == {
        "preserved": False,
        "healthy": True,
        "watcher_image_matches": True,
    }
    with pytest.raises(DeployError):
        _ = call(staged, "rollback")
    with (staged[0] / "compose.yaml").open("ab") as target:
        _ = target.write(b" ")
    with pytest.raises(DeployError, match="unrelated live compose"):
        _ = call(staged, "rollback")


@pytest.mark.parametrize(
    "changed", ["none", "dispatch", "main", "publication", "ci", "digest"]
)
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
                        # Main CI runs on push again since #272.
                        "event": "workflow_dispatch"
                        if changed == "dispatch"
                        else "push",
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
    if changed in ("none", "dispatch"):
        deploy_watcher.reviewed(plan)
    else:
        with pytest.raises(DeployError):
            deploy_watcher.reviewed(plan)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("apis", "accepted"),
    [
        (["reference-api"], True),
        (["reference-api-green"], True),
        (["reference-api-blue", "reference-api-green"], False),
        ([], False),
    ],
)
def test_watcher_baseline_preserves_exactly_one_api_colour(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    apis: list[str],
    accepted: bool,
) -> None:
    services = [*apis, "edge", "cloudflared", "uptime-kuma", "watcher"]
    records = [(name, name + "-0", "0", "healthy") for name in sorted(services)]

    def containers(_layout: validation_host.Layout) -> list[tuple[str, str, str, str]]:
        return records

    monkeypatch.setattr(validation_host, "containers", containers)
    layout = validation_host.Layout(tmp_path, "minos")
    if accepted:
        assert [row[0] for row in watcher_host.baseline(layout)] == sorted(
            [*apis, "edge", "cloudflared", "uptime-kuma"]
        )
    else:
        with pytest.raises(validation_host.HostError, match="unexpected live"):
            _ = watcher_host.baseline(layout)


_R2_STORAGE = {
    "bucket": "minos-archive",
    "endpoint": "https://0123456789abcdef0123456789abcdef.eu.r2.cloudflarestorage.com",
}
_AWS_KEY = (
    b"[default]\naws_access_key_id = AKIA"
    + b"A" * 16
    + b"\naws_secret_access_key = "
    + b"a" * 40
    + b"\n"
)
_R2_KEY = (
    b"[default]\naws_access_key_id = "
    + b"1" * 32
    + b"\naws_secret_access_key = "
    + b"2" * 64
    + b"\n"
)


def _storage_site(staged: tuple[Path, Path, Path]) -> dict[str, object]:
    """The live AWS layout (#200): S3 environment and a mounted key file."""
    root = staged[0]
    credentials = root / "watcher-aws-credentials"
    _ = credentials.write_bytes(_AWS_KEY)
    credentials.chmod(0o440)
    _, document = validation_host.load_compose(root / "compose.yaml")
    watcher = watcher_host.service(document)
    environment = cast(dict[str, str], watcher["environment"])
    environment.update(
        AWS_DEFAULT_REGION="eu-central-1",
        AWS_REGION="eu-central-1",
        AWS_SHARED_CREDENTIALS_FILE="/run/secrets/aws-credentials",
        MINOS_WATCHER_ARCHIVE_BACKEND="s3",
        MINOS_WATCHER_S3_BUCKET="minos-health-snapshots",
        MINOS_WATCHER_S3_EXPECTED_BUCKET_OWNER="123456789012",
    )
    watcher["volumes"] = [
        {
            "type": "bind",
            "source": str(credentials),
            "target": "/run/secrets/aws-credentials",
            "read_only": True,
        }
    ]
    content = validation_host.canonical(document)
    _ = (root / "compose.yaml").write_bytes(content)
    storage = {
        **_R2_STORAGE,
        "credentials_sha256": validation_host.sha256_bytes(_R2_KEY),
    }
    directory = root / "watcher-stage" / NEW_REVISION
    _ = (directory / "previous.json").write_bytes(content)
    _ = (directory / "candidate.json").write_bytes(
        validation_host.canonical(watcher_host.render(document, STALE_WATCHER, storage))
    )
    _ = (directory / "storage-credentials").write_bytes(_R2_KEY)
    params: dict[str, object] = {
        "revision": NEW_REVISION,
        "root": str(root),
        "storage": storage,
    }
    _ = (directory / "prepared.json").write_text(json.dumps(params))
    return params


def test_storage_render_moves_aws_environment_to_r2_exactly_once(
    staged: tuple[Path, Path, Path],
) -> None:
    params = _storage_site(staged)
    _, document = validation_host.load_compose(staged[0] / "compose.yaml")
    storage = cast(dict[str, str], params["storage"])

    environment = cast(
        dict[str, str],
        watcher_host.service(watcher_host.render(document, STALE_WATCHER, storage))[
            "environment"
        ],
    )

    assert not {"AWS_REGION", "AWS_DEFAULT_REGION"} & set(environment)
    assert "MINOS_WATCHER_S3_EXPECTED_BUCKET_OWNER" not in environment
    assert environment["MINOS_WATCHER_S3_BUCKET"] == "minos-archive"
    assert environment["MINOS_WATCHER_S3_ENDPOINT"] == storage["endpoint"]
    migrated = watcher_host.render(document, STALE_WATCHER, storage)
    with pytest.raises(validation_host.HostError, match="not the AWS S3 layout"):
        _ = watcher_host.render(migrated, STALE_WATCHER, storage)


def test_storage_activation_swaps_key_with_compose_and_rollback_restores_both(
    staged: tuple[Path, Path, Path],
) -> None:
    params = _storage_site(staged)
    credentials = staged[0] / "watcher-aws-credentials"
    original = (staged[0] / "compose.yaml").read_bytes()

    assert call(staged, "activate", params)["event"] == "watcher_activated"
    assert credentials.read_bytes() == _R2_KEY
    assert oct(credentials.stat().st_mode & 0o777) == "0o440"
    live = watcher_host.service(
        validation_host.load_compose(staged[0] / "compose.yaml")[1]
    )
    assert "MINOS_WATCHER_S3_ENDPOINT" in cast(dict[str, str], live["environment"])

    assert call(staged, "rollback", params)["event"] == "watcher_rolled_back"
    assert credentials.read_bytes() == _AWS_KEY
    assert (staged[0] / "compose.yaml").read_bytes() == original


def test_failed_storage_activation_restores_the_previous_key(
    staged: tuple[Path, Path, Path],
) -> None:
    params = _storage_site(staged)
    state = docker_state(staged)
    state["fail_after_watcher"] = 1
    _ = (staged[2] / "docker-state.json").write_text(json.dumps(state))

    with pytest.raises(DeployError, match="activation failed"):
        _ = call(staged, "activate", params)

    assert (staged[0] / "watcher-aws-credentials").read_bytes() == _AWS_KEY


@pytest.mark.parametrize("fault", ["none", "mode", "digest", "schema"])
def test_storage_key_upload_must_be_private_planned_and_r2_shaped(
    staged: tuple[Path, Path, Path], fault: str
) -> None:
    params = _storage_site(staged)
    root = staged[0]
    target = root / "watcher-stage" / NEW_REVISION
    (target / "storage-credentials").unlink()
    _, document = validation_host.load_compose(root / "compose.yaml")
    migration = cast(dict[str, str], params["storage"])
    upload = root / "upload"
    content = _AWS_KEY if fault == "schema" else _R2_KEY
    _ = upload.write_bytes(content)
    upload.chmod(0o644 if fault == "mode" else 0o600)
    if fault == "digest":
        migration = {**migration, "credentials_sha256": "0" * 64}

    if fault == "none":
        staged_key = watcher_host.stage_credentials(upload, target, document, migration)
        assert staged_key.read_bytes() == _R2_KEY
        assert oct(staged_key.stat().st_mode & 0o777) == "0o440"
    else:
        with pytest.raises(validation_host.HostError):
            _ = watcher_host.stage_credentials(upload, target, document, migration)
        assert not (target / "storage-credentials").exists()
    # The upload never outlives staging, accepted or refused.
    assert not upload.exists()
