from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any


class ArtifactFormatError(ValueError):
    """Raised when an artifact does not satisfy the canonical encoding contract."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    _validate_json_value(value)
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactFormatError(f"value is not strict JSON: {exc}") from exc
    return (encoded + "\n").encode("utf-8")


def _reject_constant(value: str) -> None:
    raise ArtifactFormatError(f"non-finite JSON number is forbidden: {value}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ArtifactFormatError(f"non-finite JSON number is forbidden: {value}")
    return parsed


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactFormatError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def strict_json_loads(data: bytes | str) -> Any:
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactFormatError("JSON is not valid UTF-8") from exc
    else:
        text = data
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_parse_finite_float,
        )
    except ArtifactFormatError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ArtifactFormatError(f"invalid JSON: {exc}") from exc


def _validate_json_value(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ArtifactFormatError("value is not strict JSON: non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ArtifactFormatError("value is not strict JSON: object keys must be strings")
            _validate_json_value(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_value(item)


def read_json(path: Path) -> Any:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ArtifactFormatError(f"cannot read JSON artifact {path}: {exc}") from exc
    return strict_json_loads(data)


def stage_atomic_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    temp_path = stage_atomic_bytes(path, data)
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, canonical_json_bytes(value))


def write_canonical_jsonl(
    path: Path,
    records: Iterable[Mapping[str, Any]],
    *,
    sort_key: Callable[[Mapping[str, Any]], Any],
) -> None:
    encoded: list[tuple[tuple[Any, ...], bytes]] = []
    try:
        for record in records:
            if not isinstance(record, Mapping):
                raise ArtifactFormatError("every JSONL record must be an object")
            record_bytes = canonical_json_bytes(dict(record))
            encoded.append((_normalize_sort_key(sort_key(record)), record_bytes))
        encoded.sort(key=lambda item: (item[0], item[1]))
    except ArtifactFormatError:
        raise
    except (TypeError, ValueError) as exc:
        raise ArtifactFormatError(f"JSONL records cannot be deterministically sorted: {exc}") from exc

    atomic_write_bytes(path, b"".join(item[1] for item in encoded))


def _normalize_sort_key(value: Any) -> tuple[Any, ...]:
    if value is None:
        return (0,)
    if isinstance(value, bool):
        return (1, value)
    if isinstance(value, int):
        return (2, value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ArtifactFormatError("JSONL sort keys must contain only finite numbers")
        return (3, value)
    if isinstance(value, str):
        return (4, value)
    if isinstance(value, (list, tuple)):
        return (5, tuple(_normalize_sort_key(item) for item in value))
    raise ArtifactFormatError(
        "JSONL sort keys must be null, boolean, integer, finite float, string, or a sequence"
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ArtifactFormatError(f"cannot read JSONL artifact {path}: {exc}") from exc

    if b"\r" in data:
        raise ArtifactFormatError("JSONL must use LF line endings")
    if data and not data.endswith(b"\n"):
        raise ArtifactFormatError("JSONL must end each record with LF")

    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(data.splitlines(), start=1):
        if not line:
            raise ArtifactFormatError(f"blank JSONL record at line {line_number}")
        record = strict_json_loads(line)
        if not isinstance(record, dict):
            raise ArtifactFormatError(f"JSONL record {line_number} is not an object")
        result.append(record)
    return result
