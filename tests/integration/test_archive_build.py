# pyright: reportMissingTypeStubs=false
# Raw transport input assertions do not reuse production validators.
# pyright: reportAny=false

import base64
import hashlib
import io
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast, final, override

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from pydantic import ValidationError

from minos.cli.main import main
from minos.domain.reference import SourceError
from minos.ingest.archive_build import (
    ArchiveBlob,
    ArchiveS3Client,
    ArchiveSelection,
    S3ArchiveReader,
)
from minos.ingest.processing.archive_versions import (
    ArchiveLocation,
    S3ArchiveVersionSelector,
    VersionClient,
)
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import dataset_identity
from minos.store.reference import open_reference_bundle
from tests.fixtures.jgp_conditions import stage as stage_conditions
from tests.integration.test_jgp_conditions_reference import conditions_manifest
from tests.integration.test_jgp_modifier_binding import binding_dataset


class _CloseFailure(io.BytesIO):
    @override
    def close(self) -> None:
        if not self.closed:
            super().close()
            raise OSError("PRIVATE_SDK_CLEANUP_TEXT")


@final
class Client:
    def __init__(self, root: Path) -> None:
        self.blobs = {
            path.name: path.read_bytes() for path in (root / "blobs/sha256").iterdir()
        }
        self.calls: list[dict[str, object]] = []
        self.streams: list[io.BytesIO] = []
        self.damage: str | None = None
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def get_object(self, **parameters: object) -> Mapping[str, object]:
        self.calls.append(parameters)
        if self.damage == "sdk-error":
            raise ClientError(
                {
                    "Error": {
                        "Code": "AccessDenied",
                        "Message": "TOKEN_AND_SIGNED_URL_SECRET",
                    }
                },
                "GetObject",
            )
        assert parameters["Bucket"] == "minos-build-synthetic"
        assert parameters["ExpectedBucketOwner"] == "123456789012"
        assert parameters["VersionId"] == "archived-version"
        assert parameters["ChecksumMode"] == "ENABLED"
        digest = cast(str, parameters["Key"]).removeprefix("blobs/sha256/")
        content = self.blobs[digest]
        body = (
            _CloseFailure(content)
            if self.damage in ("cleanup", "corrupt-cleanup")
            else io.BytesIO(content)
        )
        self.streams.append(body)
        response: dict[str, object] = {
            "Body": body,
            "VersionId": "archived-version",
            "ContentLength": len(content),
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode(),
            "ChecksumType": "FULL_OBJECT",
        }
        if self.damage == "version":
            response["VersionId"] = "newer-version"
        elif self.damage == "checksum":
            response["ChecksumSHA256"] = base64.b64encode(b"x" * 32).decode()
        elif self.damage == "size":
            response["ContentLength"] = len(content) + 1
        elif self.damage == "boolean-size":
            response["ContentLength"] = True
        elif self.damage == "composite":
            response["ChecksumType"] = "COMPOSITE"
        elif self.damage == "missing-checksum":
            del response["ChecksumSHA256"]
        elif self.damage in ("body", "corrupt-cleanup"):
            _ = body.seek(0)
            _ = body.write(b"X" * len(content))
            _ = body.seek(0)
        elif self.damage == "truncated":
            _ = body.truncate(len(content) - 1)
        elif self.damage == "oversized":
            _ = body.seek(0, io.SEEK_END)
            _ = body.write(b"X")
            _ = body.seek(0)
        elif self.damage == "delete-marker":
            response["DeleteMarker"] = True
        return response


def selection(root: Path, manifest: DatasetManifest) -> ArchiveSelection:
    return ArchiveSelection.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": dataset_identity(manifest),
                "bucket": "minos-build-synthetic",
                "region": "eu-central-1",
                "expected_bucket_owner": "123456789012",
                "objects": [
                    {
                        "sha256": path.name,
                        "size_bytes": path.stat().st_size,
                        "version_id": "archived-version",
                    }
                    for path in sorted((root / "blobs/sha256").iterdir())
                ],
            }
        )
    )


