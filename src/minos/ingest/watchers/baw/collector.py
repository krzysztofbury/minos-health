import hashlib
import json
import time
import zipfile
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from email.message import Message
from pathlib import PurePosixPath
from typing import BinaryIO, ClassVar, cast, final
from urllib.parse import unquote_to_bytes

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.ingest.watchers.archive import BlobStagingArea, StagedBlob
from minos.ingest.watchers.archive.files import open_verified_staged_blob
from minos.ingest.watchers.baw.client import (
    MAX_FILE_BYTES,
    BawDocument,
    BawRelation,
    BawResponse,
    BawRetryableError,
    BawSourceError,
    create_baw_client,
    get_json,
    parse_document,
    parse_relations,
)
from minos.ingest.watchers.config import BawConfiguration
from minos.ingest.watchers.control import (
    ChangeDraft,
    JsonValue,
    ObservationBlob,
    ObservationDraft,
)
from minos.ingest.watchers.protocols import (
    CollectedRun,
    CollectionContext,
    RetryableWatcherError,
    WatcherRuntimeError,
)

_MAX_DOCUMENTS = 32
_MAX_FILES = 256
_MAX_DEPTH = 8
_MAX_ZIP_ENTRIES = 2048
_MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
_RETRYABLE_HTTP = frozenset({408, 429, 500, 502, 503, 504})


class _Cursor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(ge=1, le=1)
    documents: dict[str, str] = Field(max_length=_MAX_DOCUMENTS)
    relations: dict[str, str] = Field(max_length=_MAX_DOCUMENTS)
    attachments: dict[str, str] = Field(max_length=_MAX_DOCUMENTS)
    products: dict[str, str] = Field(max_length=_MAX_DOCUMENTS * 4)
    state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class _File:
    document_id: int
    file_id: int
    parent_id: int | None
    name: str
    display_name: str | None
    zipx_attachment: bool
    root: bool

    @property
    def identity(self) -> str:
        return f"{self.document_id}:{self.file_id}"

    @property
    def filename(self) -> str:
        return self.display_name or self.name


