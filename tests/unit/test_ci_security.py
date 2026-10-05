from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
ACTION_REFERENCE = re.compile(
    r"^\s*(?:-\s+)?uses:\s+(?P<action>[^@\s]+)@(?P<revision>[^\s#]+)",
)
ALLOWED_TRIGGERS = {
    "pull_request",
    "push",
    "schedule",
    "workflow_call",
    "workflow_dispatch",
}


def workflows() -> list[Path]:
    return sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])


def load_workflow(workflow: Path) -> dict[str, object]:
    return cast(
        "dict[str, object]", yaml.load(workflow.read_text(), Loader=yaml.BaseLoader)
    )


def test_ci_workflows_exist() -> None:
    assert workflows(), "At least one CI workflow is required"


def test_actions_are_pinned_to_full_commit_shas() -> None:
    for workflow in workflows():
        for line_number, line in enumerate(workflow.read_text().splitlines(), start=1):
            if "uses:" not in line:
                continue

            match = ACTION_REFERENCE.match(line)
            assert match is not None, (
                f"Malformed action reference at {workflow}:{line_number}"
            )
            action = match.group("action")
            if action.startswith("./"):
                continue

            revision = match.group("revision")
            assert re.fullmatch(r"[0-9a-f]{40}", revision), (
                f"Action must use a full commit SHA at {workflow}:{line_number}"
            )


def test_workflows_use_read_only_defaults_and_safe_triggers() -> None:
    for workflow in workflows():
        document = load_workflow(workflow)
        assert document.get("permissions") == {"contents": "read"}

        jobs = cast("dict[str, object]", document.get("jobs"))
        for name, job in jobs.items():
            job_definition = cast("dict[str, object]", job)
            if workflow.name == "publish-watcher.yml" and name == "publish":
                assert job_definition.get("permissions") == {
                    "contents": "read",
                    "packages": "write",
                }
                assert job_definition.get("if") == (
                    "github.ref == 'refs/heads/main' && "
                    "github.repository == 'krzysztofbury/minos-health'"
                )
            elif workflow.name == "reference-validation.yml" and name == "publish":
                assert job_definition.get("permissions") == {
                    "contents": "read",
                    "packages": "write",
                }
                assert job_definition.get("if") == (
                    "github.event_name == 'workflow_dispatch' && "
                    "github.ref == 'refs/heads/main' && "
                    "github.repository == 'krzysztofbury/minos-health'"
                )
                assert job_definition.get("needs") == "verify"
            elif (workflow.name, name) in (
                ("freshness-oidc-reusable.yml", "observe"),
                ("freshness-oidc-rehearsal.yml", "rehearse"),
            ):
                # Only these diagnostic jobs may request the fixed non-AWS audience.
                assert job_definition.get("permissions") == {
                    "contents": "read",
                    "id-token": "write",
                }
                assert job_definition.get("if") == (
                    "github.repository_id == '1325001960' && "
                    "github.event_name == 'pull_request' && "
                    "github.event.pull_request.head.repo.id == 1325001960 && "
                    "github.event.pull_request.base.ref == 'main'"
                )
                assert "secrets" not in job_definition
                assert "environment" not in job_definition
            else:
                assert "permissions" not in job_definition

        triggers = cast("dict[str, object]", document.get("on"))
        assert set(triggers) <= ALLOWED_TRIGGERS
        assert "pull_request_target" not in triggers
        if workflow.name == "publish-watcher.yml":
            assert set(triggers) == {"workflow_dispatch"}
        elif workflow.name == "reference-validation.yml":
            assert set(triggers) == {"pull_request", "workflow_dispatch"}
        elif workflow.name == "freshness-oidc-reusable.yml":
            assert set(triggers) == {"workflow_call"}
        elif workflow.name == "freshness-oidc-rehearsal.yml":
            assert set(triggers) == {"pull_request"}


