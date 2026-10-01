"""Offline reference commands; no watcher settings, collectors or networking."""

import json
import sys
from argparse import ArgumentParser
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import ClassVar, Literal, NoReturn, Protocol, cast, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
