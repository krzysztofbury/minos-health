import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_scope import classify


def git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=repository, text=True, stderr=subprocess.DEVNULL
    ).strip()


def commit(repository: Path) -> str:
    _ = git(repository, "add", "--all")
    _ = git(repository, "commit", "--quiet", "--no-verify", "-m", "fixture")
    return git(repository, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    _ = git(tmp_path, "init", "--quiet")
    _ = git(tmp_path, "config", "user.name", "CI fixture")
    _ = git(tmp_path, "config", "user.email", "ci@example.test")
    _ = git(tmp_path, "config", "commit.gpgsign", "false")
    _ = (tmp_path / "README.md").write_text("# Fixture\n")
    (tmp_path / "src").mkdir()
    _ = (tmp_path / "src/runtime.py").write_text("value = 1\n")
    _ = commit(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    ("name", "docs_only"),
    [
        ("README.md", True),
        ("CHANGELOG.md", True),
        ("docs/validation/a note.md", True),
        ("docs/validation/probe.py", False),
        ("src/runtime.py", False),
        ("src/template.md", False),
        ("apps/web/index.html", False),
        ("infra/main.tf", False),
        (".github/workflows/ci.yml", False),
        ("uv.lock", False),
        ("SPEC.md", False),
        ("docs/AGENTS.md", False),
        ("docs/CLAUDE.md", False),
        ("docs/output\ninjected.md", False),
        ("unknown.md", False),
    ],
)
def test_scope_uses_reviewed_paths_not_just_extensions(
    repository: Path, name: str, docs_only: bool
) -> None:
    base = git(repository, "rev-parse", "HEAD")
    changed = repository / name
    changed.parent.mkdir(parents=True, exist_ok=True)
    _ = changed.write_text("changed\n")
    head = commit(repository)

    result = classify(repository, event="pull_request", base=base, head=head)

    assert result.docs_only is docs_only
    if docs_only:
        assert (result.base, result.head) == (base, head)


def test_earlier_runtime_commit_cannot_be_hidden_by_docs_followup(
    repository: Path,
) -> None:
    base = git(repository, "rev-parse", "HEAD")
    _ = (repository / "src/runtime.py").write_text("value = 2\n")
    _ = commit(repository)
    _ = (repository / "README.md").write_text("# Follow-up\n")
    head = commit(repository)

    assert not classify(
        repository, event="pull_request", base=base, head=head
    ).docs_only


@pytest.mark.parametrize("change", ["rename", "symlink", "mode", "delete-readme"])
def test_non_content_and_cross_boundary_changes_keep_full_checks(
    repository: Path, change: str
) -> None:
    base = git(repository, "rev-parse", "HEAD")
    readme = repository / "README.md"
    match change:
        case "rename":
            (repository / "docs").mkdir()
            _ = (repository / "src/runtime.py").rename(repository / "docs/runtime.md")
        case "symlink":
            readme.unlink()
            readme.symlink_to("src/runtime.py")
        case "mode":
            readme.chmod(0o755)
        case "delete-readme":
            readme.unlink()
        case _:
            raise AssertionError(change)
    head = commit(repository)

    assert not classify(
        repository, event="pull_request", base=base, head=head
    ).docs_only


def test_document_removal_and_rename_are_safe(repository: Path) -> None:
    (repository / "docs").mkdir()
    old = repository / "docs/old.md"
    _ = old.write_text("old\n")
    _ = (repository / "docs/remove.md").write_text("remove\n")
    base = commit(repository)
    _ = old.rename(repository / "docs/new.md")
    (repository / "docs/remove.md").unlink()
    head = commit(repository)

    assert classify(repository, event="pull_request", base=base, head=head).docs_only


@pytest.mark.parametrize(
    ("event", "baseline"),
    [
        ("workflow_dispatch", "valid"),
        ("push", "valid"),
        ("schedule", "valid"),
        ("pull_request", ""),
        ("pull_request", "0" * 40),
        ("pull_request", "f" * 40),
        ("pull_request", "--help"),
        ("pull_request", "abc\ndocs_only=true"),
    ],
)
def test_uncertain_context_never_skips_runtime(
    repository: Path, event: str, baseline: str
) -> None:
    base = git(repository, "rev-parse", "HEAD")
    _ = (repository / "README.md").write_text("# Update\n")
    head = commit(repository)

    assert not classify(
        repository,
        event=event,
        base=base if baseline == "valid" else baseline,
        head=head,
    ).docs_only


def test_wrong_checkout_and_empty_diff_never_skip_runtime(repository: Path) -> None:
    base = git(repository, "rev-parse", "HEAD")
    assert not classify(
        repository, event="pull_request", base=base, head=base
    ).docs_only
    _ = (repository / "README.md").write_text("# Update\n")
    _ = commit(repository)
    assert not classify(
        repository, event="pull_request", base=base, head=base
    ).docs_only


def test_invalid_packaged_readme_keeps_build_checks(repository: Path) -> None:
    base = git(repository, "rev-parse", "HEAD")
    _ = (repository / "README.md").write_bytes(b"\xff\xfe")
    head = commit(repository)

    assert not classify(
        repository, event="pull_request", base=base, head=head
    ).docs_only


def test_workflow_entry_point_emits_validated_outputs(repository: Path) -> None:
    base = git(repository, "rev-parse", "HEAD")
    _ = (repository / "README.md").write_text("# Update\n")
    head = commit(repository)
    output = repository / "actions-output"
    script = Path(__file__).parents[2] / "scripts/ci_scope.py"

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=repository,
        env={
            **os.environ,
            "CI_EVENT": "pull_request",
            "CI_BASE": base,
            "CI_HEAD": head,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert '"docs_only":true' in result.stdout
    assert output.read_text() == f"docs_only=true\nbase={base}\nhead={head}\n"
