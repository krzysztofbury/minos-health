"""Release a reviewed Uptime Kuma image, restoring its data on rollback (#274)."""

import argparse
import json
import re
from pathlib import Path
from typing import ClassVar, Literal, cast, final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scripts.deploy_tunnel import pinned, remote_main, reviewed
from scripts.deploy_validation import ROOT, DeployError, Host

# Ansible pins the image in site.yml, and the Compose template reads it there.
SITE = "infra/ansible/site.yml"
PIN = re.compile(r"docker\.io/louislam/uptime-kuma@sha256:[0-9a-f]{64}")


class KumaPlan(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    image: str = Field(pattern=r"^docker\.io/louislam/uptime-kuma@sha256:[0-9a-f]{64}$")
    version: str = Field(pattern=r"^[0-9]{1,2}\.[0-9]{1,3}\.[0-9]{1,3}$")
    compose_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@final
class Arguments(argparse.Namespace):
    command: str = ""
    plan: Path = Path()
    revision: str | None = None
    image: str | None = None
    version: str | None = None


def program() -> bytes:
    # The host has only the standard library: bundle the checked-in modules
    # and keep validation_host's refusal protocol and deployment lock.
    modules = {
        "validation_host": (ROOT / "scripts/validation_host.py").read_text(),
        "tunnel_host": (ROOT / "scripts/tunnel_host.py").read_text(),
        "kuma_host": (ROOT / "scripts/kuma_host.py").read_text(),
    }
    return f"""import sys, types
package = types.ModuleType('scripts')
sys.modules['scripts'] = package
for name, source in {modules!r}.items():
    module = types.ModuleType('scripts.' + name)
    sys.modules[module.__name__] = module
    setattr(package, name, module)
    exec(compile(source, name + '.py', 'exec'), module.__dict__)
common, kuma = package.validation_host, package.kuma_host
common.MUTATING = {{'stage': kuma.stage, 'activate': kuma.activate, 'rollback': kuma.rollback}}
sys.exit(common.main(sys.argv))
""".encode()


def check(plan: KumaPlan) -> None:
    reviewed(plan.revision, f"    uptime_kuma_image_reference: {plan.image}\n", SITE)


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
            revision = arguments.revision or remote_main()
            facts = host.call("inspect", {})
            plan = KumaPlan.model_validate(
                {
                    "revision": revision,
                    "image": arguments.image or pinned(revision, PIN, SITE),
                    "version": arguments.version,
                    "compose_sha256": facts["compose_sha256"],
                }
            )
            check(plan)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("x") as target:
                _ = target.write(plan.model_dump_json(indent=2) + "\n")
            print(
                json.dumps(
                    {
                        "event": "kuma_planned",
                        **plan.model_dump(exclude={"compose_sha256"}),
                    }
                )
            )
            return
        plan = KumaPlan.model_validate_json(path.read_bytes())
        if command != "rollback":
            check(plan)
        result = host.call(
            command, plan.model_dump(exclude={"revision"}), program=program()
        )
        _ = path.with_name(command + "-receipt.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
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
