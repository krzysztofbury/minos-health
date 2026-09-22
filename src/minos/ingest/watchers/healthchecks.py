import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import ClassVar, Literal, Self, assert_never, cast, final
from urllib.parse import urlsplit

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

_MAX_TARGET_FILE_BYTES = 65_536
_MAX_TARGETS = 128
_SECRET_READ_CHUNK_BYTES = 4096
type HeartbeatEvent = Literal["start", "success", "failure"]


class HealthcheckError(RuntimeError):
    """A privacy-safe heartbeat operation failed."""


class HealthchecksFileSettings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MINOS_WATCHER_",
        extra="ignore",
        strict=True,
    )

    healthchecks_targets_file: Path


@dataclass(frozen=True, slots=True)
class _PingTarget:
    url: str = field(repr=False)


@final
class HealthcheckTargets:
    def __init__(
        self,
        sources: dict[str, _PingTarget],
        preflight: _PingTarget | None,
    ) -> None:
        self._sources = dict(sources)
        self._preflight = preflight

    @classmethod
    def load(cls, path: str | Path) -> Self:
        encoded = _read_bounded_secret(Path(path))
        try:
            value = cast(object, json.loads(encoded))
        except json.JSONDecodeError, UnicodeDecodeError, RecursionError:
            raise HealthcheckError("healthcheck target file is invalid") from None
        if not isinstance(value, dict):
            raise HealthcheckError("healthcheck target file has an invalid shape")
        document = cast(dict[object, object], value)
        if set(document) != {
            "version",
            "sources",
            "preflight",
        }:
            raise HealthcheckError("healthcheck target file has an invalid shape")
        if document.get("version") != 1:
            raise HealthcheckError("healthcheck target file version is unsupported")
        raw_sources = document.get("sources")
        if not isinstance(raw_sources, dict):
            raise HealthcheckError("healthcheck source targets are invalid")
        source_document = cast(dict[object, object], raw_sources)
        if len(source_document) > _MAX_TARGETS:
            raise HealthcheckError("healthcheck target count exceeds its bound")
        sources: dict[str, _PingTarget] = {}
        for source_id, raw_url in source_document.items():
            if not isinstance(source_id, str):
                raise HealthcheckError("healthcheck source target is invalid")
            _validate_source_id(source_id)
            if not isinstance(raw_url, str):
                raise HealthcheckError("healthcheck source target is invalid")
            sources[source_id] = _PingTarget(_validate_target_url(raw_url))
        raw_preflight = document.get("preflight")
        if raw_preflight is not None and not isinstance(raw_preflight, str):
            raise HealthcheckError("healthcheck preflight target is invalid")
        preflight = (
            None
            if raw_preflight is None
            else _PingTarget(_validate_target_url(raw_preflight))
        )
        return cls(sources, preflight)

    def source(self, source_id: str) -> _PingTarget:
        _validate_source_id(source_id)
        target = self._sources.get(source_id)
        if target is None:
            raise HealthcheckError("healthcheck source target is not configured")
        return target

    def preflight(self) -> _PingTarget:
        if self._preflight is None:
            raise HealthcheckError("healthcheck preflight target is not configured")
        return self._preflight

    def configured_sources(self) -> tuple[str, ...]:
        return tuple(sorted(self._sources))

    def require_sources(self, source_ids: tuple[str, ...]) -> None:
        if len(source_ids) > _MAX_TARGETS or len(source_ids) != len(set(source_ids)):
            raise HealthcheckError("enabled source target set is invalid")
        for source_id in source_ids:
            _ = self.source(source_id)


@final
class HealthchecksClient:
    def __init__(
        self,
        targets: HealthcheckTargets,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise HealthcheckError("healthcheck timeout is outside the supported range")
        self._targets = targets
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=timeout_seconds),
            limits=httpx.Limits(
                max_connections=1,
                max_keepalive_connections=1,
                keepalive_expiry=5.0,
            ),
            follow_redirects=False,
            headers={"User-Agent": "minos-health/0.0.1 watcher"},
            transport=transport,
        )

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
        self._ping(self._targets.source(source_id), "start")

    def success(self, source_id: str) -> None:
        self._ping(self._targets.source(source_id), "success")

    def failure(self, source_id: str) -> None:
        self._ping(self._targets.source(source_id), "failure")

    def check_preflight(self) -> None:
        self._ping(self._targets.preflight(), "success")

    def check(self) -> None:
        self.check_preflight()

    def _ping(self, target: _PingTarget, event: HeartbeatEvent) -> None:
        url = _event_url(target.url, event)
        try:
            with self._client.stream("POST", url, content=b"") as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise HealthcheckError("healthcheck endpoint rejected the ping")
        except HealthcheckError:
            raise
        except httpx.HTTPError, OSError:
            raise HealthcheckError("healthcheck request failed") from None


def _read_bounded_secret(path: Path) -> str:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise HealthcheckError("healthcheck target file is not regular")
        if status.st_size < 2 or status.st_size > _MAX_TARGET_FILE_BYTES:
            raise HealthcheckError("healthcheck target file size is unsupported")
        chunks: list[bytes] = []
        received_bytes = 0
        maximum_reads = _MAX_TARGET_FILE_BYTES // _SECRET_READ_CHUNK_BYTES + 1
        for _read in range(maximum_reads):
            chunk = os.read(
                descriptor,
                min(_SECRET_READ_CHUNK_BYTES, status.st_size - received_bytes),
            )
            if not chunk:
                break
            chunks.append(chunk)
            received_bytes += len(chunk)
        if received_bytes != status.st_size:
            raise HealthcheckError("healthcheck target file changed while reading")
        return b"".join(chunks).decode("utf-8")
    except HealthcheckError:
        raise
    except OSError, UnicodeDecodeError:
        raise HealthcheckError("healthcheck target file could not be read") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_target_url(url: str) -> str:
    if len(url) < 10 or len(url) > 2048:
        raise HealthcheckError("healthcheck target URL is invalid")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise HealthcheckError("healthcheck target URL is invalid") from None
    if parsed.scheme != "https" or parsed.hostname is None:
        raise HealthcheckError("healthcheck target URL is invalid")
    if parsed.username is not None or parsed.password is not None:
        raise HealthcheckError("healthcheck target URL is invalid")
    if parsed.query or parsed.fragment or not parsed.path or port not in (None, 443):
        raise HealthcheckError("healthcheck target URL is invalid")
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in url):
        raise HealthcheckError("healthcheck target URL is invalid")
    return url.rstrip("/")


def _event_url(base_url: str, event: HeartbeatEvent) -> str:
    if event == "success":
        return base_url
    if event == "start":
        return f"{base_url}/start"
    if event == "failure":
        return f"{base_url}/fail"
    assert_never(event)


def _validate_source_id(source_id: object) -> None:
    if not isinstance(source_id, str) or len(source_id) < 1 or len(source_id) > 64:
        raise HealthcheckError("healthcheck source ID is invalid")
