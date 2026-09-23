# pyright: reportAny=false, reportUnannotatedClassAttribute=false, reportUnusedParameter=false

from pathlib import Path

import pytest

from scripts.aws_watcher_acceptance_guard import (
    ACCOUNT_ID,
    expected_bucket_name,
    write_empty_object_manifest,
)
from tests.aws.harness import AcceptanceObjectManifest, ManifestRecordingS3Client

ROOT = Path(__file__).parents[2]
HARNESS = ROOT / "infra" / "terraform" / "watcher-acceptance"
SCRIPTS = ROOT / "scripts"
RUN_ID = "0123456789abcdef"


def _terraform() -> str:
    return "\n".join(path.read_text() for path in sorted(HARNESS.glob("*.tf")))


def test_disposable_root_is_randomized_local_and_account_guarded() -> None:
    terraform = _terraform()
    provider = (HARNESS / "provider.tf").read_text()
    archive = (HARNESS / "archive.tf").read_text()
    locals_config = (HARNESS / "locals.tf").read_text()
    iam = (HARNESS / "iam.tf").read_text()
    normalized_terraform = " ".join(terraform.split())
    normalized_archive = " ".join(archive.split())
    normalized_locals = " ".join(locals_config.split())
    normalized_iam = " ".join(iam.split())

    assert 'resource "terraform_data" "run_guard"' in provider
    assert "input = var.test_run_id" in provider
    assert "backend " not in terraform
    assert "force_destroy = false" in normalized_archive
    assert "object_lock_enabled = false" in normalized_archive
    assert "prevent_destroy" not in terraform
    assert "allowed_account_ids = [var.expected_test_account_id]" in provider
    assert "forbidden_account_ids" not in provider
    assert 'known_production_account_id = "218375352108"' in terraform
    assert "var.expected_test_account_id == var.production_account_id" in provider
    assert (
        "data.aws_caller_identity.current.account_id == local.known_production_account_id"
        in provider
    )
    assert "var.production_acceptance_enabled" in provider
    assert (
        "var.production_acceptance_confirmation == local.production_confirmation"
        in provider
    )
    assert "var.resource_prefix" not in terraform
    assert "operator_principal_arn" not in (HARNESS / "variables.tf").read_text()
    assert (
        'expected_operator_arn = "arn:aws:iam::${local.known_production_account_id}:role/minos-health-watcher-acceptance-operator"'
        in normalized_locals
    )
    assert (
        'permissions_boundary_arn = "arn:aws:iam::${local.known_production_account_id}:policy/minos-health-watcher-acceptance-boundary"'
        in normalized_locals
    )
    assert 'resource_prefix = "minos-health-watcher-acceptance"' in normalized_terraform
    assert 'Environment = "acceptance"' in normalized_terraform
    assert 'Disposable = "true"' in normalized_terraform
    assert "tags = local.acceptance_tags" in normalized_archive
    assert "tags = local.acceptance_tags" in normalized_iam
    assert "permissions_boundary = local.permissions_boundary_arn" in normalized_iam
    assert "inline_policy" not in iam
    assert "managed_policy_arns" not in iam
    assert (
        "depends_on = [ aws_iam_role.watcher, aws_s3_bucket.snapshots, ]"
        in normalized_iam
    )


def test_watcher_is_an_assumable_role_with_no_user_or_access_key() -> None:
    terraform = _terraform()
    iam = (HARNESS / "iam.tf").read_text()

    assert 'resource "aws_iam_role" "watcher"' in iam
    assert 'actions = ["sts:AssumeRole"]' in iam
    assert 'variable = "aws:PrincipalArn"' in iam
    assert 'actions = ["s3:PutObject"]' in iam
    assert 'variable = "s3:if-none-match"' in iam
    assert "aws_iam_user" not in terraform
    assert "aws_iam_access_key" not in terraform
    for forbidden in (
        "trusted-recovery",
        "recovery-inventories",
        "release-manifests",
        "deployment-receipts",
        "terraform-state",
    ):
        assert forbidden not in iam


