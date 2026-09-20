# pyright: reportExplicitAny=false

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AwsHarness:
    account_id: str
    region: str
    bucket: str
    role_arn: str
    role_name: str
    run_id: str
    operator_arn: str
    admin_s3: Any
    admin_iam: Any
    watcher_s3: Any
