import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from minos.ingest.watchers.recovery.models import (
    RecoveryClass,
    RecoveryImageMetadata,
    RecoveryVerificationError,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MAX_TIMESTAMP_MS = 253_402_300_799_999
_KEY_PATTERNS: dict[RecoveryClass, re.Pattern[str]] = {
    "run": re.compile(r"\Acontrol-recovery/run/([0-9]{20})/([0-9a-f]{64})\.sqlite3\Z"),
    "daily": re.compile(
        r"\Acontrol-recovery/daily/([0-9]{4}-[0-9]{2}-[0-9]{2})/"
        + r"([0-9]{20})/([0-9a-f]{64})\.sqlite3\Z"
    ),
    "weekly": re.compile(
        r"\Acontrol-recovery/weekly/([0-9]{4}-W[0-9]{2})/"
        + r"([0-9]{20})/([0-9a-f]{64})\.sqlite3\Z"
    ),
    "monthly": re.compile(
        r"\Acontrol-recovery/monthly/([0-9]{4}-[0-9]{2})/"
        + r"([0-9]{20})/([0-9a-f]{64})\.sqlite3\Z"
    ),
}


@dataclass(frozen=True, slots=True)
class RecoveryKey:
    recovery_class: RecoveryClass
    period: str | None
    state_revision: int
    sha256: str


def recovery_prefix(recovery_class: RecoveryClass) -> str:
    if recovery_class not in _KEY_PATTERNS:
        raise RecoveryVerificationError("recovery class is unsupported")
    return f"control-recovery/{recovery_class}/"


def recovery_key(metadata: RecoveryImageMetadata, sha256: str) -> str:
    _validate_sha256(sha256)
    _validate_revision(metadata.state_revision)
    period = recovery_period(metadata.recovery_class, metadata.created_at_ms)
    revision = f"{metadata.state_revision:020d}"
    if period is None:
        return f"control-recovery/run/{revision}/{sha256}.sqlite3"
    return (
        f"control-recovery/{metadata.recovery_class}/{period}/"
        f"{revision}/{sha256}.sqlite3"
    )


def parse_recovery_key(key: str) -> RecoveryKey:
    if len(key) > 256:
        raise RecoveryVerificationError("recovery key is outside the supported range")
    for recovery_class, pattern in _KEY_PATTERNS.items():
        match = pattern.fullmatch(key)
        if match is None:
            continue
        groups = match.groups()
        if recovery_class == "run":
            period = None
            revision_text, sha256 = groups
        else:
            period, revision_text, sha256 = groups
            _validate_period(recovery_class, period)
        revision = int(revision_text)
        _validate_revision(revision)
        return RecoveryKey(recovery_class, period, revision, sha256)
    raise RecoveryVerificationError("recovery key does not match an immutable grammar")


def recovery_period(recovery_class: RecoveryClass, created_at_ms: int) -> str | None:
    if created_at_ms < 1 or created_at_ms > _MAX_TIMESTAMP_MS:
        raise RecoveryVerificationError("recovery creation time is invalid")
    created_at = _EPOCH + timedelta(milliseconds=created_at_ms)
    if recovery_class == "run":
        return None
    if recovery_class == "daily":
        return created_at.strftime("%Y-%m-%d")
    if recovery_class == "weekly":
        iso_year, iso_week, _ = created_at.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    return created_at.strftime("%Y-%m")


def _validate_period(recovery_class: RecoveryClass, period: str) -> None:
    try:
        if recovery_class == "daily":
            parsed = datetime.strptime(period, "%Y-%m-%d").replace(tzinfo=UTC)
            canonical = parsed.strftime("%Y-%m-%d")
        elif recovery_class == "weekly":
            parsed = datetime.strptime(f"{period}-1", "%G-W%V-%u").replace(tzinfo=UTC)
            iso_year, iso_week, _ = parsed.isocalendar()
            canonical = f"{iso_year:04d}-W{iso_week:02d}"
        elif recovery_class == "monthly":
            parsed = datetime.strptime(period, "%Y-%m").replace(tzinfo=UTC)
            canonical = parsed.strftime("%Y-%m")
        else:
            raise RecoveryVerificationError("run recovery keys cannot contain a period")
    except ValueError as error:
        raise RecoveryVerificationError("recovery key period is invalid") from error
    if canonical != period:
        raise RecoveryVerificationError("recovery key period is not canonical")


def _validate_revision(state_revision: object) -> None:
    if (
        not isinstance(state_revision, int)
        or isinstance(state_revision, bool)
        or state_revision < 0
        or state_revision >= 10**20
    ):
        raise RecoveryVerificationError("recovery state revision is invalid")


def _validate_sha256(sha256: str) -> None:
    if len(sha256) != 64 or any(
        character not in "0123456789abcdef" for character in sha256
    ):
        raise RecoveryVerificationError(
            "recovery SHA-256 must be lowercase hexadecimal"
        )
