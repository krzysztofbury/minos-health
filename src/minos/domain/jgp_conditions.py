"""Attachment 9 reference evidence. No encounter evaluation or implicit defaults."""

from dataclasses import dataclass
from typing import ClassVar, Literal

type System = Literal["icd9-pl", "icd10-pl"]
type Comparison = Literal["gt", "ge", "lt", "le"]
type Role = Literal["principal", "secondary", "source_unspecified"]
type Operator = Literal["all", "any", "not", "when"]
type EvidenceCategory = Literal[
    "section",
    "title",
    "list_heading",
    "marker",
    "member",
    "unbound_member",
    "label",
    "connector",
]


@dataclass(frozen=True)
class Evidence:
    row: int
    section: str | None
    context: str | None
    text: str
    category: EvidenceCategory


@dataclass(frozen=True)
class Member:
    row: int
    code: str
    marks: tuple[str, ...]


@dataclass(frozen=True)
class DirectionalList:
    identity: int
    label: str
    system: System
    context: str
    marker_row: int
    naming_rows: tuple[int, ...]
    members: tuple[Member, ...]


@dataclass(frozen=True)
class ListSelector:
    list_ids: tuple[int, ...]
    system: System
    kind: ClassVar[Literal["list"]] = "list"


@dataclass(frozen=True)
class CodeSelector:
    codes: tuple[str, ...]
    system: System
    # Literal declarations are NFZ source identities, not canonical concepts.
    kind: ClassVar[Literal["codes"]] = "codes"


@dataclass(frozen=True)
class UnionSelector:
    selector_ids: tuple[int, ...]
    system: System
    kind: ClassVar[Literal["union"]] = "union"


@dataclass(frozen=True)
class FamilySelector:
    category: str
    system: System
    expansion_policy: Literal["unbound"] = "unbound"
    kind: ClassVar[Literal["classification_family"]] = "classification_family"


type SelectorBody = ListSelector | CodeSelector | UnionSelector | FamilySelector


@dataclass(frozen=True)
class Selector:
    identity: int
    body: SelectorBody
    source_rows: tuple[int, ...]


@dataclass(frozen=True)
class Presence:
    selector_id: int
    role: Role | None


@dataclass(frozen=True)
class Count:
    selector_id: int
    required_count: int
    comparison: Literal["minimum", "source_cardinality"]
    counting_policy: Literal["distinct_codes", "repeat_occurrences", "review_required"]
    role: Role | None
    included_node_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class AllMembers:
    selector_id: int
    minimum_per_member: int
    counting_policy: Literal["review_required"] = "review_required"


@dataclass(frozen=True)
class ListCoverage:
    selector_ids: tuple[int, ...]
    minimum_distinct_lists: int
    minimum_procedures: int
    assignment_policy: Literal["review_required"] = "review_required"


@dataclass(frozen=True)
class DateRelation:
    # Each ordered set defines its source selectors; no dangling occurrence ID.
    occurrence_sets: tuple[tuple[int, ...], ...]
    relation: Literal["different_dates"] = "different_dates"
    missing_date_policy: Literal["review_required"] = "review_required"


@dataclass(frozen=True)
class Laterality:
    selector_id: int
    required_sites: tuple[Literal["right_breast", "left_breast"], ...]
    minimum_per_site: int = 1
    same_code_policy: Literal["review_required"] = "review_required"


@dataclass(frozen=True)
class ProcedureDuration:
    selector_id: int
    threshold_hours: int
    qualifying_occurrences: int
    comparison: Comparison = "ge"


@dataclass(frozen=True)
class Age:
    comparison: Comparison
    threshold: int
    unit: Literal["completed_years", "elapsed_weeks", "calendar_months"]
    anchor: Literal["admission"] = "admission"
    boundary_policy: Literal["review_required"] = "review_required"


@dataclass(frozen=True)
class DurationBound:
    comparison: Comparison
    days: int


@dataclass(frozen=True)
class EpisodeDuration:
    bounds: tuple[DurationBound, ...]
    duration_policy: Literal["review_required"] = "review_required"


@dataclass(frozen=True)
class WardDuration:
    comparison: Comparison
    days: int
    ward: Literal["OINK"] = "OINK"
    ward_mapping_policy: Literal["review_required"] = "review_required"
    duration_policy: Literal["review_required"] = "review_required"


@dataclass(frozen=True)
class DischargeMode:
    allowed_reported_codes: tuple[int, ...]


@dataclass(frozen=True)
class ClinicalMeasure:
    measure: Literal["ECOG", "mMRC", "NYHA", "LVEF", "EGFR"]
    comparison: Comparison
    exact_threshold: str
    unit_or_scale: Literal["source_ordinal", "%"] | None
    observation_anchor: Literal["review_required"] = "review_required"


@dataclass(frozen=True)
class ProgramMembership:
    program: Literal["KOC"] = "KOC"
    evidence_policy: Literal["review_required"] = "review_required"


@dataclass(frozen=True)
class ExternalDiseaseCode:
    exact_code: str
    system: Literal["orpha"] = "orpha"
    reporting_field: Literal["review_required"] = "review_required"


@dataclass(frozen=True)
class Expression:
    children: tuple[int, ...]
    # Only when: exact physical member occurrences that trigger this guard.
    member_rows: tuple[int, ...] = ()
    interpretation_policy: Literal["review_required"] = "review_required"


@dataclass(frozen=True)
class Unsupported:
    reason: str
    # Extracted fragments, deliberately NOT an all/any eligibility expression.
    fragments: tuple[int, ...] = ()


type Payload = (
    Presence
    | Count
    | AllMembers
    | ListCoverage
    | DateRelation
    | Laterality
    | ProcedureDuration
    | Age
    | EpisodeDuration
    | WardDuration
    | DischargeMode
    | ClinicalMeasure
    | ProgramMembership
    | ExternalDiseaseCode
    | Expression
    | Unsupported
)
type Kind = Literal[
    "procedure_presence",
    "procedure_count",
    "procedure_all_members",
    "procedure_list_coverage",
    "procedure_date_relation",
    "procedure_laterality",
    "procedure_duration",
    "diagnosis_presence",
    "diagnosis_count",
    "patient_age",
    "episode_duration",
    "ward_duration",
    "discharge_mode",
    "clinical_measure",
    "program_membership",
    "external_disease_code",
    "all",
    "any",
    "not",
    "when",
]


@dataclass(frozen=True)
class Condition:
    identity: int
    group: str
    kind: Kind | None
    payload: Payload
    source_rows: tuple[int, ...]
    status: Literal["proposed", "unsupported"]
    reason: str | None
    evidence_type: Literal["label", "annotation", "title_qualifier"] = "label"


@dataclass(frozen=True)
class Group:
    code: str
    title_row: int
    # Ordered reference roots, never an implicit conjunction or grouper input.
    roots: tuple[int, ...]


@dataclass(frozen=True)
class Characteristics:
    catalog: Literal["1a", "1ae"]
    worksheet: str
    evidence: tuple[Evidence, ...]
    groups: tuple[Group, ...]
    lists: tuple[DirectionalList, ...]
    selectors: tuple[Selector, ...]
    conditions: tuple[Condition, ...]
