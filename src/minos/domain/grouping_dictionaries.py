"""Named codes the grouping builder offers instead of bare numbers (#151)."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SpecialtyCode:
    code: str
    name: str
    section: str
    # `children_rule` codes are not printed in the annex; § 9 ust. 4 assigns a
    # children's unit the odd code one above the listed adult code.
    derivation: Literal["listed", "children_rule"]
    position: int


@dataclass(frozen=True)
class DischargeModeCode:
    code: int
    name: str
    point: int
