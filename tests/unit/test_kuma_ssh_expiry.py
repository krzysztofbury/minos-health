import os
import subprocess
from pathlib import Path

import pytest

from scripts import expire_kuma_ssh_setup as expiry
from scripts.expire_kuma_ssh_setup import EXCEPTION, exception_for, without_exception


def test_only_exact_terminal_exception_can_be_removed() -> None:
    baseline = b"Include /etc/ssh/sshd_config.d/*.conf\nUsePAM yes\n"

    assert without_exception(baseline + EXCEPTION) == baseline


def test_current_private_kuma_address_is_removed_only_when_exact() -> None:
    baseline = b"Include /etc/ssh/sshd_config.d/*.conf\nUsePAM yes\n"
    expected = exception_for("172.19.0.2")
    assert without_exception(baseline + expected, expected) == baseline
    with pytest.raises(RuntimeError):
        _ = without_exception(baseline + expected, EXCEPTION)


@pytest.mark.parametrize("address", ["127.0.0.1", "8.8.8.8", "::1", "not-an-ip"])
def test_expiry_refuses_non_docker_addresses(address: str) -> None:
    with pytest.raises(ValueError):
        _ = exception_for(address)


@pytest.mark.parametrize(
    "content",
    [
        EXCEPTION[:-1],
        EXCEPTION.replace(b"172.21.0.2:3001", b"127.0.0.1:3001"),
        EXCEPTION + EXCEPTION,
        b"Match User root\n" + EXCEPTION + b"UsePAM yes\n",
    ],
)
def test_changed_or_nonterminal_exception_fails_closed(content: bytes) -> None:
    with pytest.raises(RuntimeError):
        _ = without_exception(content)


def test_a_retry_after_a_failed_reload_still_reloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first run rewrites the file and then fails to reload; the retry finds
    # no marker and must reload, not report absence while sshd forwards (#231).
    config = tmp_path / "sshd_config"
    _ = config.write_bytes(b"PermitRootLogin prohibit-password\n" + EXCEPTION)
    real_lstat = Path.lstat

    def root_owned(path: Path) -> os.stat_result:
        found = real_lstat(path)
        return os.stat_result((found.st_mode, found.st_ino, found.st_dev,
                               found.st_nlink, 0, found.st_gid, found.st_size,
                               int(found.st_atime), int(found.st_mtime),
                               int(found.st_ctime)))  # fmt: skip

    reloads: list[bool] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[:2] == ["systemctl", "reload"]:
            reloads.append(len(reloads) > 0)
            if len(reloads) == 1:
                raise subprocess.CalledProcessError(1, argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(Path, "lstat", root_owned)

    def keep_owner(_descriptor: int, _uid: int, _gid: int) -> None:
        pass

    monkeypatch.setattr(os, "fchown", keep_owner)
    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(subprocess.CalledProcessError):
        _ = expiry.expire(config)
    assert config.read_bytes() == b"PermitRootLogin prohibit-password\n"

    assert expiry.expire(config) is False
    assert reloads == [False, True]
