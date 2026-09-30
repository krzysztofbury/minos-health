"""Validated BAW response shapes and source relationships."""

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from minos.ingest.watchers.protocols import SourceDriftWatcherError


class BawSourceError(SourceDriftWatcherError):
    """An upstream BAW response is outside its supported contract."""


class BawRelation(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="allow")

    Id: int = Field(gt=0)
    SourceDocumentId: int | None = Field(default=None, gt=0)
    IntroductoryDocumentId: int | None = Field(default=None, gt=0)
    AutomaticType: int = Field(ge=1, le=6)
    IsProject: bool


class BawDocument(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="allow")

    Id: int = Field(gt=0)
    InstitutionId: Literal[4]
    IsPrivate: bool
    IsRemoved: bool
    WasEverPublished: bool
    ActNumber: Annotated[str, Field(min_length=1, max_length=128)]
    LegalActTypeDescription: str | None = None
    ActDate: str | None = None
    PublishedDate: str | None = None
    ValidFrom: str | None = None
    ValidTo: str | None = None
    LegalActStatus: dict[str, object]
    Files: list[dict[str, object]] = Field(max_length=128)


class BawLineage(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid")

    automatic_type: Literal[1, 3, 4, 5, 6]
    source_document_id: int | None = Field(gt=0)
    introductory_document_id: int | None = Field(gt=0)

    @field_validator("automatic_type", mode="before")
    @classmethod
    def require_integer_role(cls, value: object) -> object:
        # Pydantic's Literal matcher accepts True and 1.0 as the integer 1.
        if type(value) is not int:
            raise ValueError("BAW document role must be an integer")
        return value


def validate_lineage(
    lineage: dict[int, BawLineage], act_numbers: dict[int, str]
) -> None:
    """Require direct original-act edges, never infer identity from a title."""
    if not 1 <= len(lineage) <= 32 or lineage.keys() != act_numbers.keys():
        raise BawSourceError("BAW lineage document set is invalid")
    for document_id, link in lineage.items():
        source_id = link.source_document_id
        introductory_id = link.introductory_document_id
        if link.automatic_type == 1:
            if source_id is not None or introductory_id is not None:
                raise BawSourceError("BAW original act has unexpected lineage")
            continue
        if source_id is not None:
            source = lineage.get(source_id)
            if source is None or source.automatic_type != 1:
                raise BawSourceError("BAW lineage does not reference an original act")
        if link.automatic_type == 6:
            if introductory_id is not None:
                raise BawSourceError("BAW amendment has unexpected introductory act")
            continue
        if source_id is None or introductory_id is None:
            raise BawSourceError("BAW variant lineage is incomplete")
        if act_numbers[document_id] != act_numbers[source_id]:
            raise BawSourceError("BAW variant does not match its original act")
        introductory = lineage.get(introductory_id)
        if introductory is None or introductory.automatic_type != 6:
            raise BawSourceError("BAW variant does not reference an amendment")
        if introductory.source_document_id != source_id:
            raise BawSourceError("BAW variant and amendment have conflicting sources")


@dataclass(frozen=True, slots=True)
class BawResponse:
    body: bytes
    media_type: str


def parse_document(response: BawResponse, document_id: int) -> BawDocument:
    try:
        document = BawDocument.model_validate_json(response.body)
    except ValidationError:
        raise BawSourceError("BAW document schema changed") from None
    if document.Id != document_id:
        raise BawSourceError("BAW returned another document")
    if document.IsPrivate or document.IsRemoved or not document.WasEverPublished:
        raise BawSourceError("BAW document is not a public issued source")
    return document


def document_identity(response: BawResponse, document_id: int) -> str:
    """Ignore only BAW's generated legal-basis UUIDs, not legal or file facts."""
    _ = parse_document(response, document_id)
    payload = cast(object, json.loads(response.body))
    if not isinstance(payload, dict):
        raise BawSourceError("BAW document is not an object")
    document = cast(dict[str, object], payload)
    bases = document.get("LegalBasisActs")
    if bases is not None:
        if not isinstance(bases, list):
            raise BawSourceError("BAW legal basis structure is invalid")
        basis_items = cast(list[object], bases)
        if len(basis_items) > 128:
            raise BawSourceError("BAW legal basis structure is invalid")
        for basis in basis_items:
            if not isinstance(basis, dict):
                raise BawSourceError("BAW legal basis is invalid")
            basis_fields = cast(dict[str, object], basis)
            _discard_generated_identifier(basis_fields)
            rules = basis_fields.get("LegalActRegulations")
            if rules is None:
                continue
            if not isinstance(rules, list):
                raise BawSourceError("BAW legal regulations are invalid")
            rule_items = cast(list[object], rules)
            if len(rule_items) > 512:
                raise BawSourceError("BAW legal regulations are invalid")
            for rule in rule_items:
                if not isinstance(rule, dict):
                    raise BawSourceError("BAW legal regulation is invalid")
                _discard_generated_identifier(cast(dict[str, object], rule))
    encoded = json.dumps(
        document, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _discard_generated_identifier(fields: dict[str, object]) -> None:
    value = fields.get("ObjectIdentifier")
    if value is None:
        return
    if not isinstance(value, str) or len(value) != 36:
        raise BawSourceError("BAW generated identifier is invalid")
    try:
        if str(UUID(value)) != value:
            raise ValueError("non-canonical UUID")
    except ValueError:
        raise BawSourceError("BAW generated identifier is invalid") from None
    del fields["ObjectIdentifier"]


def parse_relations(response: BawResponse) -> tuple[BawRelation, ...]:
    if not response.body:
        return ()
    try:
        value = cast(object, json.loads(response.body))
        if not isinstance(value, list):
            raise ValueError("BAW relation groups are invalid")
        groups = cast(list[object], value)
        if len(groups) > 32:
            raise ValueError("BAW relation groups are invalid")
        rows: list[BawRelation] = []
        for group in groups:
            if not isinstance(group, list):
                raise ValueError("BAW relation group is invalid")
            items = cast(list[object], group)
            if len(items) > 32:
                raise ValueError("BAW relation group is invalid")
            rows.extend(BawRelation.model_validate(item) for item in items)
            if len(rows) > 32:
                raise ValueError("BAW relation count exceeds its bound")
        if len({row.Id for row in rows}) != len(rows):
            raise ValueError("BAW relations contain duplicate documents")
        return tuple(rows)
    except ValidationError, ValueError, TypeError:
        raise BawSourceError("BAW relation graph is invalid") from None
