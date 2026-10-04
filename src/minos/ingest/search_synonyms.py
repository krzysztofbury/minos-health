"""Reviewed text rewrites, never code additions or silent spelling correction."""

from datetime import date
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from minos.domain.normalization import normalize_description
from minos.domain.search import fold_polish, prepare_query
from minos.ingest.manifest_types import Identifier, SourceText


class SearchSynonym(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    id: Identifier
    version: Literal[1]
    system: Literal["icd10-pl", "icd9-pl"]
    term: Annotated[str, Field(min_length=1, max_length=128)]
    source_wording: Annotated[str, Field(min_length=1, max_length=128)]
    source_url: Annotated[str, Field(pattern=r"^https://[^\s]{1,1024}$")]
    evidence: SourceText
    status: Literal["proposed", "reviewed"]
    reviewer: SourceText | None = None
    reviewed_on: date | None = None

    @model_validator(mode="after")
    def review_boundary(self) -> Self:
        if self.status == "reviewed" and (
            self.reviewer is None or self.reviewed_on is None
        ):
            raise ValueError("reviewed synonym requires a reviewer and date")
        for text in (self.term, self.source_wording):
            query = prepare_query(text)
            if query.code_like or query.effective_text != normalize_description(text):
                raise ValueError(
                    "synonyms must be literal terminology phrases, not codes"
                )
        if fold_polish(normalize_description(self.term)) == fold_polish(
            normalize_description(self.source_wording)
        ):
            raise ValueError("synonym does not change source wording")
        return self
