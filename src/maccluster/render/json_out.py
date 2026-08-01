"""JSON output envelopes with schema_version."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any

from maccluster.constants import SCHEMA_VERSION


def envelope(
    command: str, data: dict[str, Any], *, schema_version: int = SCHEMA_VERSION
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "command": command,
        "data": data,
    }


def dumps(command: str, data: dict[str, Any], *, schema_version: int = SCHEMA_VERSION) -> str:
    return json.dumps(
        envelope(command, data, schema_version=schema_version), indent=2, default=_default
    )


def _default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (IPv4Address, IPv4Network)):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def to_jsonable(obj: Any) -> Any:
    """Recursively convert domain objects to JSON-serializable structures."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (IPv4Address, IPv4Network)):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    return str(obj)
