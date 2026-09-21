#!/usr/bin/env python3
# pyright: reportAny=false

import json
import re
import sys
from pathlib import Path

IAM_PRINCIPAL = re.compile(
    r"^(arn:[a-z0-9-]+):iam::([0-9]{12}):(user|role)/([A-Za-z0-9+=,.@_/-]+)$"
)
ASSUMED_ROLE = re.compile(
    r"^(arn:[a-z0-9-]+):sts::([0-9]{12}):assumed-role/([A-Za-z0-9+=,.@_/-]+)/[^/]+$"
)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: aws-watcher-principal.py IDENTITY_JSON")
    document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    arn = document.get("Arn")
    if not isinstance(arn, str):
        raise SystemExit("STS identity does not contain an ARN")
    if IAM_PRINCIPAL.fullmatch(arn):
        print(arn)
        return 0
    match = ASSUMED_ROLE.fullmatch(arn)
    if match is None:
        raise SystemExit("STS identity must be an IAM user or assumed IAM role")
    partition, account_id, role_path = match.groups()
    print(f"{partition}:iam::{account_id}:role/{role_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
