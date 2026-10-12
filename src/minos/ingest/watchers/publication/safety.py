"""Shared ownership boundary for watcher-written public metadata."""

import os
import stat
from pathlib import Path


class PublicPublicationError(RuntimeError):
    """Public metadata could not be installed without weakening isolation."""


def prepare_public_root(root: Path) -> None:
    try:
        root.mkdir(mode=0o755, parents=True, exist_ok=True)
        status = root.lstat()
        if (
            not stat.S_ISDIR(status.st_mode)
            or status.st_uid != os.geteuid()
            or stat.S_IMODE(status.st_mode) & 0o022
        ):
            raise PublicPublicationError("public root ownership or mode is unsafe")
        root.chmod(0o755)
    except PublicPublicationError:
        raise
    except OSError:
        raise PublicPublicationError("public root is unavailable") from None
