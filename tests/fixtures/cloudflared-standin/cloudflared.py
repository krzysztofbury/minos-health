#!/usr/local/bin/python3
"""Answer the cloudflared commands the tunnel release uses, without a tunnel (#273).

The real healthcheck (`tunnel ready`) needs a connected tunnel, so the Docker
smoke runs this stand-in to exercise Compose recreation and health, not
cloudflared itself.
"""

import os
import sys
import time
from pathlib import Path

READY = Path("/tmp/ready")
variant = os.environ["STANDIN_VARIANT"]
arguments = sys.argv[1:]
if arguments == ["--version"]:
    print(f"cloudflared version {os.environ['STANDIN_VERSION']} (stand-in)")
elif arguments[-1:] == ["run"]:
    # Readiness is decided once at start, like a connector that failed to
    # register: a later control change does not heal a running container.
    refused = Path("/control/refuse-" + variant).exists()
    if variant != "broken" and not refused:
        READY.touch()
    while True:
        time.sleep(3600)
elif arguments[-1:] == ["ready"]:
    sys.exit(0 if READY.exists() else 1)
elif arguments[-2:] == ["ingress", "validate"]:
    pass
else:
    sys.exit(2)
