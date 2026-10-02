"""Shared bounds for reviewed source identities."""

from typing import Annotated

from pydantic import Field

type Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")]
type SourceText = Annotated[str, Field(min_length=1, max_length=4096)]
