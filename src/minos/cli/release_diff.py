"""Versioned JSON and escaped Markdown for selected-scope release comparisons."""

import json
import re
from pathlib import Path
from typing import ClassVar, cast

from pydantic import BaseModel, ConfigDict, Field

from minos.domain.reference import QueryLimitError
from minos.domain.release_diff import Change
from minos.store.artifact import ArtifactManifest, dataset_identity
from minos.store.query_budget import QueryBudget
from minos.store.release_diff import Comparison, compare_bundles

MAX_OUTPUT_BYTES = 16 * 1024 * 1024
PREVIEW_CHANGES = 20
PREVIEW_VALUE_CHARACTERS = 2048
PAIR_PATTERN = (
    r"^(?:terminology|catalog|conditions):[A-Za-z0-9_.-]{1,80}:[A-Za-z0-9_.-]{1,80}$"
)


class DiffArguments(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    before: Path
    after: Path
    pairs: list[str] = Field(min_length=1, max_length=16)
    full: bool = False
    output_json: bool = False

    def execute(self) -> int:
        from minos.domain.release_diff import DiffInputError

        if any(re.fullmatch(PAIR_PATTERN, pair) is None for pair in self.pairs):
            raise DiffInputError("invalid comparison pair")
        comparison = compare_bundles(self.before, self.after, tuple(self.pairs))
        envelope = diff_envelope(comparison, full=self.full)
        output = (
            _bounded_json(envelope, comparison.budget)
            if self.output_json
            else diff_markdown(envelope, budget=comparison.budget)
        )
        if len(output.encode()) > MAX_OUTPUT_BYTES:
            raise QueryLimitError("release diff output bound exceeded")
        comparison.budget.check()
        print(output)
        return 0


def _bounded_json(value: object, budget: QueryBudget) -> str:
    chunks: list[str] = []
    size_bytes = 0
    encoder = json.JSONEncoder(
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    for chunk in encoder.iterencode(value):
        budget.check()
        size_bytes += len(chunk.encode())
        if size_bytes > MAX_OUTPUT_BYTES:
            raise QueryLimitError("release diff output byte bound exceeded")
        chunks.append(chunk)
    return "".join(chunks)


def _identity(manifest: ArtifactManifest) -> dict[str, object]:
    return {
        "dataset_id": dataset_identity(manifest.dataset),
        "database_sha256": manifest.database_sha256,
        "schema_version": manifest.schema_version,
    }


def _detail(change: Change, full: bool) -> dict[str, object]:
    value: dict[str, object] = {
        "pair": change.pair,
        "category": change.category,
        "identity": change.identity,
        "action": change.action,
    }
    truncated = False
    for name, side in (("before", change.before), ("after", change.after)):
        if not full and side is not None and len(side) > PREVIEW_VALUE_CHARACTERS:
            value[name + "_excerpt"] = side[:PREVIEW_VALUE_CHARACTERS]
            truncated = True
        else:
            value[name] = json.loads(side) if side is not None else None
    value["values_truncated"] = truncated
    for name, context in (
        ("before_applicability", change.before_context),
        ("after_applicability", change.after_context),
    ):
        if context is not None:
            value[name] = json.loads(context)
    if (
        change.category == "base_values"
        and change.before is not None
        and change.after is not None
    ):
        value["exact_deltas"] = _deltas(change.before, change.after)
    return value


def _deltas(before: str, after: str) -> dict[str, object]:
    left = cast(list[dict[str, object]], json.loads(before))
    right = cast(list[dict[str, object]], json.loads(after))
    if (
        len(left) != 1
        or len(right) != 1
        or left[0]["product_code"] != right[0]["product_code"]
    ):
        return {"status": "row_population_not_unambiguously_aligned"}
    old = {
        measure: (unit, amount)
        for measure, unit, amount in cast(list[tuple[str, str, int]], left[0]["values"])
    }
    new = {
        measure: (unit, amount)
        for measure, unit, amount in cast(
            list[tuple[str, str, int]], right[0]["values"]
        )
    }
    return {
        "status": "comparable_single_rows",
        "measures": [
            {"measure": key, "unit": old[key][0], "delta": new[key][1] - old[key][1]}
            for key in sorted(old.keys() & new.keys())
            if old[key][0] == new[key][0] and old[key][1] != new[key][1]
        ],
    }


def diff_envelope(comparison: Comparison, *, full: bool) -> dict[str, object]:
    summary: dict[str, dict[str, int]] = {}
    for change in comparison.changes:
        comparison.budget.check()
        counts = summary.setdefault(
            change.category, {"added": 0, "removed": 0, "changed": 0}
        )
        counts[change.action] += 1
    selected = comparison.changes if full else comparison.changes[:PREVIEW_CHANGES]
    # Refuse oversized values before decoding into Python object populations.
    estimated = sum(
        sum(
            len(value.encode())
            for value in (
                change.before,
                change.after,
                change.before_context,
                change.after_context,
            )
            if value is not None
        )
        if full
        else 4 * PREVIEW_VALUE_CHARACTERS
        for change in selected
    )
    if estimated > MAX_OUTPUT_BYTES:
        raise QueryLimitError("release diff presentation byte bound exceeded")
    details = [_detail(change, full) for change in selected]
    return {
        "version": 1,
        "status": "ok",
        "before": _identity(comparison.before),
        "after": _identity(comparison.after),
        "coverage": {
            "selected_pairs": comparison.pairs,
            "complete_candidate_audit": False,
            "excluded": [
                "unpaired releases",
                "MODTAR mapping contents",
                "algorithm execution/rank contents",
                "search index/synonym contents",
            ],
            "notice": "Selected reference releases only; not legal clearance or an automatic activation decision.",
        },
        "summary": summary,
        "total_changes": len(comparison.changes),
        "changes": details,
        "omitted_changes": len(comparison.changes) - len(selected),
        "details_truncated": len(selected) < len(comparison.changes)
        or any(item["values_truncated"] for item in details),
        "limits": {
            "preview_changes": PREVIEW_CHANGES,
            "preview_value_characters": PREVIEW_VALUE_CHARACTERS,
            "output_bytes": MAX_OUTPUT_BYTES,
        },
        "points_kind": "catalog_base_without_modifiers",
    }


def _escape(value: str) -> str:
    clean = "".join(
        character if character.isprintable() else f"\\u{ord(character):04x}"
        for character in value
    )
    return re.sub(r"([\\`*_{}\[\]()#+.!|<>~-])", r"\\\1", clean)


def diff_markdown(
    answer: dict[str, object], *, budget: QueryBudget | None = None
) -> str:
    summary = cast(dict[str, dict[str, int]], answer["summary"])
    total = cast(int, answer["total_changes"])
    lines: list[str] = []
    size_bytes = 0

    def append(line: str) -> None:
        nonlocal size_bytes
        if budget is not None:
            budget.check()
        size_bytes += len(line.encode()) + 1
        if size_bytes > MAX_OUTPUT_BYTES:
            raise QueryLimitError("release diff output byte bound exceeded")
        lines.append(line)

    for line in [
        f"# Reference release diff: {total} logical changes",
        "",
        "Selected scope only. Catalog base points without modifiers; no final tariff or activation approval.",
        "",
        "| Category | Added | Removed | Changed |",
        "| --- | ---: | ---: | ---: |",
    ]:
        append(line)
    for category, counts in sorted(summary.items()):
        append(
            f"| {_escape(category)} | {counts['added']} | {counts['removed']} | {counts['changed']} |"
        )
    coverage = cast(dict[str, object], answer["coverage"])
    for side in ("before", "after"):
        append(
            "\nVerified "
            + side
            + ": "
            + _escape(json.dumps(answer[side], sort_keys=True))
        )
    for pair in cast(tuple[str, ...], coverage["selected_pairs"]):
        append("\nPair: " + _escape(pair))
    for change in cast(list[dict[str, object]], answer["changes"]):
        label = "/".join(cast(tuple[str, ...], change["identity"]))
        append(
            f"\n## {_escape(str(change['pair']))}: {_escape(str(change['category']))}: {_escape(label)} ({change['action']})"
        )
        for side in ("before", "after"):
            value = change.get(side + "_excerpt", change.get(side))
            text = (
                value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False, sort_keys=True)
            )
            append(f"\n{side}: {_escape(text)}")
        if "exact_deltas" in change:
            append(
                "\nExact integer deltas: "
                + _escape(json.dumps(change["exact_deltas"], sort_keys=True))
            )
            for side in ("before_applicability", "after_applicability"):
                append(
                    "\n"
                    + side
                    + ": "
                    + _escape(json.dumps(change.get(side), sort_keys=True))
                )
        if change["values_truncated"]:
            append("\nValue preview truncated; use --full for complete values.")
    if answer["details_truncated"]:
        append(
            f"\nPresentation truncated; {answer['omitted_changes']} changes omitted. Counts are complete. Use --full (resource limits still apply)."
        )
    return "\n".join(lines)
