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
    "acceptance_operator_boundary": "467f052b28ba661edd43ea30652c069835486163386c47063d7d8b8716be57ef",
    "acceptance_watcher_boundary": "91ab68d1b81b319c49134c39730361e3e4598aa7f6a5ac41014f0e6b38185d0b",
    "github_reader_boundary": "73bcefd93a5886a60fc799194ac4c5568823fe7f86799257d7eb961da9930f80",
    "production_operator_boundary": "f13c1342409b8bd4d9e68c53e6485447871d1fa769bed14a0c2588f0f40b431b",
    "state_recovery_boundary": "4aee3685f6cb01246537c1202fd6a1d0ba4839d57aaf2f0ccbb8a6302672ed88",
    "watcher_boundary": "f869bbd94ba056a2ea4641cba5f6f34d3173d798f760da0ab0d2f581409757ce",
}
PREVIOUS_ACCEPTANCE_OPERATOR_SHA256 = (
    "c48b2378feb4cb75fbcf616c58884bed462fcad100c82e787e3225ffbdf64c14"
)
PREVIOUS_ACCEPTANCE_PROVIDER_SHA256 = (
    "124dbccc1b978439d325130e7ae37cde0caa0be44363e939524e0f5247f73d86"
)
PREVIOUS_PRODUCTION_OPERATOR_SHA256 = (
    "684745fd184c208c721f61843cbc0f6633b0588f80b0796cd46969f2f9ba0f7a"
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
        if is_production_provider_update(document):
            validate_production_provider_update(document)
        else:
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


def is_production_provider_update(document: object) -> bool:
    root = mapping(document, "plan root")
    resources = sequence(root.get("resource_changes"), "resource changes")
    return {
        record.get("address")
        for item in resources
        for record in [mapping(item, "resource record")]
        if record.get("mode") == "managed"
        and mapping(record.get("change"), "resource change").get("actions") != ["no-op"]
    } == {
        "aws_iam_policy.production_operator_boundary",
        "aws_iam_role_policy.production_operator",
    }


def validate_production_provider_update(document: object) -> None:
    root = mapping(document, "plan root")
    version = root.get("format_version")
    require(
        isinstance(version, str) and version.startswith("1."), "plan format unsupported"
    )
    resources = sequence(root.get("resource_changes"), "resource changes")
    require(len(resources) <= 32, "resource count unsupported")
    expected = {
        "aws_iam_policy.production_operator_boundary",
        "aws_iam_role_policy.production_operator",
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
        if change.get("actions") == ["no-op"]:
            require(
                change.get("before") == change.get("after"), "unexpected no-op values"
            )
            continue
        require(
            address in expected
            and address not in changed
            and change.get("actions") == ["update"],
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
        "production read update must change exactly two policies",
    )
    boundary_before, boundary_after = changed[
        "aws_iam_policy.production_operator_boundary"
    ]
    inline_before, inline_after = changed["aws_iam_role_policy.production_operator"]
    require(
        boundary_before.get("name") == POLICY_NAMES["production_operator_boundary"],
        "production boundary identity changed",
    )
    require(
        inline_before.get("name") == INLINE_POLICY_NAMES["production_operator"]
        and inline_before.get("role") == ROLE_NAMES["production_operator"],
        "production inline identity changed",
    )
    previous = boundary_before.get("policy")
    if not isinstance(previous, str):
        raise PlanError("previous production boundary unavailable")
    require(
        hashlib.sha256(previous.encode()).hexdigest()
        == PREVIOUS_PRODUCTION_OPERATOR_SHA256,
        "previous production boundary differs from reviewed baseline",
    )
    old_policy = decode_policy(previous, "previous production boundary")
    require(
        decode_policy(inline_before.get("policy"), "previous production inline")
        == old_policy,
        "previous production inline differs from boundary",
    )
    statements = policy_statements(old_policy)
    buckets = [
        statement
        for statement in statements
        if statement.get("Sid") == "ConfigureOnlyReviewedSnapshotBucket"
    ]
    budgets = [
        statement
        for statement in statements
        if statement.get("Sid") == "ManageOnlyMinosBudget"
    ]
    topics = [
        statement
        for statement in statements
        if statement.get("Sid") == "ManageOnlyMinosBudgetTopic"
    ]
    subscriptions = [
        statement
        for statement in statements
        if statement.get("Sid") == "ReadOnlyMinosBudgetSubscription"
    ]
    require(
        len(buckets) == len(budgets) == len(topics) == len(subscriptions) == 1,
        "reviewed production statements missing",
    )
    bucket = buckets[0]
    budget = budgets[0]
    topic = topics[0]
    subscription = subscriptions[0]
    require(
        bucket.get("Resource")
        == f"arn:aws:s3:::minos-health-snapshots-{ACCOUNT}-eu-central-1",
        "production bucket read scope changed",
    )
    require(
        budget.get("Resource")
        == f"arn:aws:budgets::{ACCOUNT}:budget/minos-health-watcher-monthly",
        "production budget scope changed",
    )
    topic_arn = f"arn:aws:sns:eu-central-1:{ACCOUNT}:minos-health-watcher-budget-alerts"
    require(
        topic.get("Resource") == topic_arn
        and subscription.get("Resource") == f"{topic_arn}:*",
        "production SNS scope changed",
    )
    bucket_actions = statement_actions(bucket)
    budget_actions = statement_actions(budget)
    topic_actions = statement_actions(topic)
    subscription_actions = statement_actions(subscription)
    require(
        bucket_actions.count("s3:GetBucketAccelerateConfiguration") == 1
        and bucket_actions.count("s3:GetBucketReplication") == 1,
        "previous S3 action mapping differs",
    )
    require(
        budget_actions[:2] == ["budgets:CreateBudget", "budgets:DescribeBudget"],
        "budget action baseline changed",
    )
    require(
        topic_actions[:2] == ["sns:CreateTopic", "sns:GetTopicAttributes"],
        "topic action baseline changed",
    )
    require(
        subscription_actions
        == ["sns:GetSubscriptionAttributes", "sns:ListTagsForResource"],
        "subscription action baseline changed",
    )
    bucket["Action"] = [
        "s3:GetAccelerateConfiguration"
        if action == "s3:GetBucketAccelerateConfiguration"
        else "s3:GetReplicationConfiguration"
        if action == "s3:GetBucketReplication"
        else action
        for action in bucket_actions
    ]
    budget["Action"] = (
        budget_actions[:1]
        + ["budgets:ModifyBudget", "budgets:ViewBudget"]
        + budget_actions[1:]
    )
    subscription["Action"] = ["sns:ListTagsForResource"]
    statements.insert(
        statements.index(subscription),
        {
            "Sid": "ReadSubscriptionAttributesInWatcherRegion",
            "Effect": "Allow",
            "Action": "sns:GetSubscriptionAttributes",
            "Resource": "*",
            "Condition": {"StringEquals": {"aws:RequestedRegion": "eu-central-1"}},
        },
    )
    old_policy["Statement"] = statements
    updated = decode_policy(boundary_after.get("policy"), "updated production boundary")
    require(
        updated == old_policy,
        "production boundary adds permissions beyond scoped reads",
    )
    require(
        decode_policy(inline_after.get("policy"), "updated production inline")
        == updated,
        "updated production inline differs from boundary",
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
    if (
        hashlib.sha256(previous.encode()).hexdigest()
        == PREVIOUS_ACCEPTANCE_PROVIDER_SHA256
    ):
        validate_acceptance_provider_policy(
            previous, inline_before, boundary_after, inline_after
        )
        return
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
    bucket_matches = [
        statement
        for statement in statements
        if statement.get("Sid") == "ManageOnlyDisposableBuckets"
    ]
    role_matches = [
        statement
        for statement in statements
        if statement.get("Sid") == "ManageOnlyDisposableWatcherRoles"
    ]
    require(
        len(bucket_matches) == len(role_matches) == 1,
        "reviewed management statements missing",
    )
    bucket = bucket_matches[0]
    role = role_matches[0]
    require(
        bucket.get("Resource") == "arn:aws:s3:::minos-health-watcher-acceptance-*",
        "bucket read scope changed",
    )
    require(
        role.get("Resource")
        == f"arn:aws:iam::{ACCOUNT}:role/minos-health-watcher-acceptance-*-watcher",
        "role read scope changed",
    )
    bucket_actions = statement_actions(bucket)
    role_actions = statement_actions(role)
    require(
        bucket_actions[:3]
        == ["s3:GetBucketLocation", "s3:GetBucketTagging", "s3:PutBucketTagging"],
        "bucket action baseline changed",
    )
    require(role_actions[0] == "iam:ListRoleTags", "role action baseline changed")
    bucket["Action"] = (
        bucket_actions[:3]
        + [
            "s3:GetBucketCORS",
            "s3:GetBucketWebsite",
            "s3:GetBucketAccelerateConfiguration",
            "s3:GetBucketRequestPayment",
            "s3:GetBucketLogging",
            "s3:GetBucketReplication",
            "s3:GetBucketObjectLockConfiguration",
        ]
        + bucket_actions[3:]
    )
    role["Action"] = (
        role_actions[:1]
        + ["iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole"]
        + role_actions[1:]
    )
    updated = decode_policy(boundary_after.get("policy"), "updated acceptance boundary")
    require(updated == old_policy, "boundary adds permissions beyond scoped role read")
    require(
        decode_policy(inline_after.get("policy"), "updated inline") == updated,
        "updated inline differs from boundary",
    )


def validate_acceptance_provider_policy(
    previous: str,
    inline_before: dict[str, object],
    boundary_after: dict[str, object],
    inline_after: dict[str, object],
) -> None:
    """Correct exactly two API-to-IAM names, never resources or write permissions."""
    old_policy = decode_policy(previous, "previous acceptance boundary")
    require(
        decode_policy(inline_before.get("policy"), "previous inline") == old_policy,
        "previous inline differs from boundary",
    )
    buckets = [
        item
        for item in policy_statements(old_policy)
        if item.get("Sid") == "ManageOnlyDisposableBuckets"
    ]
    require(len(buckets) == 1, "disposable bucket statement missing")
    bucket = buckets[0]
    require(
        bucket.get("Resource") == "arn:aws:s3:::minos-health-watcher-acceptance-*",
        "bucket read scope changed",
    )
    actions = statement_actions(bucket)
    require(
        actions.count("s3:GetBucketAccelerateConfiguration") == 1,
        "previous acceleration action differs",
    )
    require(
        actions.count("s3:GetBucketReplication") == 1,
        "previous replication action differs",
    )
    bucket["Action"] = [
        "s3:GetAccelerateConfiguration"
        if action == "s3:GetBucketAccelerateConfiguration"
        else "s3:GetReplicationConfiguration"
        if action == "s3:GetBucketReplication"
        else action
        for action in actions
    ]
    updated = decode_policy(boundary_after.get("policy"), "updated acceptance boundary")
    require(updated == old_policy, "boundary changes more than two provider reads")
    require(
        decode_policy(inline_after.get("policy"), "updated inline") == updated,
        "updated inline differs from boundary",
    )


def main() -> int:
    if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != "--mode"):
        print(
            "usage: aws_watcher_operator_plan_guard.py PLAN_JSON [--mode]",
            file=sys.stderr,
        )
        return 2
    try:
        path = Path(sys.argv[1])
        if path.stat().st_size > 2_000_000:
            raise PlanError("operator plan is oversized")
        document = cast(object, json.loads(path.read_text()))
        validate_plan(document)
    except (OSError, ValueError, TypeError, PlanError) as error:
        print(f"operator plan rejected: {error}", file=sys.stderr)
        return 1
    if len(sys.argv) == 3:
        print("update" if is_read_preflight_update(document) else "bootstrap")
    else:
        print("Validated exact operator bootstrap or two-policy read preflight update.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
