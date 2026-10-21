# pyright: reportMissingTypeStubs=false
import hashlib
import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast, final

import httpx
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from minos.cli.processor import execute_finalize
from minos.ingest.receipts import ReceiptClient
from minos.ingest.watchers.uptime_kuma import UptimeKumaClient, UptimeKumaTargets

_TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz012345"


@final
class _Bucket:
    """Create-once object store, as R2 answers under the receipts lock."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def list_objects_v2(self, **parameters: object) -> Mapping[str, object]:
        prefix = cast(str, parameters["Prefix"])
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}

    def get_object(self, **parameters: object) -> Mapping[str, object]:
        return {"Body": io.BytesIO(self.objects[cast(str, parameters["Key"])])}

    def put_object(self, **parameters: object) -> Mapping[str, object]:
        key = cast(str, parameters["Key"])
        assert parameters["IfNoneMatch"] == "*"
        if key in self.objects:
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed"},
                 "ResponseMetadata": {"HTTPStatusCode": 412}},
                "PutObject",
            )  # fmt: skip
        self.objects[key] = cast(bytes, parameters["Body"])
        return {}


def _finalize(
    tmp_path: Path, bucket: _Bucket, outcome: dict[str, object]
) -> tuple[int, list[tuple[str, str]]]:
    targets = tmp_path / "targets.json"
    _ = targets.write_text(
        json.dumps({"version": 1, "sources": {"data-activation": _TOKEN}})
    )
    path = tmp_path / "outcome.json"
    _ = path.write_text(json.dumps({"version": 1, **outcome}))
    pushes: list[tuple[str, str]] = []

    def kuma(loaded: UptimeKumaTargets) -> UptimeKumaClient:
        def handle(request: httpx.Request) -> httpx.Response:
            pushes.append((request.url.params["status"], request.url.params["msg"]))
            return httpx.Response(200, json={"ok": True})

        return UptimeKumaClient(loaded, transport=httpx.MockTransport(handle))

    def client(_endpoint: str) -> ReceiptClient:
        return bucket

    code = execute_finalize(
        {
            "outcome": path,
            "uptime_kuma_targets": targets,
            "archive_bucket": "minos-archive",
            "archive_endpoint": "https://synthetic.invalid",
        },
        client_factory=client,
        kuma_factory=kuma,
    )
    return code, pushes


def _activated(database: str) -> dict[str, object]:
    return {
        "status": "activated",
        "release_id": "icd9-czs-5-90-gen-94-0a1b2c3d4e5f",
        "activation": {
            "database_sha256": database,
            "activated_at": "2026-10-21T14:00:00Z",
        },
    }


def test_receipts_form_a_linked_chain_and_retries_are_idempotent(
    tmp_path: Path,
) -> None:
    bucket = _Bucket()

    first = _finalize(tmp_path, bucket, _activated("a" * 64))
    second = _finalize(tmp_path, bucket, _activated("b" * 64))
    retried = _finalize(tmp_path, bucket, _activated("b" * 64))

    assert [result[0] for result in (first, second, retried)] == [0, 0, 0]
    keys = sorted(bucket.objects)
    assert keys == [
        "deployment-receipts/00000000000000000001.json",
        "deployment-receipts/00000000000000000002.json",
    ]
    one, two = (
        cast(dict[str, object], json.loads(bucket.objects[key])) for key in keys
    )
    assert one["prior_receipt_sha256"] is None
    assert (
        two["prior_receipt_sha256"]
        == hashlib.sha256(bucket.objects[keys[0]]).hexdigest()
    )
    assert (two["kind"], two["sequence"], two["release_manifest_sha256"]) == (
        "data-only",
        2,
        None,
    )
    assert second[1] == [("up", "activated icd9-czs-5-90-gen-94-0a1b2c3d4e5f")]


@pytest.mark.parametrize("damage", ["gap", "tampered_link"])
def test_a_broken_chain_reports_the_receipt_failure(
    tmp_path: Path, damage: str
) -> None:
    bucket = _Bucket()
    _ = _finalize(tmp_path, bucket, _activated("a" * 64))
    if damage == "gap":
        bucket.objects["deployment-receipts/00000000000000000003.json"] = b"{}"
    else:
        key = "deployment-receipts/00000000000000000001.json"
        value = cast(dict[str, object], json.loads(bucket.objects[key]))
        value["sequence"] = 7
        bucket.objects[key] = json.dumps(value).encode()

    code, pushes = _finalize(tmp_path, bucket, _activated("b" * 64))

    assert code == 1
    assert pushes == [("down", "activation failed: receipt")]
    assert len(bucket.objects) == (2 if damage == "gap" else 1)


@pytest.mark.parametrize(
    "outcome,push",
    [
        (
            {"status": "failed", "stage": "activate"},
            ("down", "activation failed: activate"),
        ),
        (
            {"status": "disabled", "release_id": "icd10-rsk-014-0a1b2c3d4e5f"},
            ("up", "disabled icd10-rsk-014-0a1b2c3d4e5f"),
        ),
        ({"status": "idle"}, ("up", "idle")),
    ],
)
def test_outcomes_without_a_receipt_report_their_status(
    tmp_path: Path, outcome: dict[str, object], push: tuple[str, str]
) -> None:
    bucket = _Bucket()

    code, pushes = _finalize(tmp_path, bucket, outcome)

    assert pushes == [push]
    assert code == (1 if push[0] == "down" else 0)
    assert bucket.objects == {}
