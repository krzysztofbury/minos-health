"""Archive official NFZ hospital publication evidence and request BAW checks."""

import hashlib
import json
import re
import time
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import ClassVar, cast, final

import httpx
from defusedxml.ElementTree import fromstring as safe_fromstring
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from minos.ingest.watchers.aotmit.client import (
    AotmitRetryableError,
    AotmitSourceError,
    create_aotmit_client,
    honor_retry_after,
)
from minos.ingest.watchers.aotmit.client import (
    fetch_listing as fetch_aotmit_listing,
)
from minos.ingest.watchers.aotmit.client import (
    fetch_pdf as fetch_aotmit_pdf,
)
from minos.ingest.watchers.archive import BlobStagingArea
from minos.ingest.watchers.baw.client import (
    BawRetryableError,
    create_baw_client,
    get_json,
)
from minos.ingest.watchers.config import NfzConfiguration, RskSettings
from minos.ingest.watchers.control import (
    BawDispatchDraft,
    ChangeDraft,
    IncidentDraft,
    JsonValue,
    ObservationBlob,
    ObservationDraft,
)
from minos.ingest.watchers.czs.client import (
    CzsRetryableError,
    CzsSourceError,
    create_czs_client,
    discover_version,
    fetch_pinned_xml,
)
from minos.ingest.watchers.czs.display import fetch_display_version
from minos.ingest.watchers.eli.client import (
    WATCHED,
    EliResponse,
    EliRetryableError,
    EliSourceError,
    create_eli_client,
    fetch_act,
    findings,
)
from minos.ingest.watchers.nfz.discovery import (
    NfzListingError,
    NfzRetryableError,
    create_nfz_client,
    fetch_hospital_listing_evidence,
    fetch_order_detail_evidence,
    verify_baw_candidate,
)
from minos.ingest.watchers.nfz.icd9 import fetch_companion, fetch_companion_page
from minos.ingest.watchers.nfz.jgp import fetch_jgp_file, parse_jgp_files
from minos.ingest.watchers.nfz.signals import (
    NfzSignal,
    fetch_signal_detail,
    fetch_signal_listing,
)
from minos.ingest.watchers.protocols import (
    CollectedRun,
    CollectionContext,
    RetryableWatcherError,
    SourceDriftWatcherError,
    WatcherRuntimeError,
)
from minos.ingest.watchers.rsk.client import (
    create_rsk_client,
    get_rsk_catalog_basic_info,
    parse_rsk_catalog_basic_info,
)

_LISTING_URL = (
    "https://www.nfz.gov.pl/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/"
    "1,strona.html?szukaj=szpitaln"
)
_MAX_CANDIDATES = 8
_MAX_KNOWN_DOCUMENTS = 1024
_MAX_SIGNALS = 256
_BASELINE_NOTICE = NfzSignal(
    "news",
    8987,
    "Komunikat dla świadczeniodawców",
    "2026 JGP settlement notice",
    "2026-07-29",
    "https://www.nfz.gov.pl/aktualnosci/aktualnosci-centrali/"
    + "komunikat-dla-swiadczeniodawcow,8987.html",
)


