import re
from dataclasses import dataclass

_MAX_SEQUENCE = 10**20 - 1
_TRUSTED_RECOVERY_PATTERN = re.compile(
    r"\Atrusted-recovery/([0-9]{20})/([0-9a-f]{64})\.sqlite3\Z"
)
_INVENTORY_PATTERN = re.compile(r"\Arecovery-inventories/([0-9]{20})\.json\Z")


class TrustedRecoveryKeyError(ValueError):
    """An operator recovery key is not canonical."""


@dataclass(frozen=True, slots=True)
class TrustedRecoveryKey:
    sequence: int
    sha256: str


def trusted_recovery_key(sequence: int, sha256: str) -> str:
    _validate_sequence(sequence)
    _validate_sha256(sha256)
    return f"trusted-recovery/{sequence:020d}/{sha256}.sqlite3"


def parse_trusted_recovery_key(key: str) -> TrustedRecoveryKey:
    if len(key) > 128:
        raise TrustedRecoveryKeyError("trusted recovery key is invalid")
    match = _TRUSTED_RECOVERY_PATTERN.fullmatch(key)
    if match is None:
        raise TrustedRecoveryKeyError("trusted recovery key is invalid")
    sequence_text, sha256 = match.groups()
    sequence = int(sequence_text)
    _validate_sequence(sequence)
    return TrustedRecoveryKey(sequence, sha256)


def recovery_inventory_key(sequence: int) -> str:
    _validate_sequence(sequence)
    return f"recovery-inventories/{sequence:020d}.json"


def parse_recovery_inventory_key(key: str) -> int:
    if len(key) > 64:
        raise TrustedRecoveryKeyError("recovery inventory key is invalid")
    match = _INVENTORY_PATTERN.fullmatch(key)
    if match is None:
        raise TrustedRecoveryKeyError("recovery inventory key is invalid")
    sequence = int(match.group(1))
    _validate_sequence(sequence)
    return sequence


def _validate_sequence(sequence: object) -> None:
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 1
        or sequence > _MAX_SEQUENCE
    ):
        raise TrustedRecoveryKeyError("recovery sequence is invalid")


def _validate_sha256(sha256: object) -> None:
    if not isinstance(sha256, str) or len(sha256) != 64:
        raise TrustedRecoveryKeyError("trusted recovery checksum is invalid")
    if any(character not in "0123456789abcdef" for character in sha256):
        raise TrustedRecoveryKeyError("trusted recovery checksum is invalid")
