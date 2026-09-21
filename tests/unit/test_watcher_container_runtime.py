from pathlib import Path
from typing import cast

import pytest
import yaml

from minos.cli.watcher import WatcherRuntimeSettings, paths_from_environment

ROOT = Path(__file__).parents[2]


def source(path: str) -> str:
    return (ROOT / path).read_text()


def mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    untyped = cast(dict[object, object], value)
    assert all(isinstance(key, str) for key in untyped)
    return cast(dict[str, object], value)


def rendered_production_compose() -> dict[str, object]:
    rendered = source("infra/ansible/templates/minos-compose.yml.j2")
    replacements = {
        "{{ watcher_aws_region }}": "eu-central-1",
        "{{ watcher_archive_backend }}": "s3",
        "{{ watcher_s3_bucket }}": "minos-test-bucket",
        "{{ watcher_s3_expected_bucket_owner }}": "123456789012",
        "{{ watcher_image_repository }}": "ghcr.io/example/minos-watcher",
        "{{ watcher_expected_image_digest }}": f"sha256:{'a' * 64}",
        "{{ watcher_runtime_image_digest }}": f"sha256:{'a' * 64}",
        "{{ watcher_uid }}": "10001",
        "{{ watcher_gid }}": "10001",
        "{{ watcher_secrets_gid }}": "10002",
        "{{ cloudflared_credential_path }}": (
            "/etc/minos/cloudflared/11111111-2222-3333-4444-555555555555.json"
        ),
    }
    for template, value in replacements.items():
        rendered = rendered.replace(template, value)
    assert "{{" not in rendered
    return mapping(cast(object, yaml.safe_load(rendered)))


def test_watcher_image_is_lockfile_driven_wheel_only_and_non_root() -> None:
    dockerfile = source("Dockerfile")

    assert dockerfile.count("FROM --platform=$TARGETPLATFORM") == 3
    assert "ARG TARGETPLATFORM=linux/amd64" in dockerfile
    expected_python_image = "python:3.14.7-alpine3.23@sha256:" + (
        "976bfed94200c6377b094feb91b7b1bd4849c8a4e3a7b2a364d95ddf1f1c6e01"
    )
    assert expected_python_image in dockerfile
    assert 'org.opencontainers.image.revision="${VCS_REF}"' in dockerfile
    assert "uv sync --frozen --no-dev --no-install-project" in dockerfile
    assert "uv build --wheel --no-sources" in dockerfile
    assert "uv pip install" in dockerfile
    assert "COPY --from=wheel-builder /app/.venv /app/.venv" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["/app/.venv/bin/minos"]' in dockerfile
    assert 'CMD ["watcher", "run"]' in dockerfile
    assert "/bin/sh" in dockerfile
    assert "/bin/busybox" in dockerfile
    assert "/sbin/apk" in dockerfile
    assert "/usr/local/bin/pip*" in dockerfile
    assert "COPY ." not in dockerfile
    assert "EXPOSE" not in dockerfile


