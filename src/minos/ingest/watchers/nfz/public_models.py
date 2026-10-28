"""Strict public metadata contract; official links never expose private archives."""

import re
import unicodedata
from datetime import date, datetime
from typing import Annotated, ClassVar, Literal, Self
from urllib.parse import unquote

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def text(value: str) -> str:
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("public text contains control characters")
    return value


def calendar_date(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("public date is not canonical")
    return value


def timestamp(value: str) -> str:
    _ = datetime.fromisoformat(value)
    offset = re.search(r"[+-]([0-9]{2}):([0-9]{2})$", value)
    if offset is not None and (int(offset[1]) > 23 or int(offset[2]) > 59):
        raise ValueError("public timestamp offset is invalid")
    return value


type Text = Annotated[str, Field(min_length=1, max_length=512), AfterValidator(text)]
type Digest = Annotated[
    str, Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)
]
type Day = Annotated[
    str,
    Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", max_length=10),
    AfterValidator(calendar_date),
]
type Timestamp = Annotated[
    str,
    Field(
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$",
        max_length=24,
    ),
    AfterValidator(timestamp),
]
type GenerationTime = Annotated[
    str,
    Field(
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})?$",
        max_length=32,
    ),
    AfterValidator(timestamp),
]
type Identifier = Annotated[int, Field(ge=1, le=2_147_483_647)]
type Version = Annotated[
    str, Field(pattern=r"^[1-9][0-9]{0,2}\.[0-9]{1,3}$", max_length=7)
]

# Full-string contracts reject credentials, ports, fragments and arbitrary queries.
URL_PATTERNS = {
    "order": r"https://www\.nfz\.gov\.pl/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/[a-z0-9-]+,[1-9][0-9]{0,9}\.html",
    "news": r"https://www\.nfz\.gov\.pl/aktualnosci/aktualnosci-centrali/[a-z0-9-]+,[1-9][0-9]{0,9}\.html",
    "project": r"https://www\.nfz\.gov\.pl/zarzadzenia-prezesa/projekty-zarzadzen/[a-z0-9-]+,[1-9][0-9]{0,9}\.html",
    "jgp": r"https://www\.nfz\.gov\.pl/dla-swiadczeniodawcy/jednorodne-grupy-pacjentow/komunikaty-jgp/[a-z0-9-]+,[1-9][0-9]{0,9}\.html",
    "jgp_file": r"https://www\.nfz\.gov\.pl/download/gfx/nfz/pl/defaultaktualnosci/354/[1-9][0-9]{0,9}/[1-9][0-9]{0,2}/[a-zA-Z0-9_.-]{3,160}\.(docx|xlsx)",
    "icd9_companion": r"https://www\.nfz\.gov\.pl/download/gfx/nfz/pl/defaultstronaopisowa/782/1/2/[a-zA-Z0-9_.-]{3,140}\.xlsx",
    "czs": r"https://slowniki\.nfz\.gov\.pl/WersjeSlownikow/PobierzXml\?kodSlownika=(ICD9|MODTAR)&nrGen=[1-9][0-9]{0,5}",
    "display": r"https://slowniki\.nfz\.gov\.pl/WersjeSlownikow/Index/[1-9][0-9]{0,9}\?kod=MODTAR",
    "aotmit": r"https://bip\.aotm\.gov\.pl/assets/files/taryfikacja/taryfy/obwieszczenia/20[0-9]{2}/[\w .%-]{3,180}\.pdf",
    "eli": r"https://api\.sejm\.gov\.pl/eli/acts/DU/(19|20)[0-9]{2}/[1-9][0-9]{0,4}",
}


def official_url(value: str, channel: str) -> str:
    if len(value) > 1024 or re.fullmatch(URL_PATTERNS[channel], value) is None:
        raise ValueError("unsupported official URL")
    # AOTMiT filenames can be percent encoded. Decode once, refusing encoded
    # separators, repeated encoding and traversal even on an official host.
    suffix = value.split("?", 1)[0].rsplit("/", 1)[-1]
    decoded = unquote(suffix, errors="strict")
    if any(part in decoded for part in ("..", "/", "\\", "%", "?", "#")):
        raise ValueError("unsafe official URL path")
    _ = text(decoded)
    return value


class PublicModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )


class Evidence(PublicModel):
    source_url: Annotated[str, Field(min_length=1, max_length=1024)]
    sha256: Digest
    observed_at: Timestamp


class Entry(Evidence):
    title: Text
    service_applicability: Literal["not_reviewed"] = "not_reviewed"


class Order(Entry):
    channel: Literal["order"]
    act_number: Annotated[
        str, Field(pattern=r"^[1-9][0-9]{0,3}/20[0-9]{2}/DSOZ$", max_length=16)
    ]
    baw_document_id: Identifier
    published_date: Day

    @model_validator(mode="after")
    def links(self) -> Self:
        _ = official_url(self.source_url, self.channel)
        return self


class Notice(Entry):
    channel: Literal["news", "project", "jgp"]
    item_id: Identifier
    published_date: Day
    files: Annotated[tuple[Evidence, ...], Field(max_length=8)] = ()

    @model_validator(mode="after")
    def links(self) -> Self:
        _ = official_url(self.source_url, self.channel)
        if not self.source_url.endswith(f",{self.item_id}.html"):
            raise ValueError("notice identity differs from URL")
        if self.channel != "jgp" and self.files:
            raise ValueError("files on unsupported notice")
        if self.channel == "jgp" and not self.files:
            raise ValueError("JGP file evidence is missing")
        for file in self.files:
            _ = official_url(file.source_url, "jgp_file")
            if f"/354/{self.item_id}/" not in file.source_url:
                raise ValueError("file belongs to another notice")
        if len({file.source_url for file in self.files}) != len(self.files):
            raise ValueError("duplicate notice file")
        return self


class Czs(Entry):
    channel: Literal["czs"]
    code: Literal["ICD9", "MODTAR"]
    generation: Annotated[int, Field(ge=1, le=999_999)]
    generated_at: GenerationTime
    year: Annotated[int, Field(ge=2020, le=2100)] | None
    grouper_type: Literal["SZP"] | None
    version: Text | None
    published_date: Day | None
    publisher_valid_from: Day | None
    display_evidence: Evidence | None

    @model_validator(mode="after")
    def links(self) -> Self:
        _ = official_url(self.source_url, self.channel)
        if (
            self.source_url
            != f"https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika={self.code}&nrGen={self.generation}"
        ):
            raise ValueError("CZS generation differs from URL")
        if self.code == "ICD9":
            if any(
                item is not None
                for item in (
                    self.year,
                    self.grouper_type,
                    self.display_evidence,
                    self.published_date,
                    self.publisher_valid_from,
                )
            ):
                raise ValueError("ICD9 contains MODTAR attributes")
            if (
                self.version is not None
                and re.fullmatch(r"[1-9][0-9]{0,2}\.[0-9]{1,3}", self.version) is None
            ):
                raise ValueError("invalid ICD9 version")
        elif self.year is None or self.grouper_type != "SZP":
            raise ValueError("MODTAR scope is missing")
        if self.display_evidence is not None:
            _ = official_url(self.display_evidence.source_url, "display")
        elif self.code == "MODTAR" and any(
            item is not None
            for item in (self.version, self.published_date, self.publisher_valid_from)
        ):
            raise ValueError("MODTAR display evidence is missing")
        return self


class Companion(Entry):
    channel: Literal["icd9_companion"]
    version: Version
    czs_xml_sha256: Digest

    @model_validator(mode="after")
    def links(self) -> Self:
        _ = official_url(self.source_url, self.channel)
        return self


class Aotmit(Entry):
    channel: Literal["aotmit"]
    meaning: Literal["leading_indicator"] = "leading_indicator"
    published_date: Day

    @model_validator(mode="after")
    def links(self) -> Self:
        _ = official_url(self.source_url, self.channel)
        return self


