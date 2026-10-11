"""Stateful stand-in for the docker CLI calls made by scripts/validation_host.py.

State lives in the JSON file named by FAKE_DOCKER_STATE so a test can arrange
containers and images, inject failures and inspect every recorded call.
"""

import json
import os
import sys
from pathlib import Path
from typing import cast

STATE = Path(os.environ["FAKE_DOCKER_STATE"])


def load() -> dict[str, object]:
    return cast(dict[str, object], json.loads(STATE.read_text()))


def save(state: dict[str, object]) -> None:
    _ = STATE.write_text(json.dumps(state, indent=2, sort_keys=True))


def image_id(state: dict[str, object], reference: str) -> str:
    for identifier, image in cast(
        dict[str, dict[str, object]], state["images"]
    ).items():
        if reference in cast(list[str], image["references"]) or reference == identifier:
            return identifier
    raise KeyError(reference)


def compose_up(state: dict[str, object], arguments: list[str]) -> int:
    if cast(int, state["fail_up"]) > 0:
        state["fail_up"] = cast(int, state["fail_up"]) - 1
        print(
            "dependency failed to start: container is unhealthy PRIVATE_TOKEN=/private/path",
            file=sys.stderr,
        )
        return 1
    compose = cast(
        dict[str, dict[str, dict[str, str]]],
        json.loads(Path(arguments[arguments.index("--file") + 1]).read_text()),
    )
    containers = cast(dict[str, dict[str, object]], state["containers"])
    for service in ("reference-api", "edge"):
        image = compose["services"][service]["image"]
        old = next(
            (key for key, value in containers.items() if value["service"] == service),
            None,
        )
        if old is not None:
            del containers[old]
        state["sequence"] = cast(int, state["sequence"]) + 1
        containers[f"{service}-{state['sequence']}"] = {
            "service": service,
            "image": image_id(state, image),
            "restarts": 0,
            "health": "healthy",
        }
        if service == "reference-api" and state.get("fail_after_api"):
            edge = next(
                value for value in containers.values() if value["service"] == "edge"
            )
            edge["health"] = "created"
            print(
                "dependency failed to start: container is unhealthy PRIVATE_TOKEN=/private/path",
                file=sys.stderr,
            )
            return 1
    if state.get("omit_edge"):
        for identifier, container in tuple(containers.items()):
            if container["service"] == "edge":
                del containers[identifier]
    for container in containers.values():
        if container["service"] == "edge":
            if state.get("unhealthy_edge"):
                container["health"] = "unhealthy"
            if state.get("wrong_edge_image"):
                container["image"] = "stale-api"
    if state.get("restart_watcher_on_up"):
        watcher = next(
            value for value in containers.values() if value["service"] == "watcher"
        )
        watcher["restarts"] = cast(int, watcher["restarts"]) + 1
    return 0


def main(arguments: list[str]) -> int:
    state = load()
    cast(list[list[str]], state["calls"]).append(arguments)
    containers = cast(dict[str, dict[str, object]], state["containers"])
    images = cast(dict[str, dict[str, object]], state["images"])
    output: list[str] = []
    status = 0
    match arguments:
        case ["ps", "--all", "--no-trunc", "--filter", _, "--format", _]:
            output = [f"{key} {value['service']}" for key, value in containers.items()]
        case ["ps", "-aq", "--no-trunc"]:
            output = list(containers)
        case ["inspect", "--format", "{{.Image}}", *identifiers]:
            output = [cast(str, containers[key]["image"]) for key in identifiers]
        case ["inspect", "--format", "{{.Config.Image}}", identifier]:
            output = [
                cast(
                    list[str],
                    images[cast(str, containers[identifier]["image"])]["references"],
                )[0]
            ]
        case ["inspect", "--format", _, *identifiers]:
            output = [
                f"{key} {containers[key]['restarts']} {containers[key]['health']}"
                for key in identifiers
            ]
        case ["pull", "-q", reference]:
            status = 0 if reference in cast(dict[str, str], state["registry"]) else 1
            if status == 0 and not any(
                reference in cast(list[str], image["references"])
                for image in images.values()
            ):
                images["pulled-" + reference[-12:]] = {
                    "references": [reference],
                    "size": 1,
                    "revision": cast(dict[str, str], state["registry"])[reference],
                }
        case ["image", "inspect", "--format", _, reference]:
            output = [f"linux/amd64 {images[image_id(state, reference)]['revision']}"]
        case ["image", "ls", "-q", "--no-trunc"]:
            output = list(images)
        case ["image", "inspect", *identifiers]:
            output = [
                json.dumps(
                    [
                        {
                            "Id": key,
                            "RepoDigests": images[key]["references"],
                            "Size": images[key]["size"],
                        }
                        for key in identifiers
                    ]
                )
            ]
        case ["image", "rm", *references]:
            identifier = image_id(state, references[0])
            if any(value["image"] == identifier for value in containers.values()):
                status = 1
            else:
                del images[identifier]
        case ["compose", *rest] if "up" in rest:
            status = compose_up(state, rest)
        case _:
            status = 64
    save(state)
    print("\n".join(output))
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
