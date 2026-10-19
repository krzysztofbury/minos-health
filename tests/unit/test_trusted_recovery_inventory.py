import hashlib
import json
import subprocess
from pathlib import Path
from typing import cast, final

import pytest

from minos.ingest.watchers.recovery.inventory import (
    MAX_INVENTORY_BYTES,
    MAX_INVENTORY_OBJECTS,
    BoundedInventoryListing,
    ConditionalWriteResult,
    ListedInventoryObject,
    RecoveryInventory,
    RecoveryInventoryChainError,
    RecoveryInventoryFormatError,
    RecoveryInventorySelectionError,
    RecoveryInventoryStorageError,
    TrustedCopyRequest,
    TrustedObjectIdentity,
    VerifiedInventory,
    VerifiedInventoryChain,
    VerifiedRecoverySource,
    append_trusted_recovery,
    canonical_inventory_bytes,
    canonical_inventory_payload_bytes,
    derive_next_sequence,
    parse_inventory,
    select_pre_cutoff_trusted_image,
    verify_inventory_chain,
)
from minos.ingest.watchers.recovery.inventory_signing import (
    RECOVERY_INVENTORY_NAMESPACE,
    SSH_KEYGEN_PATH,
    InventorySigningError,
    SshKeygenEd25519Signer,
    SshKeygenEd25519Verifier,
)
from minos.ingest.watchers.recovery.trusted_keys import (
    TrustedRecoveryKeyError,
    parse_recovery_inventory_key,
    parse_trusted_recovery_key,
    recovery_inventory_key,
    trusted_recovery_key,
)

_CREATED_AT_MS = 1_758_225_600_000
_SHA256_A = "a" * 64
_SHA256_B = "b" * 64


@final
class _DigestSigner:
    def sign(self, content: bytes) -> str:
        return "test-signature:" + hashlib.sha256(content).hexdigest()


@final
class _DigestVerifier:
    def __init__(self, *, accept: bool = True) -> None:
        self.accept = accept

    def verify(self, content: bytes, signature: str) -> bool:
        return self.accept and signature == _DigestSigner().sign(content)


@final
class _FakeStorage:
    def __init__(self) -> None:
        self.inventories: dict[str, bytes] = {}
        self.trusted: dict[str, TrustedObjectIdentity] = {}
        self.listing_override: BoundedInventoryListing | None = None
        self.list_calls = 0
        self.copy_calls: list[TrustedCopyRequest] = []
        self.race_copy = False
        self.race_put_same = False
        self.race_put_conflict = False
        self.put_calls = 0
        self.failure: BaseException | None = None

    def list_inventories(self, *, maximum_objects: int) -> BoundedInventoryListing:
        self._raise_failure()
        self.list_calls += 1
        if self.listing_override is not None:
            return self.listing_override
        listed = tuple(ListedInventoryObject(key) for key in sorted(self.inventories))
        return BoundedInventoryListing(
            listed[:maximum_objects], len(listed) <= maximum_objects
        )

    def read_inventory(self, key: str, *, maximum_bytes: int) -> bytes:
        self._raise_failure()
        content = self.inventories[key]
        assert len(content) <= maximum_bytes
        return content

    def copy_trusted_if_absent(
        self, request: TrustedCopyRequest
    ) -> ConditionalWriteResult:
        self._raise_failure()
        self.copy_calls.append(request)
        if request.destination_key in self.trusted:
            return ConditionalWriteResult.PRECONDITION_FAILED
        source = request.source
        sequence = parse_trusted_recovery_key(request.destination_key).sequence
        self.trusted[request.destination_key] = TrustedObjectIdentity(
            request.destination_key,
            source.sha256,
            source.size_bytes,
            _CREATED_AT_MS + sequence,
        )
        if self.race_copy:
            return ConditionalWriteResult.PRECONDITION_FAILED
        return ConditionalWriteResult.CREATED

    def head_trusted(self, key: str) -> TrustedObjectIdentity:
        self._raise_failure()
        return self.trusted[key]

    def put_inventory_if_absent(
        self, key: str, content: bytes
    ) -> ConditionalWriteResult:
        self._raise_failure()
        self.put_calls += 1
        if self.race_put_conflict:
            self.inventories[key] = content + b"\n"
            return ConditionalWriteResult.PRECONDITION_FAILED
        if self.race_put_same:
            self.inventories[key] = content
            return ConditionalWriteResult.PRECONDITION_FAILED
        if key in self.inventories:
            return ConditionalWriteResult.PRECONDITION_FAILED
        self.inventories[key] = content
        return ConditionalWriteResult.CREATED

    def _raise_failure(self) -> None:
        if self.failure is not None:
            raise self.failure