def test_local_compose_has_hardened_disk_backed_filesystem_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = mapping(cast(object, yaml.safe_load(source("compose.yaml"))))
    services = mapping(document["services"])
    watcher = mapping(services["watcher"])
    preflight = mapping(services["watcher-preflight"])

    assert watcher["platform"] == "linux/amd64"
    assert watcher["user"] == "10001:10001"
    assert watcher["command"] == ["watcher", "run"]
    assert preflight["command"] == ["watcher", "preflight"]
    assert preflight["profiles"] == ["preflight"]
    assert watcher["read_only"] is True
    assert watcher["cap_drop"] == ["ALL"]
    assert watcher["security_opt"] == ["no-new-privileges:true"]
    assert "ports" not in watcher
    assert "expose" not in watcher
    assert watcher["pids_limit"] == 64
    assert watcher["mem_limit"] == "768m"
    assert watcher["cpus"] == "0.75"
    assert mapping(watcher["logging"]) == {
        "driver": "local",
        "options": {"max-file": "3", "max-size": "10m"},
    }
    healthcheck = mapping(watcher["healthcheck"])
    assert healthcheck["test"] == [
        "CMD",
        "/app/.venv/bin/minos",
        "watcher",
        "health",
    ]
    assert healthcheck["start_period"] == "10s"

    environment = mapping(watcher["environment"])
    assert set(environment) == {
        "MINOS_WATCHER_ARCHIVE_DIRECTORY",
        "MINOS_WATCHER_DATABASE",
        "MINOS_WATCHER_PROCESS_LOCK",
        "MINOS_WATCHER_READINESS_FILE",
        "MINOS_WATCHER_SCHEDULER_PAUSED",
        "MINOS_WATCHER_STAGING_DIRECTORY",
    }
    assert environment["MINOS_WATCHER_SCHEDULER_PAUSED"] == "false"
    assert mapping(preflight["environment"])["MINOS_WATCHER_SCHEDULER_PAUSED"] == (
        "true"
    )
    _assert_cli_paths_parse(environment, monkeypatch)

    volumes = mapping(document["volumes"])
    assert set(volumes) == {
        "watcher-control",
        "watcher-archive",
        "watcher-staging",
    }
    watcher_volumes = cast(list[str], watcher["volumes"])
    watcher_tmpfs = cast(list[str], watcher["tmpfs"])
    assert "watcher-staging:/var/lib/minos/staging" in watcher_volumes
    assert all("/var/lib/minos/staging" not in mount for mount in watcher_tmpfs)


def test_rendered_production_compose_smoke_parses_real_cli_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = rendered_production_compose()
    services = mapping(document["services"])
    watcher = mapping(services["watcher"])
    preflight = mapping(services["watcher-preflight"])
    candidate_preflight = mapping(services["watcher-candidate-preflight"])

    assert watcher["image"] == f"ghcr.io/example/minos-watcher@sha256:{'a' * 64}"
    assert watcher["platform"] == "linux/amd64"
    assert watcher["user"] == "10001:10001"
    assert watcher["group_add"] == ["10002"]
    assert watcher["command"] == ["watcher", "run"]
    assert preflight["command"] == ["watcher", "preflight"]
    assert preflight["profiles"] == ["preflight"]
    assert candidate_preflight["profiles"] == ["candidate-preflight"]
    assert mapping(preflight["environment"])["MINOS_WATCHER_SCHEDULER_PAUSED"] == (
        "true"
    )
    assert (
        mapping(candidate_preflight["environment"])["MINOS_WATCHER_SCHEDULER_PAUSED"]
        == "true"
    )
    assert any(
        str(volume).startswith("/var/lib/minos/watcher/candidate-preflight/control:")
        for volume in cast(list[str], candidate_preflight["volumes"])
    )
    candidate_volumes = cast(list[str], candidate_preflight["volumes"])
    assert (
        "/etc/minos/watcher/candidate/aws/credentials:/run/secrets/aws-credentials:ro"
    ) in candidate_volumes
    assert (
        "/etc/minos/watcher/candidate/healthchecks/targets.json:"
        "/run/secrets/healthchecks-targets.json:ro"
    ) in candidate_volumes
    assert watcher["read_only"] is True
    assert watcher["cap_drop"] == ["ALL"]
    assert watcher["security_opt"] == ["no-new-privileges:true"]
    assert watcher["restart"] == "unless-stopped"
    healthcheck = mapping(watcher["healthcheck"])
    assert healthcheck["test"] == [
        "CMD",
        "/app/.venv/bin/minos",
        "watcher",
        "health",
    ]
    assert healthcheck["retries"] == 3
    assert healthcheck["start_period"] == "6400s"
    assert "ports" not in watcher
    assert "expose" not in watcher

    environment = mapping(watcher["environment"])
    assert set(environment) == {
        "AWS_DEFAULT_REGION",
        "AWS_REGION",
        "AWS_SHARED_CREDENTIALS_FILE",
        "MINOS_WATCHER_ARCHIVE_BACKEND",
        "MINOS_WATCHER_ARCHIVE_DIRECTORY",
        "MINOS_WATCHER_DATABASE",
        "MINOS_WATCHER_HEALTHCHECKS_TARGETS_FILE",
        "MINOS_WATCHER_IMAGE_DIGEST",
        "MINOS_WATCHER_PROCESS_LOCK",
        "MINOS_WATCHER_READINESS_FILE",
        "MINOS_WATCHER_RUNTIME_IMAGE_DIGEST",
        "MINOS_WATCHER_S3_BUCKET",
        "MINOS_WATCHER_S3_EXPECTED_BUCKET_OWNER",
        "MINOS_WATCHER_SCHEDULER_PAUSED",
        "MINOS_WATCHER_STAGING_DIRECTORY",
    }
    assert environment["AWS_SHARED_CREDENTIALS_FILE"] == (
        "/run/secrets/aws-credentials"
    )
    assert environment["MINOS_WATCHER_HEALTHCHECKS_TARGETS_FILE"] == (
        "/run/secrets/healthchecks-targets.json"
    )
    assert environment["MINOS_WATCHER_SCHEDULER_PAUSED"] == "false"
    _assert_cli_paths_parse(environment, monkeypatch)
    _assert_cli_runtime_settings_parse(environment, monkeypatch)
    assert all("{{" not in str(value) for value in environment.values())
    watcher_volumes = cast(list[str], watcher["volumes"])
    assert set(watcher_volumes) == {
        "/var/lib/minos/watcher/control:/var/lib/minos/control:rw",
        "/var/lib/minos/watcher/archive:/var/lib/minos/archive:rw",
        "/var/lib/minos/watcher/staging:/var/lib/minos/staging:rw",
        "/etc/minos/watcher/aws/credentials:/run/secrets/aws-credentials:ro",
        "/etc/minos/watcher/healthchecks/targets.json:/run/secrets/healthchecks-targets.json:ro",
    }