@pytest.mark.parametrize("include_optional_inputs", [False, True])
def test_pinned_archive_reuses_full_builder_and_is_repeat_deterministic(
    tmp_path: Path,
    include_optional_inputs: bool,
) -> None:
    if include_optional_inputs:
        data = binding_dataset(tmp_path).model_dump(mode="json")
        data["characteristics"] = [stage_conditions(tmp_path).model_dump(mode="json")]
        data["characteristics_knowledge_as_of"] = "2026-10-02"
        data["characteristics_knowledge_evidence"] = "Synthetic lineage review"
        manifest = DatasetManifest.model_validate_json(json.dumps(data))
    else:
        manifest = conditions_manifest(tmp_path)
    pins = selection(tmp_path, manifest)
    client = Client(tmp_path)
    expected = build_reference_artifact(manifest, tmp_path, tmp_path / "local")
    reader = S3ArchiveReader(client, pins)
    for name in ("first", "second"):
        with reader.materialize(manifest, staging_directory=tmp_path) as archive:
            built = build_reference_artifact(manifest, archive, tmp_path / name)
        assert not archive.exists()
        assert built.database_sha256 == expected.database_sha256
        assert built.row_counts == expected.row_counts
        with open_reference_bundle(tmp_path / name) as (connection, _):
            assert connection.execute(
                "SELECT COUNT(*) FROM group_condition"
            ).fetchone() == (6,)
            if include_optional_inputs:
                assert connection.execute(
                    "SELECT COUNT(*) FROM modifier_binding"
                ).fetchone() == (1,)
                assert connection.execute(
                    "SELECT COUNT(*) FROM procedure_rank"
                ).fetchone() == (4,)
    assert len(client.calls) == len(pins.objects) * 2
    assert all(stream.closed for stream in client.streams)


@pytest.mark.parametrize(
    "damage",
    [
        "version",
        "checksum",
        "size",
        "boolean-size",
        "composite",
        "missing-checksum",
        "body",
        "truncated",
        "oversized",
        "delete-marker",
    ],
)
def test_unverified_archive_never_reaches_builder_and_cleans_owned_staging(
    tmp_path: Path,
    damage: str,
) -> None:
    manifest = conditions_manifest(tmp_path)
    client = Client(tmp_path)

    client.damage = damage
    reader = S3ArchiveReader(client, selection(tmp_path, manifest))
    with pytest.raises(SourceError):
        with reader.materialize(manifest, staging_directory=tmp_path):
            pytest.fail("unverified archive was offered to the builder")
    assert all(stream.closed for stream in client.streams)
    assert not list(tmp_path.glob(".minos-archive-*"))


@pytest.mark.parametrize("damage", ["dataset", "missing", "extra", "size", "duplicate"])
def test_selection_must_match_every_input_before_any_remote_read(
    tmp_path: Path,
    damage: str,
) -> None:
    manifest = conditions_manifest(tmp_path)
    raw = selection(tmp_path, manifest).model_dump(mode="json")
    if damage == "dataset":
        raw["dataset_id"] = "a" * 64
    elif damage == "missing":
        raw["objects"].pop()
    elif damage == "extra":
        raw["objects"].append(
            {"sha256": "a" * 64, "size_bytes": 1, "version_id": "other"}
        )
    elif damage == "duplicate":
        raw["objects"].append(raw["objects"][0])
    else:
        raw["objects"][0]["size_bytes"] += 1
    client = Client(tmp_path)
    with pytest.raises((SourceError, ValidationError)):
        reader = S3ArchiveReader(
            client, ArchiveSelection.model_validate_json(json.dumps(raw))
        )
        with reader.materialize(manifest, staging_directory=tmp_path):
            pytest.fail("conflicting selection was materialized")
    assert client.calls == []


def test_archive_build_cli_preserves_offline_cli_and_reuses_real_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from minos.cli.reference import reference_main

    manifest = conditions_manifest(tmp_path)
    pins = selection(tmp_path, manifest)
    client = Client(tmp_path)

    def create(pin: ArchiveSelection) -> S3ArchiveReader:
        rotated = manifest.model_dump(mode="json")
        rotated["sources"][0]["notices"] = ["Rotated after the CLI input was verified"]
        _ = source_path.write_text(json.dumps(rotated))
        return S3ArchiveReader(client, pin)

    monkeypatch.setattr(S3ArchiveReader, "create", create)
    source_path = tmp_path / "source.json"
    _ = source_path.write_text(manifest.model_dump_json())
    pins_path = tmp_path / "selection.json"
    _ = pins_path.write_text(pins.model_dump_json())
    args = [
        "data",
        "build",
        "--manifest",
        str(source_path),
        "--archive-s3-selection",
        str(pins_path),
        "--output",
        str(tmp_path / "bundle"),
        "--json",
    ]
    main(args)
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "built"
    assert json.loads(captured.out)["dataset_id"] == dataset_identity(manifest)
    assert len(json.loads(captured.out)["archive_selection_sha256"]) == 64
    assert captured.err == ""
    assert client.closed is True
    with open_reference_bundle(tmp_path / "bundle") as (_, built_manifest):
        assert built_manifest.dataset == manifest
    calls = len(client.calls)
    with pytest.raises(SystemExit) as error:
        reference_main(args)
    assert error.value.code == 2
    assert len(client.calls) == calls
    assert str(tmp_path) not in capsys.readouterr().err