def test_operator_key_grammars_are_strict_and_sequence_only() -> None:
    trusted = trusted_recovery_key(7, _SHA256_A)
    inventory = recovery_inventory_key(7)
    assert trusted == f"trusted-recovery/{7:020d}/{_SHA256_A}.sqlite3"
    assert parse_trusted_recovery_key(trusted).sequence == 7
    assert inventory == f"recovery-inventories/{7:020d}.json"
    assert parse_recovery_inventory_key(inventory) == 7

    malformed = (
        f"trusted-recovery/7/{_SHA256_A}.sqlite3",
        f"trusted-recovery/{7:020d}/{_SHA256_A.upper()}.sqlite3",
        f"trusted-recovery/{0:020d}/{_SHA256_A}.sqlite3",
        f"trusted-recovery/{7:020d}/{_SHA256_A}.sqlite3/extra",
        "recovery-inventories/7.json",
        f"recovery-inventories/{7:020d}/{_SHA256_A}.json",
        f"recovery-inventories/{0:020d}.json",
    )
    for key in malformed:
        with pytest.raises(TrustedRecoveryKeyError):
            if key.startswith("trusted-recovery"):
                _ = parse_trusted_recovery_key(key)
            else:
                _ = parse_recovery_inventory_key(key)


def test_canonical_inventory_parser_rejects_malformed_and_ambiguous_json() -> None:
    inventory = _signed_inventory(1, None, _SHA256_A)
    canonical = canonical_inventory_bytes(inventory)
    assert parse_inventory(canonical) == inventory
    assert canonical == canonical_inventory_bytes(parse_inventory(canonical))

    decoded = cast(object, json.loads(canonical))
    assert isinstance(decoded, dict)
    decoded["unexpected"] = True
    extra = json.dumps(decoded, separators=(",", ":"), sort_keys=True).encode()
    duplicate = canonical[:-1] + b',"sequence":1}'
    noncanonical = b" " + canonical
    wrong_type = canonical.replace(b'"sequence":1', b'"sequence":true')
    wrong_key = canonical.replace(
        f"trusted-recovery/{1:020d}".encode(),
        f"trusted-recovery/{2:020d}".encode(),
    )
    for content in (
        b"{",
        b"[]",
        extra,
        duplicate,
        noncanonical,
        wrong_type,
        wrong_key,
        b"x" * (MAX_INVENTORY_BYTES + 1),
    ):
        with pytest.raises(RecoveryInventoryFormatError):
            _ = parse_inventory(content)


def test_append_copies_signs_writes_and_reverifies_contiguous_chain() -> None:
    storage = _FakeStorage()
    verifier = _DigestVerifier()
    first = append_trusted_recovery(
        storage,
        _DigestSigner(),
        verifier,
        _source(_SHA256_A, revision=11),
        maximum_objects=4,
    )
    second = append_trusted_recovery(
        storage,
        _DigestSigner(),
        verifier,
        _source(_SHA256_B, revision=12),
        maximum_objects=4,
    )

    assert storage.list_calls == 4
    assert [item.inventory.sequence for item in second.inventories] == [1, 2]
    assert second.inventories[1].inventory.prior_inventory_sha256 == (
        first.inventories[0].sha256
    )
    assert second.inventories[1].inventory.trusted_object_revision == 12
    assert verify_inventory_chain(storage, verifier, maximum_objects=4) == second


def test_new_signature_is_verified_before_inventory_write() -> None:
    storage = _FakeStorage()
    with pytest.raises(InventorySigningError, match="signature is invalid"):
        _ = append_trusted_recovery(
            storage,
            _DigestSigner(),
            _DigestVerifier(accept=False),
            _source(_SHA256_A, revision=11),
            maximum_objects=2,
        )
    assert storage.put_calls == 0