def test_bucket_guards_and_lifecycle_match_the_watcher_contract() -> None:
    archive = (HARNESS / "archive.tf").read_text()
    lifecycle = (HARNESS / "lifecycle.tf").read_text()
    normalized_archive = " ".join(archive.split())
    normalized_lifecycle = " ".join(lifecycle.split())

    for sid in (
        "DenyWatcherObjectDestructionOrAclMutation",
        "DenyWatcherBucketAdministration",
        "DenyInsecureTransport",
        "DenyNonConditionalImmutableWrites",
        "DenyExplicitNonSSES3Encryption",
    ):
        assert f'sid    = "{sid}"' in archive
    assert 'sse_algorithm = "AES256"' in archive
    assert 'object_ownership = "BucketOwnerEnforced"' in archive
    for prefix, days in (
        ("control-recovery/run/", 30),
        ("control-recovery/daily/", 31),
        ("control-recovery/weekly/", 91),
        ("control-recovery/monthly/", 400),
        ("trusted-recovery/", 400),
        ("probes/", 7),
    ):
        assert f'filter {{ prefix = "{prefix}" }}' in lifecycle
        assert f"expiration {{ days = {days} }}" in lifecycle
    assert lifecycle.count("noncurrent_version_expiration { noncurrent_days = 1 }") == 6
    assert lifecycle.count("expiration { expired_object_delete_marker = true }") == 6
    assert (
        "abort_incomplete_multipart_upload { days_after_initiation = 7 }" in lifecycle
    )
    for permanent_prefix in (
        "blobs/sha256/",
        "recovery-inventories/",
        "release-manifests/",
        "deployment-receipts/",
    ):
        assert permanent_prefix not in lifecycle
    assert archive.count("depends_on") == 6
    assert normalized_archive.count("depends_on = [aws_s3_bucket.snapshots]") == 4
    assert (
        "depends_on = [ aws_iam_role.watcher, aws_s3_bucket.snapshots, aws_s3_bucket_public_access_block.snapshots, ]"
        in normalized_archive
    )
    assert (
        "depends_on = [ aws_s3_bucket.snapshots, aws_s3_bucket_versioning.snapshots, ]"
        in normalized_lifecycle
    )


def test_scripts_separate_apply_test_destroy_and_never_embed_credentials() -> None:
    common = (SCRIPTS / "aws-watcher-common.sh").read_text()
    setup = (SCRIPTS / "aws-watcher-acceptance-setup.sh").read_text()
    test = (SCRIPTS / "aws-watcher-acceptance-test.sh").read_text()
    destroy = (SCRIPTS / "aws-watcher-acceptance-destroy.sh").read_text()
    production = (SCRIPTS / "aws-watcher-production-apply.sh").read_text()

    assert (
        "MINOS_AWS_TEST_APPLY_CONFIRM=APPLY:$MINOS_AWS_TEST_ACCOUNT_ID:$plan_digest"
        in setup
    )
    assert (
        "MINOS_AWS_TEST_DESTROY_CONFIRM=DESTROY:$MINOS_AWS_TEST_ACCOUNT_ID:$plan_digest"
        in destroy
    )
    assert "watcher_apply_immutable_plan" not in setup
    assert "watcher_apply_immutable_plan" not in destroy
    assert setup.index("watcher_open_confirmed_plan") < setup.index(
        "watcher_require_create_targets_absent"
    )
    assert setup.index("watcher_require_create_targets_absent") < setup.index(
        "watcher_apply_opened_plan"
    )
    assert destroy.index("watcher_open_confirmed_plan") < destroy.index(
        "cleanup-bucket"
    )
    assert destroy.index("cleanup-bucket") < destroy.index("watcher_apply_opened_plan")
    assert "apply-plan.json" in setup
    assert "destroy-plan.json" in destroy
    assert "sts get-caller-identity" in common
    assert 'MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID="218375352108"' in common
    assert "AWS_PROFILE must exactly match" in common
    assert "MINOS_AWS_REQUIRED_PRODUCTION_CONFIRMATION" in common
    assert "terraform" not in test
    assert "pytest" in test
    assert "watcher_terraform apply" not in test
    assert "watcher_load_runtime" in test
    assert "watcher_load_runtime" in destroy
    assert "terraform state is missing" in destroy.lower()
    assert "destroy-state-before.json" in destroy
    assert "watcher_verify_teardown" in destroy
    assert "validate-destroy-plan" in destroy
    assert "cleanup-bucket" in destroy
    assert "revoke-writer" in destroy
    assert "-destroy" in destroy
    assert "bucket still exists" in destroy
    assert "role still exists" in destroy
    assert "AWS_SECRET_ACCESS_KEY=" not in common + setup + test + destroy + production