def test_transport_pins_reject_unversioned_or_arbitrary_remote_requests(
    tmp_path: Path,
) -> None:
    manifest = conditions_manifest(tmp_path)
    for version in ("", "null", "\nversion", "a" * 1025):
        raw = selection(tmp_path, manifest).model_dump(mode="json")
        raw["objects"][0]["version_id"] = version
        with pytest.raises(ValidationError):
            _ = ArchiveSelection.model_validate_json(json.dumps(raw))
    raw = selection(tmp_path, manifest).model_dump(mode="json")
    raw["objects"][0]["key"] = "terraform/state"
    with pytest.raises(ValidationError):
        _ = ArchiveSelection.model_validate_json(json.dumps(raw))


class _SdkFactory(Protocol):
    def client(self, service: str, **kwargs: object) -> object: ...


class _SdkConfig(Protocol):
    @property
    def retries(self) -> Mapping[str, object]: ...

    @property
    def ignore_configured_endpoint_urls(self) -> bool: ...

    @property
    def connect_timeout(self) -> float: ...

    @property
    def read_timeout(self) -> float: ...


class _Stub(Protocol):
    def activate(self) -> None: ...

    def deactivate(self) -> None: ...

    def add_response(
        self, method: str, response: dict[str, object], parameters: dict[str, object]
    ) -> None: ...

    def assert_no_pending_responses(self) -> None: ...


def test_sdk_request_pins_owner_version_key_and_native_checksum(tmp_path: Path) -> None:
    from botocore.response import StreamingBody  # type: ignore[import-untyped]
    from botocore.stub import Stubber  # type: ignore[import-untyped]

    manifest = conditions_manifest(tmp_path)
    pins = selection(tmp_path, manifest)
    factory = cast(_SdkFactory, boto3)  # pyright: ignore[reportInvalidCast]
    sdk = cast(
        ArchiveS3Client,
        factory.client(
            "s3",
            region_name="eu-central-1",
            aws_access_key_id="synthetic",
            aws_secret_access_key="synthetic",
        ),
    )
    stub = cast(_Stub, cast(object, Stubber(sdk)))
    stub.activate()
    try:
        streams: list[io.BytesIO] = []
        for item in sorted(pins.objects, key=lambda pin: pin.sha256):
            content = (tmp_path / "blobs/sha256" / item.sha256).read_bytes()
            stream = io.BytesIO(content)
            streams.append(stream)
            stub.add_response(
                "get_object",
                {
                    "Body": StreamingBody(stream, len(content)),
                    "VersionId": "archived-version",
                    "ContentLength": len(content),
                    "ChecksumType": "FULL_OBJECT",
                    "ChecksumSHA256": base64.b64encode(
                        hashlib.sha256(content).digest()
                    ).decode(),
                },
                {
                    "Bucket": "minos-build-synthetic",
                    "Key": f"blobs/sha256/{item.sha256}",
                    "VersionId": "archived-version",
                    "ExpectedBucketOwner": "123456789012",
                    "ChecksumMode": "ENABLED",
                },
            )
        reader = S3ArchiveReader(sdk, pins)
        with reader.materialize(manifest, staging_directory=tmp_path) as archive:
            assert len(list((archive / "blobs/sha256").iterdir())) == len(pins.objects)
        stub.assert_no_pending_responses()
        assert all(stream.closed for stream in streams)
    finally:
        stub.deactivate()
        sdk.close()


