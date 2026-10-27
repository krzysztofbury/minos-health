"""Tunnel-only cloudflared image transitions sharing the validation deployment lock.

Validation releases keep the tunnel container unchanged and the maintenance
playbook never runs over a validation deployment, so a cloudflared security
release needs its own bounded path (#270). Only the ``cloudflared`` image in the
live Compose changes; a changed command or user is refused.
"""

import copy
import json
import re
from pathlib import Path
from typing import cast

from scripts import validation_host as host

IMAGE = re.compile(r"docker\.io/cloudflare/cloudflared@sha256:[0-9a-f]{64}")
VERSION = re.compile(r"20[0-9]{2}\.[0-9]{1,2}\.[0-9]{1,3}")
COMMAND = ["tunnel", "--config", "/etc/cloudflared/config.yml", "run"]
CONFIG_TARGET = "/etc/cloudflared/config.yml"
USER = "65532:65532"
# Exactly one reference API colour is preserved beside these services (#192).
PRESERVED = {"edge", "watcher", "uptime-kuma"}
# A single tunnel instance: the public site is unreachable while it restarts.
WAIT_TIMEOUT_S = 120


def service(document: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, dict[str, object]], document["services"])["cloudflared"]


def planned(params: dict[str, object]) -> tuple[str, str, str]:
    image, version = params.get("image"), params.get("version")
    compose = params.get("compose_sha256")
    host.require(
        isinstance(compose, str) and host.DIGEST.fullmatch(compose) is not None,
        "compose digest is invalid",
    )
    host.require(
        isinstance(image, str) and IMAGE.fullmatch(image) is not None,
        "cloudflared image must be pinned by digest",
    )
    host.require(
        isinstance(version, str) and VERSION.fullmatch(version) is not None,
        "cloudflared version is invalid",
    )
    return cast(str, image), cast(str, version), cast(str, compose)


def directory(layout: host.Layout, image: str, compose: str) -> Path:
    # Keyed by the starting Compose too: the digest outlives main revisions, and
    # a plan against a newer live Compose must not meet stale write-once records.
    return layout.root / "tunnel-stage" / f"{image.rsplit(':', 1)[1]}-{compose}"


def config_source(document: dict[str, object]) -> str:
    mounts = [
        cast(dict[str, object], mount)
        for mount in cast(list[object], service(document).get("volumes", []))
        if isinstance(mount, dict)
        and cast(dict[str, object], mount).get("target") == CONFIG_TARGET
    ]
    host.require(
        len(mounts) == 1 and mounts[0].get("read_only") is True,
        "cloudflared configuration mount differs",
    )
    return cast(str, mounts[0]["source"])


def render(document: dict[str, object], image: str) -> dict[str, object]:
    host.require(IMAGE.fullmatch(image) is not None, "cloudflared image must be pinned")
    result = copy.deepcopy(document)
    selected = service(result)
    host.require(selected.get("command") == COMMAND, "cloudflared command differs")
    host.require(selected.get("user") == USER, "cloudflared user differs")
    selected["image"] = image
    return result


def baseline(layout: host.Layout) -> list[list[str]]:
    records = host.containers(layout)
    services = {row[0] for row in records}
    apis = services & set(host.API_SERVICES)
    host.require(
        len(apis) == 1 and services - apis == PRESERVED | {"cloudflared"},
        "unexpected live services",
    )
    host.require(len(records) == 5, "duplicate live services")
    host.require(
        all(row[3] == "healthy" for row in records), "live services must be healthy"
    )
    return [list(row[:3]) for row in records if row[0] != "cloudflared"]


def state(
    layout: host.Layout, expected: list[list[str]], image: str
) -> dict[str, object]:
    records = host.containers(layout)
    unchanged = [
        list(row[:3]) for row in records if row[0] != "cloudflared"
    ] == expected
    healthy = len(records) == 5 and all(row[3] == "healthy" for row in records)
    tunnels = [row for row in records if row[0] == "cloudflared"]
    matching = False
    if len(tunnels) == 1:
        running = host.run(
            ["docker", "inspect", "--format", "{{.Config.Image}}", tunnels[0][1]],
            host.DOCKER_TIMEOUT_S,
        ).strip()
        matching = running == image and tunnels[0][2] == "0"
    return {
        "preserved": unchanged,
        "healthy": healthy,
        "cloudflared_image_matches": matching,
    }


def checked(
    layout: host.Layout, expected: list[list[str]], image: str
) -> dict[str, object]:
    result = state(layout, expected, image)
    host.require(
        all(value is True for value in result.values()),
        "tunnel transition did not pass health/isolation checks",
    )
    return result


