"""Offline JSON Schema publication for ``hardware.yaml`` version 1."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from patchcord.hardware.models import HardwareDocument

HARDWARE_SCHEMA_VERSION = 1
HARDWARE_SCHEMA_ID = "https://patchcord.dev/schema/hardware-v1.json"


def _strip_null_defaults(value: object) -> None:
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        if mapping.get("default", object()) is None:
            mapping.pop("default", None)
        for child in mapping.values():
            _strip_null_defaults(child)
    elif isinstance(value, list):
        sequence = cast("list[object]", value)
        for child in sequence:
            _strip_null_defaults(child)


def hardware_schema() -> dict[str, Any]:
    """Return the public JSON Schema generated from the Pydantic v2 models."""

    schema = HardwareDocument.model_json_schema(
        by_alias=True,
        ref_template="#/$defs/{model}",
        mode="validation",
    )
    # Pydantic emits constrained mapping keys as ``patternProperties`` but does
    # not close the mapping. The runtime model does reject nonmatching keys, so
    # close these three maps to keep the published schema equivalent.
    schema["properties"]["parts"]["additionalProperties"] = False
    schema["properties"]["nets"]["additionalProperties"] = False
    schema["$defs"]["PartDefinition"]["properties"]["pins"]["additionalProperties"] = False
    _strip_null_defaults(schema)
    schema["$id"] = HARDWARE_SCHEMA_ID
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Patchcord hardware.yaml v1"
    return schema


def hardware_schema_json(*, indent: int | None = 2) -> str:
    """Serialize the generated public schema deterministically."""

    return json.dumps(hardware_schema(), indent=indent, sort_keys=True) + ("\n" if indent else "")


def packaged_hardware_schema() -> dict[str, Any]:
    """Load the schema distributed with Patchcord without network access."""

    resource = files("patchcord").joinpath("resources", "hardware-v1.schema.json")
    loaded: object = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        msg = "Packaged hardware schema is not a JSON object."
        raise RuntimeError(msg)
    return cast("dict[str, Any]", loaded)


def write_hardware_schema(path: str | Path) -> Path:
    """Write the generated schema to *path* and return the resolved path."""

    output_path = Path(path).resolve()
    output_path.write_text(hardware_schema_json(), encoding="utf-8", newline="\n")
    return output_path


__all__ = [
    "HARDWARE_SCHEMA_ID",
    "HARDWARE_SCHEMA_VERSION",
    "hardware_schema",
    "hardware_schema_json",
    "packaged_hardware_schema",
    "write_hardware_schema",
]
