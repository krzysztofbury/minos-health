# pyright: reportAny=false, reportExplicitAny=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from dataclasses import dataclass
from pathlib import Path
from typing import Any, final

from scripts.aws_watcher_acceptance_guard import (
    ACCOUNT_ID,
    ObjectIdentity,
    load_object_manifest,
    validate_acceptance_key,
    write_json_atomic,
)


@final
class AcceptanceObjectManifest:
    def __init__(self, path: str, *, bucket: str, run_id: str) -> None:
        self.path = Path(path)
        self.bucket = bucket
        self.run_id = run_id
        _ = self.identities()

    def identities(self) -> frozenset[ObjectIdentity]:
        return load_object_manifest(
            self.path,
            account_id=ACCOUNT_ID,
            bucket=self.bucket,
            run_id=self.run_id,
        )

    def record(self, key: str, version_id: str, kind: str) -> None:
        validate_acceptance_key(key)
        if not version_id or kind not in {"version", "delete_marker"}:
            raise RuntimeError("S3 operation did not return a valid object identity")
        identities = set(self.identities())
        identities.add((key, version_id, kind))
        write_json_atomic(
            self.path,
            {
                "schema_version": 2,
                "account_id": ACCOUNT_ID,
                "bucket": self.bucket,
                "run_id": self.run_id,
                "objects": [
                    {"key": item[0], "version_id": item[1], "kind": item[2]}
                    for item in sorted(identities)
                ],
            },
        )


@final
class ManifestRecordingS3Client:
    def __init__(self, client: Any, manifest: AcceptanceObjectManifest) -> None:
        self._client = client
        self._manifest = manifest

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def put_object(self, **kwargs: object) -> Any:
        key = _key_argument(kwargs)
        response = self._client.put_object(**kwargs)
        self._record_response(key, response, "version")
        return response

    def copy_object(self, **kwargs: object) -> Any:
        key = _key_argument(kwargs)
        response = self._client.copy_object(**kwargs)
        self._record_response(key, response, "version")
        return response

    def delete_object(self, **kwargs: object) -> Any:
        key = _key_argument(kwargs)
        version_id = kwargs.get("VersionId")
        identities = self._manifest.identities()
        if isinstance(version_id, str):
            if not any(item[:2] == (key, version_id) for item in identities):
                raise RuntimeError(
                    "refusing to delete an object version absent from the manifest"
                )
        elif not any(item[0] == key for item in identities):
            raise RuntimeError("refusing to delete an object absent from the manifest")
        response = self._client.delete_object(**kwargs)
        kind = "delete_marker" if response.get("DeleteMarker") is True else "version"
        self._record_response(key, response, kind)
        return response

    def _record_response(self, key: str, response: Any, kind: str) -> None:
        version_id = response.get("VersionId") if isinstance(response, dict) else None
        if not isinstance(version_id, str):
            raise RuntimeError("S3 operation did not return VersionId")
        self._manifest.record(key, version_id, kind)


def _key_argument(arguments: dict[str, object]) -> str:
    key = arguments.get("Key")
    if not isinstance(key, str):
        raise RuntimeError("S3 operation does not contain a valid object key")
    validate_acceptance_key(key)
    return key


@dataclass(frozen=True, slots=True)
class AwsHarness:
    account_id: str
    region: str
    bucket: str
    role_arn: str
    role_id: str
    role_name: str
    run_id: str
    operator_arn: str
    permissions_boundary_arn: str
    object_manifest: AcceptanceObjectManifest
    admin_sts: Any
    admin_s3: Any
    admin_iam: Any
    watcher_s3: Any