class _Cursor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(ge=1, le=2)
    details: dict[str, str] = Field(max_length=_MAX_KNOWN_DOCUMENTS)
    czs: dict[str, str] = Field(default_factory=dict, max_length=3)
    signals: dict[str, str] = Field(default_factory=dict, max_length=_MAX_SIGNALS)
    jgp_files: dict[str, str] = Field(default_factory=dict, max_length=32)
    baseline_notice_recorded: bool = False
    aotmit: dict[str, str] = Field(default_factory=dict, max_length=32)
    eli: dict[str, str] = Field(default_factory=dict, max_length=len(WATCHED))
    icd9_xlsx: dict[str, str] = Field(default_factory=dict, max_length=1)
    reported_disagreements: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("details")
    @classmethod
    def validate_details(cls, details: dict[str, str]) -> dict[str, str]:
        for document_id, digest in details.items():
            if re.fullmatch(r"[1-9][0-9]{0,9}", document_id) is None:
                raise ValueError("NFZ cursor document ID is invalid")
            if int(document_id) > 2_147_483_647:
                raise ValueError("NFZ cursor document ID exceeds its bound")
            if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("NFZ cursor document digest is invalid")
        return details

    @field_validator("czs")
    @classmethod
    def validate_czs(cls, versions: dict[str, str]) -> dict[str, str]:
        for key, value in versions.items():
            if key != "ICD9" and re.fullmatch(r"MODTAR:20[0-9]{2}:SZP", key) is None:
                raise ValueError("unknown CZS cursor channel")
            if re.fullmatch(r"[1-9][0-9]{0,5}:[0-9a-f]{64}", value) is None:
                raise ValueError("CZS cursor digest is invalid")
        return versions

    @field_validator("signals")
    @classmethod
    def validate_signals(cls, signals: dict[str, str]) -> dict[str, str]:
        for key, digest in signals.items():
            if re.fullmatch(r"(?:news|project|jgp):[1-9][0-9]{0,9}", key) is None:
                raise ValueError("NFZ signal cursor key is invalid")
            if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("NFZ signal cursor digest is invalid")
        return signals

    @field_validator("jgp_files")
    @classmethod
    def validate_jgp_files(cls, files: dict[str, str]) -> dict[str, str]:
        for url, digest in files.items():
            if (
                re.fullmatch(
                    r"https://www\.nfz\.gov\.pl/download/gfx/nfz/pl/defaultaktualnosci/354/"
                    + r"[1-9][0-9]{0,9}/[1-9][0-9]{0,2}/[a-zA-Z0-9_.-]{3,160}\.(?:docx|xlsx)",
                    url,
                )
                is None
            ):
                raise ValueError("NFZ JGP cursor file URL is invalid")
            if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("NFZ JGP cursor digest is invalid")
        return files

    @field_validator("aotmit")
    @classmethod
    def validate_aotmit(cls, files: dict[str, str]) -> dict[str, str]:
        for url, digest in files.items():
            if (
                not url.startswith(
                    "https://bip.aotm.gov.pl/assets/files/taryfikacja/taryfy/obwieszczenia/"
                )
                or len(url) > 256
            ):
                raise ValueError("AOTMiT cursor URL is invalid")
            if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("AOTMiT cursor digest is invalid")
        return files

    @field_validator("eli")
    @classmethod
    def validate_eli(cls, states: dict[str, str]) -> dict[str, str]:
        names = {item.name for item in WATCHED}
        for name, digest in states.items():
            if name not in names or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("ELI cursor entry is invalid")
        return states

    @field_validator("icd9_xlsx")
    @classmethod
    def validate_icd9_xlsx(cls, files: dict[str, str]) -> dict[str, str]:
        for url, digest in files.items():
            if (
                re.fullmatch(
                    r"https://www\.nfz\.gov\.pl/download/gfx/nfz/pl/defaultstronaopisowa/782/1/2/"
                    + r"[a-zA-Z0-9_.-]{3,140}\.xlsx",
                    url,
                )
                is None
            ):
                raise ValueError("NFZ ICD-9 cursor URL is invalid")
            if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("NFZ ICD-9 cursor digest is invalid")
        return files

    @field_validator("reported_disagreements")
    @classmethod
    def validate_disagreements(cls, identifiers: list[str]) -> list[str]:
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("duplicate NFZ disagreement ID")
        if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in identifiers):
            raise ValueError("NFZ disagreement ID is invalid")
        return identifiers


