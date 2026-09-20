from pathlib import Path

ROOT = Path(__file__).parents[2]
HARNESS = ROOT / "infra" / "terraform" / "test-phase0"
SCRIPTS = ROOT / "scripts"


def _terraform() -> str:
    return "\n".join(path.read_text() for path in sorted(HARNESS.glob("*.tf")))


def test_disposable_root_is_randomized_local_and_account_guarded() -> None:
    terraform = _terraform()
    provider = (HARNESS / "provider.tf").read_text()
    archive = (HARNESS / "archive.tf").read_text()

    assert 'resource "random_id" "suffix"' in provider
    assert "backend " not in terraform
    assert "force_destroy = true" in archive
    assert "prevent_destroy" not in terraform
    assert "allowed_account_ids = [var.expected_test_account_id]" in provider
    assert 'known_production_account_id = "218375352108"' in terraform
    assert "local.known_production_account_id" in provider
    assert "var.production_account_id" in provider
    assert (
        "data.aws_caller_identity.current.account_id != var.production_account_id"
        in provider
    )
    assert (
        "data.aws_caller_identity.current.account_id != local.known_production_account_id"
        in provider
    )
    assert 'Environment = "acceptance"' in provider
    assert 'Disposable  = "true"' in provider


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


def test_bucket_guards_and_lifecycle_match_the_phase0_contract() -> None:
    archive = (HARNESS / "archive.tf").read_text()
    lifecycle = (HARNESS / "lifecycle.tf").read_text()

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


def test_scripts_separate_apply_test_destroy_and_never_embed_credentials() -> None:
    common = (SCRIPTS / "aws-phase0-common.sh").read_text()
    setup = (SCRIPTS / "aws-phase0-setup.sh").read_text()
    test = (SCRIPTS / "aws-phase0-test.sh").read_text()
    destroy = (SCRIPTS / "aws-phase0-destroy.sh").read_text()
    production = (SCRIPTS / "aws-phase0-production-apply.sh").read_text()

    assert (
        "MINOS_AWS_TEST_APPLY_CONFIRM=APPLY:$MINOS_AWS_TEST_ACCOUNT_ID:$plan_digest"
        in setup
    )
    assert (
        "MINOS_AWS_TEST_DESTROY_CONFIRM=DESTROY:$MINOS_AWS_TEST_ACCOUNT_ID:$plan_digest"
        in destroy
    )
    assert "apply confirmation does not match the exact saved plan" in setup
    assert "destroy confirmation does not match the exact saved plan" in destroy
    assert "apply-plan.json" in setup
    assert "destroy-plan.json" in destroy
    assert "sts get-caller-identity" in common
    assert 'MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID="218375352108"' in common
    assert "AWS_PROFILE must exactly match" in common
    assert "configured production account" in common
    assert "terraform" not in test
    assert "pytest" in test
    assert "phase0_terraform apply" not in test
    assert "phase0_load_runtime" in test
    assert "phase0_load_runtime" not in destroy
    assert "terraform state is missing" in destroy.lower()
    assert "destroy-state-before.json" in destroy
    assert "phase0_state_resource_value" in destroy
    assert "-destroy" in destroy
    assert "bucket still exists" in destroy
    assert "role still exists" in destroy
    assert "AWS_SECRET_ACCESS_KEY=" not in common + setup + test + destroy + production


def test_production_apply_is_bound_to_the_reviewed_plan_digest() -> None:
    script = (SCRIPTS / "aws-phase0-production-apply.sh").read_text()
    readme = (ROOT / "infra" / "terraform" / "phase0" / "README.md").read_text()

    assert (
        'MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID="218375352108"'
        in (SCRIPTS / "aws-phase0-common.sh").read_text()
    )
    assert "sts get-caller-identity" in script
    assert "phase0_terraform plan \\" in script
    assert (
        '-var="expected_aws_account_id=$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID"'
        in script
    )
    assert 'phase0_terraform show -json "$plan_path"' in script
    assert 'plan_digest="$(phase0_file_sha256 "$plan_path")"' in script
    assert (
        'expected_confirmation="APPLY:$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID:$plan_digest"'
        in script
    )
    assert 'phase0_terraform apply "$plan_path"' in script
    assert (
        "production apply confirmation does not include the exact reviewed plan digest"
        in script
    )
    assert "APPLY:218375352108:<reviewed-plan-sha256>" in readme
    assert "aws_iam_access_key" not in script


def test_aws_tests_are_marked_and_default_to_skip_without_opt_in() -> None:
    conftest = (ROOT / "tests" / "aws" / "conftest.py").read_text()
    acceptance = (ROOT / "tests" / "aws" / "test_phase0_acceptance.py").read_text()
    pyproject = (ROOT / "pyproject.toml").read_text()

    assert "pytestmark = pytest.mark.aws" in acceptance
    assert 'os.environ.get("MINOS_AWS_ACCEPTANCE") != "1"' in conftest
    assert "a matching dedicated non-default AWS profile is required" in conftest
    assert "the AWS acceptance account must not be production" in conftest
    assert 'KNOWN_PRODUCTION_ACCOUNT_ID = "218375352108"' in conftest
    assert '"aws: opt-in tests against disposable resources' in pyproject
    assert "subprocess" not in conftest + acceptance
    assert "phase0_terraform" not in conftest + acceptance
    assert "aws-phase0-setup" not in conftest + acceptance
    assert "S3BlobArchive(" in acceptance
    assert "BlobStagingArea(" in acceptance
    assert "S3OperatorRecoveryStorage(" in acceptance
    assert "copy_trusted_if_absent" in acceptance
    assert "put_inventory_if_absent" in acceptance
    assert "secrets.token_hex" in acceptance


def test_ci_backend_free_initializes_and_validates_both_phase0_roots() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    for terraform_root in (
        "infra/terraform/phase0",
        "infra/terraform/test-phase0",
    ):
        assert f"terraform -chdir={terraform_root} init" in ci
        assert f"terraform -chdir={terraform_root} validate" in ci
    assert ci.count("-backend=false -lockfile=readonly") >= 2
