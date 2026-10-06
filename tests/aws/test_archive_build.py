# pyright: reportAny=false, reportExplicitAny=false, reportMissingTypeStubs=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

"""Real version/checksum semantics on the guarded synthetic acceptance stack."""

import base64
import hashlib
import json
import secrets
from collections.abc import Generator
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from minos.cli.main import main
from minos.domain.reference import SourceError
from minos.ingest.archive_build import ArchiveSelection, S3ArchiveReader
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import canonical_json, dataset_identity
from minos.store.reference import open_reference_bundle
from tests.aws.harness import AwsHarness
from tests.fixtures.jgp_conditions import stage as stage_conditions
from tests.integration.test_jgp_modifier_binding import binding_dataset
from tests.integration.test_reference_artifact import stage_source

pytestmark = pytest.mark.aws


def synthetic_source(root: Path) -> DatasetManifest:
    content = (Path(__file__).parents[1] / "fixtures/icd10-pl/minimal.xml").read_bytes()
    # Give each refusal case its own immutable object, including the case that
    # deliberately omits a native SHA-256 checksum. XML facts remain unchanged.
    return stage_source(root, content + f"<!-- {secrets.token_hex(8)} -->".encode())


@pytest.fixture
def archive_session(aws_harness: AwsHarness) -> Generator[Any]:
    """Intersect the test role with version-read-only access to blob keys."""
    credentials = aws_harness.admin_sts.assume_role(
        RoleArn=aws_harness.role_arn,
        RoleSessionName=f"minos-build-{aws_harness.run_id}",
        DurationSeconds=900,
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "s3:GetObjectVersion",
                        "Resource": f"arn:aws:s3:::{aws_harness.bucket}/blobs/sha256/*",
                    }
                ],
            }
        ),
    )["Credentials"]
    session = boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=aws_harness.region,
    )
    sts = session.client("sts")
    try:
        identity = sts.get_caller_identity()
        assert identity["Account"] == aws_harness.account_id
        assert f":assumed-role/{aws_harness.role_name}/" in identity["Arn"]
        yield session
    finally:
        _ = sts.close()


def upload_selection(
    harness: AwsHarness,
    manifest: DatasetManifest,
    root: Path,
    *,
    native_sha256: bool = True,
) -> ArchiveSelection:
    objects: list[dict[str, object]] = []
    for path in sorted((root / "blobs/sha256").iterdir()):
        content = path.read_bytes()
        assert hashlib.sha256(content).hexdigest() == path.name
        checksum = (
            {
                "ChecksumAlgorithm": "SHA256",
                "ChecksumSHA256": base64.b64encode(
                    hashlib.sha256(content).digest()
                ).decode(),
            }
            if native_sha256
            else {}
        )
        response = harness.watcher_s3.put_object(
            Bucket=harness.bucket,
            Key=f"blobs/sha256/{path.name}",
            Body=content,
            ExpectedBucketOwner=harness.account_id,
            IfNoneMatch="*",
            ServerSideEncryption="AES256",
            **checksum,
        )
        objects.append(
            {
                "sha256": path.name,
                "size_bytes": len(content),
                "version_id": response["VersionId"],
            }
        )
    return ArchiveSelection.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": dataset_identity(manifest),
                "bucket": harness.bucket,
                "region": harness.region,
                "expected_bucket_owner": harness.account_id,
                "objects": objects,
            }
        )
    )


