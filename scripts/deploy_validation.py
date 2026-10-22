"""Guarded validation deployment updates (#109).

Run as `uv run python -m scripts.deploy_validation <command>` from the repository
root; see docs/validation/validation-deploy.md. Local steps resolve exact identities
from reviewed `main`, the publish run artifact and a verified bundle; host steps
run `scripts/validation_host.py` over the pinned SSH configuration. No step
accepts a tag, a moved revision or a changed live Compose file.
"""

import argparse
import base64
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from http.client import HTTPResponse
from pathlib import Path
from typing import Annotated, ClassVar, Literal, cast, final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.store.artifact import dataset_identity
from minos.store.reference import open_reference_bundle, read_artifact_manifest
from scripts.render_validation_compose import Deployment

ROOT = Path(__file__).resolve().parents[1]
HOST_PROGRAM = ROOT / "scripts" / "validation_host.py"
HOST_VARS = ROOT / "infra/ansible/inventory/host_vars/minos-production.yml"
KNOWN_HOSTS = ROOT / "infra/ansible/known_hosts"
SSH_KEY = Path.home() / ".ssh" / "minos_mikrus_ed25519"
WORK_ROOT = ROOT / "test-output" / "validation-deploy"
PUBLIC_BASE = "https://minoshealth.pl"
PUBLISH_WORKFLOW = "Verify and publish reference validation"
RELEASE_ARTIFACT = "validation-release"
BUNDLE_FILES = ("manifest.json", "minos.db", "review.txt")
SSH_TIMEOUT_S = 1500
# Mirrors infra/ansible/ansible.cfg: pinned host key, key-only, no fallbacks.
SSH_OPTIONS = (
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=yes",
    "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
    "-o", "GlobalKnownHostsFile=/dev/null",
    "-o", "KnownHostsCommand=none",
    "-o", "VerifyHostKeyDNS=no",
    "-o", "UpdateHostKeys=no",
    "-o", "CheckHostIP=no",
    "-o", "HostKeyAlgorithms=ssh-ed25519",
    "-o", "IdentitiesOnly=yes",
    "-o", "PreferredAuthentications=publickey",
    "-o", "PasswordAuthentication=no",
    "-o", "KbdInteractiveAuthentication=no",
    "-i", str(SSH_KEY),
)  # fmt: skip


class DeployError(Exception):
    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details: dict[str, object] = details or {}


