from __future__ import annotations

from typing import Annotated, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
PositiveInt = Annotated[int, Field(ge=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]

T = TypeVar("T")


class FrozenEnvelope(BaseModel):
    """Closed immutable base for every canonical wire envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def ensure_unique(values: tuple[T, ...], field_name: str) -> tuple[T, ...]:
    for index, value in enumerate(values):
        if value in values[:index]:
            raise ValueError(f"{field_name} must contain unique items")
    return values