def test_ansible_enforces_restore_staging_secret_and_runtime_boundaries() -> None:
    playbook = source("infra/ansible/site.yml")
    deployment_readme = source("infra/ansible/README.md")
    unit = source("infra/ansible/files/minos-compose.service")

    assert "watcher_uid: 10001" in playbook
    assert "watcher_gid: 10001" in playbook
    assert "watcher_secrets_gid: 10002" in playbook
    assert "watcher_uid == 10001" in playbook
    assert "recurse: true" not in playbook
    assert "Reapply ownership to known control files after a restore" in playbook
    assert "match('^sha256:[a-f0-9]{64}$')" in playbook
    assert "src: minos-compose.yml.j2" in playbook
    assert playbook.count('mode: "0700"') >= 2
    assert playbook.count('mode: "0750"') >= 2
    assert playbook.count('mode: "0440"') >= 3
    assert "remount,bind,rw,noexec,nosuid,nodev" in playbook
    assert "The watcher staging path is not a hardened disk-backed mount" in playbook
    assert "watcher_archive_backend | default('') == 's3'" in playbook
    assert "watcher_s3_bucket | default('')" in playbook
    assert "watcher_s3_expected_bucket_owner | default('')" in playbook
    assert "watcher_expected_image_digest | default('')" in playbook
    assert "watcher_source_revision | default('')" in playbook
    assert "Require the exact AWS watcher credential schema" in playbook
    assert "Require the exact Healthchecks target document schema" in playbook
    assert "Require the reviewed tunnel credential before mutating the host" in playbook
    assert ".stat.size in range(64, 4097)" in playbook
    assert ".stat.size in range(2, 65537)" in playbook
    assert "candidate_payload | length in range(64, 4097)" in playbook
    assert "candidate_payload | length in range(2, 65537)" in playbook
    assert "sources | length in range(1, 129)" in playbook
    assert "Stage validated watcher secret candidates" in playbook
    assert "Stage the validated cloudflared credential candidate" in playbook
    assert "Load the candidate cloudflared credential with the pinned image" in playbook
    assert "Atomically promote validated watcher secrets while stopped" in playbook
    assert (
        "Atomically promote the validated cloudflared credential while stopped"
        in playbook
    )
    assert "Restore prior watcher secrets" in playbook
    assert "Restore the prior cloudflared credential" in playbook
    assert "Remove rejected initial watcher secrets" in playbook
    assert "Remove a rejected initial cloudflared credential" in playbook
    assert "Install operator-supplied watcher secret files" not in playbook
    assert "Install the operator-supplied cloudflared credential" not in playbook
    assert "watcher_image_repository + '@' + watcher_runtime_image_digest" in playbook
    assert "one runnable candidate watcher manifest" in playbook
    assert playbook.count("copy-control-database") == 2
    assert "source.backup(destination" not in playbook
    assert playbook.count("--kill-after=5s") == 2
    assert 'minos_watcher_boundary[17] == "0"' in playbook

    validate_aws_secret = playbook.index(
        "Require the exact AWS watcher credential schema"
    )
    validate_healthchecks_secret = playbook.index(
        "Require the exact Healthchecks target document schema"
    )
    validate_cloudflared_secret = playbook.index(
        "Require the reviewed tunnel credential before mutating the host"
    )
    stage_secrets = playbook.index("Stage validated watcher secret candidates")
    stage_cloudflared = playbook.index(
        "Stage the validated cloudflared credential candidate"
    )
    load_cloudflared = playbook.index(
        "Load the candidate cloudflared credential with the pinned image"
    )
    validate_compose = playbook.index("Validate the candidate Minos Compose model")
    cloudflared_probe = playbook[load_cloudflared:validate_compose]
    assert "- --network\n          - none" in cloudflared_probe
    assert "- --read-only" in cloudflared_probe
    assert '- "65532:65532"' in cloudflared_probe
    assert "- no-new-privileges:true" in cloudflared_probe
    assert '- --retries\n          - "100"' in cloudflared_probe
    assert "cloudflared_candidate_credential_load.rc != 124" in cloudflared_probe
    assert "/etc/minos/cloudflared/candidate/" in cloudflared_probe
    calculate_activation = playbook.index(
        "Calculate whether Minos activation is required"
    )
    candidate_preflight = playbook.index(
        "Run the isolated scheduler-paused candidate preflight"
    )
    stop_active = playbook.index(
        "Stop the active Minos stack after candidate validation"
    )
    promote_secrets = playbook.index(
        "Atomically promote validated watcher secrets while stopped"
    )
    promote_cloudflared = playbook.index(
        "Atomically promote the validated cloudflared credential while stopped"
    )
    promote = playbook.index("Promote validated Minos runtime files while stopped")
    assert (
        validate_cloudflared_secret
        < stage_cloudflared
        < load_cloudflared
        < calculate_activation
        and validate_aws_secret < stage_secrets
        and validate_healthchecks_secret
        < stage_secrets
        < calculate_activation
        < candidate_preflight
        < stop_active
        < promote_secrets
        < promote_cloudflared
        < promote
    )
    assert "watcher_candidate_runtime_files.results" in playbook
    assert "watcher_active_runtime_files.results" in playbook
    assert "watcher_active_image.stdout" in playbook
    assert playbook.count("- watcher_activation_required") >= 8
    assert "rescue:" in playbook[stop_active:]
    restore_secrets = playbook.index("Restore prior watcher secrets", stop_active)
    restore_cloudflared = playbook.index(
        "Restore the prior cloudflared credential", stop_active
    )
    restore_runtime = playbook.index(
        "Restore prior Minos runtime files and image reference", stop_active
    )
    assert restore_cloudflared < restore_secrets < restore_runtime
    assert (
        "Restore prior Minos runtime files and image reference"
        in playbook[stop_active:]
    )
    assert "Capture stopped preactivation database evidence" in playbook[stop_active:]
    assert (
        "Restart the prior Minos stack against the current database"
        in playbook[stop_active:]
    )
    assert "Restore the prior watcher control database" not in playbook
    assert "Remove rejected control database sidecars" not in playbook
    assert "Remove a control database created by the rejected candidate" not in playbook
    assert "Reject a partial active Minos runtime" in playbook
    assert "Remove a rejected initial Minos runtime" in playbook[stop_active:]
    assert "unsafe_writes: false" in playbook[stop_active:]
    assert "cloudflared_credential_candidate.stat.checksum" in playbook
    assert "cloudflared_credential_preflight.stat.checksum" in playbook[stop_active:]

    assert "set -euo pipefail" in deployment_readme
    assert 'credential_dir="$(mktemp -d)"' in deployment_readme
    assert "trap cleanup EXIT" in deployment_readme
    assert "/tmp/minos-" not in deployment_readme

    start_stop = unit.index("ExecStartPre=/")
    start_preflight = unit.index("ExecStartPre=/", start_stop + 1)
    start_runtime = unit.index("ExecStart=/", start_preflight)
    assert start_stop < start_preflight < start_runtime
    assert "ExecReload=" not in unit
    assert " stop --timeout 30 watcher" in unit
    assert " run --rm watcher-preflight" in unit
    assert "--wait --wait-timeout 6600" in unit
    assert "TimeoutStartSec=7800" in unit


