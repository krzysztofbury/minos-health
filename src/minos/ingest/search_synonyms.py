"""Reviewed text rewrites, never code additions or silent spelling correction."""

from datetime import date
from importlib.resources import files
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from minos.domain.normalization import normalize_description
from minos.domain.search import fold_polish, prepare_query
from minos.ingest.manifest_types import Identifier, SourceText


def dictionary_notice(entries: tuple[SearchSynonym, ...]) -> str:
    if any(
        entry.source_url == "https://github.com/omwn/omw-data/releases/tag/v2.0"
        for entry in entries
    ):
        return (
            files("minos.ingest")
            .joinpath("wordnet-license.txt")
            .read_text(encoding="utf-8")
        )
    return ""


def _unscoped(value: str | None) -> bool:
    return value is None


def _phrase_match(value: bool) -> bool:
    return not value


def _synonym(value: str) -> bool:
    return value == "synonym"


class SearchSynonym(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    id: Identifier
    version: Literal[1]
    system: Literal["icd10-pl", "icd9-pl", "jgp-szp"]
    term: Annotated[str, Field(min_length=1, max_length=128)]
    source_wording: Annotated[str, Field(min_length=1, max_length=128)]
    source_url: Annotated[str, Field(pattern=r"^https://[^\s]{1,1024}$")]
    evidence: SourceText
    status: Literal["proposed", "reviewed"]
    reviewer: SourceText | None = None
    reviewed_on: date | None = None
    release_id: Identifier | None = Field(default=None, exclude_if=_unscoped)
    whole_query_only: bool = Field(default=False, exclude_if=_phrase_match)
    relation: Literal["synonym", "related_procedure"] = Field(
        default="synonym", exclude_if=_synonym
    )

    @model_validator(mode="after")
    def review_boundary(self) -> Self:
        if self.system == "jgp-szp" and (
            not self.whole_query_only or self.relation != "related_procedure"
        ):
            raise ValueError(
                "group aliases require a whole-query related-procedure phrase"
            )
        if self.relation == "related_procedure" and not self.whole_query_only:
            raise ValueError("related procedure aliases cannot consume qualifiers")
        if self.relation == "related_procedure" and self.system != "jgp-szp":
            raise ValueError("related procedure expansions require group search")
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
