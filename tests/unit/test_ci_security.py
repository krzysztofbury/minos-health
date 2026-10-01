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
            else:
                assert "permissions" not in job_definition

        triggers = cast("dict[str, object]", document.get("on"))
        assert set(triggers) <= ALLOWED_TRIGGERS
        assert "pull_request_target" not in triggers
        if workflow.name == "publish-watcher.yml":
            assert set(triggers) == {"workflow_dispatch"}
        elif workflow.name == "reference-validation.yml":
            assert set(triggers) == {"pull_request", "workflow_dispatch"}


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
