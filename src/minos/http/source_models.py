"""Strict BAW public wire boundary shared by source-page readers."""

import re
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from minos.ingest.watchers.baw.models import (
    BawLineage,
    BawSourceError,
    validate_lineage,
)
from minos.ingest.watchers.nfz.public_models import Day
from minos.ingest.watchers.rsk.public_models import (
    PublicBoundaryModel,
    PublicText,
    RedistributionManifest,
    Sha256,
    Timestamp,
)

Identifier = Annotated[int, Field(ge=1, le=9_007_199_254_740_991)]


class BawFile(PublicBoundaryModel):
    file_id: Identifier
    name: PublicText
    size_bytes: int = Field(ge=1, le=512 * 1024 * 1024)
    sha256: Sha256


class BawDocument(PublicBoundaryModel):
    document_id: Identifier
    act_number: PublicText
    act_date: Day | None
    published_date: Day | None
    entry_into_force: Day | None
    service_applicability_date: Day | None
    service_applicability_evidence: PublicText | None = None
    valid_to: Day | None
    legal_status: PublicText
    observed_at: Timestamp
    metadata_sha256: Sha256
    source_url: Annotated[str, Field(max_length=256)] | None
    files: list[BawFile] = Field(max_length=128)
    lineage: BawLineage | None = None

    @model_validator(mode="after")
    def evidence(self) -> Self:
        if (self.service_applicability_date is None) != (
            self.service_applicability_evidence is None
        ):
            raise ValueError("BAW applicability evidence is incomplete")
        if len({item.file_id for item in self.files}) != len(self.files):
            raise ValueError("BAW files are duplicated")
        legacy = f"https://baw.nfz.gov.pl/NFZ/document/{self.document_id}"
        official = legacy + "/Zarzadzenie-" + self.act_number.replace("/", "_")
        if self.source_url not in (None, legacy) and (
            re.fullmatch(r"[1-9][0-9]{0,3}/20[0-9]{2}/DSOZ", self.act_number) is None
            or self.source_url != official
        ):
            raise ValueError("BAW official URL is invalid")
        return self


class BawProjection(PublicBoundaryModel):
    schema_version: Literal[1, 2]
    source: Literal["BAW"]
    through_state_revision: Identifier
    observed_at: Timestamp
    documents: list[BawDocument] = Field(min_length=1, max_length=32)
    redistribution: RedistributionManifest

    @model_validator(mode="after")
    def identities(self) -> Self:
        if len({doc.document_id for doc in self.documents}) != len(self.documents):
            raise ValueError("BAW documents are duplicated")
        lineage = {
            doc.document_id: doc.lineage for doc in self.documents if doc.lineage
        }
        if self.schema_version == 2:
            if len(lineage) != len(self.documents):
                raise ValueError("BAW lineage is incomplete")
            try:
                validate_lineage(
                    lineage, {doc.document_id: doc.act_number for doc in self.documents}
                )
            except BawSourceError:
                raise ValueError("BAW lineage is invalid") from None
        elif lineage:
            raise ValueError("legacy BAW lineage is unexpected")
        return self