def test_checkout_does_not_persist_credentials() -> None:
    for workflow in workflows():
        lines = workflow.read_text().splitlines()
        for index, line in enumerate(lines):
            if "uses: actions/checkout@" not in line:
                continue
            checkout_block = "\n".join(lines[index : index + 6])
            assert "persist-credentials: false" in checkout_block
            if workflow.name == "ci.yml":
                assert "fetch-depth: 0" in checkout_block


def test_claim_rehearsal_has_no_caller_inputs_checkout_secrets_or_restored_cache() -> (
    None
):
    document = load_workflow(WORKFLOW_DIR / "freshness-oidc-reusable.yml")
    trigger = cast("dict[str, object]", document["on"])["workflow_call"]
    assert trigger in ("", {})
    jobs = cast("dict[str, dict[str, object]]", document["jobs"])
    assert set(jobs) == {"observe"}
    # GitHub rejects runner context at job-level env before assigning a runner.
    job_environment = cast("dict[str, str]", jobs["observe"]["env"])
    assert not any("runner." in value for value in job_environment.values())
    steps = cast("list[dict[str, object]]", jobs["observe"]["steps"])
    checkout = steps[0]
    assert (
        checkout["uses"] == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    settings = cast("dict[str, str]", checkout["with"])
    assert settings["repository"] == "krzysztofbury/minos-health"
    assert re.fullmatch(r"[0-9a-f]{40}", settings["ref"])
    assert settings["persist-credentials"] == "false"
    assert settings["path"] == "trusted-tools"
    runtime = cast("dict[str, str]", steps[1]["with"])
    assert runtime["enable-cache"] == "false"
    for step in steps[2:]:
        assert step.get("working-directory") == "trusted-tools"
        assert "uses" not in step
        step_environment = cast("dict[str, str]", step["env"])
        assert step_environment["UV_CACHE_DIR"] == (
            "${{ runner.temp }}/minos-oidc-rehearsal-uv"
        )
    assert len(steps) == 4


def test_ci_scans_nonempty_git_history_for_secrets() -> None:
    ci = (WORKFLOW_DIR / "ci.yml").read_text()
    assert "pre-commit run gitleaks-history --hook-stage manual --all-files" in ci

    configuration = cast(
        "dict[str, object]",
        yaml.load(
            (ROOT / ".pre-commit-config.yaml").read_text(), Loader=yaml.BaseLoader
        ),
    )
    repositories = cast("list[dict[str, object]]", configuration["repos"])
    gitleaks_repository = next(
        repository
        for repository in repositories
        if repository["repo"] == "https://github.com/gitleaks/gitleaks"
    )
    hooks = cast("list[dict[str, object]]", gitleaks_repository["hooks"])
    history_hook = next(
        hook for hook in hooks if hook.get("alias") == "gitleaks-history"
    )
    assert history_hook["entry"] == "gitleaks git --redact --verbose"
    assert history_hook["stages"] == ["manual"]


def test_ci_bootstraps_and_preflights_a_clean_watcher_volume_before_start() -> None:
    ci = (WORKFLOW_DIR / "ci.yml").read_text()
    build = ci.index("docker build --platform linux/amd64 --target watcher")
    bootstrap = ci.index("watcher-bootstrap-rsk", build)
    preflight = ci.index("watcher-preflight", bootstrap)
    start = ci.index("docker compose --file compose.yaml up --detach", preflight)

    assert build < bootstrap < preflight < start
    assert "--profile bootstrap run --rm --no-deps" in ci[build:preflight]
    assert "--profile preflight run --rm --no-deps" in ci[bootstrap:start]
    assert '"/var/lib/minos/public"' in ci[start:]


def test_trivy_exception_is_narrow_and_expires() -> None:
    exception = (ROOT / ".trivyignore.yaml").read_text()
    assert "id: AVD-AWS-0132" in exception
    assert "- bootstrap/encryption.tf" in exception
    assert "expired_at: 2027-03-17" in exception

    encryption = (ROOT / "infra" / "bootstrap" / "encryption.tf").read_text()
    assert encryption.count('resource "') == 1
    assert encryption.count('sse_algorithm = "AES256"') == 1
