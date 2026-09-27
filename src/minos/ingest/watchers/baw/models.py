"""Validated BAW response shapes and source relationships."""

import json
from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    ActDate: str | None = None
    PublishedDate: str | None = None
    ValidFrom: str | None = None
    ValidTo: str | None = None
    LegalActStatus: dict[str, object]
    Files: list[dict[str, object]] = Field(max_length=128)


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
