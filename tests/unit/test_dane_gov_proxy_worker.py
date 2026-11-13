import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def test_dane_gov_proxy_worker_suite() -> None:
    """Run the Worker's own suite (#349) the way the Sources suite runs."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the dane.gov.pl proxy Worker suite")

    completed = subprocess.run(
        [node, "--test", "tests/js/dane-gov-proxy.test.mjs"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
