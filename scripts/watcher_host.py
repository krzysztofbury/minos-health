"""Watcher-only release transitions sharing the validation deployment lock."""

import copy
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import cast

from scripts import validation_host as host

IMAGE = re.compile(r"ghcr\.io/krzysztofbury/minos-health-watcher@sha256:[0-9a-f]{64}")
REVISION = re.compile(r"[0-9a-f]{40}")
# One-time AWS S3 to Cloudflare R2 cutover (#200); see storage_environment().
R2_ENDPOINT = re.compile(
    r"https://[0-9a-f]{32}(?:\.(?:eu|fedramp))?\.r2\.cloudflarestorage\.com"
)
R2_BUCKET = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
R2_CREDENTIALS = re.compile(
    rb"\[default\]\naws_access_key_id[ \t]*=[ \t]*[0-9a-f]{32}\naws_secret_access_key[ \t]*=[ \t]*[0-9a-f]{64}\n?"  # noqa: E501
)
CREDENTIALS_TARGET = "/run/secrets/aws-credentials"
AWS_STORAGE_ENVIRONMENT = (
    "AWS_DEFAULT_REGION",
    "AWS_REGION",
    "MINOS_WATCHER_S3_EXPECTED_BUCKET_OWNER",
)
# Exactly one reference API colour is preserved beside these services (#192).
PRESERVED = {"edge", "cloudflared", "uptime-kuma"}
PYTHON = "/app/.venv/bin/python"


def service(document: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, dict[str, object]], document["services"])["watcher"]


def storage(params: dict[str, object]) -> dict[str, str] | None:
    value = params.get("storage")
    if value is None:
        return None
    host.require(isinstance(value, dict), "watcher storage migration is invalid")
    migration = cast(dict[str, object], value)
    host.require(
        set(migration) == {"bucket", "endpoint", "credentials_sha256"}
        and all(isinstance(item, str) for item in migration.values()),
        "watcher storage migration is invalid",
    )
    fields = cast(dict[str, str], migration)
    host.require(
        R2_BUCKET.fullmatch(fields["bucket"]) is not None
        and R2_ENDPOINT.fullmatch(fields["endpoint"]) is not None
        and re.fullmatch(r"[0-9a-f]{64}", fields["credentials_sha256"]) is not None,
        "watcher storage migration is invalid",
    )
    return fields


def credentials_mount(selected: dict[str, object]) -> dict[str, object]:
    mounts = [
        cast(dict[str, object], mount)
        for mount in cast(list[object], selected["volumes"])
        if isinstance(mount, dict)
        and cast(dict[str, object], mount).get("target") == CREDENTIALS_TARGET
    ]
    host.require(len(mounts) == 1, "watcher credential mount differs")
    host.require(mounts[0].get("read_only") is True, "watcher credential mount differs")
    return mounts[0]


def storage_environment(environment: dict[str, str], migration: dict[str, str]) -> None:
    """Move the live AWS S3 environment to R2 exactly once, never partially."""
    host.require(
        all(name in environment for name in AWS_STORAGE_ENVIRONMENT)
        and "MINOS_WATCHER_S3_ENDPOINT" not in environment
        and environment.get("MINOS_WATCHER_ARCHIVE_BACKEND") == "s3",
        "watcher storage environment is not the AWS S3 layout",
    )
    for name in AWS_STORAGE_ENVIRONMENT:
        del environment[name]
    environment["MINOS_WATCHER_S3_BUCKET"] = migration["bucket"]
    environment["MINOS_WATCHER_S3_ENDPOINT"] = migration["endpoint"]


def render(
    document: dict[str, object],
    image: str,
    migration: dict[str, str] | None = None,
) -> dict[str, object]:
    host.require(IMAGE.fullmatch(image) is not None, "watcher image must be pinned")
    result = copy.deepcopy(document)
    selected = service(result)
    host.require(
        selected.get("command") == ["watcher", "run"], "watcher command differs"
    )
    selected["image"] = image
    environment = cast(dict[str, str], selected["environment"])
    host.require(
        environment.get("MINOS_WATCHER_SCHEDULER_PAUSED") == "false",
        "watcher is paused",
    )
    for name in ("MINOS_WATCHER_IMAGE_DIGEST", "MINOS_WATCHER_RUNTIME_IMAGE_DIGEST"):
        host.require(name in environment, "watcher identity environment is missing")
        environment[name] = image.split("@", 1)[1]
    if migration is not None:
        storage_environment(environment, migration)
    return result


def credentials_upload(revision: str) -> Path:
    # Root's home (0700), never the world-traversable stage tree.
    return Path("/root") / f"minos-watcher-{revision}.storage-credentials"


