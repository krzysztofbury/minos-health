from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from minos.ingest.watchers.control.models import JsonValue

_MAX_SOURCE_CONFIGURATION_CODECS = 64


class SourceConfigurationCodecError(ValueError):
    """A source configuration cannot be validated or decoded safely."""


class SourceConfigurationCodec(Protocol):
    def canonicalize(
        self,
        configuration: dict[str, JsonValue],
    ) -> dict[str, JsonValue]: ...

    def claim_timeout_ms(self, canonical_configuration_json: str) -> int: ...


@dataclass(frozen=True, slots=True, init=False)
class SourceConfigurationCodecRegistry:
    _codecs: Mapping[str, SourceConfigurationCodec]

    def __init__(
        self,
        codecs: Mapping[str, SourceConfigurationCodec] | None = None,
    ) -> None:
        if codecs is None:
            object.__setattr__(self, "_codecs", MappingProxyType({}))
            return
        if len(codecs) > _MAX_SOURCE_CONFIGURATION_CODECS:
            raise ValueError("source configuration codec registry exceeds its bound")
        registered: dict[str, SourceConfigurationCodec] = {}
        for visit_count, (declared_watcher_type, codec) in enumerate(
            codecs.items(),
            start=1,
        ):
            if visit_count > _MAX_SOURCE_CONFIGURATION_CODECS:
                raise ValueError(
                    "source configuration codec registry exceeds its bound"
                )
            watcher_type = _validate_registry_watcher_type(declared_watcher_type)
            if len(watcher_type) < 1 or len(watcher_type) > 64:
                raise ValueError(
                    "source configuration codec watcher type is outside its bound"
                )
            registered[watcher_type] = codec
        object.__setattr__(self, "_codecs", MappingProxyType(registered))

    def resolve(self, watcher_type: str) -> SourceConfigurationCodec | None:
        return self._codecs.get(watcher_type)

    def registered_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._codecs))


def _validate_registry_watcher_type(watcher_type: object) -> str:
    if not isinstance(watcher_type, str):
        raise TypeError("source configuration codec watcher type must be text")
    return watcher_type
