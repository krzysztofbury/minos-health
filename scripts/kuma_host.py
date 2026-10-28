"""Uptime Kuma image releases sharing the validation deployment lock (#274).

Kuma keeps its state in a bind-mounted data directory that a new release may
migrate, and Ansible pins that data to one image through a marker file it
refuses to change. A release therefore stops Kuma, keeps a stopped copy of the
whole directory, rewrites the marker, and restores that copy on automatic
recovery and on rollback, because an older Kuma cannot be trusted to read data
a newer one migrated. Only the ``uptime-kuma`` image changes; every other
service keeps its container.
"""

import copy
import json
import os
import re
import shutil
import stat
import time
from pathlib import Path
from typing import cast

from scripts import tunnel_host as single
from scripts import validation_host as host

NAME = "uptime-kuma"
IMAGE = re.compile(r"docker\.io/louislam/uptime-kuma@sha256:[0-9a-f]{64}")
VERSION = re.compile(r"[0-9]{1,2}\.[0-9]{1,3}\.[0-9]{1,3}")
# Numeric and never root; Ansible pins 10003:10003, the smoke runs as its user.
USER = re.compile(r"[1-9][0-9]{0,9}:[1-9][0-9]{0,9}")
DATA_TARGET = "/app/data"
MARKER = ".minos-image-reference"
MAX_MARKER_BYTES = 256
# Compose waits out the 30-second start period and six 10-second retries
# before it reports an unhealthy Kuma.
WAIT_TIMEOUT_S = 180
COPY_TIMEOUT_S = 300


def service(document: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, dict[str, object]], document["services"])[NAME]


def planned(params: dict[str, object]) -> tuple[str, str, str]:
    image, version = params.get("image"), params.get("version")
    compose = params.get("compose_sha256")
    host.require(
        isinstance(compose, str) and host.DIGEST.fullmatch(compose) is not None,
        "compose digest is invalid",
    )
    host.require(
        isinstance(image, str) and IMAGE.fullmatch(image) is not None,
        "Uptime Kuma image must be pinned by digest",
    )
    host.require(
        isinstance(version, str) and VERSION.fullmatch(version) is not None,
        "Uptime Kuma version is invalid",
    )
    return cast(str, image), cast(str, version), cast(str, compose)


def directory(layout: host.Layout, image: str, compose: str) -> Path:
    # Keyed like tunnel-stage: a plan against another live Compose never meets
    # these write-once records.
    return layout.root / "kuma-stage" / f"{image.rsplit(':', 1)[1]}-{compose}"


def user(document: dict[str, object]) -> str:
    value = service(document).get("user")
    host.require(
        isinstance(value, str) and USER.fullmatch(value) is not None,
        "Uptime Kuma user must be a numeric non-root identity",
    )
    return cast(str, value)


def data_source(document: dict[str, object]) -> Path:
    sources: list[str] = []
    for mount in cast(list[object], service(document).get("volumes", [])):
        if isinstance(mount, str) and mount.split(":")[1:2] == [DATA_TARGET]:
            host.require(
                mount.split(":")[2:] in ([], ["rw"]), "Uptime Kuma data mount differs"
            )
            sources.append(mount.split(":")[0])
        elif (
            isinstance(mount, dict)
            and cast(dict[str, object], mount).get("target") == DATA_TARGET
        ):
            entry = cast(dict[str, object], mount)
            host.require(
                entry.get("type") == "bind" and entry.get("read_only") is not True,
                "Uptime Kuma data mount differs",
            )
            sources.append(cast(str, entry["source"]))
    host.require(len(sources) == 1, "Uptime Kuma data mount differs")
    source = Path(sources[0])
    host.require(
        source.is_absolute() and source.is_dir() and not source.is_symlink(),
        "Uptime Kuma data directory is invalid",
    )
    return source


def render(document: dict[str, object], image: str) -> dict[str, object]:
    host.require(IMAGE.fullmatch(image) is not None, "Uptime Kuma image must be pinned")
    result = copy.deepcopy(document)
    _ = user(result)
    service(result)["image"] = image
    return result


def marker(data: Path) -> str:
    path = data / MARKER
    details = path.lstat()
    host.require(
        stat.S_ISREG(details.st_mode) and details.st_size <= MAX_MARKER_BYTES,
        "Uptime Kuma data image marker is invalid",
    )
    return path.read_text().strip()


def write_marker(data: Path, image: str) -> None:
    """Replace the marker atomically, keeping the owner and mode Ansible set."""
    path = data / MARKER
    details = path.lstat()
    temporary = data / f".{MARKER}.partial"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as target:
        _ = target.write(f"{image}\n".encode())
        target.flush()
        os.fsync(target.fileno())
    os.chown(temporary, details.st_uid, details.st_gid)
    os.chmod(temporary, stat.S_IMODE(details.st_mode))
    os.replace(temporary, path)


