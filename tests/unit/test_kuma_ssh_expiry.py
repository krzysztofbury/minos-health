import pytest

from scripts.expire_kuma_ssh_setup import EXCEPTION, without_exception


def test_only_exact_terminal_exception_can_be_removed() -> None:
    baseline = b"Include /etc/ssh/sshd_config.d/*.conf\nUsePAM yes\n"

    assert without_exception(baseline + EXCEPTION) == baseline


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