def stage_credentials(
    upload: Path,
    target: Path,
    current: dict[str, object],
    migration: dict[str, str],
) -> Path:
    """Validate the uploaded R2 key and keep a private copy beside the plan.

    The live file keeps serving the running watcher until activation, which
    replaces it together with the Compose environment (and rollback restores
    both).
    """
    staged = target / "storage-credentials"
    if upload.exists():
        uploaded = upload.lstat()
        content = upload.read_bytes()
        upload.unlink()
        host.require(
            stat.S_ISREG(uploaded.st_mode)
            and uploaded.st_uid == os.geteuid()
            and stat.S_IMODE(uploaded.st_mode) == 0o600,
            "uploaded R2 credential file is not private",
        )
        host.require(
            R2_CREDENTIALS.fullmatch(content) is not None,
            "R2 credential schema is invalid",
        )
        host.require(
            host.sha256_bytes(content) == migration["credentials_sha256"],
            "R2 credential digest differs from the plan",
        )
        live = Path(cast(str, credentials_mount(service(current))["source"]))
        status = live.stat()
        host.write_private(staged, content)
        os.chown(staged, status.st_uid, status.st_gid)
        staged.chmod(status.st_mode & 0o777)
    host.require(
        staged.is_file()
        and host.sha256_bytes(staged.read_bytes()) == migration["credentials_sha256"],
        "staged R2 credential is missing",
    )
    return staged


def swap_credentials(
    current: dict[str, object], target: Path, *, restore: bool
) -> None:
    live = Path(cast(str, credentials_mount(service(current))["source"]))
    previous = target / "credentials.previous"
    staged = target / "storage-credentials"
    status = live.stat()
    if restore:
        if not previous.is_file():
            return
        content = previous.read_bytes()
    else:
        if not previous.exists():
            host.write_once(previous, live.read_bytes())
        content = staged.read_bytes()
    host.write_private(live, content)
    os.chown(live, status.st_uid, status.st_gid)
    live.chmod(status.st_mode & 0o777)


def baseline(layout: host.Layout) -> list[list[str]]:
    records = host.containers(layout)
    services = {row[0] for row in records}
    apis = services & set(host.API_SERVICES)
    host.require(
        len(apis) == 1 and services - apis == PRESERVED | {"watcher"},
        "unexpected live services",
    )
    host.require(len(records) == 5, "duplicate live services")
    host.require(
        all(row[3] == "healthy" for row in records), "live services must be healthy"
    )
    return [list(row[:3]) for row in records if row[0] != "watcher"]


def state(
    layout: host.Layout, expected: list[list[str]], image: str
) -> dict[str, object]:
    records = host.containers(layout)
    unchanged = [list(row[:3]) for row in records if row[0] != "watcher"] == expected
    healthy = len(records) == 5 and all(row[3] == "healthy" for row in records)
    watchers = [row for row in records if row[0] == "watcher"]
    matching = False
    if len(watchers) == 1:
        running = host.run(
            ["docker", "inspect", "--format", "{{.Config.Image}}", watchers[0][1]],
            host.DOCKER_TIMEOUT_S,
        ).strip()
        matching = running == image and watchers[0][2] == "0"
    return {
        "preserved": unchanged,
        "healthy": healthy,
        "watcher_image_matches": matching,
    }


def checked(
    layout: host.Layout, expected: list[list[str]], image: str
) -> dict[str, object]:
    result = state(layout, expected, image)
    host.require(
        all(value is True for value in result.values()),
        "watcher transition did not pass health/isolation checks",
    )
    return result


def command(layout: host.Layout, arguments: list[str], record: Path) -> None:
    result = host.bounded_command(
        [
            "docker",
            "compose",
            "--project-name",
            layout.project,
            "--file",
            str(layout.compose),
            *arguments,
        ]
    )
    host.write_private(record, host.canonical(result))
    if result["status"] != "completed":
        raise host.HostError("watcher command failed", result)


def up(layout: host.Layout, record: Path) -> None:
    command(
        layout,
        ["up", "--detach", "--no-deps", "--wait", "--wait-timeout", "240", "watcher"],
        record,
    )


def directory(layout: host.Layout, params: dict[str, object]) -> Path:
    revision = params.get("revision")
    host.require(
        isinstance(revision, str) and REVISION.fullmatch(revision) is not None,
        "invalid watcher revision",
    )
    return layout.root / "watcher-stage" / cast(str, revision)


