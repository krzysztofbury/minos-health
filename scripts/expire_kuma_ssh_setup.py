#!/usr/bin/env python3

"""Remove the reviewed short-lived Kuma SSH forwarding exception."""

import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path

SSH_CONFIG = Path("/etc/ssh/sshd_config")
EXCEPTION = (
    b"# BEGIN MINOS TEMP KUMA SETUP\n"
    b"Match User root\n"
    b"    AllowTcpForwarding local\n"
    b"    PermitOpen 172.21.0.2:3001\n"
    b"# END MINOS TEMP KUMA SETUP\n"
)
_EXPIRY_ERRORS = (OSError, RuntimeError, subprocess.SubprocessError)


def without_exception(content: bytes) -> bytes:
    if content.count(b"# BEGIN MINOS TEMP KUMA SETUP") != 1:
        raise RuntimeError("Kuma SSH exception marker is missing or duplicated")
    if not content.endswith(EXCEPTION):
        raise RuntimeError("Kuma SSH exception differs from the reviewed tail")
    return content[: -len(EXCEPTION)]


def expire(path: Path = SSH_CONFIG) -> bool:
    original_stat = path.lstat()
    if not stat.S_ISREG(original_stat.st_mode) or original_stat.st_uid != 0:
        raise RuntimeError("SSH configuration ownership is unsafe")
    content = path.read_bytes()
    if b"# BEGIN MINOS TEMP KUMA SETUP" not in content:
        if b"# END MINOS TEMP KUMA SETUP" in content:
            raise RuntimeError("Kuma SSH exception has an orphaned end marker")
        return False
    restored = without_exception(content)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            _ = stream.write(restored)
            stream.flush()
            os.fchmod(stream.fileno(), stat.S_IMODE(original_stat.st_mode))
            os.fchown(stream.fileno(), original_stat.st_uid, original_stat.st_gid)
            os.fsync(stream.fileno())
        _ = subprocess.run(
            ["/usr/sbin/sshd", "-t", "-f", str(temporary)],
            check=True,
            capture_output=True,
            timeout=10,
        )
        if path.read_bytes() != content:
            raise RuntimeError("SSH configuration changed during expiry")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    _ = subprocess.run(["systemctl", "reload", "ssh"], check=True, timeout=15)
    return True


def main() -> int:
    try:
        changed = expire()
    except _EXPIRY_ERRORS:
        print("Kuma SSH exception expiry failed; inspect SSH config", file=sys.stderr)
        return 1
    print("Kuma SSH forwarding exception removed" if changed else "Already absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
