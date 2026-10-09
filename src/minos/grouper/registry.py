"""Exact immutable registry. No defaults, latest selectors or fallback engines."""

from dataclasses import dataclass, replace

from minos.domain.encounter import Encounter
from minos.domain.grouper import (
    AlgorithmData,
    Decision,
    ExecutionKey,
    GrouperError,
    PreparationResult,
    ProductClassification,
    UnknownEngineBinding,
)
from minos.grouper.szp_2026 import prepare, validate_preparation_binding


@dataclass(frozen=True)
class EngineBinding:
    key: ExecutionKey
    algorithm: AlgorithmData

    def __post_init__(self) -> None:
        if (
            type(self.key) is not ExecutionKey
            or type(self.algorithm) is not AlgorithmData
        ):
            raise GrouperError("invalid engine binding types")
        validate_preparation_binding(self.key, self.algorithm)


@dataclass(frozen=True)
class EngineRegistry:
    bindings: tuple[EngineBinding, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.bindings) is not tuple
            or len(self.bindings) > 64
            or any(type(b) is not EngineBinding for b in self.bindings)
        ):
            raise GrouperError("invalid bounded engine registry")
        if len({b.key for b in self.bindings}) != len(self.bindings):
            raise GrouperError("duplicate engine registration")

    def execute(
        self,
        key: ExecutionKey,
        encounter: Encounter,
        classification: ProductClassification | None = None,
    ) -> PreparationResult:
        if (
            type(key) is not ExecutionKey
            or type(encounter) is not Encounter
            or (
                classification is not None
                and type(classification) is not ProductClassification
            )
        ):
            raise GrouperError("invalid execution request types")
        for binding in self.bindings:
            if binding.key == key:
                result = prepare(
                    encounter, binding.key, binding.algorithm, classification
                )
                # Only a resolved registration can truthfully attest dispatch.
                return replace(
                    result,
                    trace=(
                        Decision("dispatch", 1, "recorded", "exact_binding"),
                        *result.trace,
                    ),
                )
        raise UnknownEngineBinding(
            "no exact ruleset, algorithm, engine and reporting-month binding"
        )
