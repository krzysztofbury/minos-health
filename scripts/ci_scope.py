"""Allow a documentation-only CI path only after inspecting the complete Git diff."""

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_DIFF_BYTES = 1024 * 1024
MAX_CHANGED_PATHS = 10_000
ROOT_DOCUMENTS = frozenset({"README.md", "CHANGELOG.md", "TODO.md"})


class Context(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    event: Literal["pull_request", "push", "workflow_dispatch"]
    base: str = Field(pattern=r"^(?:[0-9a-f]{40})?$")
    head: str = Field(pattern=r"^[0-9a-f]{40}$")


class Scope(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    docs_only: bool = False
    reason: str
    base: str = ""
    head: str = ""


def git(repository: Path, *arguments: str) -> bytes:
    # Spool before reading so a pathological tree cannot create unbounded output
    # in the classifier process. Git itself is bounded by the subprocess timeout.
    with tempfile.TemporaryFile() as output:
        _ = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            stdout=output,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=30,
        )
        _ = output.seek(0)
        result = output.read(MAX_DIFF_BYTES + 1)
    if len(result) > MAX_DIFF_BYTES:
        raise ValueError("diff exceeds classification limit")
    return result


def documentation_diff(raw: bytes) -> bool:
    tokens = raw.split(b"\0")
    if tokens[-1] != b"" or not 2 <= len(tokens) - 1 <= 2 * MAX_CHANGED_PATHS:
        return False
    _ = tokens.pop()
    if len(tokens) % 2:
        return False
    for index in range(0, len(tokens), 2):
        header = re.fullmatch(
            rb":(100644|000000) (100644|000000) [0-9a-f]{40} [0-9a-f]{40} ([AMD])",
            tokens[index],
        )
        if header is None:
            return False
        try:
            name = tokens[index + 1].decode("utf-8")
        except UnicodeDecodeError:
            return False
        parts = name.split("/")
        if (
            any(part in {"", ".", "..", "AGENTS.md", "CLAUDE.md"} for part in parts)
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
            or "\\" in name
        ):
            return False
        if name not in ROOT_DOCUMENTS and not (
            name.startswith("docs/") and name.endswith(".md")
        ):
            return False
        # README is package metadata. Content-only changes need document checks;
        # deleting it can break packaging and must exercise the build boundary.
        if name == "README.md" and header[3] != b"M":
            return False
    return True


def classify(repository: Path, *, event: str, base: str, head: str) -> Scope:
    try:
        context = Context.model_validate({"event": event, "base": base, "head": head})
    except ValidationError:
        return Scope(reason="invalid event context")
    if context.event == "workflow_dispatch" or not context.base.strip("0"):
        return Scope(reason="manual run or missing baseline")
    try:
        if git(repository, "rev-parse", "--verify", "HEAD").strip().decode() != head:
            return Scope(reason="checkout differs from event revision")
        _ = git(repository, "cat-file", "-e", f"{base}^{{commit}}")
        raw = git(
            repository,
            "diff",
            "--raw",
            "--no-abbrev",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            "-z",
            base,
            head,
            "--",
        )
        # Hatch reads this metadata as UTF-8 even when no runtime file changed.
        _ = git(repository, "show", f"{head}:README.md").decode("utf-8")
    except OSError, subprocess.SubprocessError, ValueError:
        return Scope(reason="Git evidence unavailable or over limit")
    if not documentation_diff(raw):
        return Scope(reason="runtime, unknown, empty or nonregular-file change")
    return Scope(
        docs_only=True, reason="only reviewed documentation paths", base=base, head=head
    )


def main() -> None:
    scope = classify(
        Path.cwd(),
        event=os.environ.get("CI_EVENT", ""),
        base=os.environ.get("CI_BASE", ""),
        head=os.environ.get("CI_HEAD", ""),
    )
    print(scope.model_dump_json())
    # Values are fixed booleans or validated object IDs, never filenames or refs.
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        _ = output.write(
            f"docs_only={str(scope.docs_only).lower()}\nbase={scope.base}\nhead={scope.head}\n"
        )


if __name__ == "__main__":
    main()