def test_revisions_are_monotonic_before_copy_and_while_verifying() -> None:
    first = _signed_inventory(1, None, _SHA256_A, revision=11)
    first_content = canonical_inventory_bytes(first)
    decreasing = _signed_inventory(
        2,
        hashlib.sha256(first_content).hexdigest(),
        _SHA256_B,
        revision=10,
    )
    storage = _storage_with(first, decreasing)
    with pytest.raises(RecoveryInventoryChainError, match="revision decreases"):
        _ = verify_inventory_chain(storage, _DigestVerifier(), maximum_objects=2)

    storage = _storage_with(first)
    with pytest.raises(RecoveryInventoryChainError, match="would decrease"):
        _ = append_trusted_recovery(
            storage,
            _DigestSigner(),
            _DigestVerifier(),
            _source(_SHA256_B, revision=10),
            maximum_objects=2,
        )
    assert storage.copy_calls == []


def test_exact_412_is_idempotent_but_concurrent_conflict_fails_closed() -> None:
    storage = _FakeStorage()
    storage.race_copy = True
    storage.race_put_same = True
    chain = append_trusted_recovery(
        storage,
        _DigestSigner(),
        _DigestVerifier(),
        _source(_SHA256_A, revision=1),
        maximum_objects=2,
    )
    assert chain.head is not None
    assert chain.head.inventory.sequence == 1

    conflicting = _FakeStorage()
    conflicting.race_put_conflict = True
    with pytest.raises(RecoveryInventoryStorageError, match="conflicts"):
        _ = append_trusted_recovery(
            conflicting,
            _DigestSigner(),
            _DigestVerifier(),
            _source(_SHA256_A, revision=1),
            maximum_objects=2,
        )

    wrong_copy = _FakeStorage()
    destination = trusted_recovery_key(1, _SHA256_A)
    wrong_copy.trusted[destination] = TrustedObjectIdentity(
        destination,
        _SHA256_A,
        999,
        _CREATED_AT_MS,
    )
    with pytest.raises(RecoveryInventoryStorageError, match="bytes differ"):
        _ = append_trusted_recovery(
            wrong_copy,
            _DigestSigner(),
            _DigestVerifier(),
            _source(_SHA256_A, revision=1),
            maximum_objects=2,
        )


def test_chain_rejects_gaps_duplicates_conflicts_links_and_signatures() -> None:
    first = _signed_inventory(1, None, _SHA256_A)
    first_content = canonical_inventory_bytes(first)
    first_digest = hashlib.sha256(first_content).hexdigest()
    second = _signed_inventory(2, first_digest, _SHA256_B)
    storage = _storage_with(first, second)
    assert (
        len(
            verify_inventory_chain(
                storage, _DigestVerifier(), maximum_objects=3
            ).inventories
        )
        == 2
    )

    storage.listing_override = BoundedInventoryListing(
        (
            ListedInventoryObject(recovery_inventory_key(1)),
            ListedInventoryObject(recovery_inventory_key(1)),
        ),
        True,
    )
    with pytest.raises(RecoveryInventoryChainError, match="conflicts"):
        _ = verify_inventory_chain(storage, _DigestVerifier(), maximum_objects=3)

    gap = _FakeStorage()
    gap.inventories[recovery_inventory_key(1)] = first_content
    gap.inventories[recovery_inventory_key(3)] = canonical_inventory_bytes(
        _signed_inventory(3, first_digest, "c" * 64)
    )
    with pytest.raises(RecoveryInventoryChainError, match="gap"):
        _ = verify_inventory_chain(gap, _DigestVerifier(), maximum_objects=3)

    storage = _storage_with(first, second)
    with pytest.raises(RecoveryInventoryChainError, match="signature"):
        _ = verify_inventory_chain(
            storage, _DigestVerifier(accept=False), maximum_objects=3
        )

    bad_link = _storage_with(
        first,
        _signed_inventory(2, "f" * 64, _SHA256_B),
    )
    with pytest.raises(RecoveryInventoryChainError, match="link"):
        _ = verify_inventory_chain(bad_link, _DigestVerifier(), maximum_objects=3)

    third_branch = _signed_inventory(3, first_digest, "c" * 64)
    branch = _storage_with(first, second, third_branch)
    with pytest.raises(RecoveryInventoryChainError, match="multiple heads"):
        _ = verify_inventory_chain(branch, _DigestVerifier(), maximum_objects=3)

    wrong_sequence = _signed_inventory(2, first_digest, _SHA256_B)
    sequence_conflict = _FakeStorage()
    sequence_conflict.inventories[recovery_inventory_key(1)] = (
        canonical_inventory_bytes(wrong_sequence)
    )
    with pytest.raises(RecoveryInventoryChainError, match="sequence differs"):
        _ = verify_inventory_chain(
            sequence_conflict, _DigestVerifier(), maximum_objects=1
        )