def test_no_secret_value_or_mutable_watcher_tag_is_committed() -> None:
    runtime_sources = "\n".join(
        (
            source("compose.yaml"),
            source("infra/ansible/templates/minos-compose.yml.j2"),
        )
    )
    playbook = source("infra/ansible/site.yml")

    assert "aws_access_key_id" not in runtime_sources.lower()
    assert "aws_secret_access_key" not in runtime_sources.lower()
    assert "hc-ping.com/" not in runtime_sources
    assert "AKIA[A-Z0-9]{16}" in playbook
    assert "[A-Za-z0-9/+=]{40}" in playbook
    assert "minos-health-watcher:latest" not in runtime_sources
    assert "watchtower" not in runtime_sources.lower()


def _assert_cli_paths_parse(
    environment: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in environment.items():
        if name.startswith("MINOS_WATCHER_"):
            monkeypatch.setenv(name, str(value))
    paths = paths_from_environment(
        database=None,
        staging_directory=None,
        archive_directory=None,
        process_lock=None,
    )
    assert str(paths.database) == environment["MINOS_WATCHER_DATABASE"]
    assert (
        str(paths.staging_directory) == environment["MINOS_WATCHER_STAGING_DIRECTORY"]
    )
    assert (
        str(paths.archive_directory) == environment["MINOS_WATCHER_ARCHIVE_DIRECTORY"]
    )
    assert str(paths.process_lock) == environment["MINOS_WATCHER_PROCESS_LOCK"]


def _assert_cli_runtime_settings_parse(
    environment: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, str(value))
    settings = WatcherRuntimeSettings.model_validate({})
    assert settings.archive_backend == "s3"
    assert settings.s3_bucket == environment["MINOS_WATCHER_S3_BUCKET"]
    assert (
        settings.s3_expected_bucket_owner
        == (environment["MINOS_WATCHER_S3_EXPECTED_BUCKET_OWNER"])
    )
    assert settings.aws_region == environment["AWS_REGION"]
    assert settings.scheduler_paused is False
    assert settings.image_digest == environment["MINOS_WATCHER_IMAGE_DIGEST"]
    assert (
        settings.runtime_image_digest
        == (environment["MINOS_WATCHER_RUNTIME_IMAGE_DIGEST"])
    )
    assert (
        str(settings.aws_shared_credentials_file)
        == (environment["AWS_SHARED_CREDENTIALS_FILE"])
    )
    assert (
        str(settings.healthchecks_targets_file)
        == (environment["MINOS_WATCHER_HEALTHCHECKS_TARGETS_FILE"])
    )
