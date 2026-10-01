import pytest

from scripts.check_reference_web import check


@pytest.mark.parametrize(
    "base", ["https://minoshealth.pl", "http://minoshealth.pl:8080", "http://192.0.2.1"]
)
def test_load_checker_refuses_nonlocal_targets_before_network_io(base: str) -> None:
    with pytest.raises(ValueError, match="local loopback"):
        _ = check(base)
