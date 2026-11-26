import pytest

import minos


@pytest.mark.to_delete(issue=376)
def test_package_imports() -> None:
    assert minos.__doc__
