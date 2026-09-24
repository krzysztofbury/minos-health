#!/usr/bin/env python3

"""Remove the reviewed short-lived Kuma SSH forwarding exception."""

import os
import stat
import subprocess
import sys
import uuid
from ipaddress import IPv4Address, IPv4Network, ip_address
from pathlib import Path

SSH_CONFIG = Path("/etc/ssh/sshd_config")
_MONITORING_SUBNET = IPv4Network("172.16.0.0/12")
_EXPIRY_ERRORS = (OSError, RuntimeError, ValueError, subprocess.SubprocessError)


def exception_for(private_ip: str) -> bytes:
    address = ip_address(private_ip)
    if not isinstance(address, IPv4Address) or address not in _MONITORING_SUBNET:
        raise ValueError("Kuma address must be a private Docker IPv4 address")
    return (
        b"# BEGIN MINOS TEMP KUMA SETUP\n"
        + b"Match User root\n"
        + b"    AllowTcpForwarding local\n"
        + f"    PermitOpen {address}:3001\n".encode()
        + b"# END MINOS TEMP KUMA SETUP\n"
    )


EXCEPTION = exception_for("172.21.0.2")


def without_exception(content: bytes, expected: bytes = EXCEPTION) -> bytes:
    if content.count(b"# BEGIN MINOS TEMP KUMA SETUP") != 1:
        raise RuntimeError("Kuma SSH exception marker is missing or duplicated")
    if not content.endswith(expected):
        raise RuntimeError("Kuma SSH exception differs from the reviewed tail")
    return content[: -len(expected)]


def expire(path: Path = SSH_CONFIG, *, expected: bytes = EXCEPTION) -> bool:
    original_stat = path.lstat()
    if not stat.S_ISREG(original_stat.st_mode) or original_stat.st_uid != 0:
        raise RuntimeError("SSH configuration ownership is unsafe")
    content = path.read_bytes()
    if b"# BEGIN MINOS TEMP KUMA SETUP" not in content:
        if b"# END MINOS TEMP KUMA SETUP" in content:
            raise RuntimeError("Kuma SSH exception has an orphaned end marker")
        return False
    restored = without_exception(content, expected)
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
    if len(sys.argv) > 2:
        print("usage: expire_kuma_ssh_setup.py [private-container-ip]", file=sys.stderr)
        return 2
    try:
        expected = exception_for(sys.argv[1]) if len(sys.argv) == 2 else EXCEPTION
        changed = expire(expected=expected)
    except _EXPIRY_ERRORS:
        print("Kuma SSH exception expiry failed; inspect SSH config", file=sys.stderr)
        return 1
    print("Kuma SSH forwarding exception removed" if changed else "Already absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
