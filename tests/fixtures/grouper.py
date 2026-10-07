"""Small synthetic preparation tables, independent of real-source acceptance."""

from datetime import date

from minos.domain.encounter import Diagnosis, Encounter, Procedure, WardStay
from minos.domain.grouper import AlgorithmData, AlgorithmIdentity, ExecutionKey
from minos.domain.komunikat import AlgorithmMetadata, DepartmentSpecialty, ProcedureRank
from minos.grouper.registry import EngineBinding, EngineRegistry
from minos.grouper.szp_2026 import ENGINE_VERSION


def algorithm() -> AlgorithmData:
    return AlgorithmData(
        # Trusted domain fixture: real description coordinates, synthetic tables.
        # This is not evidence that synthetic Office bytes passed archive loading.
        AlgorithmIdentity(
            "synthetic-komunikat",
            "a233b231e2d20448f14d28187590803bce743371b727d3c7d6c5dd4fba8de51c",
            "2" * 64,
            "3" * 64,
        ),
        AlgorithmMetadata(
            "1.1.2026", date(2026, 1, 1), "synthetic version evidence", 60
        ),
        tuple(
            ProcedureRank("SZP", f"00.{i}", "synthetic", i, "SZP 2026", 4 + i)
            for i in range(5)
        ),
        (
            DepartmentSpecialty("SZP", "4260", "WSZYSTKIE", "WSZYSTKIE", "SZP 2026", 4),
            DepartmentSpecialty("SZP", "1220", "4", "WSZYSTKIE", "SZP 2026", 5),
        ),
    )


def key(sources: AlgorithmIdentity | None = None) -> ExecutionKey:
    return ExecutionKey(
        "synthetic-preparation",
        "1.1.2026",
        ENGINE_VERSION,
        date(2026, 10, 1),
        sources or algorithm().identity,
    )


def registry(data: AlgorithmData | None = None) -> EngineRegistry:
    selected = data or algorithm()
    return EngineRegistry((EngineBinding(key(selected.identity), selected),))


def encounter() -> Encounter:
    return Encounter(
        date(2026, 10, 1),
        date(2026, 10, 10),
        "hospitalization",
        Diagnosis("A00", "main"),
        (WardStay("main", "4000", date(2026, 10, 1), date(2026, 10, 10)),),
        (Diagnosis("B00", "main"), Diagnosis("C00")),
        tuple(Procedure(f"00.{i}", date(2026, 10, 2), "main") for i in range(5)),
        birth_date=date(2008, 10, 2),
    )
