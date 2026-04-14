"""Shared serialization helpers for canonical object models."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, TypeVar, cast

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]

TSerializable = TypeVar("TSerializable", bound="SerializableModel")


def to_json_value(value: Any) -> JsonValue:
    """Convert nested model values into JSON-compatible primitives."""
    if isinstance(value, Enum):
        enum_value = value.value
        if not isinstance(enum_value, (str, int)):
            raise TypeError(f"Unsupported enum value type: {type(enum_value)!r}")
        return cast(JsonValue, enum_value)

    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_json_value(getattr(value, field.name)) for field in fields(value)}

    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}

    if isinstance(value, list):
        return [to_json_value(item) for item in value]

    if isinstance(value, tuple):
        return [to_json_value(item) for item in value]

    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    raise TypeError(f"Unsupported JSON value type: {type(value)!r}")


class SerializableModel:
    """Mixin for dataclass-based canonical models."""

    def to_dict(self) -> JsonObject:
        if not is_dataclass(self):
            raise TypeError("SerializableModel requires dataclass subclasses")

        return {field.name: to_json_value(getattr(self, field.name)) for field in fields(self)}

    def to_json(self) -> JsonObject:
        return self.to_dict()

    @classmethod
    def from_dict(cls: type[TSerializable], data: JsonObject) -> TSerializable:
        return cls(**data)
