"""Strict, canonical JSON boundaries for stored typed reference nodes."""

import json
from dataclasses import asdict

from pydantic import TypeAdapter, ValidationError

from minos.domain.jgp_conditions import Condition, Selector
from minos.domain.reference import ArtifactError

CONDITION = TypeAdapter(Condition)
SELECTOR = TypeAdapter(Selector)
MAX_NODE_BYTES = 65_536


def encode(value: Condition | Selector) -> str:
    return json.dumps(
        asdict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def decode_condition(text: str) -> Condition:
    _bound(text)
    try:
        result = CONDITION.validate_json(text, strict=True)
        _canonical(text, result)
        return result
    except (ValidationError, ValueError, TypeError) as error:
        raise ArtifactError("stored condition payload is incompatible") from error


def decode_selector(text: str) -> Selector:
    _bound(text)
    try:
        result = SELECTOR.validate_json(text, strict=True)
        _canonical(text, result)
        return result
    except (ValidationError, ValueError, TypeError) as error:
        raise ArtifactError("stored selector payload is incompatible") from error


def _bound(text: str) -> None:
    if len(text.encode()) > MAX_NODE_BYTES:
        raise ArtifactError("stored characteristics payload exceeds byte bound")


def _canonical(text: str, result: Condition | Selector) -> None:
    # Dataclass TypeAdapters otherwise ignore extra fields. A canonical shape
    # comparison also rejects omitted defaults and duplicate JSON object keys.
    if text != encode(result):
        raise ArtifactError("stored characteristics payload is not canonical")
