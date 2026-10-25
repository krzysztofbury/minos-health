# pyright: reportMissingTypeStubs=false
"""Deployment receipts for data-only activations (#141 phase C).

Receipts are the `deployment-receipts/<20-digit sequence>.json` chain of
operations.md: each names its predecessor's SHA-256, and a writer verifies the
whole chain from sequence one before it creates the next key with
`If-None-Match: *`. The bucket lock refuses overwrite and delete. Data-only
receipts are unsigned and carry no release manifest; #41 still governs code.
"""

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Protocol, cast

from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)

from minos.domain.reference import SourceError

PREFIX = "deployment-receipts/"
MAX_RECEIPTS = 1000
MAX_RECEIPT_BYTES = 16 * 1024


class _Readable(Protocol):
    def read(self, amount: int, /) -> bytes: ...


class ReceiptClient(Protocol):
    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...


def append_receipt(
    client: ReceiptClient, bucket: str, content: dict[str, object]
) -> int:
    """Append one receipt after verifying the chain; return its sequence.

    The same content already at the head is an idempotent retry, so a receipt
    is never written twice for one activation.
    """
    chain = _chain(client, bucket)
    if chain and _body(chain[-1]).get("activation") == content:
        return len(chain)
    sequence = len(chain) + 1
    receipt = {
        "version": 1,
        "kind": "data-only",
        "sequence": sequence,
        "prior_receipt_sha256": hashlib.sha256(chain[-1]).hexdigest()
        if chain
        else None,
        "release_manifest_sha256": None,
        "activation": content,
    }
    encoded = (
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise SourceError("receipt exceeds its byte bound")
    key = f"{PREFIX}{sequence:020d}.json"
    try:
        _ = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=encoded,
            IfNoneMatch="*",
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=base64.b64encode(hashlib.sha256(encoded).digest()).decode(),
        )
    except ClientError as error:
        response = cast(dict[str, dict[str, object]], error.response)
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        # Another writer took the sequence; only identical bytes are a retry.
        if status not in (409, 412) or _read(client, bucket, key) != encoded:
            raise SourceError("receipt sequence is already taken") from None
    except BotoCoreError:
        raise SourceError("receipt write failed") from None
    return sequence


def _chain(client: ReceiptClient, bucket: str) -> list[bytes]:
    try:
        listed = client.list_objects_v2(
            Bucket=bucket, Prefix=PREFIX, MaxKeys=MAX_RECEIPTS + 1
        )
    except BotoCoreError, ClientError:
        raise SourceError("receipt listing failed") from None
    contents = cast(list[dict[str, object]], listed.get("Contents", []))
    if listed.get("IsTruncated") or len(contents) > MAX_RECEIPTS:
        raise SourceError("receipt chain exceeds its bound")
    keys = sorted(cast(str, item["Key"]) for item in contents)
    expected = [f"{PREFIX}{index:020d}.json" for index in range(1, len(keys) + 1)]
    if keys != expected:
        raise SourceError("receipt chain has a gap or a foreign key")
    chain: list[bytes] = []
    for index, key in enumerate(keys, 1):
        content = _read(client, bucket, key)
        body = _body(content)
        prior = hashlib.sha256(chain[-1]).hexdigest() if chain else None
        if body.get("sequence") != index or body.get("prior_receipt_sha256") != prior:
            raise SourceError("receipt chain link differs")
        chain.append(content)
    return chain


def _read(client: ReceiptClient, bucket: str, key: str) -> bytes:
    try:
        response = client.get_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
        stream = cast(_Readable, response["Body"])
        content = stream.read(MAX_RECEIPT_BYTES + 1)
    except BotoCoreError, ClientError, KeyError:
        raise SourceError("receipt read failed") from None
    if len(content) > MAX_RECEIPT_BYTES:
        raise SourceError("receipt exceeds its byte bound")
    return content


def _body(content: bytes) -> dict[str, object]:
    try:
        value = cast(object, json.loads(content))
    except ValueError:
        raise SourceError("receipt is not JSON") from None
    if not isinstance(value, dict):
        raise SourceError("receipt is not an object")
    return cast(dict[str, object], value)
