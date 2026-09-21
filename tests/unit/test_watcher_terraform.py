import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
WATCHER = ROOT / "infra" / "terraform" / "watcher"
WATCHER_ACCEPTANCE = ROOT / "infra" / "terraform" / "watcher-acceptance"


def source(name: str) -> str:
    return (WATCHER / name).read_text()


def disposable_source(name: str) -> str:
    return (WATCHER_ACCEPTANCE / name).read_text()


def blocks(document: str, header: str) -> list[str]:
    matches: list[str] = []
    start = 0
    marker = re.compile(rf"(?m)^\s*{re.escape(header)}\s*{{")
    while match := marker.search(document, start):
        depth = 1
        cursor = match.end()
        while cursor < len(document) and depth:
            if document[cursor] == "{":
                depth += 1
            elif document[cursor] == "}":
                depth -= 1
            cursor += 1
        assert depth == 0, f"unterminated {header} block"
        matches.append(document[match.end() : cursor - 1])
        start = cursor
    return matches


def quoted_values(block: str, attribute: str) -> set[str]:
    list_match = re.search(
        rf"(?s)\b{re.escape(attribute)}\s*=\s*\[(.*?)\]",
        block,
    )
    if list_match is not None:
        return set(re.findall(r'"([^"]+)"', list_match.group(1)))
    scalar_match = re.search(
        rf'\b{re.escape(attribute)}\s*=\s*"([^"]+)"',
        block,
    )
    assert scalar_match is not None
    return {scalar_match.group(1)}


def statement_by_sid(document: str, sid: str) -> str:
    for statement in blocks(document, "statement"):
        if re.search(rf'\bsid\s*=\s*"{re.escape(sid)}"', statement):
            return statement
    raise AssertionError(f"statement {sid} not found")


def test_watcher_is_remote_state_ready_and_account_guarded() -> None:
    versions = source("versions.tf")
    provider = source("provider.tf")
    lockfile = source(".terraform.lock.hcl")

    assert 'required_version = ">= 1.16.0, < 2.0.0"' in versions
    assert 'version = "~> 6.0"' in versions
    assert 'backend "s3"' in versions
    assert 'key          = "watcher/terraform.tfstate"' in versions
    assert "use_lockfile = true" in versions
    assert "allowed_account_ids = [var.expected_aws_account_id]" in provider
    assert 'provider "registry.terraform.io/hashicorp/aws"' in lockfile
    assert 'constraints = "~> 6.0"' in lockfile


def test_snapshot_bucket_has_required_destruction_and_access_guards() -> None:
    archive = source("archive.tf")

    assert "force_destroy = false" in archive
    assert "prevent_destroy = true" in archive
    assert 'sse_algorithm = "AES256"' in archive
    assert 'status = "Enabled"' in archive
    for setting in (
        "block_public_acls       = true",
        "block_public_policy     = true",
        "ignore_public_acls      = true",
        "restrict_public_buckets = true",
    ):
        assert setting in archive
    assert 'sid    = "DenyInsecureTransport"' in archive
    assert 'variable = "aws:SecureTransport"' in archive
    assert 'sid    = "DenyNonConditionalImmutableWrites"' in archive
    assert 'variable = "s3:if-none-match"' in archive
    assert 'variable = "s3:ObjectCreationOperation"' in archive

    object_deny = statement_by_sid(
        archive,
        "DenyWatcherObjectDestructionOrAclMutation",
    )
    assert "identifiers = [aws_iam_user.watcher.arn]" in object_deny
    assert quoted_values(object_deny, "actions") == {
        "s3:BypassGovernanceRetention",
        "s3:DeleteObject",
        "s3:DeleteObjectTagging",
        "s3:DeleteObjectVersion",
        "s3:DeleteObjectVersionTagging",
        "s3:PutObjectAcl",
        "s3:PutObjectLegalHold",
        "s3:PutObjectRetention",
        "s3:PutObjectTagging",
        "s3:RestoreObject",
    }
    bucket_deny = statement_by_sid(archive, "DenyWatcherBucketAdministration")
    assert "identifiers = [aws_iam_user.watcher.arn]" in bucket_deny
    assert quoted_values(bucket_deny, "actions") == {
        "s3:DeleteBucket",
        "s3:DeleteBucketPolicy",
        "s3:PutBucketAcl",
        "s3:PutBucketOwnershipControls",
        "s3:PutBucketPolicy",
        "s3:PutBucketPublicAccessBlock",
        "s3:PutBucketVersioning",
        "s3:PutEncryptionConfiguration",
        "s3:PutLifecycleConfiguration",
        "s3:PutReplicationConfiguration",
    }


def test_lifecycle_retention_is_exact_and_leaves_permanent_prefixes_alone() -> None:
    lifecycle = source("lifecycle.tf")
    expected_expiration = {
        "control-recovery/run/": 30,
        "control-recovery/daily/": 31,
        "control-recovery/weekly/": 91,
        "control-recovery/monthly/": 400,
        "trusted-recovery/": 400,
        "probes/": 7,
    }

    rules = blocks(lifecycle, "rule")
    expiration_pairs: dict[str, int] = {}
    delete_marker_prefixes: set[str] = set()
    multipart_rules = 0
    for rule in rules:
        prefix_match = re.search(r'\bprefix\s*=\s*"([^"]+)"', rule)
        day_match = re.search(r"(?m)^\s*days\s*=\s*(\d+)\s*$", rule)
        if prefix_match is not None and day_match is not None:
            prefix = prefix_match.group(1)
            assert prefix not in expiration_pairs
            expiration_pairs[prefix] = int(day_match.group(1))
            assert re.search(r"\bnoncurrent_days\s*=\s*1\b", rule)
        if prefix_match is not None and "expired_object_delete_marker = true" in rule:
            delete_marker_prefixes.add(prefix_match.group(1))
        if "abort_incomplete_multipart_upload" in rule:
            multipart_rules += 1
            assert re.search(r"\bdays_after_initiation\s*=\s*7\b", rule)

    assert expiration_pairs == expected_expiration
    assert delete_marker_prefixes == set(expected_expiration)
    assert multipart_rules == 1
    for permanent_prefix in (
        "blobs/sha256/",
        "recovery-inventories/",
        "release-manifests/",
        "deployment-receipts/",
    ):
        assert permanent_prefix not in lifecycle