def isolated_configuration(
    layout: host.Layout,
    candidate: dict[str, object],
    runtime: dict[str, object],
    root: Path,
) -> tuple[str, host.Layout]:
    uid, gid = (int(value) for value in cast(str, runtime["user"]).split(":"))
    os.chown(root, uid, gid)
    mounts = cast(list[dict[str, object]], runtime["volumes"])
    required = {
        "/var/lib/minos/control",
        "/var/lib/minos/archive",
        "/var/lib/minos/staging",
        "/var/lib/minos/public",
    }
    found: set[str] = set()
    source_control = ""
    for mount in mounts:
        target = cast(str, mount["target"])
        if target in required:
            found.add(target)
            if target == "/var/lib/minos/control":
                source_control = cast(str, mount["source"])
            child = root / target.rsplit("/", 1)[-1]
            child.mkdir(mode=0o700)
            os.chown(child, uid, gid)
            mount["source"] = str(child)
        else:
            host.require(
                mount.get("read_only") is True, "unexpected writable watcher mount"
            )
    host.require(found == required, "watcher storage mounts differ")
    networks = copy.deepcopy(cast(dict[str, dict[str, object]], candidate["networks"]))
    for name, network in networks.items():
        networks[name] = {
            "external": True,
            "name": network.get("name", f"{layout.project}_{name}"),
        }
    preflight = {"services": {"watcher": runtime}, "networks": networks}
    temporary = host.Layout(root, layout.project)
    host.write_private(temporary.compose, host.canonical(preflight))
    return source_control, temporary


def isolated_preflight(
    layout: host.Layout, candidate: dict[str, object], destination: Path
) -> None:
    """Use a unique private copy, never the active DB or public projection mount."""
    runtime = copy.deepcopy(service(candidate))
    environment = cast(dict[str, str], runtime["environment"])
    environment["MINOS_WATCHER_SCHEDULER_PAUSED"] = "true"
    _ = runtime.pop("depends_on", None)
    _ = runtime.pop("container_name", None)
    runtime["restart"] = "no"
    runtime["healthcheck"] = {"disable": True}
    root = Path(tempfile.mkdtemp(prefix=".preflight-", dir=destination))
    # The named one-shot is removed even if its client exceeds the output/deadline
    # bound. It cannot outlive the files that it owns.
    name = "minos-watcher-preflight-" + root.name.removeprefix(".preflight-")
    try:
        source_control, temporary = isolated_configuration(
            layout, candidate, runtime, root
        )
        result = host.bounded_command(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--network",
                "none",
                "--read-only",
                "--user",
                cast(str, runtime["user"]),
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--memory",
                "512m",
                "--cpus",
                "0.75",
                "--pids-limit",
                "64",
                "--volume",
                source_control + ":/source:ro",
                "--volume",
                str(root / "control") + ":/destination:rw",
                cast(str, runtime["image"]),
                "watcher",
                "copy-control-database",
                "--source-database",
                "/source/control.sqlite3",
                "--destination-database",
                "/destination/control.sqlite3",
            ]
        )
        host.write_private(destination / "copy.json", host.canonical(result))
        host.require(result["status"] == "completed", "isolated control copy failed")
        command(
            temporary,
            [
                "run",
                "--rm",
                "--no-deps",
                "--name",
                name,
                "watcher",
                "watcher",
                "preflight",
            ],
            destination / "preflight.json",
        )
        # Also exercise the new publication code against the copied committed
        # evidence. No collection, recovery upload or heartbeat occurs here.
        program = """from pathlib import Path
from minos.ingest.watchers.config import WATCHER_SOURCE_CONFIGURATION_CODECS
from minos.ingest.watchers.control import ControlStore
from minos.ingest.watchers.nfz.publication import NfzPublicProjectionPublisher
from minos.ingest.watchers.publication import ImmutableGenerationPublisher, prepare_public_root
root = Path('/var/lib/minos/public/nfz')
prepare_public_root(root)
with ControlStore.open('/var/lib/minos/control/control.sqlite3', source_configuration_codecs=WATCHER_SOURCE_CONFIGURATION_CODECS) as store:
    NfzPublicProjectionPublisher(ImmutableGenerationPublisher(root, source='NFZ')).restore(store)
if not (root / 'current/api/v1/provenance/nfz.json').is_file():
    raise RuntimeError('NFZ committed evidence is unavailable')
"""
        command(
            temporary,
            [
                "run",
                "--rm",
                "--no-deps",
                "--name",
                name,
                "--entrypoint",
                PYTHON,
                "watcher",
                "-c",
                program,
            ],
            destination / "projection.json",
        )
    finally:
        # `docker rm --force` also handles a timed-out one-shot. Refuse cleanup
        # if the daemon cannot prove no process retains these mounts.
        _ = host.bounded_command(["docker", "rm", "--force", name])
        active = host.run(
            [
                "docker",
                "ps",
                "--all",
                "--filter",
                f"name=^/{name}$",
                "--format",
                "{{.ID}}",
            ],
            host.DOCKER_TIMEOUT_S,
        ).strip()
        host.require(
            not active, "preflight container remains; private scratch retained"
        )
        for parent, _, _ in os.walk(root, followlinks=False):
            path = Path(parent)
            path.chmod(path.stat().st_mode | 0o700)
        shutil.rmtree(root)


