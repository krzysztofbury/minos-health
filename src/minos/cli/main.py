import json
import os
from argparse import ArgumentParser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import cast

from minos.cli.recovery import (
    RecoveryCommand,
    RecoveryCommandArguments,
    create_recovery_command,
)
from minos.cli.watcher import (
    WatcherCommand,
    create_deployment_control_copy,
    create_watcher_command,
)
from minos.ingest.watchers.protocols import WatcherRuntimeError
from minos.ingest.watchers.rsk.publication import validate_deployed_rsk_projection


@dataclass(frozen=True, slots=True)
class WatcherCommandArguments:
    command: str
    database: Path | None
    staging_directory: Path | None
    archive_directory: Path | None
    process_lock: Path | None
    source_id: str | None
    source_database: Path | None = None
    destination_database: Path | None = None
    expected_current_revision: int | None = None
    expected_current_sha256: str | None = None
    target_revision: int | None = None
    target_sha256: str | None = None


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="minos",
        description="Explainable settlement intelligence for Polish hospital care.",
    )
    _ = parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version('minos-health')}",
    )
    commands = parser.add_subparsers(dest="command")
    watcher = commands.add_parser("watcher", help="Operate the source watcher.")
    watcher_commands = watcher.add_subparsers(dest="watcher_command", required=True)
    preflight = watcher_commands.add_parser(
        "preflight",
        help="Run watcher checks with scheduling paused.",
    )
    run = watcher_commands.add_parser("run", help="Run the blocking watcher scheduler.")
    run_once = watcher_commands.add_parser(
        "run-once",
        help="Run one configured source through the fenced manual path.",
    )
    health = watcher_commands.add_parser(
        "health",
        help="Check that the active watcher reached and retained readiness.",
    )
    bootstrap_rsk = watcher_commands.add_parser(
        "bootstrap-rsk",
        help="Create the canonical RSK source and durable schedules if absent.",
    )
    migrate_control = watcher_commands.add_parser(
        "migrate-control-database",
        help="Migrate the quiesced live watcher control database.",
    )
    rebuild_public_rsk = watcher_commands.add_parser(
        "rebuild-public-rsk",
        help="Explicitly rebuild the public RSK generation behind revision fences.",
    )
    _ = rebuild_public_rsk.add_argument(
        "--expected-current-revision",
        type=int,
        required=True,
    )
    _ = rebuild_public_rsk.add_argument(
        "--expected-current-sha256",
        required=True,
    )
    _ = rebuild_public_rsk.add_argument("--target-revision", type=int, required=True)
    _ = rebuild_public_rsk.add_argument("--target-sha256", required=True)
    validate_public_rsk = watcher_commands.add_parser(
        "validate-public-rsk",
        help="Validate an existing public RSK generation without collecting data.",
    )
    _ = validate_public_rsk.add_argument("--public-directory", type=Path)
    copy_control = watcher_commands.add_parser(
        "copy-control-database",
        help="Create bounded deployment evidence from a live control database.",
    )
    _ = copy_control.add_argument("--source-database", type=Path, required=True)
    _ = copy_control.add_argument("--destination-database", type=Path, required=True)
    _ = run_once.add_argument("source_id")
    for command in (
        preflight,
        run,
        run_once,
        health,
        bootstrap_rsk,
        migrate_control,
        rebuild_public_rsk,
    ):
        _add_watcher_paths(command)
    recovery = commands.add_parser("recovery", help="Operate trusted recovery data.")
    recovery_commands = recovery.add_subparsers(dest="recovery_command", required=True)
    inventory = recovery_commands.add_parser(
        "inventory", help="Operate the signed recovery inventory."
    )
    inventory_commands = inventory.add_subparsers(
        dest="inventory_command", required=True
    )
    verify = inventory_commands.add_parser(
        "verify", help="Verify the complete signed inventory chain."
    )
    verify.set_defaults(recovery_action="inventory-verify")
    trust = recovery_commands.add_parser(
        "trust", help="Copy and sign one verified recovery image."
    )
    trust.set_defaults(recovery_action="trust")
    select = recovery_commands.add_parser(
        "select", help="Select a trusted pre-compromise recovery image."
    )
    select.set_defaults(recovery_action="select")
    restore = recovery_commands.add_parser(
        "restore", help="Install one explicitly selected trusted image."
    )
    restore.set_defaults(recovery_action="restore")
    for command in (verify, trust, select, restore):
        _add_recovery_common_arguments(command)
    _add_trust_arguments(trust)
    _ = select.add_argument("--compromise-cutoff", required=True)
    _ = restore.add_argument("--sequence", required=True)
    _ = restore.add_argument("--compromise-cutoff", required=True)
    _ = restore.add_argument("--database", type=Path)
    _ = restore.add_argument("--process-lock", type=Path)
    _ = restore.add_argument(
        "--confirm-watcher-stopped",
        action="store_true",
        required=True,
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    watcher_factory: Callable[[WatcherCommandArguments], WatcherCommand] | None = None,
    recovery_factory: Callable[[RecoveryCommandArguments], RecoveryCommand]
    | None = None,
) -> None:
    parser = build_parser()
    raw_arguments = cast(dict[str, object], vars(parser.parse_args(argv)))
    top_level_command = raw_arguments.get("command")
    if top_level_command == "recovery":
        try:
            recovery_arguments = _read_recovery_arguments(raw_arguments)
            selected_recovery_factory = recovery_factory or _default_recovery_factory
            result = selected_recovery_factory(recovery_arguments).execute()
            print(result.as_json())
        except RuntimeError, OSError:
            parser.exit(1, "minos: recovery command failed\n")
        return
    if top_level_command != "watcher":
        parser.print_help()
        return
    try:
        if raw_arguments.get("watcher_command") == "validate-public-rsk":
            public_directory = raw_arguments.get("public_directory")
            if public_directory is None:
                configured_directory = os.environ.get("MINOS_WATCHER_PUBLIC_DIRECTORY")
                public_directory = (
                    Path(configured_directory) if configured_directory else None
                )
            if not isinstance(public_directory, Path):
                raise WatcherRuntimeError("public projection directory is required")
            projection_result = validate_deployed_rsk_projection(public_directory)
            print(json.dumps(projection_result, sort_keys=True, separators=(",", ":")))
            return
        watcher_arguments = _read_watcher_arguments(raw_arguments)
        if watcher_arguments.command == "copy-control-database":
            if (
                watcher_arguments.source_database is None
                or watcher_arguments.destination_database is None
            ):
                raise WatcherRuntimeError("control copy paths are required")
            _ = create_deployment_control_copy(
                watcher_arguments.source_database,
                watcher_arguments.destination_database,
            )
            return
        selected_watcher_factory = watcher_factory or _default_watcher_factory
        watcher_command = selected_watcher_factory(watcher_arguments)
        if watcher_arguments.command == "preflight":
            watcher_command.preflight()
        elif watcher_arguments.command == "run":
            watcher_command.run()
        elif watcher_arguments.command == "health":
            watcher_command.health()
        elif watcher_arguments.command == "bootstrap-rsk":
            watcher_command.bootstrap_rsk()
        elif watcher_arguments.command == "migrate-control-database":
            prior_version, schema_version = watcher_command.migrate_control_database()
            print(
                json.dumps(
                    {
                        "from_version": prior_version,
                        "migrated": prior_version != schema_version,
                        "schema_version": schema_version,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        elif watcher_arguments.command == "rebuild-public-rsk":
            if (
                watcher_arguments.expected_current_revision is None
                or watcher_arguments.expected_current_sha256 is None
                or watcher_arguments.target_revision is None
                or watcher_arguments.target_sha256 is None
            ):
                raise WatcherRuntimeError("public rebuild fences are required")
            _ = watcher_command.rebuild_public_rsk(
                expected_current_revision=(watcher_arguments.expected_current_revision),
                expected_current_sha256=watcher_arguments.expected_current_sha256,
                target_revision=watcher_arguments.target_revision,
                target_sha256=watcher_arguments.target_sha256,
            )
        elif (
            watcher_arguments.command == "run-once"
            and watcher_arguments.source_id is not None
        ):
            watcher_command.run_once(watcher_arguments.source_id)
        else:
            raise WatcherRuntimeError("watcher command is unsupported")
    except RuntimeError:
        parser.exit(1, "minos: watcher command failed\n")


def _add_watcher_paths(parser: ArgumentParser) -> None:
    _ = parser.add_argument("--database", type=Path)
    _ = parser.add_argument("--staging-directory", type=Path)
    _ = parser.add_argument("--archive-directory", type=Path)
    _ = parser.add_argument("--process-lock", type=Path)


def _add_recovery_common_arguments(parser: ArgumentParser) -> None:
    _ = parser.add_argument("--bucket")
    _ = parser.add_argument("--expected-bucket-owner")
    _ = parser.add_argument("--region")
    _ = parser.add_argument("--maximum-inventories")
    verification = parser.add_mutually_exclusive_group()
    _ = verification.add_argument("--public-key-file", type=Path)
    _ = verification.add_argument("--allowed-signers-file", type=Path)
    _ = parser.add_argument("--signer-identity")


def _add_trust_arguments(parser: ArgumentParser) -> None:
    _ = parser.add_argument("--private-signing-key", type=Path)
    _ = parser.add_argument("--staging-directory", type=Path)
    _ = parser.add_argument("--maximum-run-images")
    _ = parser.add_argument("--maximum-daily-images")
    _ = parser.add_argument("--maximum-weekly-images")
    _ = parser.add_argument("--maximum-monthly-images")


def _default_watcher_factory(arguments: WatcherCommandArguments) -> WatcherCommand:
    return create_watcher_command(arguments)


def _default_recovery_factory(arguments: RecoveryCommandArguments) -> RecoveryCommand:
    return create_recovery_command(arguments)


def _read_watcher_arguments(
    arguments: dict[str, object],
) -> WatcherCommandArguments:
    command = arguments.get("watcher_command")
    source_id = arguments.get("source_id")
    source_database = arguments.get("source_database")
    destination_database = arguments.get("destination_database")
    expected_current_revision = arguments.get("expected_current_revision")
    expected_current_sha256 = arguments.get("expected_current_sha256")
    target_revision = arguments.get("target_revision")
    target_sha256 = arguments.get("target_sha256")
    paths = tuple(
        arguments.get(name)
        for name in (
            "database",
            "staging_directory",
            "archive_directory",
            "process_lock",
        )
    )
    if not isinstance(command, str):
        raise WatcherRuntimeError("watcher command is invalid")
    if source_id is not None and not isinstance(source_id, str):
        raise WatcherRuntimeError("watcher source ID is invalid")
    if source_database is not None and not isinstance(source_database, Path):
        raise WatcherRuntimeError("watcher source database path is invalid")
    if destination_database is not None and not isinstance(destination_database, Path):
        raise WatcherRuntimeError("watcher destination database path is invalid")
    if (
        expected_current_revision is not None
        and type(expected_current_revision) is not int
    ):
        raise WatcherRuntimeError("public rebuild current revision is invalid")
    if target_revision is not None and type(target_revision) is not int:
        raise WatcherRuntimeError("public rebuild target revision is invalid")
    if expected_current_sha256 is not None and not isinstance(
        expected_current_sha256, str
    ):
        raise WatcherRuntimeError("public rebuild current digest is invalid")
    if target_sha256 is not None and not isinstance(target_sha256, str):
        raise WatcherRuntimeError("public rebuild target digest is invalid")
    if any(path is not None and not isinstance(path, Path) for path in paths):
        raise WatcherRuntimeError("watcher path argument is invalid")
    database, staging, archive, process_lock = cast(
        tuple[Path | None, Path | None, Path | None, Path | None],
        paths,
    )
    return WatcherCommandArguments(
        command,
        database,
        staging,
        archive,
        process_lock,
        source_id,
        source_database,
        destination_database,
        expected_current_revision,
        expected_current_sha256,
        target_revision,
        target_sha256,
    )


def _read_recovery_arguments(
    arguments: dict[str, object],
) -> RecoveryCommandArguments:
    action = arguments.get("recovery_action")
    if not isinstance(action, str):
        raise RuntimeError("recovery command is invalid")
    path_names = (
        "public_key_file",
        "allowed_signers_file",
        "private_signing_key",
        "staging_directory",
        "database",
        "process_lock",
    )
    values: dict[str, object] = {}
    for name in path_names:
        value = arguments.get(name)
        if value is not None and not isinstance(value, Path):
            raise RuntimeError("recovery path argument is invalid")
        values[name] = value
    string_names = (
        "bucket",
        "expected_bucket_owner",
        "region",
        "maximum_inventories",
        "signer_identity",
        "maximum_run_images",
        "maximum_daily_images",
        "maximum_weekly_images",
        "maximum_monthly_images",
        "compromise_cutoff",
        "sequence",
    )
    for name in string_names:
        value = arguments.get(name)
        if value is not None and not isinstance(value, str):
            raise RuntimeError("recovery argument is invalid")
        values[name] = value
    confirmation = arguments.get("confirm_watcher_stopped", False)
    if not isinstance(confirmation, bool):
        raise RuntimeError("recovery confirmation is invalid")
    return RecoveryCommandArguments(
        action=action,
        bucket=cast(str | None, values["bucket"]),
        expected_bucket_owner=cast(str | None, values["expected_bucket_owner"]),
        region=cast(str | None, values["region"]),
        maximum_inventories=cast(str | None, values["maximum_inventories"]),
        public_key_file=cast(Path | None, values["public_key_file"]),
        allowed_signers_file=cast(Path | None, values["allowed_signers_file"]),
        signer_identity=cast(str | None, values["signer_identity"]),
        private_signing_key=cast(Path | None, values["private_signing_key"]),
        staging_directory=cast(Path | None, values["staging_directory"]),
        maximum_run_images=cast(str | None, values["maximum_run_images"]),
        maximum_daily_images=cast(str | None, values["maximum_daily_images"]),
        maximum_weekly_images=cast(str | None, values["maximum_weekly_images"]),
        maximum_monthly_images=cast(str | None, values["maximum_monthly_images"]),
        compromise_cutoff=cast(str | None, values["compromise_cutoff"]),
        sequence=cast(str | None, values["sequence"]),
        database=cast(Path | None, values["database"]),
        process_lock=cast(Path | None, values["process_lock"]),
        confirm_watcher_stopped=confirmation,
    )
