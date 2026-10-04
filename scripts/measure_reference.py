"""Measure a verified bundle in an isolated VPS container without live activation."""

import argparse
import base64
import json
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import cast

from scripts.deploy_validation import (
    ROOT,
    SSH_OPTIONS,
    DeployError,
    Host,
    bundle_archive,
    verify_bundle,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--bundle", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    host = Host()
    state = host.call("inspect", {})
    bundle = cast(Path, args.bundle)
    output_path = cast(Path, args.output)
    database, dataset, _ = verify_bundle(bundle)
    live = cast(dict[str, object], state["live"])
    params = {
        "image": live["api_image"],
        "revision": state["live_revision"],
        "database_sha256": database,
        "dataset_id": dataset,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(params).encode()).decode()
    program = (ROOT / "scripts/measure_reference_host.py").read_text()
    command = "python3 -c " + shlex.quote(program) + " " + shlex.quote(encoded)
    with tempfile.TemporaryDirectory(prefix="minos-reference-measure-") as directory:
        archive = Path(directory) / "bundle.tar.gz"
        _ = bundle_archive(bundle, archive)
        with archive.open("rb") as content:
            result = subprocess.run(
                ["ssh", *SSH_OPTIONS, "-p", host.port, f"root@{host.address}", command],
                stdin=content,
                capture_output=True,
                timeout=300,
                check=False,
            )
    if result.returncode:
        answer = cast(dict[str, object], json.loads(result.stdout or b"{}"))
        raise RuntimeError(str(answer.get("error", "isolated host measurement failed")))
    answer = cast(dict[str, object], json.loads(result.stdout))
    if host.call("inspect", {})["compose_sha256"] != state["compose_sha256"]:
        raise RuntimeError("live Compose changed")
    with output_path.open("x") as output:
        _ = output.write(json.dumps(answer, indent=2, sort_keys=True) + "\n")
    print(json.dumps(answer, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, DeployError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {
                    "error": str(error)
                    if isinstance(error, RuntimeError)
                    else "measurement transport failed"
                }
            )
        )
        raise SystemExit(1) from None
