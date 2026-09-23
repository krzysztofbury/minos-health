import copy
import hashlib
import json
from typing import cast

import pytest

import scripts.aws_watcher_operator_plan_guard as guard_module
from scripts.aws_watcher_operator_plan_guard import (
    ACCOUNT,
    EXPECTED,
    ROLE_NAMES,
    SSO_ROLE,
    PlanError,
    validate_plan,
)


def _policy(*actions: str, conditional_put: bool = False) -> str:
    statements: list[dict[str, object]] = []
    for action in actions:
        statement: dict[str, object] = {
            "Effect": "Allow",
            "Action": [action],
            "Resource": f"arn:aws:s3:::minos-health-snapshots-{ACCOUNT}-eu-central-1/*",
        }
        if conditional_put and action == "s3:PutObject":
            statement["Condition"] = {"StringEquals": {"s3:if-none-match": "*"}}
        statements.append(statement)
    return json.dumps({"Version": "2012-10-17", "Statement": statements})


def _plan() -> dict[str, object]:
    boundary_names = {
        "watcher_boundary": "minos-health-watcher-boundary",
        "github_reader_boundary": "minos-health-watcher-github-reader-boundary",
        "acceptance_watcher_boundary": "minos-health-watcher-acceptance-boundary",
        "production_operator_boundary": "minos-health-watcher-terraform-operator-boundary",
        "acceptance_operator_boundary": "minos-health-watcher-acceptance-operator-boundary",
        "state_recovery_boundary": "minos-health-watcher-state-recovery-boundary",
    }
    policy = {
        "watcher_boundary": _policy(
            "s3:GetObject", "s3:PutObject", conditional_put=True
        ),
        "github_reader_boundary": _policy("s3:GetObject"),
        "acceptance_watcher_boundary": _policy(
            "s3:GetObject", "s3:PutObject", conditional_put=True
        ),
        "production_operator_boundary": _policy("sts:GetCallerIdentity"),
        "acceptance_operator_boundary": _policy("iam:CreateRole", "s3:DeleteBucket"),
        "state_recovery_boundary": _policy("s3:GetObject"),
    }
    recovery = cast(dict[str, object], json.loads(policy["state_recovery_boundary"]))
    statements = cast(list[object], recovery["Statement"])
    statements.append(
        {
            "Effect": "Allow",
            "Action": "s3:PutObject",
            "Resource": f"arn:aws:s3:::minos-health-tfstate-{ACCOUNT}-eu-central-1/watcher/terraform.tfstate",
            "Condition": {"Null": {"s3:if-match": "false"}},
        }
    )
    policy["state_recovery_boundary"] = json.dumps(recovery)
    trust = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT}:root"},
                    "Condition": {"ArnEquals": {"aws:PrincipalArn": SSO_ROLE}},
                }
            ],
        }
    )
    changes: list[dict[str, object]] = []
    for address in sorted(EXPECTED):
        kind, name = address.split(".")
        after: dict[str, object] = {}
        if kind == "aws_iam_policy":
            after = {
                "name": boundary_names[name],
                "policy": policy[name],
                "tags_all": {"Project": "minos-health"},
            }
        elif kind == "aws_iam_role":
            after = {
                "name": ROLE_NAMES[name],
                "permissions_boundary": (
                    f"arn:aws:iam::{ACCOUNT}:policy/{boundary_names[name + '_boundary']}"
                ),
                "assume_role_policy": trust,
                "tags_all": {"Project": "minos-health"},
            }
        elif kind == "aws_iam_role_policy":
            after = {"role": ROLE_NAMES[name], "policy": policy[name + "_boundary"]}
        else:
            after = {"input": ACCOUNT}
        changes.append(
            {
                "address": address,
                "mode": "managed",
                "change": {"actions": ["create"], "before": None, "after": after},
            }
        )
    return {"format_version": "1.2", "resource_changes": changes}


def _after(plan: dict[str, object], address: str) -> dict[str, object]:
    changes = cast(list[object], plan["resource_changes"])
    for entry in changes:
        assert isinstance(entry, dict)
        record = cast(dict[str, object], entry)
        if record["address"] == address:
            change = record["change"]
            assert isinstance(change, dict)
            after = cast(dict[str, object], change)["after"]
            assert isinstance(after, dict)
            return cast(dict[str, object], after)
    raise AssertionError("expected fixture resource missing")


def test_exact_create_only_operator_plan_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    _use_fixture_hashes(plan, monkeypatch)
    validate_plan(plan)


def _use_fixture_hashes(
    plan: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    expected: dict[str, str] = {}
    for address in EXPECTED:
        if not address.startswith("aws_iam_policy."):
            continue
        policy = _after(plan, address)["policy"]
        assert isinstance(policy, str)
        expected[address.split(".")[-1]] = hashlib.sha256(policy.encode()).hexdigest()
    monkeypatch.setattr(guard_module, "EXPECTED_POLICY_SHA256", expected)


@pytest.mark.parametrize(
    "defect",
    [
        "missing_resource",
        "extra_access_key",
        "github_write",
        "admin_boundary",
        "public_trust",
        "inline_escalation",
        "unconditional_put",
    ],
)
def test_plan_guard_rejects_omissions_escalation_and_broken_creation(
    defect: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = copy.deepcopy(_plan())
    _use_fixture_hashes(plan, monkeypatch)
    if defect == "missing_resource":
        _ = cast(list[object], plan["resource_changes"]).pop()
    elif defect == "extra_access_key":
        cast(list[object], plan["resource_changes"]).append(
            {
                "address": "aws_iam_access_key.watcher",
                "mode": "managed",
                "change": {"actions": ["create"], "before": None, "after": {}},
            }
        )
    elif defect == "github_write":
        _after(plan, "aws_iam_policy.github_reader_boundary")["policy"] = _policy(
            "s3:PutObject"
        )
    elif defect == "admin_boundary":
        _after(plan, "aws_iam_role.production_operator")["permissions_boundary"] = (
            "arn:aws:iam::218375352108:policy/AdministratorAccess"
        )
    elif defect == "public_trust":
        _after(plan, "aws_iam_role.acceptance_operator")["assume_role_policy"] = (
            json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": "sts:AssumeRole",
                            "Principal": {"AWS": "*"},
                        }
                    ],
                }
            )
        )
    elif defect == "inline_escalation":
        _after(plan, "aws_iam_role_policy.production_operator")["policy"] = _policy(
            "iam:PassRole"
        )
    elif defect == "unconditional_put":
        _after(plan, "aws_iam_policy.watcher_boundary")["policy"] = _policy(
            "s3:PutObject"
        )
    else:
        raise AssertionError("unhandled test fault")

    with pytest.raises(PlanError):
        validate_plan(plan)
