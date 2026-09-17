from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HttpObservation:
    source_url: str
    body: bytes
    headers: tuple[tuple[str, str], ...]

    def header_map(self) -> Mapping[str, str]:
        return dict(self.headers)


@dataclass(frozen=True, slots=True)
class DownloadResult:
    source_url: str
    sha256: str
    size_bytes: int
    headers: tuple[tuple[str, str], ...]
