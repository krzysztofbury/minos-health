import subprocess
import sys


def test_module_reports_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "minos", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "minos 0.0.1"