def test_listing_and_sequence_bounds_fail_before_operator_writes() -> None:
    storage = _FakeStorage()
    storage.listing_override = BoundedInventoryListing((), False)
    with pytest.raises(RecoveryInventoryChainError, match="incomplete"):
        _ = verify_inventory_chain(storage, _DigestVerifier(), maximum_objects=1)
    for bound in (0, True, MAX_INVENTORY_OBJECTS + 1):
        with pytest.raises(RecoveryInventoryChainError, match="bound"):
            _ = verify_inventory_chain(
                storage, _DigestVerifier(), maximum_objects=bound
            )

    full = _storage_with(_signed_inventory(1, None, _SHA256_A))
    with pytest.raises(RecoveryInventoryChainError, match="exhausted"):
        _ = append_trusted_recovery(
            full,
            _DigestSigner(),
            _DigestVerifier(),
            _source(_SHA256_B, revision=2),
            maximum_objects=1,
        )
    assert full.copy_calls == []

    maximum = _signed_inventory(1, None, _SHA256_A).model_copy(
        update={"sequence": 10**20 - 1}
    )
    synthetic = VerifiedInventory("key", "d" * 64, maximum)
    with pytest.raises(RecoveryInventoryChainError, match="sequence is exhausted"):
        _ = derive_next_sequence(VerifiedInventoryChain((synthetic,)))


def test_suspected_compromise_requires_strict_pre_cutoff_trusted_image() -> None:
    first = _signed_inventory(
        1,
        None,
        _SHA256_A,
        last_modified_ms=100,
        revision=19,
    )
    first_content = canonical_inventory_bytes(first)
    second = _signed_inventory(
        2,
        hashlib.sha256(first_content).hexdigest(),
        _SHA256_B,
        last_modified_ms=200,
        revision=20,
    )
    second_content = canonical_inventory_bytes(second)
    third = _signed_inventory(
        3,
        hashlib.sha256(second_content).hexdigest(),
        "c" * 64,
        last_modified_ms=198,
        revision=20,
    )
    chain = verify_inventory_chain(
        _storage_with(first, second, third), _DigestVerifier(), maximum_objects=3
    )
    assert select_pre_cutoff_trusted_image(chain, cutoff_ms=201).sequence == 3
    assert select_pre_cutoff_trusted_image(chain, cutoff_ms=198).sequence == 1
    with pytest.raises(RecoveryInventorySelectionError, match="required"):
        _ = select_pre_cutoff_trusted_image(chain, cutoff_ms=0)
    with pytest.raises(RecoveryInventorySelectionError, match="predates"):
        _ = select_pre_cutoff_trusted_image(chain, cutoff_ms=100)


