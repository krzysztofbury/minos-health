import json
import os
import re
import stat
from pathlib import Path
from types import TracebackType
from typing import ClassVar, Literal, Self, cast, final, override
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_BASE_URL = "http://uptime-kuma:3001"
_MAX_TARGET_FILE_BYTES = 65_536
_MAX_RESPONSE_BYTES = 4096
_MAX_TARGETS = 128
_READ_CHUNK_BYTES = 4096
_SOURCE_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
# Uptime Kuma 2.5.5 generates push tokens with genSecret(32).
_PUSH_TOKEN = re.compile(r"[A-Za-z0-9]{32}")


class UptimeKumaError(RuntimeError):
    """A privacy-safe Uptime Kuma configuration or request failed."""


class _TargetsDocument(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    sources: dict[str, str] = Field(max_length=_MAX_TARGETS)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version(cls, version: object) -> object:
        if type(version) is not int or version != 1:
            raise ValueError("invalid version")
        return version

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, sources: dict[str, str]) -> dict[str, str]:
        for source_id, token in sources.items():
            _validate_source_id(source_id)
            _validate_token(token)
        return sources


@final
class UptimeKumaTargets:
    def __init__(self, sources: dict[str, str]) -> None:
        self._sources = dict(sources)

    @override
    def __repr__(self) -> str:
        return f"UptimeKumaTargets(sources={self.configured_sources()!r})"

    @classmethod
    def load(cls, path: str | Path) -> Self:
        encoded = _read_bounded_secret(Path(path))
        try:
            value = cast(
                object,
                json.loads(encoded, object_pairs_hook=_unique_object),
            )
            document = _TargetsDocument.model_validate(value)
        except (
            RecursionError,
            UnicodeDecodeError,
            UptimeKumaError,
            ValueError,
            ValidationError,
        ):
            raise UptimeKumaError("Uptime Kuma target file is invalid") from None
        return cls(document.sources)

    def token(self, source_id: str) -> str | None:
        _validate_source_id(source_id)
        return self._sources.get(source_id)

    def configured_sources(self) -> tuple[str, ...]:
        return tuple(sorted(self._sources))

    def require_sources(self, source_ids: tuple[str, ...]) -> None:
        if len(source_ids) > _MAX_TARGETS or len(source_ids) != len(set(source_ids)):
            raise UptimeKumaError("enabled Uptime Kuma source set is invalid")
        for source_id in source_ids:
            _validate_source_id(source_id)
            if source_id not in self._sources:
                raise UptimeKumaError("required Uptime Kuma source is not configured")


@final
class UptimeKumaClient:
    def __init__(
        self,
        targets: UptimeKumaTargets,
        *,
        timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise UptimeKumaError("Uptime Kuma timeout is outside the supported range")
        self._targets = targets
        self._client = httpx.Client(
            base_url=_BASE_URL,
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(
                max_connections=1,
                max_keepalive_connections=1,
                keepalive_expiry=5.0,
            ),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "minos-health/0.0.1 watcher"},
            transport=transport,
        )

    @override
    def __repr__(self) -> str:
        return f"UptimeKumaClient(sources={self._targets.configured_sources()!r})"

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _error_type: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def start(self, source_id: str) -> None:
        _validate_source_id(source_id)

    def success(self, source_id: str) -> None:
        self._push(source_id, status="up", message="completed")

    def failure(self, source_id: str) -> None:
        self._push(source_id, status="down", message="failed")

    def _push(
        self,
        source_id: str,
        *,
        status: Literal["up", "down"],
        message: Literal["completed", "failed"],
    ) -> None:
        token = self._targets.token(source_id)
        if token is None:
            return
        path = f"/api/push/{quote(token, safe='')}"
        try:
            with self._client.stream(
                "GET",
                path,
                params={"status": status, "msg": message, "ping": ""},
            ) as response:
                if response.status_code != 200:
                    raise UptimeKumaError("Uptime Kuma rejected the push")
                body = _read_bounded_response(response)
            value = cast(object, json.loads(body, object_pairs_hook=_unique_object))
        except UptimeKumaError:
            raise
        except httpx.HTTPError, OSError:
            raise UptimeKumaError("Uptime Kuma request failed") from None
        except RecursionError, UnicodeDecodeError, ValueError:
            raise UptimeKumaError("Uptime Kuma response is invalid") from None
        if not isinstance(value, dict):
            raise UptimeKumaError("Uptime Kuma response is invalid")
        document = cast(dict[object, object], value)
        if set(document) != {"ok"} or document.get("ok") is not True:
            raise UptimeKumaError("Uptime Kuma response is invalid")


def _read_bounded_secret(path: Path) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise UptimeKumaError("Uptime Kuma target file is not regular")
        if status.st_size < 2 or status.st_size > _MAX_TARGET_FILE_BYTES:
            raise UptimeKumaError("Uptime Kuma target file size is unsupported")
        chunks: list[bytes] = []
        received_bytes = 0
        maximum_reads = _MAX_TARGET_FILE_BYTES // _READ_CHUNK_BYTES + 2
        for _read in range(maximum_reads):
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            received_bytes += len(chunk)
            if received_bytes > _MAX_TARGET_FILE_BYTES:
                raise UptimeKumaError("Uptime Kuma target file size is unsupported")
            chunks.append(chunk)
        if received_bytes != status.st_size:
            raise UptimeKumaError("Uptime Kuma target file changed while reading")
        return b"".join(chunks)
    except UptimeKumaError:
        raise
    except OSError:
        raise UptimeKumaError("Uptime Kuma target file could not be read") from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_bounded_response(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    received_bytes = 0
    for chunk in response.iter_bytes(chunk_size=_MAX_RESPONSE_BYTES + 1):
        received_bytes += len(chunk)
        if received_bytes > _MAX_RESPONSE_BYTES:
            raise UptimeKumaError("Uptime Kuma response is invalid")
        chunks.append(chunk)
    return b"".join(chunks)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _validate_source_id(source_id: object) -> None:
    if not isinstance(source_id, str) or _SOURCE_ID.fullmatch(source_id) is None:
        raise UptimeKumaError("Uptime Kuma source ID is invalid")


def _validate_token(token: object) -> None:
    if not isinstance(token, str) or _PUSH_TOKEN.fullmatch(token) is None:
        raise UptimeKumaError("Uptime Kuma push token is invalid")