@final
class BawSourceCollector:
    def __init__(
        self,
        staging: BlobStagingArea,
        *,
        client_factory: Callable[
            [], AbstractContextManager[httpx.Client]
        ] = create_baw_client,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._staging = staging
        self._client_factory = client_factory
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)

    @contextmanager
    def collect(self, context: CollectionContext) -> Generator[CollectedRun]:
        try:
            yield self._collect(context)
        except httpx.TransportError, BawRetryableError:
            raise RetryableWatcherError("transient BAW collection failure") from None

    def _collect(self, context: CollectionContext) -> CollectedRun:
        try:
            settings = BawConfiguration.model_validate(context.configuration)
        except ValidationError:
            raise WatcherRuntimeError("stored BAW configuration is invalid") from None
        prior = _prior_cursor(context.cursor)
        digests: set[str] = set()
        observations: list[ObservationDraft] = []
        document_digests: dict[str, str] = {}
        relation_digests: dict[str, str] = {}
        attachment_digests: dict[str, str] = {}
        products: dict[str, str] = {}
        selected: list[_File] = []
        downloaded_document_ids: set[int] = set()
        details: dict[int, bytes] = {}
        relations: dict[int, bytes] = {}
        attachments: dict[int, bytes] = {}
        pending = list(settings.document_ids)
        all_links: dict[int, BawRelation] = {}
        with self._client_factory() as client:
            for seed_id in settings.document_ids:
                self._require_deadline(context)
                relation = get_json(
                    client, f"/api/documents/GetAmendments/{seed_id}", optional=True
                )
                attachment = get_json(
                    client,
                    f"/api/documents/4/GetDocumentAttachments/{seed_id}/true",
                    optional=True,
                )
                links = parse_relations(relation)
                if not links or seed_id not in {link.Id for link in links}:
                    raise BawSourceError(
                        "BAW source document has no legal relationships"
                    )
                relations[seed_id] = relation.body
                attachments[seed_id] = attachment.body
                relation_digests[str(seed_id)] = self._archive_metadata(
                    context, relation, digests
                )
                attachment_digests[str(seed_id)] = self._archive_metadata(
                    context, attachment, digests
                )
                observations.append(
                    _observation(
                        context,
                        "baw_relations",
                        str(seed_id),
                        relation.body,
                        relation_digests[str(seed_id)],
                        {"linked_documents": len(links)},
                    )
                )
                observations.append(
                    _observation(
                        context,
                        "baw_attachment_relations",
                        str(seed_id),
                        attachment.body,
                        attachment_digests[str(seed_id)],
                        {},
                    )
                )
                for link in links:
                    if link.IsProject:
                        continue
                    old = all_links.setdefault(link.Id, link)
                    if old != link:
                        raise BawSourceError("BAW legal relationships conflict")
                    pending.append(link.Id)
                if len(pending) > _MAX_DOCUMENTS * 8:
                    raise BawSourceError("BAW relation traversal exceeds its bound")
            for link in all_links.values():
                if any(
                    linked is not None and linked not in all_links
                    for linked in (link.SourceDocumentId, link.IntroductoryDocumentId)
                ):
                    raise BawSourceError(
                        "BAW relationship points outside the document graph"
                    )
            links_for_files = tuple(all_links.values())
            while pending:
                self._require_deadline(context)
                document_id = pending.pop(0)
                if document_id in details:
                    continue
                if len(details) >= _MAX_DOCUMENTS:
                    raise BawSourceError("BAW document graph exceeds its bound")
                response = get_json(
                    client, f"/api/documents/4/details/{document_id}/null"
                )
                document = parse_document(response, document_id)
                details[document_id] = response.body
                selected.extend(_selected_files(document, links_for_files))
                document_digests[str(document_id)] = self._archive_metadata(
                    context, response, digests
                )
                observations.append(
                    _observation(
                        context,
                        "baw_document",
                        str(document_id),
                        response.body,
                        document_digests[str(document_id)],
                        {
                            "act_number": document.ActNumber,
                            "act_date": document.ActDate,
                            "baw_published_date": document.PublishedDate,
                            "entry_into_force": document.ValidFrom,
                            "service_applicability_date": None,
                            "service_applicability_evidence": None,
                            "valid_to": document.ValidTo,
                            "legal_status": cast(
                                JsonValue, document.LegalActStatus.get("Name")
                            ),
                        },
                    )
                )
            selected.sort(key=lambda item: (item.document_id, item.file_id))
            if len(selected) > _MAX_DOCUMENTS * 4:
                raise BawSourceError("BAW selected file count exceeds its bound")
            for file in selected:
                self._require_deadline(context)
                previous = None if prior is None else prior.products.get(file.identity)
                old_document = (
                    None
                    if prior is None
                    else prior.documents.get(str(file.document_id))
                )
                if (
                    previous is not None
                    and old_document == document_digests[str(file.document_id)]
                    and context.run.run_mode not in ("manual", "weekly_verification")
                ):
                    products[file.identity] = previous
                    continue
                blob = self._download(context, client, file, digests)
                downloaded_document_ids.add(file.document_id)
                products[file.identity] = blob.sha256
                observations.append(
                    _observation(
                        context,
                        "baw_file",
                        file.identity,
                        blob.sha256.encode("ascii"),
                        blob.sha256,
                        {
                            "document_id": file.document_id,
                            "file_id": file.file_id,
                            "parent_id": file.parent_id,
                            "name": file.name,
                            "display_name": file.display_name,
                            "size_bytes": blob.size_bytes,
                        },
                        "product",
                    )
                )
            # Re-check the source views used to select bytes before advancing state.
            for document_id in sorted(downloaded_document_ids):
                self._require_deadline(context)
                refetch = get_json(
                    client, f"/api/documents/4/details/{document_id}/null"
                )
                if refetch.body != details[document_id]:
                    raise BawSourceError("BAW document changed during file collection")
            for seed_id in settings.document_ids if downloaded_document_ids else ():
                self._require_deadline(context)
                refetch_links = get_json(
                    client, f"/api/documents/GetAmendments/{seed_id}", optional=True
                )
                if refetch_links.body != relations[seed_id]:
                    raise BawSourceError(
                        "BAW legal relationships changed during collection"
                    )
                refetch_attachments = get_json(
                    client,
                    f"/api/documents/4/GetDocumentAttachments/{seed_id}/true",
                    optional=True,
                )
                if refetch_attachments.body != attachments[seed_id]:
                    raise BawSourceError(
                        "BAW attachment relationships changed during collection"
                    )
        self._require_deadline(context)
        state = _digest(
            {
                "documents": document_digests,
                "relations": relation_digests,
                "attachments": attachment_digests,
                "products": products,
            }
        )
        cursor = _Cursor(
            schema_version=1,
            documents=document_digests,
            relations=relation_digests,
            attachments=attachment_digests,
            products=products,
            state_sha256=state,
        )
        changes: tuple[ChangeDraft, ...] = ()
        if prior is None or prior.state_sha256 != state:
            summary = cast(
                dict[str, JsonValue],
                {
                    "previous_state_sha256": None
                    if prior is None
                    else prior.state_sha256,
                    "current_state_sha256": state,
                    "document_ids": sorted(details),
                    "selected_file_count": len(selected),
                },
            )
            changes = (
                ChangeDraft(
                    _digest([context.run.identity.run_id, summary]),
                    observations[0].observation_id,
                    "baw_baseline" if prior is None else "baw_source_change",
                    summary,
                ),
            )
        return CollectedRun(
            tuple(observations),
            cast(dict[str, JsonValue], cursor.model_dump(mode="json")),
            changes,
        )

    def _archive_metadata(
        self, context: CollectionContext, response: BawResponse, digests: set[str]
    ) -> str:
        with self._staging.stage() as writer:
            offset = 0
            while offset < len(response.body):
                written = writer.write(response.body[offset:])
                if written < 1:
                    raise BawSourceError("BAW metadata could not be staged completely")
                offset += written
            blob = writer.seal(
                media_type="application/json",
                expected_sha256=hashlib.sha256(response.body).hexdigest(),
                expected_size_bytes=len(response.body),
            )
            if blob.sha256 not in digests:
                _ = context.archive.put(blob)
                digests.add(blob.sha256)
            return blob.sha256

    def _download(
        self,
        context: CollectionContext,
        client: httpx.Client,
        file: _File,
        digests: set[str],
    ) -> StagedBlob:
        endpoint = "GetZipxAttachment/4" if file.zipx_attachment else "4"
        with client.stream("GET", f"/api/file/{endpoint}/{file.file_id}") as response:
            if response.status_code in _RETRYABLE_HTTP:
                raise BawRetryableError("BAW download temporarily failed")
            if response.status_code != 200:
                raise BawSourceError("BAW download failed")
            media_type = (
                cast(str, response.headers.get("content-type", ""))
                .split(";", 1)[0]
                .lower()
            )
            if media_type not in (
                "application/attachment",
                "application/octet-stream",
                "application/zip",
            ):
                raise BawSourceError("BAW download is not an attachment")
            _check_disposition(response, file)
            length = cast(str | None, response.headers.get("content-length"))
            if length is not None and (
                not length.isascii()
                or not length.isdigit()
                or int(length) > MAX_FILE_BYTES
            ):
                raise BawSourceError("BAW file length is invalid")
            with self._staging.stage() as writer:
                for chunk in response.iter_bytes(64 * 1024):
                    self._require_deadline(context)
                    offset = 0
                    while offset < len(chunk):
                        written = writer.write(chunk[offset:])
                        if written < 1:
                            raise BawSourceError("BAW file staging was incomplete")
                        offset += written
                if writer.size_bytes == 0 or (
                    length is not None and writer.size_bytes != int(length)
                ):
                    raise BawSourceError("BAW file is empty or truncated")
                blob = writer.seal(media_type=media_type)
                with open_verified_staged_blob(blob) as source:
                    _check_zip(source, file.filename)
                if blob.sha256 not in digests:
                    _ = context.archive.put(blob)
                    digests.add(blob.sha256)
                return blob

    def _require_deadline(self, context: CollectionContext) -> None:
        if self._now_ms() >= context.run.identity.deadline_at_ms:
            raise RetryableWatcherError("BAW collection claim expired")