class HostAnswer(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    ok: bool
    result: dict[str, object] | None = None
    error: str | None = Field(default=None, max_length=2048)
    details: dict[str, object] = Field(default_factory=dict)


class IngressReady(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)
    status: Literal["ready"]


type Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
type Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
type ApiImage = Annotated[
    str,
    Field(
        pattern=r"^ghcr\.io/krzysztofbury/minos-health-reference-api@sha256:[a-f0-9]{64}$"
    ),
]
type EdgeImage = Annotated[
    str,
    Field(
        pattern=r"^ghcr\.io/krzysztofbury/minos-health-validation-edge@sha256:[a-f0-9]{64}$"
    ),
]


class Release(BaseModel):
    """`validation-release.json` written by the manual publish job."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    source_revision: Revision
    run_id: int = Field(ge=1)
    run_attempt: int = Field(ge=1, le=100)
    api_image: ApiImage
    edge_image: EdgeImage


class Previous(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    api_image: ApiImage
    edge_image: EdgeImage
    database_sha256: Digest
    source_revision: Revision | None


class Plan(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    deployment: Deployment
    previous: Previous
    compose_sha256: Digest
    run_id: int = Field(ge=1)
    dataset_id: Digest
    bundle: str
    rank_sample: tuple[str, int] | None
    # (service date, group code, governing release) for catalog bundles.
    jgp_sample: tuple[str, str, str] | None = None
    jgp_modifier_binding: Literal["unbound", "reviewed"] = "unbound"
    conditions_sample: tuple[str, str, str, str] | None = None
    planned_at: str


def run(
    arguments: list[str],
    *,
    timeout_s: int = 120,
    stdin: bytes | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    try:
        completed = subprocess.run(
            arguments,
            input=stdin,
            env=environment,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise DeployError(f"{arguments[0]} timed out") from None
    if completed.returncode != 0:
        detail = " ".join(completed.stderr.decode(errors="replace").split())[-400:]
        raise DeployError(f"{' '.join(arguments[:3])} failed: {detail}")
    return completed.stdout.decode()


@final
class Host:
    def __init__(self) -> None:
        values = cast(dict[str, object], yaml.safe_load(HOST_VARS.read_text()))
        self.address = str(values["ansible_host"])
        self.port = str(values["ansible_port"])
        if values.get("ansible_user") != "root" or not self.port.isdigit():
            raise DeployError("materialized host variables are invalid")

    def call(
        self, command: str, params: dict[str, object], *, program: bytes | None = None
    ) -> dict[str, object]:
        encoded = base64.urlsafe_b64encode(json.dumps(params).encode()).decode()
        try:
            completed = subprocess.run(
                [
                    "ssh",
                    *SSH_OPTIONS,
                    "-p",
                    self.port,
                    f"root@{self.address}",
                    "python3",
                    "-",
                    command,
                    encoded,
                ],
                timeout=SSH_TIMEOUT_S,
                input=HOST_PROGRAM.read_bytes() if program is None else program,
                capture_output=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise DeployError(
                "host command timed out; inspect state before retry"
            ) from None
        # Host refusals intentionally exit nonzero and put structured errors on
        # stdout. SSH stderr is neither that protocol nor safe diagnostic text.
        return parse_host_answer(
            completed.stdout.decode(errors="replace"), command, completed.returncode
        )

    def upload(self, local: Path, remote: str) -> None:
        _ = run(
            ["scp", "-q", *SSH_OPTIONS, "-P", self.port, str(local),
             f"root@{self.address}:{remote}"],
            timeout_s=SSH_TIMEOUT_S,
        )  # fmt: skip


def parse_host_answer(
    output: str, command: str, returncode: int = 0
) -> dict[str, object]:
    if len(output.encode()) > 64 * 1024:
        raise DeployError("host response exceeds bound")
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        raise DeployError(
            f"host {command} returned nothing (exit {returncode}); inspect transport and host state"
        )
    try:
        answer = HostAnswer.model_validate_json(lines[-1])
    except ValidationError:
        raise DeployError("host response is invalid") from None
    if not answer.ok:
        raise DeployError(f"host {command} refused: {answer.error}", answer.details)
    if returncode != 0 or answer.result is None:
        raise DeployError(
            f"host {command} did not complete successfully (exit {returncode})"
        )
    return answer.result


def bundle_archive(bundle: Path, output: Path) -> str:
    """Byte-reproducible archive: fixed names, owners, modes and timestamps."""
    with (
        output.open("xb") as target,
        gzip.GzipFile(filename="", fileobj=target, mode="wb", mtime=0) as compressed,
        tarfile.open(
            fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT
        ) as archive,
    ):
        for name in BUNDLE_FILES:
            source = bundle / name
            info = tarfile.TarInfo(name)
            info.size = source.stat().st_size
            info.mode, info.uid, info.gid, info.mtime = 0o444, 0, 0, 0
            with source.open("rb") as content:
                archive.addfile(info, content)
    return file_sha256(output)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_bundle(bundle: Path) -> tuple[str, str, tuple[str, int] | None]:
    manifest = read_artifact_manifest(bundle)
    if file_sha256(bundle / "minos.db") != manifest.database_sha256:
        raise DeployError("bundle database does not match its manifest")
    sample: tuple[str, int] | None = None
    if manifest.dataset.komunikaty:
        with open_reference_bundle(bundle) as (connection, _):
            row = cast(
                tuple[str, int] | None,
                connection.execute(
                    """SELECT rank.code, rank.rank FROM procedure_rank AS rank
                    JOIN algorithm_release AS release ON release.id = rank.release_id
                    JOIN concept ON concept.source_release_id = release.icd9_release_id
                      AND concept.lookup_code = rank.code
                    WHERE rank.grouper_type = 'SZP' ORDER BY rank.code LIMIT 1"""
                ).fetchone(),
            )
        if row is None:
            raise DeployError("rank bundle has no canonical SZP rank to verify")
        sample = row
    return manifest.database_sha256, dataset_identity(manifest.dataset), sample


def jgp_sample(bundle: Path) -> tuple[str, str, str] | None:
    """One group the live API must answer from the bundle's own resolution."""
    from minos.store.jgp_ruleset import catalog_knowledge, resolve_ruleset

    manifest = read_artifact_manifest(bundle)
    if manifest.schema_version < 4:
        return None
    with open_reference_bundle(bundle) as (connection, _):
        horizon = catalog_knowledge(connection)
        ruleset = resolve_ruleset(connection, horizon, horizon)
        row = cast(
            tuple[str] | None,
            connection.execute(
                """SELECT code FROM group_definition
                WHERE release_id = ? AND service_from <= ?
                  AND (service_to IS NULL OR ? <= service_to)
                ORDER BY catalog, source_row LIMIT 1""",
                (ruleset["release_id"], horizon.isoformat(), horizon.isoformat()),
            ).fetchone(),
        )
    if row is None:
        raise DeployError("catalog bundle has no group to verify")
    return horizon.isoformat(), row[0], cast(str, ruleset["release_id"])


def resolve_release(revision: str, run_id: int) -> Release:
    remote = run(["git", "ls-remote", "origin", "refs/heads/main"]).split()
    if not remote or remote[0] != revision:
        raise DeployError("revision is not the current remote main")
    view = cast(
        dict[str, object],
        json.loads(
            run(
                ["gh", "run", "view", str(run_id), "--json",
                 "headSha,conclusion,workflowName,event,headBranch"]
            )
        ),
    )  # fmt: skip
    expected = {
        "headSha": revision,
        "conclusion": "success",
        "workflowName": PUBLISH_WORKFLOW,
        "event": "workflow_dispatch",
        "headBranch": "main",
    }
    if view != expected:
        raise DeployError(f"publish run does not match the revision: {view}")
    with tempfile.TemporaryDirectory(prefix="minos-release-") as directory:
        _ = run(
            ["gh", "run", "download", str(run_id), "--name", RELEASE_ARTIFACT,
             "--dir", directory]
        )  # fmt: skip
        release = Release.model_validate_json(
            (Path(directory) / "validation-release.json").read_bytes()
        )
    if release.source_revision != revision or release.run_id != run_id:
        raise DeployError("release artifact belongs to another revision or run")
    return release


def require_live_sources(bundle: Path, live: list[str] | None) -> None:
    """The live bundle is authoritative: the processor may have activated
    releases the repository manifest does not list yet (#141 phase C)."""
    if live is None:
        raise DeployError("live bundle manifest is unreadable on the host")
    manifest = read_artifact_manifest(bundle)
    missing = sorted(
        set(live) - {source.release_id for source in manifest.dataset.sources}
    )
    if missing:
        raise DeployError(
            "bundle drops live releases; add them to the manifest: "
            + ", ".join(missing)
        )


def plan_path(revision: str) -> Path:
    return WORK_ROOT / revision / "plan.json"


def plan(arguments: Arguments) -> Plan:
    release = resolve_release(arguments.revision, arguments.run_id)
    bundle = arguments.bundle.resolve()
    database, dataset_id, sample = verify_bundle(bundle)
    state = Host().call("inspect", {})
    live = cast(dict[str, str], state["live"])
    if live["api_image"] == release.api_image and live["database_sha256"] == database:
        raise DeployError("this release and bundle are already live")
    require_live_sources(bundle, cast(list[str] | None, state.get("live_sources")))
    group_sample = jgp_sample(bundle)
    modifier_state: Literal["unbound", "reviewed"] = "unbound"
    if group_sample is not None:
        from datetime import date

        from minos.store.jgp_ruleset import resolve_ruleset

        with open_reference_bundle(bundle) as (connection, _):
            ruleset = resolve_ruleset(
                connection, date.fromisoformat(group_sample[0]), None
            )
            modifier_state = cast(
                Literal["unbound", "reviewed"],
                cast(dict[str, object], ruleset["modifiers"])["binding"],
            )
    result = Plan(
        deployment=Deployment(
            api_image=release.api_image,
            edge_image=release.edge_image,
            source_revision=release.source_revision,
            database_sha256=database,
        ),
        previous=Previous(
            api_image=live["api_image"],
            edge_image=live["edge_image"],
            database_sha256=live["database_sha256"],
            source_revision=cast(str | None, state["live_revision"]),
        ),
        compose_sha256=cast(str, state["compose_sha256"]),
        run_id=release.run_id,
        dataset_id=dataset_id,
        bundle=str(bundle),
        rank_sample=sample,
        jgp_sample=group_sample,
        jgp_modifier_binding=modifier_state,
        conditions_sample=conditions_sample(bundle),
        planned_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    path = plan_path(release.source_revision)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(result.model_dump_json(indent=2) + "\n")
    report("planned", {"plan": str(path), "previous": result.previous.model_dump(),
                       "available_mib": state["available_mib"]})  # fmt: skip
    return result


def load_plan(revision: str) -> Plan:
    return Plan.model_validate_json(plan_path(revision).read_bytes())


def host_params(value: Plan) -> dict[str, object]:
    return {
        "deployment": value.deployment.model_dump(),
        "previous": value.previous.model_dump(exclude={"source_revision"}),
        "compose_sha256": value.compose_sha256,
    }


def stage(value: Plan) -> None:
    host = Host()
    prepared = host.call("prepare", host_params(value))
    with tempfile.TemporaryDirectory(prefix="minos-bundle-") as directory:
        archive = Path(directory) / "bundle.tar.gz"
        archive_sha256 = bundle_archive(Path(value.bundle), archive)
        if prepared["bundle_installed"] is not True:
            host.upload(archive, cast(str, prepared["archive"]))
    staged = host.call(
        "stage", {**host_params(value), "archive_sha256": archive_sha256}
    )
    report("staged", staged)


def activate(value: Plan) -> dict[str, object]:
    result = Host().call("activate", host_params(value))
    report("activated", result)
    return result


def get(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "minos-validation-deploy"}
    )
    try:
        with cast(
            HTTPResponse, urllib.request.urlopen(request, timeout=10)
        ) as response:
            return response.status, response.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, b""


def verify_ingress(base: str) -> dict[str, object]:
    """Paced public probes; local container health alone is not public ingress."""
    try:
        status, body = get(base + "/readyz")
        ready = IngressReady.model_validate_json(body) if status == 200 else None
        home, _ = get(base + "/")
    except OSError, ValueError:
        raise DeployError(
            "restored services but public ingress verification failed",
            {"public_ingress": "failed"},
        ) from None
    if ready is None or home != 200:
        raise DeployError(
            "restored services but public ingress verification failed",
            {"public_ingress": "failed", "readyz_status": status, "home_status": home},
        )
    return {"public_ingress": "healthy", "readyz_status": status, "home_status": home}


def verify_home(base: str) -> str:
    """Refuse a homepage the proxy rewrote; email obfuscation injects /cdn-cgi/."""
    status, body = get(base + "/")
    if status != 200:
        raise DeployError(f"public homepage is not served: {status}")
    if b"/cdn-cgi/" in body:
        raise DeployError("public homepage was rewritten by the edge proxy")
    return "served without edge rewriting"


def verify(value: Plan, chromium: Path | None, base: str) -> dict[str, object]:
    status, body = get(base + "/readyz")
    ready = cast(dict[str, object], json.loads(body)) if status == 200 else {}
    if ready.get("status") != "ready" or ready.get("dataset_id") != value.dataset_id:
        raise DeployError(
            f"public readiness does not serve the planned dataset: {status}"
        )
    checks: dict[str, object] = {"readyz": "ready", "dataset_id": value.dataset_id}
    checks["home"] = verify_home(base)
    if value.rank_sample is not None:
        code, rank = value.rank_sample
        status, body = get(f"{base}/slowniki/icd-9/{code}")
        if status != 200 or f"Ranga {rank}".encode() not in body:
            raise DeployError(f"public rank page for {code} is wrong: {status}")
        checks["rank_page"] = f"{code} shows Ranga {rank}"
        if chromium is not None:
            output = WORK_ROOT / value.deployment.source_revision / "browser"
            output.mkdir(parents=True, exist_ok=True)
            # Chromium's socket path must stay under the Unix path limit.
            with tempfile.TemporaryDirectory(prefix="cb-", dir="/tmp") as browser_temp:
                _ = run(
                    ["node", str(ROOT / "tests/browser/procedure-rank-browser.mjs"),
                     str(chromium), base + "/", str(output), code, str(rank)],
                    timeout_s=120,
                    environment={**os.environ, "TMPDIR": browser_temp},
                )  # fmt: skip
            checks["browser"] = (
                "desktop and mobile rank acceptance passed; review screenshots"
            )
    if value.jgp_sample is not None:
        checks["jgp_group"] = verify_jgp(
            base, value.jgp_sample, value.jgp_modifier_binding
        )
    if value.conditions_sample is not None:
        checks["conditions_group"] = verify_conditions(base, value.conditions_sample)
    if value.jgp_sample is not None and chromium is not None:
        output = WORK_ROOT / value.deployment.source_revision / "browser"
        output.mkdir(parents=True, exist_ok=True)
        # Chromium's socket path must stay under the Unix path limit.
        with tempfile.TemporaryDirectory(prefix="cb-", dir="/tmp") as browser_temp:
            _ = run(
                [
                    "node",
                    str(ROOT / "tests/browser/jgp-search-browser.mjs"),
                    str(chromium),
                    base + "/",
                    str(output),
                    "conditions" if value.conditions_sample is not None else "catalog",
                ],
                timeout_s=180,
                environment={**os.environ, "TMPDIR": browser_temp},
            )
        checks["jgp_search_browser"] = (
            "desktop/mobile search and dated links passed; review screenshots"
        )
    report("verified", checks)
    return checks


def verify_jgp(
    base: str, sample: tuple[str, str, str], expected_binding: str = "unbound"
) -> str:
    service_date, code, release_id = sample
    status, body = get(f"{base}/api/v1/jgp/{service_date}/groups/{code}")
    answer = cast(dict[str, object], json.loads(body)) if status == 200 else {}
    ruleset = cast(dict[str, object], answer.get("ruleset", {}))
    modifiers = cast(dict[str, object], ruleset.get("modifiers", {}))
    if (
        ruleset.get("release_id") != release_id
        or answer.get("points_kind") != "catalog_base_points_without_modifiers"
        or modifiers.get("binding") != expected_binding
    ):
        raise DeployError(f"public JGP group {code} is wrong: {status}")
    return f"{code} on {service_date} from {release_id}, base points, modifiers {expected_binding}"


def conditions_sample(bundle: Path) -> tuple[str, str, str, str] | None:
    from minos.domain.jgp_ruleset import GroupNotOnDate
    from minos.store.jgp_condition_reference import reference_group
    from minos.store.jgp_ruleset import catalog_knowledge, resolve_ruleset

    manifest = read_artifact_manifest(bundle)
    if not manifest.dataset.characteristics:
        return None
    with open_reference_bundle(bundle) as (connection, _):
        day = catalog_knowledge(connection)
        ruleset = resolve_ruleset(connection, day, None)
        # The sample must actually be covered, not just have a catalog row.
        codes = cast(
            list[tuple[str]],
            connection.execute(
                "SELECT DISTINCT code FROM characteristics_group ORDER BY code LIMIT 64"
            ).fetchall(),
        )
        for (code,) in codes:
            try:
                answer = reference_group(connection, ruleset, code, available=True)
            except GroupNotOnDate:
                continue
            conditions = cast(dict[str, object], answer["conditions"])
            status = conditions.get("status")
            if status in ("fully_interpreted", "not_yet_interpreted"):
                source = cast(dict[str, object], conditions["source"])
                return (
                    day.isoformat(),
                    code,
                    cast(str, source["release_id"]),
                    status,
                )
    raise DeployError("conditions bundle has no bounded covered verification sample")


def verify_conditions(base: str, sample: tuple[str, str, str, str]) -> str:
    day, code, release_id, expected_status = sample
    status, body = get(f"{base}/api/v1/jgp/{day}/groups/{code}")
    answer = cast(dict[str, object], json.loads(body)) if status == 200 else {}
    conditions = cast(dict[str, object], answer.get("conditions", {}))
    source = cast(dict[str, object], conditions.get("source", {}))
    if (
        source.get("release_id") != release_id
        or conditions.get("status") != expected_status
    ):
        raise DeployError("public conditions do not match the planned dated source")
    return f"{code} on {day} from {release_id}, {expected_status}"


def write_receipt(value: Plan, checks: dict[str, object]) -> None:
    host = Host()
    state = host.call("inspect", {})
    receipt: dict[str, object] = {
        **value.deployment.model_dump(),
        "publish_run_id": value.run_id,
        "dataset_id": value.dataset_id,
        "previous": value.previous.model_dump(),
        "rollback": f"validation-rollback/{value.deployment.source_revision}/compose.yaml",
        "available_mib": state["available_mib"],
        "operational_services": "unchanged_ids_and_restart_counts",
        "checks": checks,
        "host_stress_testing": "not_run",
    }
    stored = host.call(
        "receipt", {"deployment": value.deployment.model_dump(), "receipt": receipt}
    )
    local = WORK_ROOT / value.deployment.source_revision / "activation-receipt.json"
    _ = local.write_text(json.dumps(stored["content"], indent=2, sort_keys=True) + "\n")
    report("receipt", {"local": str(local), "host": stored["receipt"]})


def report(event: str, details: dict[str, object]) -> None:
    print(json.dumps({"event": event, **details}, sort_keys=True), flush=True)


@final
class Arguments(argparse.Namespace):
    command: str = ""
    revision: str = ""
    run_id: int = 0
    bundle: Path = Path()
    chromium: Path | None = None
    base: str = PUBLIC_BASE
    keep: int = 1
    apply: bool = False
    no_prune: bool = False


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("plan", "deploy"):
        command = commands.add_parser(name)
        _ = command.add_argument("--revision", required=True)
        _ = command.add_argument("--run-id", type=int, required=True)
        _ = command.add_argument("--bundle", type=Path, required=True)
        if name == "deploy":
            _ = command.add_argument("--chromium", type=Path)
            _ = command.add_argument("--base", default=PUBLIC_BASE)
            _ = command.add_argument("--no-prune", action="store_true")
    for name in ("stage", "activate", "verify", "rollback"):
        command = commands.add_parser(name)
        _ = command.add_argument("--revision", required=True)
        if name in ("verify", "rollback"):
            _ = command.add_argument("--base", default=PUBLIC_BASE)
        if name == "verify":
            _ = command.add_argument("--chromium", type=Path)
    command = commands.add_parser("prune")
    _ = command.add_argument("--keep", type=int, default=1)
    _ = command.add_argument("--apply", action="store_true")
    _ = commands.add_parser("inspect")
    return root


def main(arguments: Arguments) -> None:
    host = Host()
    match arguments.command:
        case "inspect":
            report("inspected", host.call("inspect", {}))
        case "plan":
            _ = plan(arguments)
        case "stage":
            stage(load_plan(arguments.revision))
        case "activate":
            _ = activate(load_plan(arguments.revision))
        case "verify":
            value = load_plan(arguments.revision)
            write_receipt(value, verify(value, arguments.chromium, arguments.base))
        case "rollback":
            result = host.call("rollback", {"revision": arguments.revision})
            try:
                ingress = verify_ingress(arguments.base)
            except DeployError as error:
                raise DeployError(str(error), {**result, **error.details}) from None
            report("rolled_back", {**result, **ingress, "status": "rolled_back"})
        case "prune":
            report(
                "pruned",
                host.call("prune", {"keep": arguments.keep, "apply": arguments.apply}),
            )
        case "deploy":
            value = plan(arguments)
            stage(value)
            _ = activate(value)
            write_receipt(value, verify(value, arguments.chromium, arguments.base))
            if not arguments.no_prune:
                report("pruned", host.call("prune", {"keep": 1, "apply": True}))
        case _:
            raise DeployError("unknown command")


if __name__ == "__main__":
    namespace = Arguments()
    _ = parser().parse_args(namespace=namespace)
    try:
        main(namespace)
    except DeployError as error:
        report("failed", {"error": str(error), "details": error.details})
        sys.exit(1)