def stage(layout: host.Layout, params: dict[str, object]) -> dict[str, object]:
    target = directory(layout, params)
    content, current = host.load_compose(layout.compose)
    host.require(
        host.sha256_bytes(content) == params["compose_sha256"],
        "live compose differs from watcher plan",
    )
    before = baseline(layout)
    image = cast(str, params["image"])
    migration = storage(params)
    candidate = render(current, image, migration)
    host.require(service(current)["image"] != image, "watcher already uses candidate")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    preflight = candidate
    if migration is not None:
        staged = stage_credentials(
            credentials_upload(cast(str, params["revision"])),
            target,
            current,
            migration,
        )
        # The isolated preflight probes R2 with the new key before any swap.
        preflight = copy.deepcopy(candidate)
        credentials_mount(service(preflight))["source"] = str(staged)
    host.write_once(target / "previous.json", content)
    host.write_once(target / "candidate.json", host.canonical(candidate))
    host.write_once(target / "baseline.json", host.canonical(before))
    result = host.bounded_command(["docker", "pull", "-q", image])
    host.write_private(target / "pull.json", host.canonical(result))
    host.require(result["status"] == "completed", "candidate image pull failed")
    identity = host.run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            '{{.Os}}/{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}',
            image,
        ],
        host.DOCKER_TIMEOUT_S,
    ).strip()
    host.require(
        identity == "linux/amd64 " + cast(str, params["revision"]),
        "watcher image revision differs",
    )
    isolated_preflight(layout, preflight, target)
    _ = checked(layout, before, cast(str, service(current)["image"]))
    host.require(
        layout.compose.read_bytes() == content, "live compose changed during preflight"
    )
    host.write_once(target / "prepared.json", host.canonical(params))
    return {
        "event": "watcher_staged",
        "revision": params["revision"],
        "preserved": True,
    }


def transition(
    layout: host.Layout, params: dict[str, object], *, rollback: bool
) -> dict[str, object]:
    target = directory(layout, params)
    prepared = cast(
        dict[str, object], json.loads((target / "prepared.json").read_bytes())
    )
    host.require(prepared == params, "watcher plan differs from staged evidence")
    previous = (target / "previous.json").read_bytes()
    candidate = (target / "candidate.json").read_bytes()
    expected = cast(
        list[list[str]], json.loads((target / "baseline.json").read_bytes())
    )
    current = layout.compose.read_bytes()
    if rollback:
        host.require(
            current in (previous, candidate), "unrelated live compose; rollback refused"
        )
        selected, label = previous, "rollback"
    else:
        host.require(
            current == previous, "live compose changed; inspect before activation retry"
        )
        host.require(
            baseline(layout) == expected, "preserved services changed since staging"
        )
        selected, label = candidate, "activate"
    migration = storage(params)
    if migration is not None:
        swap_credentials(
            cast(dict[str, object], json.loads(previous)), target, restore=rollback
        )
    host.write_private(layout.compose, selected)
    image = cast(str, service(cast(dict[str, object], json.loads(selected)))["image"])
    try:
        up(layout, target / f"{label}.json")
        result = checked(layout, expected, image)
    except (host.HostError, OSError) as error:
        if rollback:
            raise
        if migration is not None:
            swap_credentials(
                cast(dict[str, object], json.loads(previous)), target, restore=True
            )
        host.write_private(layout.compose, previous)
        try:
            up(layout, target / "automatic-rollback.json")
            restored = checked(
                layout,
                expected,
                cast(
                    str, service(cast(dict[str, object], json.loads(previous)))["image"]
                ),
            )
        except (host.HostError, OSError):
            restored = {"restored": False}
        raise host.HostError(
            "watcher activation failed",
            {
                "recovery": restored,
                "failure": error.details if isinstance(error, host.HostError) else {},
            },
        ) from None
    receipt = {
        "event": "watcher_rolled_back" if rollback else "watcher_activated",
        "revision": params["revision"],
        **result,
    }
    host.write_private(target / f"{label}-receipt.json", host.canonical(receipt))
    return receipt


def activate(layout: host.Layout, params: dict[str, object]) -> dict[str, object]:
    return transition(layout, params, rollback=False)


def rollback(layout: host.Layout, params: dict[str, object]) -> dict[str, object]:
    return transition(layout, params, rollback=True)
