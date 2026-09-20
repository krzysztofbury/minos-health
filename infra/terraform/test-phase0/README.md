# Disposable phase-0 AWS acceptance root

This root exists only for explicit real-AWS acceptance of issue #49. It uses
local Terraform state, creates a randomized versioned bucket and an assumable
watcher role, and creates no IAM user or access key.

Do not invoke Terraform directly. Use the bounded scripts in `scripts/` so STS
identity, the non-production account guard, saved plans, and separate apply and
destroy approvals are enforced. Ordinary pytest only skips these tests and
never plans or applies infrastructure.

The repository-owned production account `218375352108` is always forbidden by
Terraform, the scripts, and pytest. `MINOS_AWS_PRODUCTION_ACCOUNT_ID` is an
additional operator confirmation and cannot replace that built-in guard.

Required operator inputs are listed by each script with `--help`. The generated
state, plans, captured identities, and runtime environment are stored below the
ignored `artifacts/aws-phase0/<test-account-id>/` directory.