@pytest.mark.parametrize("damage", ["body", "sdk-error"])
def test_cli_failure_is_redacted_and_does_not_touch_last_good_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    damage: str,
) -> None:
    manifest = conditions_manifest(tmp_path)
    last_good = tmp_path / "last-good"
    _ = build_reference_artifact(manifest, tmp_path, last_good)
    before = {path.name: path.read_bytes() for path in last_good.iterdir()}
    pins = selection(tmp_path, manifest)
    source_path = tmp_path / "source.json"
    _ = source_path.write_text(manifest.model_dump_json())
    pin_path = tmp_path / "pins.json"
    _ = pin_path.write_text(pins.model_dump_json())
    client = Client(tmp_path)
    client.damage = damage

    def create(pin: ArchiveSelection) -> S3ArchiveReader:
        return S3ArchiveReader(client, pin)

    monkeypatch.setattr(S3ArchiveReader, "create", create)
    with pytest.raises(SystemExit) as error:
        main(
            [
                "data",
                "build",
                "--manifest",
                str(source_path),
                "--archive-s3-selection",
                str(pin_path),
                "--previous-bundle",
                str(last_good),
                "--output",
                str(tmp_path / "candidate"),
                "--json",
            ]
        )
    captured = capsys.readouterr()
    assert error.value.code == 3
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "data_error"
    assert str(tmp_path) not in captured.err
    assert "TOKEN_AND_SIGNED_URL_SECRET" not in captured.err
    assert not (tmp_path / "candidate").exists()
    assert client.closed is True
    assert {path.name: path.read_bytes() for path in last_good.iterdir()} == before


def test_archive_transfer_has_a_deadline_before_any_remote_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = conditions_manifest(tmp_path)
    client = Client(tmp_path)
    reader = S3ArchiveReader(client, selection(tmp_path, manifest))
    # A clock jump after the deadline is captured must not start a transfer.
    readings = iter((0.0, 601.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(readings))
    with pytest.raises(SourceError, match="deadline"):
        with reader.materialize(manifest, staging_directory=tmp_path):
            pytest.fail("expired transfer offered an archive")
    assert client.calls == []
    assert not list(tmp_path.glob(".minos-archive-*"))


@pytest.mark.parametrize("client_kind", ["reader", "version-selector"])
def test_production_client_has_bounded_retries_and_no_endpoint_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client_kind: str,
) -> None:
    manifest = conditions_manifest(tmp_path)
    pins = selection(tmp_path, manifest)
    client = Client(tmp_path) if client_kind == "reader" else VersionFixture(tmp_path)

    def factory(
        service: str, *, region_name: str, config: object
    ) -> Client | VersionFixture:
        assert service == "s3"
        assert region_name == "eu-central-1"
        settings = cast(_SdkConfig, config)
        assert settings.retries == {"total_max_attempts": 1, "mode": "standard"}
        assert settings.ignore_configured_endpoint_urls is True
        assert settings.connect_timeout == 10
        assert settings.read_timeout == 30
        return client

    monkeypatch.setattr(boto3, "client", factory)
    if client_kind == "reader":
        reader = S3ArchiveReader.create(pins)
        try:
            with reader.materialize(manifest, staging_directory=tmp_path) as archive:
                assert (archive / "blobs/sha256").is_dir()
        finally:
            reader.close()
    else:
        selector = S3ArchiveVersionSelector.create(archive_location())
        try:
            assert selector.select_dataset(manifest) == pins
        finally:
            selector.close()
    assert client.calls
    assert client.closed


def test_selection_bounds_objects_and_cumulative_transfer_bytes(tmp_path: Path) -> None:
    manifest = conditions_manifest(tmp_path)
    raw = selection(tmp_path, manifest).model_dump(mode="json")
    for count, size in ((129, 1), (9, 64 * 1024 * 1024)):
        raw["objects"] = [
            {"sha256": f"{number:064x}", "version_id": "version", "size_bytes": size}
            for number in range(count)
        ]
        with pytest.raises(ValidationError):
            _ = ArchiveSelection.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("damage", ["cleanup", "corrupt-cleanup"])
def test_stream_cleanup_failure_preserves_primary_error_and_never_offers_archive(
    tmp_path: Path,
    damage: str,
) -> None:
    manifest = conditions_manifest(tmp_path)
    client = Client(tmp_path)
    client.damage = damage
    reader = S3ArchiveReader(client, selection(tmp_path, manifest))
    expected = "cleanup failed" if damage == "cleanup" else "body checksum"
    with pytest.raises(SourceError, match=expected) as error:
        with reader.materialize(manifest, staging_directory=tmp_path):
            pytest.fail("failed cleanup offered an archive")
    assert "PRIVATE_SDK_CLEANUP_TEXT" not in str(error.value)
    assert all(stream.closed for stream in client.streams)
    assert not list(tmp_path.glob(".minos-archive-*"))