def test_production_apply_is_bound_to_the_reviewed_plan_digest() -> None:
    script = (SCRIPTS / "aws-watcher-production-apply.sh").read_text()
    readme = (ROOT / "infra" / "terraform" / "watcher" / "README.md").read_text()

    assert (
        'MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID="218375352108"'
        in (SCRIPTS / "aws-watcher-common.sh").read_text()
    )
    assert "sts get-caller-identity" in script
    assert "assumed-role/minos-health-watcher-terraform-operator/" in script
    assert "watcher_terraform plan \\" in script
    assert (
        '-var="expected_aws_account_id=$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID"'
        in script
    )
    assert 'watcher_terraform show -json "$plan_path"' in script
    assert 'chmod 0400 "$plan_path"' in script
    assert "watcher_apply_immutable_plan" in script
    assert 'watcher_terraform apply "$plan_path"' not in script
    assert "APPLY:218375352108:<reviewed-plan-sha256>" in readme
    assert "aws_iam_access_key" not in script


def test_production_backend_verifies_owner_and_uses_supported_account_guard() -> None:
    script = (SCRIPTS / "aws-watcher-production-apply.sh").read_text()
    readme = (ROOT / "infra" / "terraform" / "watcher" / "README.md").read_text()

    assert "s3api list-objects-v2" in script
    assert "--expected-bucket-owner" in script
    assert script.index("s3api list-objects-v2") < script.index(
        "watcher_terraform init"
    )
    assert "--prefix watcher/terraform.tfstate" in script
    assert "--max-keys 1 --no-paginate" in script
    assert '-backend-config="allowed_account_ids=' in script
    assert '-backend-config="expected_bucket_owner=' not in script
    assert "s3api list-objects-v2" in readme
    assert "allowed_account_ids" in readme


def test_aws_tests_are_marked_and_default_to_skip_without_opt_in() -> None:
    conftest = (ROOT / "tests" / "aws" / "conftest.py").read_text()
    acceptance = (ROOT / "tests" / "aws" / "test_watcher_acceptance.py").read_text()
    pyproject = (ROOT / "pyproject.toml").read_text()

    assert "pytestmark = pytest.mark.aws" in acceptance
    assert 'os.environ.get("MINOS_AWS_ACCEPTANCE") != "1"' in conftest
    assert (
        '"aws: explicit opt-in tests against tagged disposable resources' in pyproject
    )
    assert "subprocess" not in conftest + acceptance
    assert "S3BlobArchive(" in acceptance
    assert "BlobStagingArea(" in acceptance
    assert "S3OperatorRecoveryStorage(" in acceptance
    assert "copy_trusted_if_absent" in acceptance
    assert "put_inventory_if_absent" in acceptance
    assert "secrets.token_hex" in acceptance


class _VersionedS3:
    def __init__(self) -> None:
        self.delete_calls = 0

    def put_object(self, **kwargs: object) -> dict[str, str]:
        return {"VersionId": "put-version"}

    def copy_object(self, **kwargs: object) -> dict[str, str]:
        return {"VersionId": "copy-version"}

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        self.delete_calls += 1
        return {"VersionId": "delete-marker", "DeleteMarker": True}


def test_s3_wrapper_records_exact_versions_and_delete_markers(tmp_path: Path) -> None:
    manifest_path = tmp_path / "objects.json"
    bucket = expected_bucket_name(RUN_ID)
    write_empty_object_manifest(
        manifest_path,
        account_id=ACCOUNT_ID,
        bucket=bucket,
        run_id=RUN_ID,
    )
    manifest = AcceptanceObjectManifest(
        str(manifest_path), bucket=bucket, run_id=RUN_ID
    )
    backend = _VersionedS3()
    client = ManifestRecordingS3Client(backend, manifest)
    put_key = "blobs/sha256/" + "a" * 64
    copy_key = "trusted-recovery/10000000000000000000/" + "b" * 64 + ".sqlite3"

    _ = client.put_object(Bucket=bucket, Key=put_key)
    _ = client.copy_object(Bucket=bucket, Key=copy_key)
    _ = client.delete_object(Bucket=bucket, Key=put_key)

    assert manifest.identities() == {
        (put_key, "put-version", "version"),
        (copy_key, "copy-version", "version"),
        (put_key, "delete-marker", "delete_marker"),
    }

    with pytest.raises(RuntimeError, match="version absent"):
        _ = client.delete_object(
            Bucket=bucket,
            Key=put_key,
            VersionId="unmanifested-version",
        )
    assert backend.delete_calls == 1