def _selected_files(
    document: BawDocument, links: tuple[BawRelation, ...]
) -> list[_File]:
    # Relationship rows classify original, consolidated, and amendment documents.
    roles = {link.AutomaticType for link in links if link.Id == document.Id}
    if not roles:
        raise BawSourceError("BAW document has no classified relationship")
    if len(roles) != 1:
        raise BawSourceError("BAW document has conflicting legal roles")
    role = roles.pop()
    if role not in (1, 3, 4, 5, 6):
        raise BawSourceError("BAW legal document role is unsupported")
    selected: list[_File] = []
    seen: set[int] = set()
    stack: list[tuple[dict[str, object], int, int | None]] = [
        (node, 0, None) for node in document.Files
    ]
    while stack:
        node, depth, parent = stack.pop()
        if depth > _MAX_DEPTH or len(seen) >= _MAX_FILES:
            raise BawSourceError("BAW file tree is invalid or exceeds its bound")
        file_id, name, children = node.get("Id"), node.get("Name"), node.get("Children")
        if (
            type(file_id) is not int
            or file_id <= 0
            or file_id in seen
            or not isinstance(name, str)
            or len(name) > 255
            or not isinstance(children, list)
        ):
            raise BawSourceError("BAW file tree contains an invalid file")
        descendants = cast(list[object], children)
        if len(descendants) > _MAX_FILES or any(
            not isinstance(child, dict) for child in descendants
        ):
            raise BawSourceError("BAW file tree contains invalid children")
        seen.add(file_id)
        display = node.get("DisplayName")
        zipx = node.get("IsZipxAttachment")
        if display is not None and (not isinstance(display, str) or len(display) > 255):
            raise BawSourceError("BAW display filename is invalid")
        if type(zipx) is not bool or node.get("DocumentId") != document.Id:
            raise BawSourceError("BAW file belongs to another document")
        if node.get("ParentId") != parent and not (
            parent is None and node.get("ParentId") == 0
        ):
            raise BawSourceError("BAW file parent does not match its tree")
        file = _File(document.Id, file_id, parent, name, display, zipx, depth == 0)
        if role in (1, 3, 6) and (
            (file.root and name.lower().endswith(".zipx"))
            or name.lower()
            in (
                {"zalacznik1a.xlsx", "zalacznik9.xlsx"}
                | ({"zalacznik1.xlsx"} if role == 6 else set())
            )
        ):
            selected.append(file)
        stack.extend(
            (cast(dict[str, object], child), depth + 1, file_id)
            for child in descendants
        )
    if role in (1, 3, 6) and not any(file.root for file in selected):
        raise BawSourceError("BAW legal document is missing its root ZIPX")
    return selected


