"""Release a reviewed watcher without restarting the public or monitoring services."""

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import ClassVar, Literal, cast, final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scripts.deploy_validation import ROOT, DeployError, Host, run


class WatcherPlan(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    image: str = Field(
        pattern=r"^ghcr\.io/krzysztofbury/minos-health-watcher@sha256:[0-9a-f]{64}$"
    )
    compose_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    publish_run: int = Field(ge=1)


class WatcherRelease(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    image: str = Field(
        pattern=r"^ghcr\.io/krzysztofbury/minos-health-watcher@sha256:[0-9a-f]{64}$"
    )
    publish_run: int = Field(ge=1)
    run_attempt: int = Field(ge=1, le=100)


@final
class Arguments(argparse.Namespace):
    command: str = ""
    plan: Path = Path()
    revision: str | None = None
    image: str | None = None
    publish_run: int | None = None


def program() -> bytes:
    # The remote host has only the standard library. Bundle the same checked-in
    # modules, retaining validation_host's structured refusal protocol and lock.
    common = (ROOT / "scripts/validation_host.py").read_text()
    watcher = (ROOT / "scripts/watcher_host.py").read_text()
    return f"""import sys, types
package = types.ModuleType('scripts')
sys.modules['scripts'] = package
common = types.ModuleType('scripts.validation_host')
sys.modules[common.__name__] = common
setattr(package, 'validation_host', common)
exec(compile({common!r}, 'validation_host.py', 'exec'), common.__dict__)
watcher = types.ModuleType('scripts.watcher_host')
exec(compile({watcher!r}, 'watcher_host.py', 'exec'), watcher.__dict__)
common.MUTATING = {{'stage': watcher.stage, 'activate': watcher.activate, 'rollback': watcher.rollback}}
sys.exit(common.main(sys.argv))
""".encode()


def reviewed(plan: WatcherPlan) -> None:
    head = run(["git", "ls-remote", "origin", "refs/heads/main"]).split()[0]
    if head != plan.revision:
        raise DeployError("watcher revision is not current remote main")
    publication = cast(
        dict[str, object],
        json.loads(
            run(
                [
                    "gh",
                    "run",
                    "view",
                    str(plan.publish_run),
                    "--json",
                    "headSha,conclusion,workflowName,headBranch,event",
                ]
            )
        ),
    )
    if publication != {
        "headSha": plan.revision,
        "conclusion": "success",
        "workflowName": "Publish watcher image",
        "headBranch": "main",
        "event": "workflow_dispatch",
    }:
        raise DeployError("watcher publication is not a successful reviewed main build")
    checks = cast(
        list[dict[str, object]],
        json.loads(
            run(
                [
                    "gh",
                    "run",
                    "list",
                    "--workflow",
                    "ci.yml",
                    "--branch",
                    "main",
                    "--commit",
                    plan.revision,
                    "--json",
                    "conclusion,event",
                    "--limit",
                    "20",
                ]
            )
        ),
    )
    if not any(check == {"conclusion": "success", "event": "push"} for check in checks):
        raise DeployError("watcher main CI has not passed")
    with tempfile.TemporaryDirectory(
        prefix="watcher-release-", dir=os.environ.get("TMPDIR", "/tmp/opencode")
    ) as scratch:
        _ = run(
            [
                "gh",
                "run",
                "download",
                str(plan.publish_run),
                "--name",
                "watcher-release",
                "--dir",
                scratch,
            ]
        )
        release = WatcherRelease.model_validate_json(
            (Path(scratch) / "watcher-release.json").read_bytes()
        )
    if (release.revision, release.image, release.publish_run) != (
        plan.revision,
        plan.image,
        plan.publish_run,
    ):
        raise DeployError("watcher digest differs from published release artifact")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "command", choices=("plan", "stage", "activate", "rollback")
    )
    _ = parser.add_argument("--plan", type=Path, required=True)
    _ = parser.add_argument("--revision")
    _ = parser.add_argument("--image")
    _ = parser.add_argument("--publish-run", type=int)
    arguments = Arguments()
    _ = parser.parse_args(namespace=arguments)
    command = cast(Literal["plan", "stage", "activate", "rollback"], arguments.command)
    path = arguments.plan
    try:
        host = Host()
        if command == "plan":
            facts = host.call("inspect", {})
            plan = WatcherPlan.model_validate(
                {
                    "revision": arguments.revision,
                    "image": arguments.image,
                    "compose_sha256": facts["compose_sha256"],
                    "publish_run": arguments.publish_run,
                }
            )
            reviewed(plan)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("x") as target:
                _ = target.write(plan.model_dump_json(indent=2) + "\n")
            print(json.dumps({"event": "watcher_planned", "revision": plan.revision}))
            return
        plan = WatcherPlan.model_validate_json(path.read_bytes())
        if command != "rollback":
            reviewed(plan)
        result = host.call(command, plan.model_dump(), program=program())
        receipt = path.with_name(command + "-receipt.json")
        _ = receipt.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
    except (DeployError, ValidationError, OSError) as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(error)
                    if isinstance(error, DeployError)
                    else type(error).__name__,
                    "details": error.details if isinstance(error, DeployError) else {},
                }
            )
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