@final
class VersionFixture:
    def __init__(self, root: Path) -> None:
        self.blobs = {
            path.name: path.read_bytes() for path in (root / "blobs/sha256").iterdir()
        }
        self.calls: list[dict[str, object]] = []
        self.damage: str | None = None
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def list_object_versions(self, **kwargs: object) -> Mapping[str, object]:
        self.calls.append(kwargs)
        assert kwargs["Bucket"] == "minos-build-synthetic"
        assert kwargs["ExpectedBucketOwner"] == "123456789012"
        assert kwargs["MaxKeys"] == 2
        digest = str(kwargs["Prefix"]).removeprefix("blobs/sha256/")
        version: dict[str, object] = {
            "Key": kwargs["Prefix"],
            "VersionId": "archived-version",
            "Size": len(self.blobs[digest]),
            "IsLatest": False,
        }
        result: dict[str, object] = {"IsTruncated": False, "Versions": [version]}
        if self.damage == "list-error":
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "PRIVATE_SECRET"}},
                "ListObjectVersions",
            )
        if self.damage == "truncated":
            result["IsTruncated"] = True
        elif self.damage == "missing-truncation":
            del result["IsTruncated"]
        elif self.damage == "delete-marker":
            result["DeleteMarkers"] = [
                {"Key": kwargs["Prefix"], "VersionId": "deleted"}
            ]
        elif self.damage == "absent":
            result["Versions"] = []
        elif self.damage == "duplicate":
            result["Versions"] = [
                version,
                {**version, "VersionId": "another-version", "IsLatest": True},
            ]
        elif self.damage == "key":
            version["Key"] = "blobs/sha256/" + "a" * 64
        elif self.damage == "prefix-suffix":
            version["Key"] = str(kwargs["Prefix"]) + "-PRIVATE_SUFFIX"
        elif self.damage == "listed-size":
            version["Size"] = len(self.blobs[digest]) + 1
        elif self.damage == "boolean-size":
            version["Size"] = True
        elif self.damage == "null-version":
            version["VersionId"] = "null"
        elif self.damage == "unsafe-version":
            version["VersionId"] = "PRIVATE_SECRET\n"
        return result

    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        self.calls.append(kwargs)
        assert kwargs["VersionId"] == "archived-version"
        assert kwargs["ExpectedBucketOwner"] == "123456789012"
        assert kwargs["ChecksumMode"] == "ENABLED"
        digest = str(kwargs["Key"]).removeprefix("blobs/sha256/")
        response: dict[str, object] = {
            "VersionId": "archived-version",
            "ContentLength": len(self.blobs[digest]),
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode(),
            "ChecksumType": "FULL_OBJECT",
        }
        if self.damage == "head-error":
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "PRIVATE_SECRET"}},
                "HeadObject",
            )
        if self.damage == "head-version":
            response["VersionId"] = "another-version"
        elif self.damage == "head-size":
            response["ContentLength"] = len(self.blobs[digest]) + 1
        elif self.damage == "head-boolean-size":
            response["ContentLength"] = True
        elif self.damage == "head-checksum":
            response["ChecksumSHA256"] = base64.b64encode(b"x" * 32).decode()
        elif self.damage == "head-composite":
            response["ChecksumType"] = "COMPOSITE"
        elif self.damage == "head-delete-marker":
            response["DeleteMarker"] = True
        return response


def archive_location() -> ArchiveLocation:
    return ArchiveLocation(
        bucket="minos-build-synthetic",
        region="eu-central-1",
        expected_bucket_owner="123456789012",
    )


def test_version_selector_covers_full_dataset_then_reuses_byte_verified_builder(
    tmp_path: Path,
) -> None:
    data = binding_dataset(tmp_path).model_dump(mode="json")
    data["characteristics"] = [stage_conditions(tmp_path).model_dump(mode="json")]
    data["characteristics_knowledge_as_of"] = "2026-10-02"
    data["characteristics_knowledge_evidence"] = "Synthetic lineage review"
    manifest = DatasetManifest.model_validate_json(json.dumps(data))
    client = VersionFixture(tmp_path)
    selector = S3ArchiveVersionSelector(client, archive_location())
    first = selector.select_dataset(manifest)
    assert first == selection(tmp_path, manifest)
    assert selector.select_dataset(manifest) == first
    assert len(client.calls) == 4 * len(first.objects)
    byte_client = Client(tmp_path)
    with S3ArchiveReader(byte_client, first).materialize(
        manifest, staging_directory=tmp_path
    ) as archive:
        result = build_reference_artifact(
            manifest, archive, tmp_path / "from-selected-versions"
        )
        assert result.row_counts["modifier_binding"] == 1
        assert result.row_counts["group_condition"] == 6
    selector.close()
    assert client.closed