def _check_disposition(response: httpx.Response, file: _File) -> None:
    value = cast(str, response.headers.get("content-disposition", ""))
    message = Message()
    message["Content-Disposition"] = value
    filename = message.get_filename()
    extended = next(
        (
            item.strip().split("=", 1)[1]
            for item in value.split(";")
            if item.strip().lower().startswith("filename*=")
        ),
        None,
    )
    if extended is not None:
        if not extended.startswith("UTF-8''"):
            raise BawSourceError("BAW attachment filename encoding is unsupported")
        try:
            filename = unquote_to_bytes(extended[7:]).decode("utf-8")
        except UnicodeDecodeError:
            raise BawSourceError("BAW attachment filename is invalid UTF-8") from None
    if (
        not value.lower().startswith("attachment;")
        or not filename
        or filename.lower()
        not in {file.name.lower(), (file.display_name or file.name).lower()}
    ):
        raise BawSourceError("BAW attachment filename does not match metadata")


def _check_zip(source: BinaryIO, filename: str) -> None:
    if not filename.lower().endswith((".zipx", ".xlsx", ".docx")):
        raise BawSourceError("BAW selected attachment format is unsupported")
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > _MAX_ZIP_ENTRIES:
                raise BawSourceError("BAW ZIP entry count is invalid")
            total = 0
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in entry.filename
                    or entry.flag_bits & 1
                    or entry.file_size > _MAX_UNCOMPRESSED_BYTES
                    or (
                        entry.compress_size > 0
                        and entry.file_size > entry.compress_size * 1000
                    )
                    or (entry.compress_size == 0 and entry.file_size > 0)
                ):
                    raise BawSourceError("BAW ZIP contains unsafe entries")
                total += entry.file_size
                if total > _MAX_UNCOMPRESSED_BYTES:
                    raise BawSourceError("BAW ZIP expansion exceeds its bound")
    except zipfile.BadZipFile, OSError, ValueError:
        raise BawSourceError("BAW attachment is not a valid ZIP") from None


def _prior_cursor(value: dict[str, JsonValue]) -> _Cursor | None:
    if not value:
        return None
    try:
        prior = _Cursor.model_validate(value)
    except ValidationError:
        raise WatcherRuntimeError("stored BAW cursor is invalid") from None
    if prior.state_sha256 != _digest(
        {
            "documents": prior.documents,
            "relations": prior.relations,
            "attachments": prior.attachments,
            "products": prior.products,
        }
    ):
        raise WatcherRuntimeError("stored BAW cursor digest is invalid")
    for digest in (
        *prior.documents.values(),
        *prior.relations.values(),
        *prior.attachments.values(),
        *prior.products.values(),
    ):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise WatcherRuntimeError("stored BAW cursor contains an invalid digest")
    return prior


def _observation(
    context: CollectionContext,
    kind: str,
    identity: str,
    body: bytes,
    digest: str,
    metadata: dict[str, JsonValue],
    role: str = "response",
) -> ObservationDraft:
    return ObservationDraft(
        _digest([context.run.identity.run_id, kind, identity]),
        kind,
        identity,
        digest if kind == "baw_file" else hashlib.sha256(body).hexdigest(),
        context.run.started_at_ms,
        metadata,
        (ObservationBlob(role, 0, digest),),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
        ).encode("ascii")
    ).hexdigest()