def up(layout: host.Layout, record: Path, *, force: bool = False) -> None:
    result = host.bounded_command(
        [
            "docker",
            "compose",
            "--project-name",
            layout.project,
            "--file",
            str(layout.compose),
            "up",
            "--detach",
            "--no-deps",
            # An unhealthy tunnel already on the selected Compose is otherwise
            # left as it is; explicit rollback must replace it.
            *(["--force-recreate"] if force else []),
            "--wait",
            "--wait-timeout",
            str(WAIT_TIMEOUT_S),
            "cloudflared",
        ]
    )
    host.write_private(record, host.canonical(result))
    if result["status"] != "completed":
        raise host.HostError("tunnel command failed", result)


def isolated(image: str, options: list[str], command: list[str]) -> list[str]:
    """A one-shot of the candidate binary with no network and no privileges."""
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--user",
        USER,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        "128m",
        "--pids-limit",
        "64",
        *options,
        image,
        *command,
    ]


def verify(image: str, version: str, config: str, target: Path) -> None:
    pulled = host.bounded_command(["docker", "pull", "-q", image])
    host.write_private(target / "pull.json", host.canonical(pulled))
    host.require(pulled["status"] == "completed", "cloudflared image pull failed")
    platform = host.run(
        ["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image],
        host.DOCKER_TIMEOUT_S,
    ).strip()
    host.require(platform == "linux/amd64", "cloudflared image platform differs")
    reported = host.run(
        isolated(image, [], ["--version"]), host.DOCKER_TIMEOUT_S
    ).strip()
    host.require(
        reported.startswith(f"cloudflared version {version} "),
        "cloudflared image reports another version",
    )
    # The live ingress must parse with the candidate binary before any swap.
    validated = host.bounded_command(
        isolated(
            image,
            ["--volume", f"{config}:{CONFIG_TARGET}:ro"],
            ["tunnel", "--config", CONFIG_TARGET, "ingress", "validate"],
        )
    )
    host.write_private(target / "ingress.json", host.canonical(validated))
    host.require(validated["status"] == "completed", "cloudflared ingress is invalid")


def stage(layout: host.Layout, params: dict[str, object]) -> dict[str, object]:
    image, version, compose = planned(params)
    target = directory(layout, image, compose)
    content, current = host.load_compose(layout.compose)
    host.require(
        host.sha256_bytes(content) == params["compose_sha256"],
        "live compose differs from tunnel plan",
    )
    before = baseline(layout)
    candidate = render(current, image)
    live_image = cast(str, service(current)["image"])
    host.require(live_image != image, "cloudflared already uses candidate")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    host.write_once(target / "previous.json", content)
    host.write_once(target / "candidate.json", host.canonical(candidate))
    host.write_once(target / "baseline.json", host.canonical(before))
    verify(image, version, config_source(current), target)
    _ = checked(layout, before, live_image)
    host.require(
        layout.compose.read_bytes() == content, "live compose changed during staging"
    )
    host.write_once(target / "prepared.json", host.canonical(params))
    return {"event": "tunnel_staged", "image": image, "version": version}


def transition(
    layout: host.Layout, params: dict[str, object], *, rollback: bool
) -> dict[str, object]:
    image, version, compose = planned(params)
    target = directory(layout, image, compose)
    prepared = cast(
        dict[str, object], json.loads((target / "prepared.json").read_bytes())
    )
    host.require(prepared == params, "tunnel plan differs from staged evidence")
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
        # Judge rollback against the services as they run now, not at staging:
        # a later watcher restart must not turn a good rollback into a failure.
        expected = [
            list(row[:3]) for row in host.containers(layout) if row[0] != "cloudflared"
        ]
        host.write_private(target / "rollback-baseline.json", host.canonical(expected))
        selected, label = previous, "rollback"
    else:
        host.require(
            current == previous, "live compose changed; inspect before activation retry"
        )
        host.require(
            baseline(layout) == expected, "preserved services changed since staging"
        )
        selected, label = candidate, "activate"
    host.write_private(layout.compose, selected)
    running = cast(str, service(cast(dict[str, object], json.loads(selected)))["image"])
    try:
        up(layout, target / f"{label}.json", force=rollback)
        result = checked(layout, expected, running)
    except (host.HostError, OSError) as error:
        if rollback:
            raise
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
            "tunnel activation failed",
            {
                "recovery": restored,
                "failure": error.details if isinstance(error, host.HostError) else {},
            },
        ) from None
    receipt = {
        "event": "tunnel_rolled_back" if rollback else "tunnel_activated",
        "image": running,
        "version": None if rollback else version,
        **result,
    }
    host.write_private(target / f"{label}-receipt.json", host.canonical(receipt))
    return receipt


def activate(layout: host.Layout, params: dict[str, object]) -> dict[str, object]:
    return transition(layout, params, rollback=False)


def rollback(layout: host.Layout, params: dict[str, object]) -> dict[str, object]:
    return transition(layout, params, rollback=True)