@pytest.mark.parametrize(
    "damage",
    [
        "list-error",
        "truncated",
        "missing-truncation",
        "delete-marker",
        "absent",
        "duplicate",
        "key",
        "prefix-suffix",
        "listed-size",
        "boolean-size",
        "null-version",
        "unsafe-version",
        "head-error",
        "head-version",
        "head-size",
        "head-boolean-size",
        "head-checksum",
        "head-composite",
        "head-delete-marker",
    ],
)
def test_version_selector_refuses_incomplete_ambiguous_or_unverified_metadata(
    tmp_path: Path, damage: str
) -> None:
    manifest = conditions_manifest(tmp_path)
    client = VersionFixture(tmp_path)
    client.damage = damage
    with pytest.raises(SourceError) as failure:
        _ = S3ArchiveVersionSelector(client, archive_location()).select_dataset(
            manifest
        )
    assert "PRIVATE_" not in str(failure.value)
    assert str(tmp_path) not in str(failure.value)
    assert len(client.calls) <= 2


def test_version_selection_bounds_before_remote_requests_and_never_returns_partial_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = conditions_manifest(tmp_path)
    client = VersionFixture(tmp_path)
    selector = S3ArchiveVersionSelector(client, archive_location())
    for count, size in ((0, 1), (129, 1), (9, 64 * 1024 * 1024)):
        blobs = tuple(
            ArchiveBlob(sha256=f"{index:064x}", size_bytes=size)
            for index in range(count)
        )
        with pytest.raises(SourceError, match="bound"):
            _ = selector.select_blobs(blobs)
    with pytest.raises(SourceError, match="duplicate"):
        _ = selector.select_blobs((ArchiveBlob(sha256="a" * 64, size_bytes=1),) * 2)
    assert client.calls == []
    readings = iter((0.0, 601.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(readings))
    with pytest.raises(SourceError, match="deadline"):
        _ = selector.select_dataset(manifest)
    assert client.calls == []


def test_actual_sdk_version_listing_and_head_pin_exact_owner_key_and_version(
    tmp_path: Path,
) -> None:
    from botocore.stub import Stubber

    manifest = conditions_manifest(tmp_path)
    expected = selection(tmp_path, manifest)
    factory = cast(_SdkFactory, boto3)  # pyright: ignore[reportInvalidCast]
    sdk = cast(
        VersionClient,
        factory.client(
            "s3",
            region_name="eu-central-1",
            aws_access_key_id="synthetic",
            aws_secret_access_key="synthetic",
        ),
    )
    stub = cast(_Stub, cast(object, Stubber(sdk)))
    stub.activate()
    try:
        for pin in sorted(expected.objects, key=lambda item: item.sha256):
            key = f"blobs/sha256/{pin.sha256}"
            stub.add_response(
                "list_object_versions",
                {
                    "IsTruncated": False,
                    "Versions": [
                        {
                            "Key": key,
                            "VersionId": "archived-version",
                            "Size": pin.size_bytes,
                            "IsLatest": False,
                        }
                    ],
                },
                {
                    "Bucket": "minos-build-synthetic",
                    "Prefix": key,
                    "MaxKeys": 2,
                    "ExpectedBucketOwner": "123456789012",
                },
            )
            stub.add_response(
                "head_object",
                {
                    "VersionId": "archived-version",
                    "ContentLength": pin.size_bytes,
                    "ChecksumSHA256": base64.b64encode(
                        bytes.fromhex(pin.sha256)
                    ).decode(),
                    "ChecksumType": "FULL_OBJECT",
                },
                {
                    "Bucket": "minos-build-synthetic",
                    "Key": key,
                    "VersionId": "archived-version",
                    "ExpectedBucketOwner": "123456789012",
                    "ChecksumMode": "ENABLED",
                },
            )
        assert (
            S3ArchiveVersionSelector(sdk, archive_location()).select_dataset(manifest)
            == expected
        )
        stub.assert_no_pending_responses()
    finally:
        stub.deactivate()
        sdk.close()