def test_watcher_policy_has_no_key_delete_admin_or_operator_prefix_access() -> None:
    iam = source("iam.tf")
    watcher = iam.split(
        'resource "aws_iam_openid_connect_provider" "github"', maxsplit=1
    )[0]

    assert 'resource "aws_iam_user" "watcher"' in watcher
    assert "force_destroy = false" in watcher
    assert "aws_iam_access_key" not in iam
    assert 'actions = ["s3:PutObject"]' in watcher
    assert 'variable = "s3:if-none-match"' in watcher
    for prefix in ("blobs/sha256", "control-recovery", "probes"):
        assert f'"{prefix}"' in source("locals.tf")
    for forbidden in (
        "s3:Delete",
        "s3:PutBucket",
        "s3:*",
        "trusted-recovery",
        "recovery-inventories",
        "release-manifests",
        "deployment-receipts",
        "terraform.tfstate",
    ):
        assert forbidden not in watcher


def test_disposable_watcher_policy_contract_exactly_matches_production() -> None:
    watcher_header = 'data "aws_iam_policy_document" "watcher_archive"'
    production_policy = blocks(source("iam.tf"), watcher_header)
    disposable_policy = blocks(disposable_source("iam.tf"), watcher_header)

    assert len(production_policy) == 1
    assert len(disposable_policy) == 1
    assert production_policy[0].replace(
        "aws_s3_bucket.snapshots.arn", "BUCKET_ARN"
    ) == disposable_policy[0].replace("local.bucket_arn", "BUCKET_ARN")

    bucket_header = 'data "aws_iam_policy_document" "snapshot_bucket"'
    production_bucket_policy = blocks(source("archive.tf"), bucket_header)
    disposable_bucket_policy = blocks(disposable_source("archive.tf"), bucket_header)
    assert len(production_bucket_policy) == 1
    assert len(disposable_bucket_policy) == 1
    assert production_bucket_policy[0].replace(
        "aws_iam_user.watcher.arn", "WATCHER_PRINCIPAL"
    ).replace("aws_s3_bucket.snapshots.arn", "BUCKET_ARN") == disposable_bucket_policy[
        0
    ].replace("local.watcher_role_arn", "WATCHER_PRINCIPAL").replace(
        "local.bucket_arn", "BUCKET_ARN"
    )

    prefix_pattern = re.compile(r"(?s)watcher_object_prefixes\s*=\s*\[(.*?)\]")
    production_prefixes = prefix_pattern.search(source("locals.tf"))
    disposable_prefixes = prefix_pattern.search(disposable_source("locals.tf"))
    assert production_prefixes is not None
    assert disposable_prefixes is not None
    assert set(re.findall(r'"([^"]+)"', production_prefixes.group(1))) == {
        "blobs/sha256",
        "control-recovery",
        "probes",
    }
    assert disposable_prefixes.group(1) == production_prefixes.group(1)


def test_github_role_is_read_only_and_trusts_only_parameterized_ref() -> None:
    iam = source("iam.tf")
    github = iam.split(
        'resource "aws_iam_openid_connect_provider" "github"', maxsplit=1
    )[1]

    assert 'actions = ["sts:AssumeRoleWithWebIdentity"]' in github
    assert 'variable = "token.actions.githubusercontent.com:aud"' in github
    assert 'variable = "token.actions.githubusercontent.com:sub"' in github
    assert (
        'values   = ["repo:${var.github_repository}:ref:${var.github_ref}"]' in github
    )
    assert "s3:PutObject" not in github
    assert "s3:Delete" not in github
    assert '"s3:GetObject"' in github


def test_budget_inputs_are_validated_and_subscription_confirmation_is_documented() -> (
    None
):
    variables = source("variables.tf")
    budget = source("budget.tf")
    readme = source("README.md")

    for name in (
        "monthly_budget_amount",
        "budget_currency",
        "budget_subscriber_email",
    ):
        assert f'variable "{name}"' in variables
    assert variables.count("validation {") >= 8
    assert 'protocol  = "email"' in budget
    assert "confirmation_timeout_in_minutes = 10" in budget
    assert any(
        line.split() == ["endpoint_auto_confirms", "=", "false"]
        for line in budget.splitlines()
    )
    assert "subscriber_sns_topic_arns" in budget
    assert 'name   = "TagKeyValue"' in budget
    assert 'values = ["user:Project$minos-health"]' in budget
    assert "account-wide billing residual" in readme
    assert "PendingConfirmation" in readme
    assert "Plan Approval Gate" in readme


def test_example_contains_no_real_identifiers_or_secret_values() -> None:
    example = source("terraform.tfvars.example")

    assert 'expected_aws_account_id = "<twelve-digit-account-id>"' in example
    assert 'github_repository = "<github-owner>/<github-repository>"' in example
    assert any(
        line.split() == ["monthly_budget_amount", "=", "0"]
        for line in example.splitlines()
    )
    assert 'budget_subscriber_email = "<operator-supplied-real-email>"' in example
