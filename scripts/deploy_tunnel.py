"""Release a reviewed cloudflared image without touching the other services (#270)."""

import argparse
import json
import time
from pathlib import Path
from typing import ClassVar, Literal, cast, final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scripts.deploy_validation import (
    PUBLIC_BASE,
    ROOT,
    DeployError,
    Host,
    IngressReady,
    get,
    run,
)

TEMPLATE = "infra/ansible/templates/minos-compose.yml.j2"
# The tunnel reconnects after --wait reports it healthy; probe a few times,
# paced, before calling public ingress failed.
PROBES = 5
PROBE_INTERVAL_S = 3.0


class TunnelPlan(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    image: str = Field(
        pattern=r"^docker\.io/cloudflare/cloudflared@sha256:[0-9a-f]{64}$"
    )
    version: str = Field(pattern=r"^20[0-9]{2}\.[0-9]{1,2}\.[0-9]{1,3}$")
    compose_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@final
class Arguments(argparse.Namespace):
    command: str = ""
    plan: Path = Path()
    revision: str | None = None
    image: str | None = None
    version: str | None = None


def program() -> bytes:
    # The remote host has only the standard library. Bundle the same checked-in
    # modules, retaining validation_host's structured refusal protocol and lock.
    common = (ROOT / "scripts/validation_host.py").read_text()
    tunnel = (ROOT / "scripts/tunnel_host.py").read_text()
    return f"""import sys, types
package = types.ModuleType('scripts')
sys.modules['scripts'] = package
common = types.ModuleType('scripts.validation_host')
sys.modules[common.__name__] = common
setattr(package, 'validation_host', common)
exec(compile({common!r}, 'validation_host.py', 'exec'), common.__dict__)
tunnel = types.ModuleType('scripts.tunnel_host')
exec(compile({tunnel!r}, 'tunnel_host.py', 'exec'), tunnel.__dict__)
common.MUTATING = {{'stage': tunnel.stage, 'activate': tunnel.activate, 'rollback': tunnel.rollback}}
sys.exit(common.main(sys.argv))
""".encode()


def reviewed(plan: TunnelPlan) -> None:
    """The digest is the one reviewed into the Compose template on remote main."""
    head = run(["git", "ls-remote", "origin", "refs/heads/main"]).split()[0]
    if head != plan.revision:
        raise DeployError("tunnel revision is not current remote main")
    _ = run(["git", "fetch", "--quiet", "origin", "main"])
    template = run(["git", "show", f"{plan.revision}:{TEMPLATE}"])
    if f"    image: {plan.image}\n" not in template:
        raise DeployError("cloudflared digest is not the pin on remote main")
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
    if {"conclusion": "success", "event": "workflow_dispatch"} not in checks:
        raise DeployError("tunnel main CI has not passed")


def ingress() -> dict[str, object]:
    """Container health is local; the tunnel is only proven by the public route."""
    status = 0
    for attempt in range(PROBES):
        if attempt:
            time.sleep(PROBE_INTERVAL_S)
        try:
            status, body = get(PUBLIC_BASE + "/readyz")
            if status == 200:
                _ = IngressReady.model_validate_json(body)
                return {"public_ingress": "healthy", "readyz_status": status}
        except OSError, ValueError:
            status = 0
    raise DeployError(
        "tunnel container is healthy but public readiness failed; see the host"
        + " receipt, re-probe, or roll back",
        {"public_ingress": "failed", "readyz_status": status},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "command", choices=("plan", "stage", "activate", "rollback")
    )
    _ = parser.add_argument("--plan", type=Path, required=True)
    _ = parser.add_argument("--revision")
    _ = parser.add_argument("--image")
    _ = parser.add_argument("--version")
    arguments = Arguments()
    _ = parser.parse_args(namespace=arguments)
    command = cast(Literal["plan", "stage", "activate", "rollback"], arguments.command)
    path = arguments.plan
    try:
        host = Host()
        if command == "plan":
            facts = host.call("inspect", {})
            plan = TunnelPlan.model_validate(
                {
                    "revision": arguments.revision,
                    "image": arguments.image,
                    "version": arguments.version,
                    "compose_sha256": facts["compose_sha256"],
                }
            )
            reviewed(plan)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("x") as target:
                _ = target.write(plan.model_dump_json(indent=2) + "\n")
            print(json.dumps({"event": "tunnel_planned", "version": plan.version}))
            return
        plan = TunnelPlan.model_validate_json(path.read_bytes())
        if command != "rollback":
            reviewed(plan)
        params = plan.model_dump(exclude={"revision"})
        result = host.call(command, params, program=program())
        # Record the host transition before probing, so a failed probe never
        # hides what changed on the host.
        receipt = path.with_name(command + "-receipt.json")
        _ = receipt.write_text(json.dumps(result, indent=2) + "\n")
        if command != "stage":
            result = {**result, **ingress()}
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
