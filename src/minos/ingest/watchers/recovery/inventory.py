import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Protocol, TypeIs, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from minos.ingest.watchers.recovery.inventory_signing import (
    InventorySigner,
    InventorySigningError,
    InventoryVerifier,
)
from minos.ingest.watchers.recovery.keys import parse_recovery_key
from minos.ingest.watchers.recovery.models import RecoveryVerificationError
from minos.ingest.watchers.recovery.trusted_keys import (
    TrustedRecoveryKeyError,
    parse_recovery_inventory_key,
    parse_trusted_recovery_key,
    recovery_inventory_key,
    trusted_recovery_key,
)

MAX_INVENTORY_OBJECTS = 4_096
MAX_INVENTORY_BYTES = 8 * 1024
MAX_RECOVERY_OBJECT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_TIMESTAMP_MS = 253_402_300_799_999
_MAX_SEQUENCE = 10**20 - 1
_SHA256_CHARACTERS = frozenset("0123456789abcdef")


class RecoveryInventoryError(RuntimeError):
    """An operator recovery inventory operation could not be proven safe."""


class RecoveryInventoryFormatError(RecoveryInventoryError):
    """Recovery inventory bytes or keys are not canonical."""


class RecoveryInventoryChainError(RecoveryInventoryError):
    """The complete recovery inventory history is not one verified chain."""


class RecoveryInventoryStorageError(RecoveryInventoryError):
    """Operator recovery storage did not complete an operation safely."""


class RecoveryInventorySelectionError(RecoveryInventoryError):
    """No trusted image satisfies the compromise-recovery policy."""


class ConditionalWriteResult(Enum):
    CREATED = "created"
    PRECONDITION_FAILED = "precondition_failed"