def test_canonical_inventory_wire_payload_and_hash_vectors_are_frozen() -> None:
    inventory = _signed_inventory(1, None, _SHA256_A, revision=42)
    payload = canonical_inventory_payload_bytes(inventory)
    wire = canonical_inventory_bytes(inventory)

    assert payload == (
        b'{"prior_inventory_sha256":null,"sequence":1,'
        b'"trusted_object_checksum_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        b'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","trusted_object_key":"trusted-recovery/'
        b"00000000000000000001/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        b'aaaaaaaaaaaaaaaa.sqlite3","trusted_object_last_modified_ms":1758225600000,'
        b'"trusted_object_revision":42,"trusted_object_size_bytes":4096}'
    )
    assert hashlib.sha256(payload).hexdigest() == (
        "c1a662f82234cf4b60a47c5a5fdee397cf9da81cddf8b4442780179b7cd0ba94"
    )
    assert inventory.signature == (
        "test-signature:c1a662f82234cf4b60a47c5a5fdee397cf9da81cddf8b4442780179b7cd0ba94"
    )
    assert wire == (
        b'{"prior_inventory_sha256":null,"sequence":1,"signature":'
        b'"test-signature:c1a662f82234cf4b60a47c5a5fdee397cf9da81cddf8b4442780179b7cd0ba94",'
        b'"trusted_object_checksum_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        b'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","trusted_object_key":"trusted-recovery/'
        b"00000000000000000001/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        b'aaaaaaaaaaaaaaaa.sqlite3","trusted_object_last_modified_ms":1758225600000,'
        b'"trusted_object_revision":42,"trusted_object_size_bytes":4096}'
    )
    assert hashlib.sha256(wire).hexdigest() == (
        "4d95d2c79f568b484dc6cbc0c50ff5e853efef9c62126a4ddf3f4a91463585e6"
    )


def test_storage_and_signing_errors_do_not_disclose_paths_or_secrets() -> None:
    storage = _FakeStorage()
    storage.failure = RuntimeError("/private/operator/path super-secret-value")
    with pytest.raises(RecoveryInventoryStorageError) as captured:
        _ = verify_inventory_chain(storage, _DigestVerifier(), maximum_objects=1)
    assert "/private/operator/path" not in str(captured.value)
    assert "super-secret-value" not in str(captured.value)

    secret_path = "/private/operator/signing-key"
    with pytest.raises(InventorySigningError) as signing_error:
        _ = SshKeygenEd25519Signer(secret_path).sign(b"inventory")
    assert secret_path not in str(signing_error.value)


def test_pinned_ssh_keygen_round_trip_uses_ed25519_and_dedicated_namespace(
    tmp_path: Path,
) -> None:
    private_key = tmp_path / "operator-key"
    _ = subprocess.run(
        (
            SSH_KEYGEN_PATH,
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(private_key),
        ),
        check=True,
        shell=False,
    )
    public_key = private_key.with_suffix(".pub").read_text(encoding="ascii")
    signer = SshKeygenEd25519Signer(private_key)
    verifier = SshKeygenEd25519Verifier(public_key)
    content = b'{"operator":"recovery"}'
    signature = signer.sign(content)

    assert RECOVERY_INVENTORY_NAMESPACE == "minos-health-recovery-inventory-v1"
    assert verifier.verify(content, signature)
    assert not verifier.verify(content + b"!", signature)


def _source(sha256: str, *, revision: int) -> VerifiedRecoverySource:
    return VerifiedRecoverySource(
        f"control-recovery/daily/2025-09-18/{revision:020d}/{sha256}.sqlite3",
        sha256,
        4096,
        revision,
    )


def _signed_inventory(
    sequence: int,
    prior_sha256: str | None,
    object_sha256: str,
    *,
    last_modified_ms: int = _CREATED_AT_MS,
    revision: int | None = None,
) -> RecoveryInventory:
    unsigned = RecoveryInventory(
        sequence=sequence,
        prior_inventory_sha256=prior_sha256,
        trusted_object_key=trusted_recovery_key(sequence, object_sha256),
        trusted_object_checksum_sha256=object_sha256,
        trusted_object_revision=sequence if revision is None else revision,
        trusted_object_size_bytes=4096,
        trusted_object_last_modified_ms=last_modified_ms,
        signature="pending",
    )
    return unsigned.model_copy(
        update={
            "signature": _DigestSigner().sign(
                canonical_inventory_payload_bytes(unsigned)
            )
        }
    )


def _storage_with(*inventories: RecoveryInventory) -> _FakeStorage:
    storage = _FakeStorage()
    for inventory in inventories:
        storage.inventories[recovery_inventory_key(inventory.sequence)] = (
            canonical_inventory_bytes(inventory)
        )
    return storage
