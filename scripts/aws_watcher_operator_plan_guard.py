"""Reject unexpected or unresolved IAM bootstrap actions before plan approval."""

import hashlib
import json
import sys
from pathlib import Path
from typing import cast

ACCOUNT = "218375352108"
SSO_ROLE = (
    f"arn:aws:iam::{ACCOUNT}:role/aws-reserved/sso.amazonaws.com/eu-central-1/"
    "AWSReservedSSO_MinosAdministrator_99fe77395d256089"
)
EXPECTED = {
    "terraform_data.bootstrap_guard",
    "aws_iam_policy.watcher_boundary",
    "aws_iam_policy.github_reader_boundary",
    "aws_iam_policy.acceptance_watcher_boundary",
    "aws_iam_policy.production_operator_boundary",
    "aws_iam_policy.acceptance_operator_boundary",
    "aws_iam_policy.state_recovery_boundary",
    "aws_iam_role.production_operator",
    "aws_iam_role.acceptance_operator",
    "aws_iam_role.state_recovery",
    "aws_iam_role_policy.production_operator",
    "aws_iam_role_policy.acceptance_operator",
    "aws_iam_role_policy.state_recovery",
}
ROLE_NAMES = {
    "production_operator": "minos-health-watcher-terraform-operator",
    "acceptance_operator": "minos-health-watcher-acceptance-operator",
    "state_recovery": "minos-health-watcher-state-recovery",
}
POLICY_NAMES = {
    "watcher_boundary": "minos-health-watcher-boundary",
    "github_reader_boundary": "minos-health-watcher-github-reader-boundary",
    "acceptance_watcher_boundary": "minos-health-watcher-acceptance-boundary",
    "production_operator_boundary": "minos-health-watcher-terraform-operator-boundary",
    "acceptance_operator_boundary": "minos-health-watcher-acceptance-operator-boundary",
    "state_recovery_boundary": "minos-health-watcher-state-recovery-boundary",
}
INLINE_POLICY_NAMES = {
    "production_operator": "minos-health-watcher-terraform-scoped",
    "acceptance_operator": "minos-health-watcher-acceptance-scoped",
    "state_recovery": "minos-health-watcher-state-recovery-scoped",
}
EXPECTED_TAGS = {
    "DataClassification": "sensitive",
    "Environment": "production",
    "ManagedBy": "Terraform",
    "Project": "minos-health",
    "Repository": "krzysztofbury/minos-health",
}
EXPECTED_POLICY_SHA256 = {
    "acceptance_operator_boundary": "c48b2378feb4cb75fbcf616c58884bed462fcad100c82e787e3225ffbdf64c14",
    "acceptance_watcher_boundary": "91ab68d1b81b319c49134c39730361e3e4598aa7f6a5ac41014f0e6b38185d0b",
    "github_reader_boundary": "73bcefd93a5886a60fc799194ac4c5568823fe7f86799257d7eb961da9930f80",
    "production_operator_boundary": "cf9beec199eb81a99b43434b60ebe93fd720688cc74e51f657e295203f121b19",
    "state_recovery_boundary": "4aee3685f6cb01246537c1202fd6a1d0ba4839d57aaf2f0ccbb8a6302672ed88",
    "watcher_boundary": "f869bbd94ba056a2ea4641cba5f6f34d3173d798f760da0ab0d2f581409757ce",
}
PREVIOUS_ACCEPTANCE_OPERATOR_SHA256 = (
    "1838dec3a1843f65f7ab3d8e74211c7966d425e2dfdbe1959b44f943ffdc8e99"
)


class PlanError(RuntimeError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise PlanError(reason)


def mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PlanError(f"{label} must be an object")
    raw = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw):
        raise PlanError(f"{label} keys must be strings")
    return cast(dict[str, object], value)


def sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise PlanError(f"{label} must be a list")
    return cast(list[object], value)


def policy_statements(policy: dict[str, object]) -> list[dict[str, object]]:
    return [
        mapping(item, "policy statement")
        for item in sequence(policy.get("Statement"), "policy statements")
    ]


