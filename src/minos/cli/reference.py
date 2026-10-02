"""Offline reference commands; no watcher settings, collectors or networking."""

import json
import sys
from argparse import ArgumentParser
from collections.abc import Sequence
from datetime import date, datetime
from importlib.metadata import version
from pathlib import Path
from typing import ClassVar, Literal, NoReturn, Protocol, cast, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.domain.jgp_ruleset import (
    AmbiguousRuleset,
    GroupNotOnDate,
    KnowledgeBeyondBundle,
    NoRulesetForDate,
)
from minos.domain.reference import ArtifactError, QueryLimitError, ReferenceError
from minos.domain.search import (
    SEARCH_VERSION,
    SearchInputError,
    SearchRequest,
    prepare_query,
)
from minos.store.artifact import dataset_identity, read_regular_file

DISCLAIMER = "Reference only. Code documented care completely and correctly; do not infer diagnoses or billability."


class SafeArgumentParser(ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        # argparse's default error interpolates raw arguments, including medical queries.
        self.exit(2, "minos: invalid arguments; see --help\n")


class _SearchArguments(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    system: str
    query: str = Field(min_length=1, max_length=256)
    data: Path
    release: str | None = None
    language: Literal["pl", "en"] = "pl"
    limit: int = Field(ge=1, le=100)
    index: Literal["auto", "portable"] = "auto"
    output_json: bool = False


class _BuildArguments(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    manifest: Path
    archive_root: Path
    output: Path
    previous_bundle: Path | None = None
    output_json: bool = False


class _GroupArguments(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    code: str = Field(pattern=r"^[A-Z]{1,4}[0-9]{2,3}[A-Z]{0,3}$")
    data: Path
    service_date: str | None = Field(
        default=None, pattern=r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$"
    )
    known_on: str | None = Field(
        default=None, pattern=r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$"
    )
    output_json: bool = False


class _CommandParsers(Protocol):
    def add_parser(self, name: str, *, help: str = "") -> ArgumentParser: ...


def add_reference_commands(commands: _CommandParsers) -> None:
    search = commands.add_parser(
        "search", help="Search a verified local reference bundle."
    )
    _ = search.add_argument("--system", required=True)
    _ = search.add_argument("--query", required=True)
    _ = search.add_argument(
        "--data",
        type=Path,
        required=True,
        help="Complete bundle directory, not a bare database.",
    )
    _ = search.add_argument("--release")
    _ = search.add_argument("--language", choices=("pl", "en"), default="pl")
    _ = search.add_argument("--limit", type=int, default=20)
    _ = search.add_argument("--index", choices=("auto", "portable"), default="auto")
    _ = search.add_argument("--json", action="store_true", dest="output_json")
    jgp = commands.add_parser("jgp", help="Look up JGP catalog base points by date.")
    group = jgp.add_subparsers(dest="jgp_command", required=True).add_parser("group")
    _ = group.add_argument("--code", required=True)
    _ = group.add_argument("--data", type=Path, required=True)
    _ = group.add_argument(
        "--service-date", help="YYYY-MM-DD; defaults to today in Europe/Warsaw."
    )
    _ = group.add_argument(
        "--known-on", help="YYYY-MM-DD publication knowledge; defaults to today."
    )
    _ = group.add_argument("--json", action="store_true", dest="output_json")
    data = commands.add_parser("data", help="Build a pinned offline reference bundle.")
    build = data.add_subparsers(dest="data_command", required=True).add_parser("build")
    _ = build.add_argument(
        "--manifest", type=Path, required=True, help="Reviewed JSON source manifest."
    )
    _ = build.add_argument("--archive-root", type=Path, required=True)
    _ = build.add_argument("--output", type=Path, required=True)
    _ = build.add_argument("--previous-bundle", type=Path)
    _ = build.add_argument("--json", action="store_true", dest="output_json")


def reference_main(argv: Sequence[str] | None = None) -> None:
    """Standalone distribution entry point intentionally excludes operational commands."""
    parser = SafeArgumentParser(
        prog="minos", description="Offline terminology reference lookup."
    )
    _ = parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('minos-health')}"
    )
    add_reference_commands(parser.add_subparsers(dest="command", required=True))
    arguments = cast(dict[str, object], vars(parser.parse_args(argv)))
    code = execute_reference(arguments)
    if code:
        raise SystemExit(code)


def execute_reference(arguments: dict[str, object]) -> int:
    output_json = arguments.get("output_json") is True
    try:
        if arguments.get("command") == "jgp":
            selected = {
                name: arguments[name]
                for name in _GroupArguments.model_fields
                if name in arguments
            }
            return _group(_GroupArguments.model_validate(selected))
        if arguments.get("command") == "search":
            selected = {
                name: arguments[name]
                for name in _SearchArguments.model_fields
                if name in arguments
            }
            return _search(_SearchArguments.model_validate(selected))
        selected = {
            name: arguments[name]
            for name in _BuildArguments.model_fields
            if name in arguments
        }
        return _build(_BuildArguments.model_validate(selected))
    except GroupNotOnDate:
        return _error(
            1,
            "group_not_on_date",
            "The group has no catalog row on that service date.",
            output_json,
        )
    except KnowledgeBeyondBundle:
        return _error(
            7,
            "known_on_beyond_bundle",
            "The knowledge date is after this bundle's reviewed catalog knowledge.",
            output_json,
        )
    except _NoCatalogInBundle:
        return _error(
            8, "no_jgp_catalog", "This bundle contains no JGP catalog.", output_json
        )
    except _TimeZoneUnavailable:
        return _error(
            2,
            "timezone_unavailable",
            "Time zone data is unavailable; pass both --service-date and --known-on.",
            output_json,
        )
    except NoRulesetForDate:
        return _error(
            5,
            "no_ruleset_for_date",
            "No reviewed JGP ruleset covers that service date; no other release is substituted.",
            output_json,
        )
    except AmbiguousRuleset:
        return _error(
            6,
            "ambiguous_ruleset",
            "Reviewed JGP rulesets conflict for that date.",
            output_json,
        )
    except ValidationError, SearchInputError:
        return _error(
            2, "invalid_request", "Invalid or ambiguous lookup request.", output_json
        )
    except QueryLimitError:
        return _error(
            4,
            "limit_exceeded",
            "Lookup exceeded a resource limit; narrow the request.",
            output_json,
        )
    except ReferenceError, OSError:
        return _error(
            3,
            "data_error",
            "Reference data could not be verified, built or read.",
            output_json,
        )


def _build(arguments: _BuildArguments) -> int:
    from minos.ingest.reference_artifact import build_reference_artifact
    from minos.ingest.reference_manifest import DatasetManifest

    try:
        manifest = DatasetManifest.model_validate_json(
            read_regular_file(arguments.manifest)
        )
    except ValidationError as error:
        raise ArtifactError("invalid pinned source manifest") from error
    result = build_reference_artifact(
        manifest,
        arguments.archive_root,
        arguments.output,
        previous_bundle=arguments.previous_bundle,
    )
    response: dict[str, object] = {
        "version": 1,
        "status": "built",
        "dataset_id": dataset_identity(manifest),
        "database_sha256": result.database_sha256,
        "row_counts": result.row_counts,
        "disclaimer": DISCLAIMER,
    }
    print(
        json.dumps(response, ensure_ascii=False, sort_keys=True)
        if arguments.output_json
        else f"Built {dataset_identity(manifest)}; SHA-256 {result.database_sha256}\n{DISCLAIMER}"
    )
    return 0


class _TimeZoneUnavailable(Exception):
    pass


class _NoCatalogInBundle(Exception):
    pass


def _today_in_poland() -> date:
    # Service dates are Polish calendar dates; UTC would be wrong around midnight.
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return datetime.now(ZoneInfo("Europe/Warsaw")).date()
    except ZoneInfoNotFoundError:
        raise _TimeZoneUnavailable from None


def _given_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SearchInputError("invalid calendar date") from None


def _group(arguments: _GroupArguments) -> int:
    from minos.store.jgp_ruleset import (
        catalog_group,
        catalog_knowledge,
        resolve_ruleset,
    )
    from minos.store.reference import open_reference_bundle

    service_date = _given_date(arguments.service_date)
    known_on = _given_date(arguments.known_on)
    # One clock reading, so both defaults agree even across midnight.
    today = _today_in_poland() if service_date is None or known_on is None else None
    selection = {
        "service_date": "explicit" if service_date else "default_today_europe_warsaw",
        "known_on": "explicit" if known_on else "default_today_europe_warsaw",
    }
    with open_reference_bundle(arguments.data) as (connection, manifest):
        if manifest.schema_version < 4:
            raise _NoCatalogInBundle
        horizon = catalog_knowledge(connection)
        if known_on is None:
            known_on = min(cast(date, today), horizon)
            if known_on == horizon and horizon != today:
                selection["known_on"] = "default_capped_to_bundle_knowledge"
        answer = catalog_group(
            connection,
            resolve_ruleset(connection, service_date or cast(date, today), known_on),
            arguments.code,
        )
    answer = {
        "version": 1,
        "status": "ok",
        "date_selection": selection,
        **answer,
        "disclaimer": DISCLAIMER,
    }
    if arguments.output_json:
        print(
            json.dumps(
                answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
    else:
        print(_group_text(answer))
    return 0


def _group_text(answer: dict[str, object]) -> str:
    ruleset = cast(dict[str, object], answer["ruleset"])
    group = cast(dict[str, object], answer["group"])
    lines = [
        f"{group['code']} {group['name']}",
        f"Service date {ruleset['service_date']}, known on {ruleset['known_on']}",
        f"Governing catalog: {ruleset['act_number']} (BAW {ruleset['baw_document_id']}),"
        + f" release {ruleset['release_id']}",
        f"{answer['points_label']}:",
    ]
    for value in cast(list[dict[str, object]], answer["base_values"]):
        amount = cast(int, value["amount"])
        shown = (
            f"{amount // 100}.{amount % 100:02d}"
            if value["unit"] == "points_hundredths"
            else f"{amount} days"
        )
        lines.append(f"  {value['measure']}: {shown}")
    lines.append("Modifiers: unbound (final points cannot be calculated).")
    lines.append(cast(str, answer["disclaimer"]))
    return "\n".join(lines)


def _search(arguments: _SearchArguments) -> int:
    from minos.cli.reference_output import search_envelope, search_table
    from minos.store.reference import open_reference_bundle
    from minos.store.search import search_reference

    request = SearchRequest(
        arguments.system,
        prepare_query(arguments.query, language=arguments.language),
        arguments.release,
        arguments.language,
        arguments.limit,
        arguments.index,
    )
    with open_reference_bundle(arguments.data) as (connection, manifest):
        result = search_reference(connection, request)
    envelope = search_envelope(
        result, manifest, disclaimer=DISCLAIMER, search_version=SEARCH_VERSION
    )
    print(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if arguments.output_json
        else search_table(result, manifest, disclaimer=DISCLAIMER)
    )
    return 0 if result.hits else 1


def _error(code: int, category: str, message: str, output_json: bool) -> int:
    text = (
        json.dumps(
            {
                "version": 1,
                "status": "error",
                "error": {"code": category, "message": message},
            },
            sort_keys=True,
        )
        if output_json
        else "minos: " + message
    )
    print(text, file=sys.stderr)
    return code