def test_real_s3_operational_cli_builds_pinned_full_synthetic_bundle_twice(
    aws_harness: AwsHarness,
    archive_session: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = binding_dataset(tmp_path).model_dump(mode="json")
    raw["characteristics"] = [stage_conditions(tmp_path).model_dump(mode="json")]
    raw["characteristics_knowledge_as_of"] = "2026-10-02"
    raw["characteristics_knowledge_evidence"] = "Synthetic lineage review"
    manifest = DatasetManifest.model_validate_json(json.dumps(raw))
    pins = upload_selection(aws_harness, manifest, tmp_path)
    local = tmp_path / "local"
    expected = build_reference_artifact(manifest, tmp_path, local)
    manifest_path = tmp_path / "dataset.json"
    selection_path = tmp_path / "selection.json"
    _ = manifest_path.write_text(manifest.model_dump_json())
    _ = selection_path.write_text(pins.model_dump_json())
    # A newer current object must not replace the selected immutable predecessor.
    chosen = pins.objects[0]
    key = f"blobs/sha256/{chosen.sha256}"
    _ = aws_harness.admin_s3.delete_object(
        Bucket=aws_harness.bucket, Key=key, ExpectedBucketOwner=aws_harness.account_id
    )
    content = b"Synthetic newer corrupt bytes, not the pinned release"
    _ = aws_harness.admin_s3.put_object(
        Bucket=aws_harness.bucket,
        Key=key,
        Body=content,
        IfNoneMatch="*",
        ExpectedBucketOwner=aws_harness.account_id,
        ServerSideEncryption="AES256",
        ChecksumAlgorithm="SHA256",
        ChecksumSHA256=base64.b64encode(hashlib.sha256(content).digest()).decode(),
    )
    monkeypatch.setattr(boto3, "DEFAULT_SESSION", archive_session)
    for name in ("first", "second"):
        main(
            [
                "data",
                "build",
                "--manifest",
                str(manifest_path),
                "--archive-s3-selection",
                str(selection_path),
                "--output",
                str(tmp_path / name),
                "--json",
            ]
        )
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert captured.err == ""
        assert result["status"] == "built"
        assert result["database_sha256"] == expected.database_sha256
        assert (
            result["archive_selection_sha256"]
            == hashlib.sha256(canonical_json(pins)).hexdigest()
        )
        with open_reference_bundle(tmp_path / name) as (connection, opened):
            assert opened.dataset == manifest
            assert opened.row_counts == expected.row_counts
            assert connection.execute(
                "SELECT COUNT(*) FROM group_condition"
            ).fetchone() == (6,)
            assert connection.execute(
                "SELECT COUNT(*) FROM modifier_binding"
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT COUNT(*) FROM procedure_rank"
            ).fetchone() == (4,)
        assert (tmp_path / name / "minos.db").read_bytes() == (
            local / "minos.db"
        ).read_bytes()
    assert not list(tmp_path.glob(".minos-archive-*"))


@pytest.mark.parametrize(
    "damage",
    ["owner", "version", "size", "checksum", "native-checksum", "delete-marker"],
)
def test_real_s3_invalid_identity_never_offers_a_build_input(
    aws_harness: AwsHarness,
    archive_session: Any,
    tmp_path: Path,
    damage: str,
) -> None:
    manifest = synthetic_source(tmp_path)
    pins = upload_selection(
        aws_harness, manifest, tmp_path, native_sha256=damage != "native-checksum"
    )
    raw = pins.model_dump(mode="json")
    if damage == "owner":
        raw["expected_bucket_owner"] = "000000000000"
    elif damage == "version":
        # Another real version is a valid S3 token, but not a version of this key.
        response = aws_harness.watcher_s3.put_object(
            Bucket=aws_harness.bucket,
            Key="blobs/sha256/"
            + hashlib.sha256(b"synthetic other-key version").hexdigest(),
            Body=b"synthetic",
            IfNoneMatch="*",
            ExpectedBucketOwner=aws_harness.account_id,
            ServerSideEncryption="AES256",
        )
        raw["objects"][0]["version_id"] = response["VersionId"]
    elif damage == "size":
        raw["objects"][0]["size_bytes"] += 1
        source = manifest.model_dump(mode="json")
        source["sources"][0]["size_bytes"] += 1
        manifest = DatasetManifest.model_validate_json(json.dumps(source))
        raw["dataset_id"] = dataset_identity(manifest)
    elif damage == "checksum":
        wrong = "a" * 64
        body = b"Synthetic bytes under a deliberately wrong digest key"
        response = aws_harness.watcher_s3.put_object(
            Bucket=aws_harness.bucket,
            Key=f"blobs/sha256/{wrong}",
            Body=body,
            IfNoneMatch="*",
            ExpectedBucketOwner=aws_harness.account_id,
            ServerSideEncryption="AES256",
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=base64.b64encode(hashlib.sha256(body).digest()).decode(),
        )
        source = manifest.model_dump(mode="json")
        source["sources"][0]["sha256"] = wrong
        source["sources"][0]["size_bytes"] = len(body)
        manifest = DatasetManifest.model_validate_json(json.dumps(source))
        raw["dataset_id"] = dataset_identity(manifest)
        raw["objects"] = [
            {
                "sha256": wrong,
                "size_bytes": len(body),
                "version_id": response["VersionId"],
            }
        ]
    elif damage == "delete-marker":
        response = aws_harness.admin_s3.delete_object(
            Bucket=aws_harness.bucket,
            Key=f"blobs/sha256/{pins.objects[0].sha256}",
            ExpectedBucketOwner=aws_harness.account_id,
        )
        raw["objects"][0]["version_id"] = response["VersionId"]
    selected = ArchiveSelection.model_validate_json(json.dumps(raw))
    client = archive_session.client(
        "s3", config=Config(retries={"total_max_attempts": 1})
    )
    reader = S3ArchiveReader(client, selected)
    try:
        with pytest.raises(SourceError):
            with reader.materialize(manifest, staging_directory=tmp_path):
                pytest.fail("real S3 failure offered unverified bytes to the builder")
        assert not list(tmp_path.glob(".minos-archive-*"))
    finally:
        reader.close()


def test_real_archive_reader_session_denies_writes_latest_and_unrelated_keys(
    aws_harness: AwsHarness,
    archive_session: Any,
    tmp_path: Path,
) -> None:
    manifest = synthetic_source(tmp_path)
    pins = upload_selection(aws_harness, manifest, tmp_path)
    client = archive_session.client(
        "s3", config=Config(retries={"total_max_attempts": 1})
    )
    item = pins.objects[0]
    key = f"blobs/sha256/{item.sha256}"
    try:
        requests = (
            (
                "put_object",
                {
                    "Key": "blobs/sha256/" + "b" * 64,
                    "Body": b"synthetic",
                    "IfNoneMatch": "*",
                },
            ),
            ("delete_object", {"Key": key, "VersionId": item.version_id}),
            ("get_object", {"Key": key}),
            (
                "get_object",
                {"Key": "control-recovery/run/unknown", "VersionId": item.version_id},
            ),
            ("get_object", {"Key": "terraform.tfstate", "VersionId": item.version_id}),
        )
        for operation, arguments in requests:
            with pytest.raises(ClientError) as error:
                getattr(client, operation)(
                    Bucket=aws_harness.bucket,
                    ExpectedBucketOwner=aws_harness.account_id,
                    **arguments,
                )
            assert error.value.response["Error"]["Code"] == "AccessDenied"
        with S3ArchiveReader(client, pins).materialize(
            manifest, staging_directory=tmp_path
        ) as archive:
            assert (archive / key).read_bytes() == (tmp_path / key).read_bytes()
    finally:
        client.close()