@final
class NfzSourceCollector:
    def __init__(
        self,
        staging: BlobStagingArea,
        *,
        nfz_client_factory: Callable[
            [], AbstractContextManager[httpx.Client]
        ] = create_nfz_client,
        baw_client_factory: Callable[
            [], AbstractContextManager[httpx.Client]
        ] = create_baw_client,
        czs_client_factory: Callable[
            [], AbstractContextManager[httpx.Client]
        ] = create_czs_client,
        aotmit_client_factory: Callable[
            [], AbstractContextManager[httpx.Client]
        ] = create_aotmit_client,
        eli_client_factory: Callable[
            [], AbstractContextManager[httpx.Client]
        ] = create_eli_client,
        rsk_client_factory: Callable[
            [RskSettings], AbstractContextManager[httpx.Client]
        ] = create_rsk_client,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._staging = staging
        self._nfz_client_factory = nfz_client_factory
        self._baw_client_factory = baw_client_factory
        self._czs_client_factory = czs_client_factory
        self._aotmit_client_factory = aotmit_client_factory
        self._eli_client_factory = eli_client_factory
        self._rsk_client_factory = rsk_client_factory
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)

    @contextmanager
    def collect(self, context: CollectionContext) -> Generator[CollectedRun]:
        try:
            yield self._collect(context)
        except (
            httpx.TransportError,
            BawRetryableError,
            NfzRetryableError,
            CzsRetryableError,
            AotmitRetryableError,
            EliRetryableError,
        ):
            raise RetryableWatcherError("transient NFZ publication failure") from None
        except NfzListingError, CzsSourceError, AotmitSourceError, EliSourceError:
            raise SourceDriftWatcherError("NFZ publication contract changed") from None

    def _collect(self, context: CollectionContext) -> CollectedRun:
        try:
            _ = NfzConfiguration.model_validate(context.configuration)
            prior = (
                _Cursor.model_validate(context.cursor)
                if context.cursor
                else _Cursor(schema_version=1, details={})
            )
        except ValidationError:
            raise WatcherRuntimeError(
                "stored NFZ configuration or cursor is invalid"
            ) from None
        self._require_deadline(context)
        observations: list[ObservationDraft] = []
        changes: list[ChangeDraft] = []
        incidents: list[IncidentDraft] = []
        dispatches: list[BawDispatchDraft] = []
        details = dict(prior.details)
        czs = dict(prior.czs)
        modtar_year = datetime.fromtimestamp(self._now_ms() / 1000, UTC).year
        if not 2020 <= modtar_year <= 2100:
            raise WatcherRuntimeError("NFZ watcher year is outside the supported range")
        signals = dict(prior.signals)
        jgp_files = dict(prior.jgp_files)
        aotmit = dict(prior.aotmit)
        eli = dict(prior.eli)
        icd9_xlsx = dict(prior.icd9_xlsx)
        reported_disagreements = set(prior.reported_disagreements)
        archived: set[str] = set()
        with (
            self._nfz_client_factory() as nfz_client,
            self._baw_client_factory() as baw_client,
        ):
            listing, candidates = fetch_hospital_listing_evidence(nfz_client)
            if len(candidates) > _MAX_CANDIDATES:
                raise SourceDriftWatcherError("NFZ hospital search exceeds its bound")
            listing_digest = self._archive(context, listing.body, "text/html", archived)
            observations.append(
                self._observation(
                    context,
                    "nfz_listing",
                    _LISTING_URL,
                    listing_digest,
                    {"response_headers": cast(dict[str, JsonValue], listing.headers)},
                )
            )
            for candidate in candidates:
                self._require_deadline(context)
                detail, document_id = fetch_order_detail_evidence(nfz_client, candidate)
                baw_response = get_json(
                    baw_client, f"/api/documents/4/details/{document_id}/null"
                )
                _ = verify_baw_candidate(baw_response, candidate, document_id)
                detail_digest = self._archive(
                    context, detail.body, "text/html", archived
                )
                baw_digest = self._archive(
                    context, baw_response.body, "application/json", archived
                )
                observation = self._observation(
                    context,
                    "nfz_order_detail",
                    candidate.url,
                    detail_digest,
                    {
                        "act_number": candidate.act_number,
                        "baw_document_id": document_id,
                        "published_date": candidate.published_date,
                        "baw_metadata_sha256": baw_digest,
                        "response_headers": cast(dict[str, JsonValue], detail.headers),
                    },
                    (ObservationBlob("baw_metadata", 0, baw_digest),),
                )
                observations.append(observation)
                key = str(document_id)
                if key not in details:
                    if len(details) >= _MAX_KNOWN_DOCUMENTS:
                        raise SourceDriftWatcherError(
                            "NFZ seen-document cursor is full"
                        )
                    details[key] = detail_digest
                    dispatches.append(
                        BawDispatchDraft(
                            document_id, candidate.url, observation.observation_id
                        )
                    )
                    changes.append(
                        ChangeDraft(
                            _digest(
                                ["nfz_hospital_order", context.source_id, document_id]
                            ),
                            observation.observation_id,
                            "nfz_hospital_order",
                            {
                                "act_number": candidate.act_number,
                                "baw_document_id": document_id,
                            },
                        )
                    )
                elif details[key] != detail_digest:
                    incidents.append(
                        IncidentDraft(
                            _digest(
                                [
                                    "nfz_detail_changed",
                                    document_id,
                                    details[key],
                                    detail_digest,
                                ]
                            ),
                            "nfz_detail_changed",
                            {
                                "document_id": document_id,
                                "old_sha256": details[key],
                                "new_sha256": detail_digest,
                            },
                        )
                    )
                    details[key] = detail_digest
            baseline_notice_recorded = self._collect_signals(
                context,
                nfz_client,
                observations,
                changes,
                incidents,
                signals,
                archived,
                prior.baseline_notice_recorded or "news:8987" in signals,
            )
            self._collect_jgp(
                context,
                nfz_client,
                observations,
                changes,
                incidents,
                signals,
                jgp_files,
                archived,
            )
        with self._czs_client_factory() as czs_client:
            icd9_symbol = ""
            icd9_xml_digest = ""
            for channel in ("ICD9", f"MODTAR:{modtar_year}:SZP"):
                self._require_deadline(context)
                if channel == "ICD9":
                    version, soap = discover_version(czs_client, "ICD9")
                else:
                    version, soap = discover_version(
                        czs_client, "MODTAR", year=modtar_year, grouper_type="SZP"
                    )
                xml = fetch_pinned_xml(czs_client, version)
                soap_digest = self._archive(
                    context, soap.body, soap.media_type, archived
                )
                xml_digest = self._archive(context, xml.body, xml.media_type, archived)
                if channel == "ICD9":
                    icd9_symbol = safe_fromstring(xml.body).attrib["symbol"]
                    icd9_xml_digest = xml_digest
                url = (
                    "https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml"
                    f"?kodSlownika={version.code}&nrGen={version.generation}"
                )
                observation = self._observation(
                    context,
                    "czs_dictionary",
                    url,
                    xml_digest,
                    {
                        "code": version.code,
                        "generation": version.generation,
                        "generated_at": version.generated_at,
                        "year": version.year,
                        "grouper_type": version.grouper_type,
                        "soap_sha256": soap_digest,
                        "soap_response_headers": cast(
                            dict[str, JsonValue], soap.headers
                        ),
                        "xml_response_headers": cast(dict[str, JsonValue], xml.headers),
                    },
                    (ObservationBlob("soap_discovery", 0, soap_digest),),
                )
                observations.append(observation)
                if version.code == "MODTAR":
                    display, redirect, info = fetch_display_version(czs_client, version)
                    redirect_digest = self._archive(
                        context, redirect.body, "text/html", archived
                    )
                    info_digest = self._archive(
                        context, info.body, "text/html", archived
                    )
                    observations.append(
                        self._observation(
                            context,
                            "czs_modtar_display",
                            f"https://slowniki.nfz.gov.pl/WersjeSlownikow/Index/{display.page_id}?kod=MODTAR",
                            info_digest,
                            {
                                "display_label": display.label,
                                "technical_generation": display.technical_generation,
                                "effective_from": display.effective_from,
                                "published_date": display.published_date,
                                "pinned_xml_sha256": xml_digest,
                                "soap_sha256": soap_digest,
                                "redirect_sha256": redirect_digest,
                                "response_headers": cast(
                                    dict[str, JsonValue], info.headers
                                ),
                            },
                            (
                                ObservationBlob(
                                    "current_version_redirect", 0, redirect_digest
                                ),
                            ),
                        )
                    )
                previous = czs.get(channel)
                current = f"{version.generation}:{xml_digest}"
                if previous is not None and version.generation < int(
                    previous.split(":", 1)[0]
                ):
                    raise CzsSourceError("CZS dictionary generation regressed")
                if previous != current:
                    if previous is not None and previous.split(":", 1)[0] == str(
                        version.generation
                    ):
                        incidents.append(
                            IncidentDraft(
                                _digest(
                                    [
                                        "czs_dictionary_bytes_changed",
                                        channel,
                                        previous,
                                        xml_digest,
                                    ]
                                ),
                                "czs_dictionary_bytes_changed",
                                {
                                    "channel": channel,
                                    "old_sha256": previous.split(":", 1)[1],
                                    "new_sha256": xml_digest,
                                },
                            )
                        )
                    else:
                        changes.append(
                            ChangeDraft(
                                _digest(
                                    [
                                        "czs_dictionary",
                                        context.source_id,
                                        channel,
                                        version.generation,
                                    ]
                                ),
                                observation.observation_id,
                                "czs_dictionary",
                                {
                                    "channel": channel,
                                    "generation": version.generation,
                                    "sha256": xml_digest,
                                },
                            )
                        )
                    czs[channel] = current
            for key in tuple(czs):
                if (
                    key.startswith("MODTAR:")
                    and int(key.split(":", 2)[1]) < modtar_year - 1
                ):
                    del czs[key]
        with self._nfz_client_factory() as nfz_client:
            self._collect_icd9_companion(
                context,
                nfz_client,
                icd9_symbol,
                icd9_xml_digest,
                observations,
                incidents,
                icd9_xlsx,
                archived,
                reported_disagreements,
            )
        with self._aotmit_client_factory() as aotmit_client:
            self._collect_aotmit(
                context,
                aotmit_client,
                observations,
                changes,
                incidents,
                aotmit,
                archived,
            )
        with self._eli_client_factory() as eli_client:
            self._collect_eli(context, eli_client, observations, changes, eli, archived)
        self._crosscheck_rsk_icd9(
            context,
            icd9_symbol,
            icd9_xml_digest,
            observations,
            incidents,
            archived,
            reported_disagreements,
        )
        return CollectedRun(
            tuple(observations),
            cast(
                dict[str, JsonValue],
                _Cursor(
                    schema_version=2,
                    details=details,
                    czs=czs,
                    signals=signals,
                    jgp_files=jgp_files,
                    baseline_notice_recorded=baseline_notice_recorded,
                    aotmit=aotmit,
                    eli=eli,
                    icd9_xlsx=icd9_xlsx,
                    reported_disagreements=sorted(reported_disagreements),
                ).model_dump(mode="json"),
            ),
            tuple(changes),
            tuple(incidents),
            tuple(dispatches),
        )

    def _crosscheck_rsk_icd9(
        self,
        context: CollectionContext,
        symbol: str,
        czs_digest: str,
        observations: list[ObservationDraft],
        incidents: list[IncidentDraft],
        archived: set[str],
        reported_disagreements: set[str],
    ) -> None:
        self._require_deadline(context)
        settings = RskSettings.model_validate({})
        with self._rsk_client_factory(settings) as client:
            response = get_rsk_catalog_basic_info(client, settings, "00CD09")
        metadata = parse_rsk_catalog_basic_info(response, "00CD09")
        candidates: set[str] = set()
        for file in metadata.files:
            if file.extension == "xlsx" and file.file_name is not None:
                match = re.search(r"5\.([0-9]{1,3})", file.file_name)
                if match is not None:
                    candidates.add("5." + match[1])
        if len(candidates) != 1:
            raise SourceDriftWatcherError("RSK ICD-9 file version is ambiguous")
        rsk_symbol = next(iter(candidates))
        digest = self._archive(context, response.body, "application/json", archived)
        observations.append(
            self._observation(
                context,
                "rsk_icd9_crosscheck",
                response.source_url,
                digest,
                {
                    "rsk_version": rsk_symbol,
                    "czs_version": symbol,
                    "czs_xml_sha256": czs_digest,
                    "response_headers": cast(
                        dict[str, JsonValue], dict(response.headers)
                    ),
                },
            )
        )
        incident_id = _digest(["czs_rsk_icd9_disagreement", rsk_symbol, symbol])
        if rsk_symbol != symbol and incident_id not in reported_disagreements:
            incidents.append(
                IncidentDraft(
                    incident_id,
                    "czs_rsk_icd9_disagreement",
                    {
                        "rsk_version": rsk_symbol,
                        "czs_version": symbol,
                        "rsk_metadata_sha256": digest,
                        "czs_xml_sha256": czs_digest,
                    },
                )
            )
            if len(reported_disagreements) >= 64:
                raise SourceDriftWatcherError("NFZ disagreement cursor is full")
            reported_disagreements.add(incident_id)

    def _collect_icd9_companion(
        self,
        context: CollectionContext,
        client: httpx.Client,
        symbol: str,
        czs_digest: str,
        observations: list[ObservationDraft],
        incidents: list[IncidentDraft],
        known: dict[str, str],
        archived: set[str],
        reported_disagreements: set[str],
    ) -> None:
        self._require_deadline(context)
        page, versions = fetch_companion_page(client)
        page_digest = self._archive(context, page.body, "text/html", archived)
        observations.append(
            self._observation(
                context,
                "nfz_icd9_companion_page",
                "https://www.nfz.gov.pl/dla-swiadczeniodawcy/slowniki/pliki-icd-9-pl/",
                page_digest,
                {"response_headers": cast(dict[str, JsonValue], page.headers)},
            )
        )
        latest = max(versions, key=lambda item: tuple(map(int, item.split("."))))
        incident_id = _digest(["nfz_icd9_czs_disagreement", latest, symbol])
        if latest != symbol and incident_id not in reported_disagreements:
            incidents.append(
                IncidentDraft(
                    incident_id,
                    "nfz_icd9_czs_disagreement",
                    {
                        "nfz_xlsx_latest_version": latest,
                        "czs_version": symbol,
                        "czs_xml_sha256": czs_digest,
                        "nfz_page_sha256": page_digest,
                    },
                )
            )
            if len(reported_disagreements) >= 64:
                raise SourceDriftWatcherError("NFZ disagreement cursor is full")
            reported_disagreements.add(incident_id)
        url = versions.get(symbol)
        if url is None:
            known.clear()
            return
        self._require_deadline(context)
        xlsx = fetch_companion(client, url)
        digest = self._archive(context, xlsx.body, "application/zip", archived)
        observations.append(
            self._observation(
                context,
                "nfz_icd9_companion",
                url,
                digest,
                {
                    "version": symbol,
                    "czs_xml_sha256": czs_digest,
                    "nfz_page_sha256": page_digest,
                    "response_headers": cast(dict[str, JsonValue], xlsx.headers),
                },
            )
        )
        previous = known.get(url)
        if previous is not None and previous != digest:
            incidents.append(
                IncidentDraft(
                    _digest(["nfz_icd9_xlsx_changed", url, previous, digest]),
                    "nfz_icd9_xlsx_changed",
                    {"old_sha256": previous, "new_sha256": digest},
                )
            )
        known.clear()
        known[url] = digest

    def _collect_aotmit(
        self,
        context: CollectionContext,
        client: httpx.Client,
        observations: list[ObservationDraft],
        changes: list[ChangeDraft],
        incidents: list[IncidentDraft],
        known: dict[str, str],
        archived: set[str],
    ) -> None:
        self._require_deadline(context)
        listing, notices = fetch_aotmit_listing(client)
        url = "https://bip.aotm.gov.pl/taryfikacja-swiadczen/860-taryfikacja-obwieszczenia"
        digest = self._archive(context, listing.body, "text/html", archived)
        observations.append(
            self._observation(
                context,
                "aotmit_listing",
                url,
                digest,
                {"response_headers": cast(dict[str, JsonValue], listing.headers)},
            )
        )
        recent_year = max(int(item.published[:4]) for item in notices)
        selected = [item for item in notices if int(item.published[:4]) == recent_year]
        if not selected or len(selected) > 12:
            raise AotmitSourceError(
                "AOTMiT recent hospital PDF set is outside its bound"
            )
        honor_retry_after(
            listing,
            remaining_seconds=(context.run.identity.deadline_at_ms - self._now_ms())
            / 1000,
        )
        current: dict[str, str] = {}
        for item in selected:
            self._require_deadline(context)
            pdf = fetch_aotmit_pdf(client, item)
            digest = self._archive(context, pdf.body, "application/pdf", archived)
            observation = self._observation(
                context,
                "aotmit_tariff_notice",
                item.url,
                digest,
                {
                    "title": item.title,
                    "published_date": item.published,
                    "response_headers": cast(dict[str, JsonValue], pdf.headers),
                },
            )
            observations.append(observation)
            previous = known.get(item.url)
            if previous is None:
                changes.append(
                    ChangeDraft(
                        _digest(["aotmit_tariff_notice", context.source_id, item.url]),
                        observation.observation_id,
                        "aotmit_tariff_signal",
                        {"title": item.title, "published_date": item.published},
                    )
                )
            elif previous != digest:
                incidents.append(
                    IncidentDraft(
                        _digest(
                            ["aotmit_tariff_bytes_changed", item.url, previous, digest]
                        ),
                        "aotmit_tariff_bytes_changed",
                        {"old_sha256": previous, "new_sha256": digest},
                    )
                )
            current[item.url] = digest
            honor_retry_after(
                pdf,
                remaining_seconds=(context.run.identity.deadline_at_ms - self._now_ms())
                / 1000,
            )
        known.clear()
        known.update(current)

    def _collect_eli(
        self,
        context: CollectionContext,
        client: httpx.Client,
        observations: list[ObservationDraft],
        changes: list[ChangeDraft],
        known: dict[str, str],
        archived: set[str],
    ) -> None:
        for watched in WATCHED:
            answers: list[EliResponse] = []
            digests: list[str] = []
            for eli in (watched.pinned, watched.base):
                self._require_deadline(context)
                answers.append(fetch_act(client, eli))
                digests.append(
                    self._archive(
                        context, answers[-1].body, "application/json", archived
                    )
                )
            pinned, base = answers
            found = findings(watched, pinned.act, base.act)
            observation = self._observation(
                context,
                "eli_dictionary",
                pinned.url,
                digests[0],
                {
                    "dictionary": watched.name,
                    "eli": watched.pinned,
                    "base_eli": watched.base,
                    "in_force": pinned.act.inForce,
                    "findings": cast(list[JsonValue], found),
                    "base_sha256": digests[1],
                    "response_headers": cast(dict[str, JsonValue], pinned.headers),
                },
                (ObservationBlob("base_act", 0, digests[1]),),
            )
            observations.append(observation)
            observations.append(
                self._observation(
                    context,
                    "eli_base_act",
                    base.url,
                    digests[1],
                    {
                        "dictionary": watched.name,
                        "eli": watched.base,
                        "in_force": base.act.inForce,
                        "response_headers": cast(dict[str, JsonValue], base.headers),
                    },
                )
            )
            state = _digest(["eli_legal_dictionary", watched.name, list(found)])
            # One review item per distinct set of differences, not one per run.
            if found and known.get(watched.name) != state:
                changes.append(
                    ChangeDraft(
                        # The run's observation keeps a finding that returns
                        # after it cleared from reusing a committed change ID.
                        _digest(
                            [
                                "eli_legal_dictionary",
                                context.source_id,
                                watched.name,
                                list(found),
                                observation.observation_id,
                            ]
                        ),
                        observation.observation_id,
                        "eli_legal_dictionary",
                        {
                            "dictionary": watched.name,
                            "pinned_eli": watched.pinned,
                            "findings": list(found),
                        },
                    )
                )
            known[watched.name] = state

    def _collect_jgp(
        self,
        context: CollectionContext,
        client: httpx.Client,
        observations: list[ObservationDraft],
        changes: list[ChangeDraft],
        incidents: list[IncidentDraft],
        signals: dict[str, str],
        prior_files: dict[str, str],
        archived: set[str],
    ) -> None:
        self._require_deadline(context)
        listing, candidates = fetch_signal_listing(client, "jgp")
        url = "https://www.nfz.gov.pl/dla-swiadczeniodawcy/jednorodne-grupy-pacjentow/komunikaty-jgp/"
        digest = self._archive(context, listing.body, "text/html", archived)
        observations.append(
            self._observation(
                context,
                "nfz_jgp_listing",
                url,
                digest,
                {"response_headers": cast(dict[str, JsonValue], listing.headers)},
            )
        )
        latest_id = max(item.item_id for item in candidates)
        known_ids = [
            int(key.split(":", 1)[1]) for key in signals if key.startswith("jgp:")
        ]
        previous_latest = max(known_ids, default=0)
        if previous_latest and latest_id < previous_latest:
            raise NfzListingError("NFZ JGP latest notice regressed")
        selected = (
            [item for item in candidates if item.item_id >= previous_latest]
            if previous_latest
            else [item for item in candidates if item.item_id == latest_id]
        )
        if not selected or len(selected) > 4:
            raise NfzListingError("NFZ JGP notice catch-up exceeds its bound")
        current_files: dict[str, str] = {}
        for item in sorted(selected, key=lambda value: value.item_id):
            self._require_deadline(context)
            detail = fetch_signal_detail(client, item)
            files = parse_jgp_files(detail.body, item.item_id)
            detail_digest = self._archive(context, detail.body, "text/html", archived)
            observation = self._observation(
                context,
                "nfz_jgp_detail",
                item.url,
                detail_digest,
                {
                    "item_id": item.item_id,
                    "title": item.title,
                    "published_date": item.published_date,
                    "file_urls": cast(list[JsonValue], list(files)),
                    "response_headers": cast(dict[str, JsonValue], detail.headers),
                },
            )
            observations.append(observation)
            key = f"jgp:{item.item_id}"
            old_detail = signals.get(key)
            if old_detail is None:
                if len(signals) >= _MAX_SIGNALS:
                    raise SourceDriftWatcherError("NFZ JGP cursor is full")
                changes.append(
                    ChangeDraft(
                        _digest(["nfz_jgp_notice", context.source_id, item.item_id]),
                        observation.observation_id,
                        "nfz_jgp_notice",
                        {
                            "item_id": item.item_id,
                            "title": item.title,
                            "file_count": len(files),
                        },
                    )
                )
            elif old_detail != detail_digest:
                incidents.append(
                    IncidentDraft(
                        _digest(
                            ["nfz_jgp_detail_changed", key, old_detail, detail_digest]
                        ),
                        "nfz_jgp_detail_changed",
                        {
                            "item_id": item.item_id,
                            "old_sha256": old_detail,
                            "new_sha256": detail_digest,
                        },
                    )
                )
            signals[key] = detail_digest
            for file_url in files:
                self._require_deadline(context)
                file = fetch_jgp_file(client, file_url, item.item_id)
                file_digest = self._archive(
                    context, file.body, "application/zip", archived
                )
                observations.append(
                    self._observation(
                        context,
                        "nfz_jgp_file",
                        file_url,
                        file_digest,
                        {
                            "item_id": item.item_id,
                            "detail_sha256": detail_digest,
                            "response_headers": cast(
                                dict[str, JsonValue], file.headers
                            ),
                        },
                    )
                )
                previous_digest = prior_files.get(file_url)
                if previous_digest is not None and previous_digest != file_digest:
                    incidents.append(
                        IncidentDraft(
                            _digest(
                                [
                                    "nfz_jgp_file_changed",
                                    file_url,
                                    previous_digest,
                                    file_digest,
                                ]
                            ),
                            "nfz_jgp_file_changed",
                            {
                                "item_id": item.item_id,
                                "old_sha256": previous_digest,
                                "new_sha256": file_digest,
                            },
                        )
                    )
                if item.item_id == latest_id:
                    current_files[file_url] = file_digest
        if len(current_files) > 8:
            raise SourceDriftWatcherError("NFZ JGP current files exceed the bound")
        prior_files.clear()
        prior_files.update(current_files)
        for key in tuple(signals):
            if key.startswith("jgp:") and int(key.split(":", 1)[1]) < latest_id:
                del signals[key]

    def _collect_signals(
        self,
        context: CollectionContext,
        client: httpx.Client,
        observations: list[ObservationDraft],
        changes: list[ChangeDraft],
        incidents: list[IncidentDraft],
        signals: dict[str, str],
        archived: set[str],
        baseline_notice_recorded: bool,
    ) -> bool:
        for channel in ("news", "project"):
            pages = (1, 2) if channel == "news" else (1,)
            seen: dict[int, NfzSignal] = {}
            for page in pages:
                self._require_deadline(context)
                listing, candidates = fetch_signal_listing(client, channel, page=page)
                url = (
                    "https://www.nfz.gov.pl/aktualnosci/aktualnosci-centrali/"
                    if channel == "news"
                    else "https://www.nfz.gov.pl/zarzadzenia-prezesa/projekty-zarzadzen/"
                )
                if page == 2:
                    url += "2,strona.html"
                digest = self._archive(context, listing.body, "text/html", archived)
                observations.append(
                    self._observation(
                        context,
                        "nfz_signal_listing",
                        url,
                        digest,
                        {
                            "channel": channel,
                            "page": page,
                            "response_headers": cast(
                                dict[str, JsonValue], listing.headers
                            ),
                        },
                    )
                )
                for item in candidates:
                    earlier = seen.get(item.item_id)
                    if earlier is not None and earlier != item:
                        raise NfzListingError(
                            "NFZ signal has conflicting pagination entries"
                        )
                    seen[item.item_id] = item
            if channel == "news" and not baseline_notice_recorded:
                earlier = seen.get(_BASELINE_NOTICE.item_id)
                if earlier is not None and earlier.url != _BASELINE_NOTICE.url:
                    raise NfzListingError("NFZ baseline notice URL changed")
                _ = seen.setdefault(_BASELINE_NOTICE.item_id, _BASELINE_NOTICE)
            if len(seen) > 64:
                raise SourceDriftWatcherError("NFZ signal page set exceeds its bound")
            for item in seen.values():
                self._require_deadline(context)
                detail = fetch_signal_detail(client, item)
                if item.item_id == 8987 and (
                    b"74/2026" not in detail.body or b"1ae" not in detail.body.lower()
                ):
                    raise NfzListingError(
                        "NFZ baseline JGP notice lost its settlement references"
                    )
                digest = self._archive(context, detail.body, "text/html", archived)
                observation = self._observation(
                    context,
                    "nfz_signal_detail",
                    item.url,
                    digest,
                    {
                        "channel": channel,
                        "item_id": item.item_id,
                        "title": item.title,
                        "published_date": item.published_date,
                        "summary": item.summary,
                        "response_headers": cast(dict[str, JsonValue], detail.headers),
                    },
                )
                observations.append(observation)
                key = f"{channel}:{item.item_id}"
                previous = signals.get(key)
                if previous is None:
                    if len(signals) >= _MAX_SIGNALS:
                        raise SourceDriftWatcherError("NFZ signal cursor is full")
                    changes.append(
                        ChangeDraft(
                            _digest(["nfz_signal", context.source_id, key]),
                            observation.observation_id,
                            "nfz_news" if channel == "news" else "nfz_project_notice",
                            {
                                "channel": channel,
                                "item_id": item.item_id,
                                "title": item.title,
                            },
                        )
                    )
                elif previous != digest:
                    incidents.append(
                        IncidentDraft(
                            _digest(
                                ["nfz_signal_bytes_changed", key, previous, digest]
                            ),
                            "nfz_signal_bytes_changed",
                            {
                                "channel": channel,
                                "item_id": item.item_id,
                                "old_sha256": previous,
                                "new_sha256": digest,
                            },
                        )
                    )
                signals[key] = digest
            for key in tuple(signals):
                if (
                    key.startswith(channel + ":")
                    and int(key.split(":", 1)[1]) not in seen
                ):
                    del signals[key]
            if channel == "news" and _BASELINE_NOTICE.item_id in seen:
                baseline_notice_recorded = True
        return baseline_notice_recorded

    def _archive(
        self,
        context: CollectionContext,
        body: bytes,
        media_type: str,
        archived: set[str],
    ) -> str:
        digest = hashlib.sha256(body).hexdigest()
        with self._staging.stage() as writer:
            offset = 0
            while offset < len(body):
                written = writer.write(body[offset:])
                if written < 1:
                    raise WatcherRuntimeError("NFZ metadata could not be staged")
                offset += written
            blob = writer.seal(
                media_type=media_type,
                expected_sha256=digest,
                expected_size_bytes=len(body),
            )
            if digest not in archived:
                _ = context.archive.put(blob)
                archived.add(digest)
        return digest

    def _observation(
        self,
        context: CollectionContext,
        kind: str,
        url: str,
        digest: str,
        metadata: dict[str, JsonValue],
        blobs: tuple[ObservationBlob, ...] = (),
    ) -> ObservationDraft:
        return ObservationDraft(
            _digest([context.run.identity.run_id, kind, url, digest]),
            kind,
            url,
            digest,
            self._now_ms(),
            metadata,
            blobs,
        )

    def _require_deadline(self, context: CollectionContext) -> None:
        if self._now_ms() >= context.run.identity.deadline_at_ms:
            raise WatcherRuntimeError("NFZ collection exceeded its claim deadline")


def _digest(value: list[JsonValue]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
