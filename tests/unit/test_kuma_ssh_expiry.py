import pytest

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
