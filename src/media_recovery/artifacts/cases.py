from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from media_recovery.artifacts.io import (
    ArtifactFormatError,
    atomic_write_json,
    read_json,
    sha256_file,
)
from media_recovery.artifacts.schema import (
    CASE_SCHEMA,
    CASE_SCHEMA_VERSION,
    require_compatible_schema,
    require_fields,
)


CASE_ID_HEX_LENGTH = 20
_CASE_ID_PATTERN = re.compile(r"^case-[0-9a-f]{20}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class CaseConflictError(RuntimeError):
    """Raised when a case ID already names different source content."""


def resolve_work_root(
    work_root: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
) -> Path:
    if work_root is None:
        base = Path.cwd() if cwd is None else Path(cwd)
        return (base / "work").resolve()
    return Path(work_root).expanduser().resolve()


def initialize_work_root(work_root: str | Path | None = None) -> Path:
    root = resolve_work_root(work_root)
    for name in ("cache", "tmp", "cases"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def case_id_for_sha256(source_sha256: str) -> str:
    if _SHA256_PATTERN.fullmatch(source_sha256) is None:
        raise ValueError("source_sha256 must be 64 lowercase hexadecimal characters")
    return f"case-{source_sha256[:CASE_ID_HEX_LENGTH]}"


def register_case(
    source: str | Path,
    *,
    work_root: str | Path | None = None,
    label: str | None = None,
) -> Path:
    source_path = Path(source).expanduser().resolve(strict=True)
    if not source_path.is_file():
        raise ValueError(f"case source is not a regular file: {source_path}")
    if label is not None and not isinstance(label, str):
        raise TypeError("label must be a string or None")

    source_size = source_path.stat().st_size
    source_sha256 = sha256_file(source_path)
    case_id = case_id_for_sha256(source_sha256)
    root = initialize_work_root(work_root)
    case_path = root / "cases" / case_id

    if case_path.exists():
        existing = read_case(case_path)
        if existing["source"]["sha256"] != source_sha256:
            raise CaseConflictError(
                f"case ID prefix collision for {case_id}: full SHA-256 differs"
            )
        if existing["source"]["size"] != source_size:
            raise CaseConflictError(f"case source size disagrees for {case_id}")
        return case_path

    try:
        case_path.mkdir(parents=False, exist_ok=False)
    except FileExistsError:
        existing = read_case(case_path)
        if existing["source"]["sha256"] != source_sha256:
            raise CaseConflictError(
                f"case ID prefix collision for {case_id}: full SHA-256 differs"
            )
        return case_path

    metadata = {
        "case_id": case_id,
        "created_at": _utc_timestamp(),
        "label": label,
        "required_features": [],
        "schema": CASE_SCHEMA,
        "schema_version": CASE_SCHEMA_VERSION,
        "source": {
            "path": str(source_path),
            "sha256": source_sha256,
            "size": source_size,
        },
    }
    atomic_write_json(case_path / "case.json", metadata)
    (case_path / "runs").mkdir()
    return case_path


def read_case(case_path: str | Path) -> dict[str, Any]:
    path = Path(case_path)
    record = read_json(path / "case.json")
    if not isinstance(record, dict):
        raise ArtifactFormatError("case.json must contain an object")
    require_compatible_schema(
        record,
        schema=CASE_SCHEMA,
        current_version=CASE_SCHEMA_VERSION,
    )
    require_fields(
        record,
        {
            "case_id",
            "created_at",
            "label",
            "required_features",
            "schema",
            "schema_version",
            "source",
        },
    )
    case_id = record["case_id"]
    if not isinstance(case_id, str) or _CASE_ID_PATTERN.fullmatch(case_id) is None:
        raise ArtifactFormatError("invalid case_id")
    if path.name != case_id:
        raise ArtifactFormatError("case directory name does not match case_id")
    if record["label"] is not None and not isinstance(record["label"], str):
        raise ArtifactFormatError("case label must be a string or null")
    if not isinstance(record["created_at"], str):
        raise ArtifactFormatError("case created_at must be a string")
    _validate_source(record["source"])
    if case_id != case_id_for_sha256(record["source"]["sha256"]):
        raise ArtifactFormatError("case_id does not match the full source SHA-256")
    return record


def verify_case_source(case_path: str | Path) -> dict[str, Any]:
    record = read_case(case_path)
    source = record["source"]
    source_path = Path(source["path"])
    try:
        actual_size = source_path.stat().st_size
    except OSError as exc:
        raise ArtifactFormatError(f"cannot stat registered source {source_path}: {exc}") from exc
    if actual_size != source["size"]:
        raise ArtifactFormatError("registered source size has changed")
    if sha256_file(source_path) != source["sha256"]:
        raise ArtifactFormatError("registered source SHA-256 has changed")
    return record


def _validate_source(source: Any) -> None:
    if not isinstance(source, dict):
        raise ArtifactFormatError("case source must be an object")
    require_fields(source, {"path", "sha256", "size"})
    if not isinstance(source["path"], str) or not Path(source["path"]).is_absolute():
        raise ArtifactFormatError("case source path must be absolute")
    if not isinstance(source["sha256"], str) or _SHA256_PATTERN.fullmatch(source["sha256"]) is None:
        raise ArtifactFormatError("case source sha256 must be lowercase hexadecimal")
    size = source["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ArtifactFormatError("case source size must be a non-negative integer")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