def statement_actions(statement: dict[str, object]) -> list[str]:
    raw = statement.get("Action")
    items = [raw] if isinstance(raw, str) else sequence(raw, "policy actions")
    if not items or not all(isinstance(item, str) for item in items):
        raise PlanError("policy action type is unsupported")
    return cast(list[str], items)


def decode_policy(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, str) or not 2 <= len(value) <= 6144:
        raise PlanError(f"{label} policy size is unsupported")
    parsed = mapping(cast(object, json.loads(value)), f"{label} policy")
    require(parsed.get("Version") == "2012-10-17", f"{label} policy version")
    statements = policy_statements(parsed)
    require(1 <= len(statements) <= 32, f"{label} statement count")
    for statement in statements:
        require(statement.get("Effect") == "Allow", f"{label} unsupported statement")
        require(
            all(
                item != "*" and not item.endswith(":*")
                for item in statement_actions(statement)
            ),
            f"{label} wildcard action",
        )
    return parsed


def policy_actions(policy: dict[str, object]) -> set[str]:
    actions: set[str] = set()
    for statement in policy_statements(policy):
        actions.update(statement_actions(statement))
    return actions


def _managed_changes(document: object) -> dict[str, dict[str, object]]:
    root = mapping(document, "plan root")
    format_version = root.get("format_version")
    require(
        isinstance(format_version, str) and format_version.startswith("1."),
        "plan format unsupported",
    )
    resources = sequence(root.get("resource_changes"), "resource changes")
    require(len(resources) <= 32, "resource count unsupported")
    changes: dict[str, dict[str, object]] = {}
    for item in resources:
        record = mapping(item, "resource record")
        if record.get("mode") == "data":
            continue
        require(record.get("mode") == "managed", "resource mode unsupported")
        name = record.get("address")
        require(
            isinstance(name, str) and name in EXPECTED and name not in changes,
            "unexpected or duplicate resource",
        )
        if not isinstance(name, str):
            raise PlanError("resource address is invalid")
        change = mapping(record.get("change"), "resource change")
        require(change.get("actions") == ["create"], "plan is not create-only")
        require(change.get("before") is None, "resource already exists in state")
        after = mapping(change.get("after"), "planned values")
        unknown = mapping(change.get("after_unknown", {}), "unknown planned fields")
        require(
            all(
                unknown.get(field) is not True
                for field in (
                    "name",
                    "policy",
                    "role",
                    "permissions_boundary",
                    "assume_role_policy",
                    "tags_all",
                )
            ),
            "security-sensitive planned values are unknown",
        )
        changes[name] = after
    require(set(changes) == EXPECTED, "operator bootstrap set is incomplete")
    return changes