type EliIdentifier = Annotated[
    str, Field(pattern=r"^DU/(19|20)[0-9]{2}/[1-9][0-9]{0,4}$", max_length=16)
]
type EliFinding = Annotated[
    str,
    Field(
        pattern=r"^((amendment|consolidated_text|reference_removed):DU/(19|20)[0-9]{2}/[1-9][0-9]{0,4}|(repeal|correction|tribunal):(pinned|base):(DU|MP)/(19|20)[0-9]{2}/[1-9][0-9]{0,4}|not_in_force:(pinned|base):[A-Z_]{1,32})$",
        max_length=64,
    ),
]


class Eli(Entry):
    """A pinned Dziennik Ustaw dictionary and its differences from the review."""

    channel: Literal["eli"]
    dictionary: Literal["specialties", "discharge_modes"]
    eli: EliIdentifier
    base_eli: EliIdentifier
    in_force: Annotated[str, Field(pattern=r"^[A-Z_]{1,32}$")]
    findings: Annotated[tuple[EliFinding, ...], Field(max_length=32)]
    base_evidence: Evidence

    @model_validator(mode="after")
    def links(self) -> Self:
        prefix = "https://api.sejm.gov.pl/eli/acts/"
        if (
            self.source_url != prefix + self.eli
            or self.base_evidence.source_url != prefix + self.base_eli
        ):
            raise ValueError("ELI identity differs from URL")
        _ = official_url(self.source_url, self.channel)
        _ = official_url(self.base_evidence.source_url, self.channel)
        return self


type PublicEntry = Annotated[
    Order | Notice | Czs | Companion | Aotmit | Eli, Field(discriminator="channel")
]


class Redistribution(PublicModel):
    schema_version: Literal[1] = 1
    artifacts: tuple[()] = ()


class NfzProjection(PublicModel):
    schema_version: Literal[1] = 1
    source: Literal["NFZ"] = "NFZ"
    through_state_revision: Annotated[int, Field(ge=1, le=9_007_199_254_740_991)]
    observed_at: Timestamp
    coverage: Literal["latest_reconciled_observation_only"] = (
        "latest_reconciled_observation_only"
    )
    entries: Annotated[tuple[PublicEntry, ...], Field(max_length=256)]
    redistribution: Redistribution = Redistribution()

    @model_validator(mode="after")
    def identities(self) -> Self:
        identities = {(entry.channel, entry.source_url) for entry in self.entries}
        if len(identities) != len(self.entries):
            raise ValueError("duplicate public identity")
        if any(entry.observed_at > self.observed_at for entry in self.entries):
            raise ValueError("observation exceeds commit fence")
        dictionaries = [entry for entry in self.entries if isinstance(entry, Czs)]
        if len({entry.code for entry in dictionaries}) != len(dictionaries):
            raise ValueError("duplicate dictionary code")
        for entry in self.entries:
            evidence = (
                entry.files
                if isinstance(entry, Notice)
                else (entry.display_evidence,)
                if isinstance(entry, Czs) and entry.display_evidence is not None
                else (entry.base_evidence,)
                if isinstance(entry, Eli)
                else ()
            )
            if any(item.observed_at > self.observed_at for item in evidence):
                raise ValueError("nested observation exceeds commit fence")
            if isinstance(entry, Companion):
                matches = [item for item in dictionaries if item.code == "ICD9"]
                if (
                    len(matches) != 1
                    or entry.czs_xml_sha256 != matches[0].sha256
                    or entry.version != matches[0].version
                ):
                    raise ValueError("companion dictionary evidence differs")
        for channel, maximum in (
            ("order", 32),
            ("news", 64),
            ("project", 64),
            ("jgp", 4),
            ("czs", 2),
            ("icd9_companion", 1),
            ("aotmit", 12),
            ("eli", 2),
        ):
            if sum(entry.channel == channel for entry in self.entries) > maximum:
                raise ValueError("channel population exceeds bound")
        return self
