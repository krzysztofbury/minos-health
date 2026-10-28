import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = (ROOT / "AGENTS.md").read_text()


def slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces hyphenated."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def test_agent_entrypoints_name_files_that_exist() -> None:
    # The operator checks are only useful if every named tool is real (#279).
    paths: set[str] = set(re.findall(r"`(scripts/[\w*]+\.py)`", AGENTS))
    modules: set[str] = set(
        re.findall(r"python -m (scripts\.\w+)", AGENTS.replace("\n  ", " "))
    )
    assert paths and modules
    missing = [p for p in paths if not list(ROOT.glob(p))] + [
        m for m in modules if not (ROOT / (m.replace(".", "/") + ".py")).is_file()
    ]
    assert missing == []


def test_agent_links_resolve_to_files_and_headings() -> None:
    links: list[tuple[str, str]] = re.findall(r"\]\(([^)#]+)(?:#([^)]+))?\)", AGENTS)
    assert links
    for target, anchor in links:
        document = ROOT / target
        assert document.is_file(), target
        if anchor:
            headings: list[str] = re.findall(
                r"^#+ (.+)$", document.read_text(), re.MULTILINE
            )
            assert anchor in {slug(h) for h in headings}, (target, anchor)