def _validate_boundaries(
    changes: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    policies: dict[str, dict[str, object]] = {}
    for key, name in POLICY_NAMES.items():
        resource = changes[f"aws_iam_policy.{key}"]
        require(resource.get("name") == name, f"{key} identity changed")
        require(
            mapping(resource.get("tags_all"), f"{key} tags") == EXPECTED_TAGS,
            f"{key} ownership tags changed",
        )
        require(resource.get("path") == "/", f"{key} IAM path changed")
        policies[key] = decode_policy(resource.get("policy"), key)
        policy_bytes = resource.get("policy")
        if not isinstance(policy_bytes, str):
            raise PlanError(f"{key} policy is unavailable")
        require(
            hashlib.sha256(policy_bytes.encode()).hexdigest()
            == EXPECTED_POLICY_SHA256[key],
            f"{key} policy digest differs from the reviewed boundary",
        )
    return policies


def _validate_roles(
    changes: dict[str, dict[str, object]],
    policies: dict[str, dict[str, object]],
) -> None:
    require(
        changes["terraform_data.bootstrap_guard"].get("input") == ACCOUNT,
        "bootstrap account guard changed",
    )
    for key, name in ROLE_NAMES.items():
        role = changes[f"aws_iam_role.{key}"]
        require(role.get("name") == name, f"{key} role name changed")
        require(role.get("path") == "/", f"{key} role path changed")
        require(
            role.get("max_session_duration") == 3600, f"{key} session duration changed"
        )
        require(
            role.get("force_detach_policies") is False,
            f"{key} forced policy detach enabled",
        )
        expected_boundary = (
            f"arn:aws:iam::{ACCOUNT}:policy/{POLICY_NAMES[key + '_boundary']}"
        )
        require(
            role.get("permissions_boundary") == expected_boundary,
            f"{key} role boundary changed",
        )
        require(
            mapping(role.get("tags_all"), f"{key} role tags") == EXPECTED_TAGS,
            f"{key} role ownership tags changed",
        )
        trust = decode_policy(role.get("assume_role_policy"), f"{key} trust")
        trust_statements = policy_statements(trust)
        require(len(trust_statements) == 1, f"{key} trust has extra principals")
        statement = trust_statements[0]
        require(
            statement.get("Action") == "sts:AssumeRole", f"{key} trust action changed"
        )
        require(
            statement.get("Principal") == {"AWS": f"arn:aws:iam::{ACCOUNT}:root"},
            f"{key} trust principal changed",
        )
        require(
            statement.get("Condition") == {"ArnEquals": {"aws:PrincipalArn": SSO_ROLE}},
            f"{key} SSO trust changed",
        )
        role_policy = changes[f"aws_iam_role_policy.{key}"]
        require(role_policy.get("role") == name, f"{key} inline role changed")
        require(
            role_policy.get("name") == INLINE_POLICY_NAMES[key],
            f"{key} inline policy identity changed",
        )
        inline = decode_policy(role_policy.get("policy"), f"{key} inline")
        require(
            inline == policies[key + "_boundary"], f"{key} role and boundary differ"
        )


def _validate_policy_semantics(policies: dict[str, dict[str, object]]) -> None:
    require(
        "s3:PutObject" not in policy_actions(policies["github_reader_boundary"]),
        "GitHub reader gained write access",
    )
    for key in ("watcher_boundary", "acceptance_watcher_boundary"):
        actions = policy_actions(policies[key])
        require(
            "s3:DeleteObject" not in actions and "iam:PassRole" not in actions,
            f"{key} gained destructive access",
        )
        puts = [
            s
            for s in policy_statements(policies[key])
            if statement_actions(s) == ["s3:PutObject"]
        ]
        require(
            len(puts) == 1
            and puts[0].get("Condition") == {"StringEquals": {"s3:if-none-match": "*"}},
            f"{key} conditional create guard missing",
        )
    prod_actions = policy_actions(policies["production_operator_boundary"])
    acceptance_actions = policy_actions(policies["acceptance_operator_boundary"])
    for actions in (prod_actions, acceptance_actions):
        require(
            "iam:PassRole" not in actions and "iam:CreateAccessKey" not in actions,
            "operator gained credential or role passing access",
        )
    require(
        "iam:CreateRole" in acceptance_actions
        and "s3:DeleteBucket" in acceptance_actions,
        "disposable cleanup is incomplete",
    )
    require(
        "s3:DeleteObjectVersion"
        not in policy_actions(policies["state_recovery_boundary"]),
        "state versions became deletable",
    )


def validate_plan(document: object) -> None:
    if is_read_preflight_update(document):
        validate_read_preflight_update(document)
        return
    changes = _managed_changes(document)
    policies = _validate_boundaries(changes)
    _validate_roles(changes, policies)
    _validate_policy_semantics(policies)


def is_read_preflight_update(document: object) -> bool:
    root = mapping(document, "plan root")
    resources = sequence(root.get("resource_changes"), "resource changes")
    return any(
        mapping(item, "resource record").get("mode") == "managed"
        and mapping(mapping(item, "resource record").get("change"), "change").get(
            "actions"
        )
        == ["update"]
        for item in resources
    )


def validate_read_preflight_update(document: object) -> None:
    root = mapping(document, "plan root")
    require(
        str(root.get("format_version", "")).startswith("1."), "plan format unsupported"
    )
    resources = sequence(root.get("resource_changes"), "resource changes")
    require(len(resources) <= 32, "resource count unsupported")
    expected = {
        "aws_iam_policy.acceptance_operator_boundary",
        "aws_iam_role_policy.acceptance_operator",
    }
    changed: dict[str, tuple[dict[str, object], dict[str, object]]] = {}
    for item in resources:
        record = mapping(item, "resource record")
        if record.get("mode") == "data":
            continue
        require(record.get("mode") == "managed", "resource mode unsupported")
        address = record.get("address")
        if not isinstance(address, str) or address not in EXPECTED:
            raise PlanError("unknown operator resource")
        change = mapping(record.get("change"), "resource change")
        actions = change.get("actions")
        if actions == ["no-op"]:
            require(
                change.get("before") == change.get("after"), "unexpected no-op values"
            )
            continue
        require(
            address in expected and address not in changed and actions == ["update"],
            "unexpected operator update",
        )
        before = mapping(change.get("before"), "previous resource")
        after = mapping(change.get("after"), "updated resource")
        require(change.get("after_unknown", {}) == {}, "unknown updated fields")
        require(before.keys() == after.keys(), "updated resource fields differ")
        require(
            all(before[key] == after[key] for key in before if key != "policy"),
            "update changed fields other than policy",
        )
        changed[address] = before, after
    require(
        set(changed) == expected,
        "read preflight update must change exactly two policies",
    )
    boundary_before, boundary_after = changed[
        "aws_iam_policy.acceptance_operator_boundary"
    ]
    inline_before, inline_after = changed["aws_iam_role_policy.acceptance_operator"]
    require(
        boundary_before.get("name") == POLICY_NAMES["acceptance_operator_boundary"],
        "boundary name changed",
    )
    require(
        inline_before.get("name") == INLINE_POLICY_NAMES["acceptance_operator"],
        "inline policy name changed",
    )
    require(
        inline_before.get("role") == ROLE_NAMES["acceptance_operator"],
        "inline policy role changed",
    )
    previous = boundary_before.get("policy")
    if not isinstance(previous, str):
        raise PlanError("previous boundary unavailable")
    require(
        hashlib.sha256(previous.encode()).hexdigest()
        == PREVIOUS_ACCEPTANCE_OPERATOR_SHA256,
        "previous boundary differs from reviewed baseline",
    )
    old_policy = decode_policy(previous, "previous acceptance boundary")
    require(
        decode_policy(inline_before.get("policy"), "previous inline") == old_policy,
        "previous inline differs from boundary",
    )
    statements = policy_statements(old_policy)
    matches = [
        index
        for index, statement in enumerate(statements)
        if statement.get("Sid") == "ManageOnlyDisposableWatcherRoles"
    ]
    require(len(matches) == 1, "previous role management statement missing")
    index = matches[0]
    management = statements[index]
    actions = statement_actions(management)
    require(actions.count("iam:GetRole") == 1, "previous GetRole permission missing")
    require(
        management.get("Resource")
        == f"arn:aws:iam::{ACCOUNT}:role/minos-health-watcher-acceptance-*-watcher",
        "role read resource is unbounded",
    )
    statements.insert(
        index,
        {
            "Sid": "CheckDisposableWatcherRoleExistence",
            "Effect": "Allow",
            "Action": "iam:GetRole",
            "Resource": management["Resource"],
        },
    )
    management["Action"] = [action for action in actions if action != "iam:GetRole"]
    old_policy["Statement"] = statements
    updated = decode_policy(boundary_after.get("policy"), "updated acceptance boundary")
    require(updated == old_policy, "boundary adds permissions beyond scoped role read")
    require(
        decode_policy(inline_after.get("policy"), "updated inline") == updated,
        "updated inline differs from boundary",
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: aws_watcher_operator_plan_guard.py PLAN_JSON", file=sys.stderr)
        return 2
    try:
        path = Path(sys.argv[1])
        if path.stat().st_size > 2_000_000:
            raise PlanError("operator plan is oversized")
        validate_plan(cast(object, json.loads(path.read_text())))
    except (OSError, ValueError, TypeError, PlanError) as error:
        print(f"operator plan rejected: {error}", file=sys.stderr)
        return 1
    print("Validated exact operator bootstrap or two-policy read preflight update.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
