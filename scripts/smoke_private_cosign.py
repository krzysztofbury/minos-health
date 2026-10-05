# Synthetic Cosign JSON is checked independently, not with a production verifier.
# pyright: reportAny=false

"""Rehearse private DSSE attestation with synthetic keys and no container network.

Run under agent-scratch, providing the checksum-pinned Cosign binary and a new
--work directory inside that scratch lease. The Debian image must already exist.
This does not fetch binaries/images, call KMS, attest real artifacts or authorize
activation. Every generated key and bundle is disposable synthetic evidence.
"""

import argparse
import base64
import hashlib
import json
import os
import signal
import subprocess
import uuid
from pathlib import Path
from typing import final

COSIGN_SHA256 = "4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71"
COSIGN_SIZE_BYTES = 141178250
BASE = "debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251"
PREDICATE_TYPE = "https://minoshealth.pl/attestations/synthetic-rehearsal/v1"


def stop(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def check_binary(path: Path) -> None:
    if not path.is_file() or path.stat().st_size != COSIGN_SIZE_BYTES:
        raise RuntimeError("Cosign binary size differs from reviewed release")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for _ in range(1 + COSIGN_SIZE_BYTES // (64 * 1024)):
            chunk = source.read(64 * 1024)
            digest.update(chunk)
            if not chunk:
                break
    if digest.hexdigest() != COSIGN_SHA256:
        raise RuntimeError("Cosign binary checksum differs from reviewed release")


@final
class PrivateCosign:
    def __init__(self, binary: Path, work: Path) -> None:
        self.binary = binary
        self.work = work

    def run(self, args: list[str], *, expect_success: bool = True) -> str:
        name = "minos-private-cosign-" + uuid.uuid4().hex
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--memory",
            "256m",
            "--pids-limit",
            "64",
            "--volume",
            f"{self.work}:/scratch:rw",
            "--volume",
            f"{self.binary}:/cosign:ro",
            "--workdir",
            "/scratch",
            "--env",
            "HOME=/scratch",
            "--env",
            "COSIGN_PASSWORD=disposable-synthetic-fixture",
            "--entrypoint",
            "/cosign",
            BASE,
            *args,
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            if expect_success and result.returncode != 0:
                raise RuntimeError("private Cosign synthetic command failed")
            if not expect_success and result.returncode != 1:
                raise RuntimeError(
                    "private Cosign expected verification refusal was absent"
                )
            return result.stdout
        finally:
            cleanup = subprocess.run(
                ["docker", "container", "rm", "--force", name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if cleanup.returncode != 0 and "No such container" not in cleanup.stderr:
                raise RuntimeError("private Cosign task-container cleanup failed")


def rehearsal(binary: Path, work: Path) -> dict[str, object]:
    check_binary(binary)
    _ = subprocess.run(
        ["docker", "image", "inspect", BASE],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    work.mkdir(exist_ok=False)
    config = (
        Path(__file__).resolve().parents[1]
        / "infra/signing/private-signing-config.json"
    )
    config_bytes = config.read_bytes()
    assert json.loads(config_bytes) == {
        "mediaType": "application/vnd.dev.sigstore.signingconfig.v0.2+json",
        "rekorTlogConfig": {},
        "tsaConfig": {},
    }
    _ = (work / "private-signing-config.json").write_bytes(config_bytes)
    subject = b"Synthetic artifact only\n"
    _ = (work / "artifact.txt").write_bytes(subject)
    _ = (work / "predicate.json").write_text(
        '{"fixture":"synthetic-private-predicate"}\n'
    )
    runner = PrivateCosign(binary, work)
    version = runner.run(["version"])
    assert "GitVersion:    v3.1.3" in version
    _ = runner.run(["generate-key-pair", "--output-key-prefix", "/scratch/fixture"])
    _ = runner.run(
        [
            "attest-blob",
            "--signing-config",
            "/scratch/private-signing-config.json",
            "--key",
            "/scratch/fixture.key",
            "--predicate",
            "/scratch/predicate.json",
            "--type",
            PREDICATE_TYPE,
            "--bundle",
            "/scratch/bundle.json",
            "/scratch/artifact.txt",
        ]
    )
    verification = [
        "verify-blob-attestation",
        "--key",
        "/scratch/fixture.pub",
        "--bundle",
        "/scratch/bundle.json",
        "--insecure-ignore-tlog",
        "--type",
        PREDICATE_TYPE,
        "/scratch/artifact.txt",
    ]
    _ = runner.run(verification)
    # Retained public-key verification, never KMS/online issuer substitution.
    _ = runner.run(["generate-key-pair", "--output-key-prefix", "/scratch/wrong"])
    wrong_key = verification.copy()
    wrong_key[2] = "/scratch/wrong.pub"
    _ = runner.run(wrong_key, expect_success=False)
    wrong_type = verification.copy()
    wrong_type[-2] = "https://minoshealth.pl/attestations/different-fixture/v1"
    _ = runner.run(wrong_type, expect_success=False)
    _ = (work / "artifact.txt").write_bytes(b"Changed synthetic bytes\n")
    _ = runner.run(verification, expect_success=False)
    _ = (work / "artifact.txt").write_bytes(subject)
    bundle = json.loads((work / "bundle.json").read_bytes())
    assert bundle["mediaType"] == "application/vnd.dev.sigstore.bundle.v0.3+json"
    material = bundle["verificationMaterial"]
    assert not material.get("tlogEntries")
    assert not material.get("timestampVerificationData")
    envelope = bundle["dsseEnvelope"]
    statement = json.loads(base64.b64decode(envelope["payload"], validate=True))
    # attest-blob emits v0.1 even though this release also imports v1 types.
    assert statement["_type"] == "https://in-toto.io/Statement/v0.1"
    assert statement["predicateType"] == PREDICATE_TYPE
    assert statement["subject"] == [
        {
            "name": "artifact.txt",
            "digest": {"sha256": hashlib.sha256(subject).hexdigest()},
        }
    ]
    assert statement["predicate"] == {"fixture": "synthetic-private-predicate"}
    envelope["signatures"][0]["sig"] = base64.b64encode(
        b"altered synthetic signature"
    ).decode()
    _ = (work / "bundle.json").write_text(json.dumps(bundle))
    _ = runner.run(verification, expect_success=False)
    return {
        "status": "synthetic_private_cosign_rehearsal_only",
        "cosign_version": "v3.1.3",
        "statement_type": "https://in-toto.io/Statement/v0.1",
        "cosign_sha256": COSIGN_SHA256,
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "network": "none",
        "private_dsse_verification": True,
        "wrong_key_refused": True,
        "wrong_predicate_refused": True,
        "altered_subject_refused": True,
        "altered_signature_refused": True,
        "public_log_entries": False,
        "witnessed_timestamp": False,
    }


@final
class Arguments(argparse.Namespace):
    cosign_binary: Path = Path()
    work: Path = Path()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--cosign-binary", type=Path, required=True)
    _ = parser.add_argument("--work", type=Path, required=True)
    arguments = parser.parse_args(namespace=Arguments())
    for signum in (signal.SIGTERM, signal.SIGHUP):
        _ = signal.signal(signum, stop)
    result = rehearsal(arguments.cosign_binary.resolve(), arguments.work.resolve())
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