class RecoveryInventory(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    sequence: int = Field(ge=1, le=_MAX_SEQUENCE)
    prior_inventory_sha256: str | None
    trusted_object_key: str = Field(min_length=1, max_length=128)
    trusted_object_version_id: str = Field(min_length=1, max_length=1024)
    trusted_object_checksum_sha256: str = Field(min_length=64, max_length=64)
    trusted_object_revision: int = Field(ge=0, le=_MAX_SEQUENCE)
    trusted_object_size_bytes: int = Field(ge=1, le=MAX_RECOVERY_OBJECT_BYTES)
    trusted_object_last_modified_ms: int = Field(ge=1, le=_MAX_TIMESTAMP_MS)
    signature: str = Field(min_length=1, max_length=4 * 1024)

    @model_validator(mode="after")
    def validate_inventory_identity(self) -> RecoveryInventory:
        _validate_sha256(self.trusted_object_checksum_sha256)
        if self.prior_inventory_sha256 is not None:
            _validate_sha256(self.prior_inventory_sha256)
        parsed = parse_trusted_recovery_key(self.trusted_object_key)
        if parsed.sequence != self.sequence:
            raise ValueError("trusted object sequence differs")
        if parsed.sha256 != self.trusted_object_checksum_sha256:
            raise ValueError("trusted object checksum differs")
        if self.sequence == 1 and self.prior_inventory_sha256 is not None:
            raise ValueError("first inventory cannot have a predecessor")
        if self.sequence > 1 and self.prior_inventory_sha256 is None:
            raise ValueError("later inventory requires a predecessor")
        _validate_version_id(self.trusted_object_version_id)
        return self


@dataclass(frozen=True, slots=True)
class ListedInventoryObject:
    key: str


@dataclass(frozen=True, slots=True)
class BoundedInventoryListing:
    objects: tuple[ListedInventoryObject, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class VerifiedRecoverySource:
    key: str
    version_id: str
    sha256: str
    size_bytes: int
    state_revision: int


@dataclass(frozen=True, slots=True)
class TrustedCopyRequest:
    source: VerifiedRecoverySource
    destination_key: str


@dataclass(frozen=True, slots=True)
class TrustedObjectIdentity:
    key: str
    version_id: str
    sha256: str
    size_bytes: int
    last_modified_ms: int


@dataclass(frozen=True, slots=True)
class VerifiedInventory:
    key: str
    sha256: str
    inventory: RecoveryInventory


@dataclass(frozen=True, slots=True)
class VerifiedInventoryChain:
    inventories: tuple[VerifiedInventory, ...]

    @property
    def head(self) -> VerifiedInventory | None:
        return self.inventories[-1] if self.inventories else None


class RecoveryInventoryStorage(Protocol):
    def list_inventories(self, *, maximum_objects: int) -> BoundedInventoryListing: ...

    def read_inventory(self, key: str, *, maximum_bytes: int) -> bytes: ...

    def copy_trusted_if_absent(
        self, request: TrustedCopyRequest
    ) -> ConditionalWriteResult: ...

    def head_trusted(self, key: str) -> TrustedObjectIdentity: ...

    def put_inventory_if_absent(
        self, key: str, content: bytes
    ) -> ConditionalWriteResult: ...


def canonical_inventory_payload_bytes(inventory: RecoveryInventory) -> bytes:
    payload = inventory.model_dump(exclude={"signature"}, mode="json")
    return _canonical_json(payload)


def canonical_inventory_bytes(inventory: RecoveryInventory) -> bytes:
    return _canonical_json(inventory.model_dump(mode="json"))


def parse_inventory(content: bytes) -> RecoveryInventory:
    if len(content) < 1:
        raise RecoveryInventoryFormatError("recovery inventory content is invalid")
    if len(content) > MAX_INVENTORY_BYTES:
        raise RecoveryInventoryFormatError("recovery inventory exceeds its byte bound")
    try:
        decoded = content.decode("utf-8")
        raw = cast(
            object,
            json.loads(
                decoded,
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_json_constant,
            ),
        )
        if not isinstance(raw, Mapping):
            raise ValueError("inventory root is not an object")
        inventory = RecoveryInventory.model_validate(cast(Mapping[str, object], raw))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
        TrustedRecoveryKeyError,
    ):
        raise RecoveryInventoryFormatError(
            "recovery inventory content is invalid"
        ) from None
    if canonical_inventory_bytes(inventory) != content:
        raise RecoveryInventoryFormatError("recovery inventory is not canonical")
    return inventory


def verify_inventory_chain(
    storage: RecoveryInventoryStorage,
    verifier: InventoryVerifier,
    *,
    maximum_objects: int,
) -> VerifiedInventoryChain:
    _validate_object_bound(maximum_objects)
    listing = _list_inventories(storage, maximum_objects)
    if not listing.complete or len(listing.objects) > maximum_objects:
        raise RecoveryInventoryChainError("recovery inventory listing is incomplete")
    by_sequence: dict[int, ListedInventoryObject] = {}
    for listed in listing.objects:
        try:
            sequence = parse_recovery_inventory_key(listed.key)
        except TrustedRecoveryKeyError:
            raise RecoveryInventoryChainError(
                "recovery inventory key is invalid"
            ) from None
        if sequence in by_sequence:
            raise RecoveryInventoryChainError("recovery inventory sequence conflicts")
        by_sequence[sequence] = listed
    if sorted(by_sequence) != list(range(1, len(by_sequence) + 1)):
        raise RecoveryInventoryChainError("recovery inventory chain has a gap")
    verified = _read_and_verify_inventories(storage, verifier, by_sequence)
    _verify_digest_links(verified)
    return VerifiedInventoryChain(tuple(verified))


def derive_next_sequence(chain: VerifiedInventoryChain) -> int:
    head = chain.head
    if head is None:
        return 1
    if head.inventory.sequence >= _MAX_SEQUENCE:
        raise RecoveryInventoryChainError("recovery inventory sequence is exhausted")
    return head.inventory.sequence + 1


def append_trusted_recovery(
    storage: RecoveryInventoryStorage,
    signer: InventorySigner,
    verifier: InventoryVerifier,
    source: VerifiedRecoverySource,
    *,
    maximum_objects: int,
) -> VerifiedInventoryChain:
    _validate_source(source)
    before = verify_inventory_chain(storage, verifier, maximum_objects=maximum_objects)
    if len(before.inventories) >= maximum_objects:
        raise RecoveryInventoryChainError("recovery inventory bound is exhausted")
    sequence = derive_next_sequence(before)
    head = before.head
    if (
        head is not None
        and source.state_revision < head.inventory.trusted_object_revision
    ):
        raise RecoveryInventoryChainError("trusted recovery revision would decrease")
    destination_key = trusted_recovery_key(sequence, source.sha256)
    _copy_trusted(storage, TrustedCopyRequest(source, destination_key))
    trusted = _head_trusted(storage, destination_key)
    _verify_trusted_identity(trusted, source, destination_key)
    unsigned = _unsigned_inventory(before, trusted, source.state_revision, sequence)
    payload = canonical_inventory_payload_bytes(unsigned)
    signature = _sign(signer, payload)
    if not _verify_signature(verifier, payload, signature):
        raise InventorySigningError("new recovery inventory signature is invalid")
    inventory = unsigned.model_copy(update={"signature": signature})
    content = canonical_inventory_bytes(inventory)
    key = recovery_inventory_key(sequence)
    outcome = _put_inventory(storage, key, content)
    if outcome is ConditionalWriteResult.PRECONDITION_FAILED:
        existing = _read_inventory(storage, key)
        if existing != content:
            raise RecoveryInventoryStorageError(
                "conditional recovery inventory write conflicts"
            )
    elif outcome is not ConditionalWriteResult.CREATED:
        raise RecoveryInventoryStorageError(
            "conditional recovery inventory write returned an invalid result"
        )
    after = verify_inventory_chain(storage, verifier, maximum_objects=maximum_objects)
    if not any(
        item.key == key and canonical_inventory_bytes(item.inventory) == content
        for item in after.inventories
    ):
        raise RecoveryInventoryChainError(
            "written recovery inventory is not in the chain"
        )
    return after


def select_pre_cutoff_trusted_image(
    chain: VerifiedInventoryChain, *, cutoff_ms: int
) -> RecoveryInventory:
    if not _is_strict_integer(cutoff_ms) or cutoff_ms < 1:
        raise RecoveryInventorySelectionError("compromise cutoff is required")
    if cutoff_ms > _MAX_TIMESTAMP_MS:
        raise RecoveryInventorySelectionError("compromise cutoff is invalid")
    selected: RecoveryInventory | None = None
    for verified in chain.inventories:
        candidate = verified.inventory
        if candidate.trusted_object_last_modified_ms >= cutoff_ms:
            continue
        if selected is None or (
            candidate.trusted_object_revision,
            candidate.sequence,
        ) > (selected.trusted_object_revision, selected.sequence):
            selected = candidate
    if selected is None:
        raise RecoveryInventorySelectionError(
            "no trusted recovery image predates the compromise cutoff"
        )
    return selected


def _read_and_verify_inventories(
    storage: RecoveryInventoryStorage,
    verifier: InventoryVerifier,
    by_sequence: Mapping[int, ListedInventoryObject],
) -> list[VerifiedInventory]:
    verified: list[VerifiedInventory] = []
    for sequence in range(1, len(by_sequence) + 1):
        listed = by_sequence[sequence]
        content = _read_inventory(storage, listed.key)
        inventory = parse_inventory(content)
        if inventory.sequence != sequence:
            raise RecoveryInventoryChainError("recovery inventory sequence differs")
        payload = canonical_inventory_payload_bytes(inventory)
        if not _verify_signature(verifier, payload, inventory.signature):
            raise RecoveryInventoryChainError("recovery inventory signature is invalid")
        verified.append(
            VerifiedInventory(
                listed.key,
                hashlib.sha256(content).hexdigest(),
                inventory,
            )
        )
    return verified


def _verify_digest_links(inventories: Sequence[VerifiedInventory]) -> None:
    if not inventories:
        return
    if inventories[0].inventory.prior_inventory_sha256 is not None:
        raise RecoveryInventoryChainError("first recovery inventory has a link")
    predecessor_children: set[str] = set()
    for index in range(1, len(inventories)):
        previous = inventories[index - 1].inventory
        current = inventories[index].inventory
        prior = inventories[index].inventory.prior_inventory_sha256
        expected = inventories[index - 1].sha256
        if prior in predecessor_children:
            raise RecoveryInventoryChainError("recovery inventory has multiple heads")
        if prior is None or prior != expected:
            raise RecoveryInventoryChainError("recovery inventory link is invalid")
        if current.trusted_object_revision < previous.trusted_object_revision:
            raise RecoveryInventoryChainError("trusted recovery revision decreases")
        predecessor_children.add(prior)


def _unsigned_inventory(
    before: VerifiedInventoryChain,
    trusted: TrustedObjectIdentity,
    state_revision: int,
    sequence: int,
) -> RecoveryInventory:
    head = before.head
    return RecoveryInventory(
        sequence=sequence,
        prior_inventory_sha256=head.sha256 if head is not None else None,
        trusted_object_key=trusted.key,
        trusted_object_version_id=trusted.version_id,
        trusted_object_checksum_sha256=trusted.sha256,
        trusted_object_revision=state_revision,
        trusted_object_size_bytes=trusted.size_bytes,
        trusted_object_last_modified_ms=trusted.last_modified_ms,
        signature="pending",
    )


def _validate_source(source: VerifiedRecoverySource) -> None:
    try:
        _validate_sha256(source.sha256)
        _validate_version_id(source.version_id)
        parsed = parse_recovery_key(source.key)
    except ValueError, RecoveryInventoryFormatError, RecoveryVerificationError:
        raise RecoveryInventoryFormatError(
            "verified recovery source is invalid"
        ) from None
    if parsed.sha256 != source.sha256 or parsed.state_revision != source.state_revision:
        raise RecoveryInventoryFormatError("verified recovery source is invalid")
    if not _is_strict_integer(source.size_bytes):
        raise RecoveryInventoryFormatError("verified recovery source is invalid")
    if source.size_bytes < 1 or source.size_bytes > MAX_RECOVERY_OBJECT_BYTES:
        raise RecoveryInventoryFormatError("verified recovery source is invalid")
    if not _is_strict_integer(source.state_revision):
        raise RecoveryInventoryFormatError("verified recovery source is invalid")
    if source.state_revision < 0 or source.state_revision > _MAX_SEQUENCE:
        raise RecoveryInventoryFormatError("verified recovery source is invalid")


def _verify_trusted_identity(
    trusted: TrustedObjectIdentity,
    source: VerifiedRecoverySource,
    expected_key: str,
) -> None:
    try:
        parsed = parse_trusted_recovery_key(trusted.key)
        _validate_version_id(trusted.version_id)
        _validate_sha256(trusted.sha256)
    except TrustedRecoveryKeyError, ValueError, RecoveryInventoryFormatError:
        raise RecoveryInventoryStorageError(
            "trusted recovery identity is invalid"
        ) from None
    if trusted.key != expected_key or parsed.sha256 != trusted.sha256:
        raise RecoveryInventoryStorageError("trusted recovery identity differs")
    if not _is_strict_integer(trusted.size_bytes):
        raise RecoveryInventoryStorageError("trusted recovery byte count is invalid")
    if trusted.sha256 != source.sha256 or trusted.size_bytes != source.size_bytes:
        raise RecoveryInventoryStorageError("trusted recovery bytes differ")
    if not _is_strict_integer(trusted.last_modified_ms):
        raise RecoveryInventoryStorageError("trusted recovery timestamp is invalid")
    if trusted.last_modified_ms < 1 or trusted.last_modified_ms > _MAX_TIMESTAMP_MS:
        raise RecoveryInventoryStorageError("trusted recovery timestamp is invalid")


def _list_inventories(
    storage: RecoveryInventoryStorage, maximum_objects: int
) -> BoundedInventoryListing:
    try:
        listing = storage.list_inventories(maximum_objects=maximum_objects)
    except Exception:
        raise RecoveryInventoryStorageError(
            "recovery inventory listing failed"
        ) from None
    return listing


def _read_inventory(storage: RecoveryInventoryStorage, key: str) -> bytes:
    try:
        content = storage.read_inventory(key, maximum_bytes=MAX_INVENTORY_BYTES)
    except Exception:
        raise RecoveryInventoryStorageError("recovery inventory read failed") from None
    if len(content) > MAX_INVENTORY_BYTES:
        raise RecoveryInventoryStorageError("recovery inventory read exceeds its bound")
    return content


def _copy_trusted(
    storage: RecoveryInventoryStorage, request: TrustedCopyRequest
) -> None:
    try:
        outcome = storage.copy_trusted_if_absent(request)
    except Exception:
        raise RecoveryInventoryStorageError("trusted recovery copy failed") from None
    if outcome not in (
        ConditionalWriteResult.CREATED,
        ConditionalWriteResult.PRECONDITION_FAILED,
    ):
        raise RecoveryInventoryStorageError(
            "conditional trusted recovery copy returned an invalid result"
        )


def _head_trusted(storage: RecoveryInventoryStorage, key: str) -> TrustedObjectIdentity:
    try:
        identity = storage.head_trusted(key)
    except Exception:
        raise RecoveryInventoryStorageError(
            "trusted recovery verification failed"
        ) from None
    return identity


def _put_inventory(
    storage: RecoveryInventoryStorage, key: str, content: bytes
) -> ConditionalWriteResult:
    try:
        return storage.put_inventory_if_absent(key, content)
    except Exception:
        raise RecoveryInventoryStorageError("recovery inventory write failed") from None


def _sign(signer: InventorySigner, content: bytes) -> str:
    try:
        signature = signer.sign(content)
    except Exception:
        raise InventorySigningError("recovery inventory signing failed") from None
    if len(signature) < 1 or len(signature) > 4 * 1024:
        raise InventorySigningError("recovery inventory signature is invalid")
    return signature


def _verify_signature(
    verifier: InventoryVerifier, content: bytes, signature: str
) -> bool:
    try:
        result = verifier.verify(content, signature)
    except Exception:
        raise InventorySigningError("recovery inventory verification failed") from None
    return result


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _validate_sha256(value: str) -> None:
    if len(value) != 64 or any(
        character not in _SHA256_CHARACTERS for character in value
    ):
        raise RecoveryInventoryFormatError("recovery inventory checksum is invalid")


def _validate_version_id(value: str) -> None:
    if len(value) < 1 or len(value) > 1024:
        raise RecoveryInventoryFormatError("trusted recovery version is invalid")
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in value):
        raise RecoveryInventoryFormatError("trusted recovery version is invalid")


def _validate_object_bound(maximum_objects: object) -> None:
    if not _is_strict_integer(maximum_objects):
        raise RecoveryInventoryChainError("recovery inventory bound is invalid")
    if maximum_objects < 1 or maximum_objects > MAX_INVENTORY_OBJECTS:
        raise RecoveryInventoryChainError("recovery inventory bound is invalid")


def _is_strict_integer(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)
