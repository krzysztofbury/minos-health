"""Release a reviewed watcher without restarting the public or monitoring services."""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import ClassVar, Literal, cast, final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scripts.deploy_validation import ROOT, DeployError, Host, run


class StorageMigration(BaseModel):
    """One-time AWS S3 to R2 cutover (#200); the key file itself never enters the plan."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    endpoint: str = Field(
        pattern=r"^https://[0-9a-f]{32}(?:\.(?:eu|fedramp))?\.r2\.cloudflarestorage\.com$"
    )
    credentials_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WatcherPlan(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    image: str = Field(
        pattern=r"^ghcr\.io/krzysztofbury/minos-health-watcher@sha256:[0-9a-f]{64}$"
    )
    compose_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    publish_run: int = Field(ge=1)
    storage: StorageMigration | None = None


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
    storage_bucket: str | None = None
    storage_endpoint: str | None = None
    storage_credentials: Path | None = None


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
    # Main CI ran by dispatch only from #174 until #272 restored push.
    passed = (
        {"conclusion": "success", "event": "push"},
        {"conclusion": "success", "event": "workflow_dispatch"},
    )
    if not any(check in passed for check in checks):
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


def upload_credentials(host: Host, plan: WatcherPlan, path: Path | None) -> None:
    """Copy the R2 key file by scp; it never travels in command arguments."""
    if path is None or plan.storage is None:
        raise DeployError("storage migration needs --storage-credentials at stage")
    if hashlib.sha256(path.read_bytes()).hexdigest() != plan.storage.credentials_sha256:
        raise DeployError("R2 credential file differs from the plan")
    if path.stat().st_mode & 0o777 != 0o600:
        raise DeployError("R2 credential file must be mode 0600")
    # scp keeps the 0600 mode; root's home is the only upload directory.
    host.upload(path, f"/root/minos-watcher-{plan.revision}.storage-credentials")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "command", choices=("plan", "stage", "activate", "rollback")
    )
    _ = parser.add_argument("--plan", type=Path, required=True)
    _ = parser.add_argument("--revision")
    _ = parser.add_argument("--image")
    _ = parser.add_argument("--publish-run", type=int)
    _ = parser.add_argument("--storage-bucket")
    _ = parser.add_argument("--storage-endpoint")
    _ = parser.add_argument("--storage-credentials", type=Path)
    arguments = Arguments()
    _ = parser.parse_args(namespace=arguments)
    command = cast(Literal["plan", "stage", "activate", "rollback"], arguments.command)
    path = arguments.plan
    try:
        host = Host()
        if command == "plan":
            facts = host.call("inspect", {})
            storage = None
            flags = (
                arguments.storage_bucket,
                arguments.storage_endpoint,
                arguments.storage_credentials,
            )
            if any(flag is not None for flag in flags) and None in flags:
                raise DeployError(
                    "storage migration needs bucket, endpoint and key file"
                )
            if arguments.storage_credentials is not None:
                storage = {
                    "bucket": arguments.storage_bucket,
                    "endpoint": arguments.storage_endpoint,
                    "credentials_sha256": hashlib.sha256(
                        arguments.storage_credentials.read_bytes()
                    ).hexdigest(),
                }
            plan = WatcherPlan.model_validate(
                {
                    "revision": arguments.revision,
                    "image": arguments.image,
                    "compose_sha256": facts["compose_sha256"],
                    "publish_run": arguments.publish_run,
                    "storage": storage,
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
        if command == "stage" and plan.storage is not None:
            upload_credentials(host, plan, arguments.storage_credentials)
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
