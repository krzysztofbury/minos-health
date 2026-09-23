from collections.abc import Mapping
from typing import final

import pytest

from scripts.aws_watcher_operator_live_guard import (
    verify_inline_policy,
    verify_policy,
    verify_role,
)
from scripts.aws_watcher_operator_plan_guard import ACCOUNT, EXPECTED_TAGS, SSO_ROLE

_NAME = "minos-health-watcher-acceptance-operator"
_BOUNDARY = f"arn:aws:iam::{ACCOUNT}:policy/{_NAME}-boundary"
_TRUST: dict[str, object] = {
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


@final
class _Iam:
    def __init__(self, *, boundary: str = _BOUNDARY, attached: bool = False) -> None:
        self.boundary = boundary
        self.attached = attached

    def get_role(self, **_kwargs: object) -> Mapping[str, object]:
        return {
            "Role": {
                "Arn": f"arn:aws:iam::{ACCOUNT}:role/{_NAME}",
                "PermissionsBoundary": {"PermissionsBoundaryArn": self.boundary},
                "AssumeRolePolicyDocument": _TRUST,
                "Tags": [
                    {"Key": key, "Value": value} for key, value in EXPECTED_TAGS.items()
                ],
            }
        }

    def list_attached_role_policies(self, **_kwargs: object) -> Mapping[str, object]:
        return {
            "IsTruncated": False,
            "AttachedPolicies": [{"PolicyArn": "unreviewed"}] if self.attached else [],
        }

    def get_policy(self, **_kwargs: object) -> Mapping[str, object]:
        return {"Policy": {"Arn": _BOUNDARY, "DefaultVersionId": "v1"}}

    def get_policy_version(self, **_kwargs: object) -> Mapping[str, object]:
        return {"PolicyVersion": {"Document": _TRUST}}

    def get_role_policy(self, **_kwargs: object) -> Mapping[str, object]:
        return {"PolicyDocument": _TRUST}

    def simulate_principal_policy(self, **_kwargs: object) -> Mapping[str, object]:
        return {"EvaluationResults": [{"EvalDecision": "implicitDeny"}]}


def test_live_role_requires_exact_boundary_trust_and_no_attachments() -> None:
    assert verify_role(_Iam(), "acceptance_operator", _TRUST) == (
        f"arn:aws:iam::{ACCOUNT}:role/{_NAME}"
    )
    with pytest.raises(ValueError, match="boundary"):
        _ = verify_role(
            _Iam(boundary=f"arn:aws:iam::{ACCOUNT}:policy/AdministratorAccess"),
            "acceptance_operator",
            _TRUST,
        )
    with pytest.raises(ValueError, match="attachment"):
        _ = verify_role(_Iam(attached=True), "acceptance_operator", _TRUST)


def test_live_boundary_document_must_match_reviewed_plan() -> None:
    verify_policy(_Iam(), "acceptance_operator_boundary", _TRUST)
    with pytest.raises(ValueError, match="reviewed plan"):
        verify_policy(
            _Iam(),
            "acceptance_operator_boundary",
            {"Version": "2012-10-17", "Statement": []},
        )


def test_live_inline_policy_must_match_its_boundary() -> None:
    verify_inline_policy(_Iam(), "acceptance_operator", _TRUST)
    with pytest.raises(ValueError, match="reviewed plan"):
        verify_inline_policy(
            _Iam(), "acceptance_operator", {"Version": "2012-10-17", "Statement": []}
        )
