from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from media_recovery.artifacts.io import ArtifactFormatError
from media_recovery.domain.forensics import (
    CANDIDATE_SCHEMA,
    COEFFICIENT_MANIFEST_SCHEMA,
    FORENSIC_SCHEMA_VERSION,
    OBJECT_SCHEMA,
    RESULT_SCHEMA,
)


CASE_SCHEMA = "media-recovery.case"
CASE_SCHEMA_VERSION = "1.0"
RUN_SCHEMA = "media-recovery.run"
RUN_SCHEMA_VERSION = "1.0"
COMPLETION_SCHEMA = "media-recovery.run-completion"
COMPLETION_SCHEMA_VERSION = "1.0"

_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_KNOWN_REQUIRED_FEATURES: frozenset[str] = frozenset()


def require_compatible_schema(
    record: Mapping[str, Any],
    *,
    schema: str,
    current_version: str,
) -> tuple[int, int]:
    if record.get("schema") != schema:
        raise ArtifactFormatError(f"expected schema {schema!r}")

    version = record.get("schema_version")
    if not isinstance(version, str):
        raise ArtifactFormatError("schema_version must be a string")
    parsed = _parse_version(version)
    current = _parse_version(current_version)
    if parsed[0] != current[0]:
        raise ArtifactFormatError(
            f"unsupported {schema} major version {parsed[0]}; expected {current[0]}"
        )

    features = record.get("required_features")
    if not isinstance(features, list) or not all(isinstance(item, str) for item in features):
        raise ArtifactFormatError("required_features must be a list of strings")
    unknown = sorted(set(features) - _KNOWN_REQUIRED_FEATURES)
    if unknown:
        raise ArtifactFormatError(f"unknown required_features: {', '.join(unknown)}")
    return parsed


def require_fields(record: Mapping[str, Any], fields: set[str]) -> None:
    missing = sorted(fields - record.keys())
    if missing:
        raise ArtifactFormatError(f"missing required fields: {', '.join(missing)}")


def _parse_version(value: str) -> tuple[int, int]:
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise ArtifactFormatError(f"invalid schema version: {value!r}")
    return int(match.group(1)), int(match.group(2))