def size_mib(path: Path) -> int:
    total = 0
    for parent, _, files in os.walk(path):
        total += sum((Path(parent) / name).lstat().st_size for name in files)
    return total // (1024 * 1024) + 1


def require_space(data: Path, target: Path) -> None:
    # One copy lands in the stage directory and a restore stages another one
    # beside the data directory.
    needed = size_mib(data) + host.MIN_AVAILABLE_MIB
    for path in (data.parent, target):
        details = os.statvfs(path)
        free = details.f_bavail * details.f_frsize // (1024 * 1024)
        host.require(free >= needed, "not enough free disk for the Kuma data copy")


def copy_tree(source: Path, destination: Path) -> None:
    # cp -a keeps the owner, mode and timestamps of every file, which a
    # restored Kuma needs; the host has GNU coreutils but only the stdlib.
    _ = host.run(["cp", "-a", "--", str(source), str(destination)], COPY_TIMEOUT_S)


def stop(layout: host.Layout, record: Path) -> None:
    result = host.bounded_command(
        [
            "docker",
            "compose",
            "--project-name",
            layout.project,
            "--file",
            str(layout.compose),
            "stop",
            NAME,
        ]
    )
    host.write_private(record, host.canonical(result))
    if result["status"] != "completed":
        raise host.HostError("Uptime Kuma stop failed", result)
    rows = [row for row in host.containers(layout) if row[0] == NAME]
    # The data directory is copied only while no Kuma process holds SQLite.
    host.require(len(rows) == 1 and rows[0][3] == "exited", "Uptime Kuma did not stop")


def back_up(data: Path, target: Path) -> None:
    backup, partial = target / "data", target / "data.partial"
    # A backup left by an earlier attempt predates data Kuma has written since
    # it was restored or rolled back; the stopped live directory supersedes it.
    for stale in (backup, partial):
        if stale.exists():
            shutil.rmtree(stale)
    copy_tree(data, partial)
    os.rename(partial, backup)


def restore(data: Path, target: Path, label: str) -> None:
    """Put the stopped backup in place and keep the replaced directory."""
    backup = target / "data"
    staging = data.with_name(f".{data.name}.restoring")
    replaced = data.with_name(f".{data.name}.replaced")
    host.require(not replaced.exists(), "an interrupted Kuma restore needs inspection")
    if staging.exists():
        shutil.rmtree(staging)
    copy_tree(backup, staging)
    os.rename(data, replaced)
    os.rename(staging, data)
    stamp = time.time_ns()
    # The backup is consumed: a later rollback must not rewind data again.
    os.rename(backup, target / f"data-restored-{label}-{stamp}")
    _ = shutil.move(replaced, target / f"replaced-{label}-{stamp}")


def verify(image: str, version: str, identity: str, target: Path) -> None:
    pulled = host.bounded_command(["docker", "pull", "-q", image])
    host.write_private(target / "pull.json", host.canonical(pulled))
    host.require(pulled["status"] == "completed", "Uptime Kuma image pull failed")
    inspected = host.run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Os}}/{{.Architecture}} {{.Config.User}}",
            image,
        ],
        host.DOCKER_TIMEOUT_S,
    ).strip()
    host.require(
        inspected == "linux/amd64 node", "Uptime Kuma image platform or user differs"
    )
    reported = host.run(
        single.isolated(
            image,
            ["--entrypoint", "node"],
            ["-p", "require('/app/package.json').version"],
            identity,
        ),
        host.DOCKER_TIMEOUT_S,
    ).strip()
    host.require(reported == version, "Uptime Kuma image reports another version")


def stage(layout: host.Layout, params: dict[str, object]) -> dict[str, object]:
    image, version, compose = planned(params)
    target = directory(layout, image, compose)
    content, current = host.load_compose(layout.compose)
    host.require(
        host.sha256_bytes(content) == compose, "live compose differs from Kuma plan"
    )
    before = single.baseline(layout, NAME)
    candidate = render(current, image)
    live_image = cast(str, service(current)["image"])
    host.require(live_image != image, "Uptime Kuma already uses candidate")
    data = data_source(current)
    host.require(
        marker(data) == live_image, "Uptime Kuma data marker differs from live image"
    )
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    host.write_once(target / "previous.json", content)
    host.write_once(target / "candidate.json", host.canonical(candidate))
    host.write_once(target / "baseline.json", host.canonical(before))
    verify(image, version, user(current), target)
    require_space(data, target)
    _ = single.checked(layout, before, live_image, NAME, fresh=False)
    host.require(
        layout.compose.read_bytes() == content, "live compose changed during staging"
    )
    host.write_once(target / "prepared.json", host.canonical(params))
    return {"event": "kuma_staged", "image": image, "version": version}


