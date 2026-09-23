from dataclasses import dataclass
from typing import Literal

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type RunMode = Literal["scheduled", "weekly_verification", "manual", "catch_up"]
type ScheduledRunMode = Literal["scheduled", "weekly_verification"]
type ReconcilableStatus = Literal["committed", "heartbeat_pending"]


@dataclass(frozen=True, slots=True)
class SourceConfiguration:
    source_id: str
    watcher_type: str
    configuration: dict[str, JsonValue]
    enabled: bool


@dataclass(frozen=True, slots=True)
class ConfiguredSource:
    source_id: str
    configuration_revision: int
    configuration_sha256: str
    state_revision: int
    changed: bool


@dataclass(frozen=True, slots=True)
class SourceSchedule:
    source_id: str
    cron_expression: str
    misfire_grace_seconds: int
    retry_max_attempts: int
    retry_base_seconds: float
    retry_max_seconds: float
    run_mode: ScheduledRunMode = "scheduled"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_seconds: float
    max_seconds: float


@dataclass(frozen=True, slots=True)
class ScheduledSource:
    source_id: str
    watcher_type: str
    cron_expression: str
    next_due_at_ms: int
    misfire_grace_seconds: int
    retry_policy: RetryPolicy
    run_mode: ScheduledRunMode = "scheduled"


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    source_id: str
    state_revision: int
    changed: bool


@dataclass(frozen=True, slots=True)
class PersistedSourceState:
    source_id: str
    watcher_type: str
    configuration_revision: int
    configuration: dict[str, JsonValue]
    cursor: dict[str, JsonValue]
    cursor_state_revision: int | None


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    source_id: str
    next_due_at_ms: int
    state_revision: int
    changed: bool


@dataclass(frozen=True, slots=True)
class ClaimRequest:
    source_id: str
    run_mode: RunMode
    scheduled_at_ms: int
    deadline_after_ms: int
    expected_due_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ClaimIdentity:
    source_id: str
    run_id: str
    claim_token: str
    fencing_epoch: int
    configuration_revision: int
    deadline_at_ms: int


@dataclass(frozen=True, slots=True)
class RunClaim:
    identity: ClaimIdentity
    run_mode: RunMode
    scheduled_at_ms: int
    started_at_ms: int
    state_revision: int
    retry_policy: RetryPolicy


@dataclass(frozen=True, slots=True)
class StaleRunClaim:
    identity: ClaimIdentity
    run_mode: RunMode
    scheduled_at_ms: int
    started_at_ms: int


@dataclass(frozen=True, slots=True)
class BlobReceipt:
    sha256: str
    size_bytes: int
    media_type: str | None
    archive_key: str
    verified_sha256: str
    verified_size_bytes: int


@dataclass(frozen=True, slots=True)
class RecoveryReceipt:
    run_id: str
    committed_state_revision: int
    state_revision: int
    sha256: str
    size_bytes: int
    archive_key: str
    version_id: str
    verified_sha256: str
    verified_size_bytes: int


@dataclass(frozen=True, slots=True)
class ObservationBlob:
    blob_role: str
    ordinal: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ObservationDraft:
    observation_id: str
    observation_kind: str
    source_identity: str
    response_sha256: str
    observed_at_ms: int
    metadata: dict[str, JsonValue]
    blobs: tuple[ObservationBlob, ...] = ()


@dataclass(frozen=True, slots=True)
class ChangeDraft:
    change_id: str
    observation_id: str
    change_category: str
    summary: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class IncidentDraft:
    incident_id: str
    incident_category: str
    details: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ObservationCommit:
    blobs: tuple[BlobReceipt, ...]
    observations: tuple[ObservationDraft, ...]
    cursor: dict[str, JsonValue]
    changes: tuple[ChangeDraft, ...] = ()
    incidents: tuple[IncidentDraft, ...] = ()


@dataclass(frozen=True, slots=True)
class CommittedRun:
    source_id: str
    run_id: str
    committed_state_revision: int
    committed_at_ms: int
    status: ReconcilableStatus = "committed"


@dataclass(frozen=True, slots=True)
class CommittedObservation:
    observation_id: str
    observation_kind: str
    source_identity: str
    response_sha256: str
    observed_at_ms: int
    metadata: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class CommittedBlobEvidence:
    blob_role: str
    ordinal: int
    sha256: str
    size_bytes: int
    media_type: str | None


@dataclass(frozen=True, slots=True)
class CommittedProductEvidence:
    state_revision: int
    observation_id: str
    source_identity: str
    response_sha256: str
    observed_at_ms: int
    metadata: dict[str, JsonValue]
    response_size_bytes: int
    response_media_type: str | None
    blobs: tuple[CommittedBlobEvidence, ...]


@dataclass(frozen=True, slots=True)
class CommittedObservationRevision:
    state_revision: int
    committed_at_ms: int
    observations: tuple[CommittedObservation, ...]


@dataclass(frozen=True, slots=True)
class CommittedObservationHistory:
    source_id: str
    through_state_revision: int
    revisions: tuple[CommittedObservationRevision, ...]
    older_revisions_exist: bool
    product_evidence: tuple[CommittedProductEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class RunTransition:
    source_id: str
    run_id: str
    status: str
    committed_state_revision: int | None
    state_revision: int
    changed: bool