def recover(
    layout: host.Layout,
    target: Path,
    previous: bytes,
    expected: list[list[str]],
    *,
    started: bool,
) -> dict[str, object]:
    """Return to the previous image, and to the backup once the candidate ran."""
    document = cast(dict[str, object], json.loads(previous))
    image, data = cast(str, service(document)["image"]), data_source(document)
    try:
        if started:
            stop(layout, target / "automatic-rollback-stop.json")
            restore(data, target, "activation")
        elif marker(data) != image:
            write_marker(data, image)
        host.write_private(layout.compose, previous)
        single.up(
            layout,
            target / "automatic-rollback.json",
            force=True,
            name=NAME,
            wait_s=WAIT_TIMEOUT_S,
        )
        return {
            **single.checked(layout, expected, image, NAME),
            "data_restored": started,
        }
    except (host.HostError, OSError):
        return {"restored": False}


def activate(layout: host.Layout, params: dict[str, object]) -> dict[str, object]:
    image, version, compose = planned(params)
    target = directory(layout, image, compose)
    prepared = cast(
        dict[str, object], json.loads((target / "prepared.json").read_bytes())
    )
    host.require(prepared == params, "Kuma plan differs from staged evidence")
    previous = (target / "previous.json").read_bytes()
    candidate = (target / "candidate.json").read_bytes()
    expected = cast(
        list[list[str]], json.loads((target / "baseline.json").read_bytes())
    )
    host.require(
        layout.compose.read_bytes() == previous,
        "live compose changed; inspect before activation retry",
    )
    host.require(
        single.baseline(layout, NAME) == expected,
        "preserved services changed since staging",
    )
    old = cast(dict[str, object], json.loads(previous))
    data = data_source(old)
    host.require(
        marker(data) == service(old)["image"],
        "Uptime Kuma data marker differs from live image",
    )
    started = False
    try:
        stop(layout, target / "stop.json")
        back_up(data, target)
        write_marker(data, image)
        host.write_private(layout.compose, candidate)
        started = True
        single.up(layout, target / "activate.json", name=NAME, wait_s=WAIT_TIMEOUT_S)
        result = single.checked(layout, expected, image, NAME)
    except (host.HostError, OSError) as error:
        raise host.HostError(
            "Uptime Kuma activation failed",
            {
                "recovery": recover(
                    layout, target, previous, expected, started=started
                ),
                "failure": error.details if isinstance(error, host.HostError) else {},
            },
        ) from None
    receipt = {"event": "kuma_activated", "image": image, "version": version, **result}
    host.write_private(target / "activate-receipt.json", host.canonical(receipt))
    return receipt


def rollback(layout: host.Layout, params: dict[str, object]) -> dict[str, object]:
    image, _, compose = planned(params)
    target = directory(layout, image, compose)
    prepared = cast(
        dict[str, object], json.loads((target / "prepared.json").read_bytes())
    )
    host.require(prepared == params, "Kuma plan differs from staged evidence")
    previous = (target / "previous.json").read_bytes()
    candidate = (target / "candidate.json").read_bytes()
    host.require(
        layout.compose.read_bytes() in (previous, candidate),
        "unrelated live compose; rollback refused",
    )
    old = cast(dict[str, object], json.loads(previous))
    running, data = cast(str, service(old)["image"]), data_source(old)
    # Judged against the services as they run now, like a tunnel rollback.
    expected = [list(row[:3]) for row in host.containers(layout) if row[0] != NAME]
    host.write_private(target / "rollback-baseline.json", host.canonical(expected))
    restored = (target / "data").is_dir()
    if restored:
        stop(layout, target / "rollback-stop.json")
        restore(data, target, "rollback")
    else:
        # Nothing to rewind: the backup was already restored, or the candidate
        # never started, so the data still belongs to the previous image.
        host.require(
            marker(data) == running, "Uptime Kuma data has no backup to restore"
        )
    host.write_private(layout.compose, previous)
    single.up(
        layout,
        target / "rollback.json",
        force=True,
        name=NAME,
        wait_s=WAIT_TIMEOUT_S,
    )
    result = single.checked(layout, expected, running, NAME)
    receipt = {
        "event": "kuma_rolled_back",
        "image": running,
        "data_restored": restored,
        **result,
    }
    host.write_private(target / "rollback-receipt.json", host.canonical(receipt))
    return receipt
