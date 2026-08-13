from __future__ import annotations

import os
import re
import secrets
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from media_recovery.artifacts.cases import read_case, verify_case_source
from media_recovery.artifacts.io import (
    ArtifactFormatError,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    stage_atomic_bytes,
    write_canonical_jsonl,
)
from media_recovery.artifacts.schema import (
    COMPLETION_SCHEMA,
    COMPLETION_SCHEMA_VERSION,
    RUN_SCHEMA,
    RUN_SCHEMA_VERSION,
    require_compatible_schema,
    require_fields,
)


RUN_STAGES = frozenset({"discovery", "reconstruction", "rendering", "enhancement"})
RUN_STATUSES = frozenset({"created", "running", "completed", "interrupted", "error"})
COMPLETION_FILE = "completed.json"
_RUN_ID_PATTERN = re.compile(r"^run-[0-9]{8}T[0-9]{6}Z-[a-z2-7]{6}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_PARENT_STAGES = {
    "discovery": frozenset(),
    "reconstruction": frozenset({"discovery"}),
    "rendering": frozenset({"reconstruction"}),
    "enhancement": frozenset({"reconstruction", "rendering"}),
}


class RunStateError(RuntimeError):
    """Raised when a run operation violates lifecycle or immutability rules."""


class RunCompatibilityError(RuntimeError):
    """Raised when an interrupted or error run cannot be resumed safely."""


def create_run(
    case_path: str | Path,
    *,
    stage: str,
    parent_run_ids: Sequence[str] = (),
    tool_version: str,
    engine_version: str,
    policy_version: str,
    artifact_schema_version: str,
    environment: Mapping[str, Any],
    options: Mapping[str, Any] | None = None,
    random_seed: int | None = None,
    task_id: str | None = None,
    git_commit: str | None = None,
    git_dirty: bool = False,
    dirty_patch: bytes | None = None,
) -> Path:
    case_dir = Path(case_path).resolve()
    case = read_case(case_dir)
    _validate_stage(stage)
    parents = list(parent_run_ids)
    _validate_parent_runs(case_dir, stage, parents)
    versions = _validate_versions(
        tool_version,
        engine_version,
        policy_version,
        artifact_schema_version,
    )
    normalized_environment = _validate_environment(environment)
    normalized_options = dict(options or {})
    canonical_json_bytes(normalized_options)
    _validate_random_seed(random_seed)
    _validate_optional_string(task_id, "task_id")
    _validate_optional_string(git_commit, "git_commit")
    if git_dirty and not dirty_patch:
        raise ValueError("dirty runs require non-empty dirty_patch bytes")
    if not git_dirty and dirty_patch is not None:
        raise ValueError("dirty_patch is only valid when git_dirty is true")

    runs_dir = case_dir / "runs"
    runs_dir.mkdir(exist_ok=True)
    run_dir, run_id = _reserve_run_directory(runs_dir)
    created_at = _utc_timestamp()
    patch_record: dict[str, Any] | None = None
    if dirty_patch is not None:
        patch_path = run_dir / "provenance" / "dirty.patch"
        atomic_write_bytes(patch_path, dirty_patch)
        patch_record = {
            "path": "provenance/dirty.patch",
            "sha256": sha256_bytes(dirty_patch),
            "size": len(dirty_patch),
        }

    record = {
        "attempts": [],
        "case_id": case["case_id"],
        "created_at": created_at,
        "environment": normalized_environment,
        "finished_at": None,
        "options": normalized_options,
        "parent_run_ids": parents,
        "provenance": {
            "dirty": git_dirty,
            "git_commit": git_commit,
            "patch": patch_record,
        },
        "random_seed": random_seed,
        "required_features": [],
        "run_id": run_id,
        "schema": RUN_SCHEMA,
        "schema_version": RUN_SCHEMA_VERSION,
        "source": {
            "sha256": case["source"]["sha256"],
            "size": case["source"]["size"],
        },
        "stage": stage,
        "started_at": None,
        "status": "created",
        "status_detail": None,
        "task_id": task_id,
        "versions": versions,
    }
    atomic_write_json(run_dir / "run.json", record)
    return run_dir


def read_run(run_path: str | Path) -> dict[str, Any]:
    run_dir = Path(run_path).resolve()
    return _read_run_with_lineage(run_dir, visited=set())


def _read_run_with_lineage(run_dir: Path, *, visited: set[str]) -> dict[str, Any]:
    if run_dir.name in visited:
        raise ArtifactFormatError(f"run lineage cycle detected at {run_dir.name}")
    current_visited = visited | {run_dir.name}
    record = _read_run_record(run_dir)
    marker_path = run_dir / COMPLETION_FILE
    if record["status"] == "completed":
        if not marker_path.is_file():
            raise ArtifactFormatError("completed run is missing completed.json")
        _validate_completion(run_dir, record, marker_path)
    elif marker_path.exists():
        raise ArtifactFormatError("non-completed run has a completion marker")
    allowed = _PARENT_STAGES[record["stage"]]
    for parent_id in record["parent_run_ids"]:
        parent = _read_run_with_lineage(run_dir.parent / parent_id, visited=current_visited)
        if parent["status"] != "completed":
            raise ArtifactFormatError(f"parent run is not completed: {parent_id}")
        if parent["stage"] not in allowed:
            raise ArtifactFormatError(
                f"{record['stage']} run cannot use {parent['stage']} parent {parent_id}"
            )
    return record


def start_run(run_path: str | Path) -> dict[str, Any]:
    run_dir = Path(run_path).resolve()
    record = _read_mutable_run(run_dir)
    if record["status"] != "created":
        raise RunStateError("only a created run can be started")
    verify_case_source(run_dir.parent.parent)
    now = _utc_timestamp()
    record["status"] = "running"
    record["started_at"] = now
    record["status_detail"] = None
    record["attempts"].append(
        {"detail": None, "finished_at": None, "outcome": None, "started_at": now}
    )
    atomic_write_json(run_dir / "run.json", record)
    return record


def interrupt_run(run_path: str | Path, *, detail: str | None = None) -> dict[str, Any]:
    return _finish_unsuccessful_run(run_path, status="interrupted", detail=detail)


def fail_run(run_path: str | Path, *, detail: str) -> dict[str, Any]:
    if not isinstance(detail, str) or not detail:
        raise ValueError("error detail must be a non-empty string")
    return _finish_unsuccessful_run(run_path, status="error", detail=detail)


def resume_run(
    run_path: str | Path,
    *,
    stage_supports_resume: bool,
    tool_version: str,
    engine_version: str,
    policy_version: str,
    artifact_schema_version: str,
    environment: Mapping[str, Any],
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_path).resolve()
    record = _read_mutable_run(run_dir)
    if record["status"] not in {"interrupted", "error"}:
        raise RunStateError("only interrupted or error runs can be resumed")
    if not stage_supports_resume:
        raise RunCompatibilityError(f"stage {record['stage']} did not declare resume support")

    requested_versions = _validate_versions(
        tool_version,
        engine_version,
        policy_version,
        artifact_schema_version,
    )
    requested_environment = _validate_environment(environment)
    requested_options = dict(options or {})
    canonical_json_bytes(requested_options)
    case = verify_case_source(run_dir.parent.parent)
    mismatches: list[str] = []
    if record["source"] != {
        "sha256": case["source"]["sha256"],
        "size": case["source"]["size"],
    }:
        mismatches.append("source")
    if record["versions"] != requested_versions:
        mismatches.append("tool/engine/policy/schema version")
    if canonical_json_bytes(record["environment"]) != canonical_json_bytes(
        requested_environment
    ):
        mismatches.append("environment")
    if canonical_json_bytes(record["options"]) != canonical_json_bytes(requested_options):
        mismatches.append("options")
    if mismatches:
        raise RunCompatibilityError("resume metadata differs: " + ", ".join(mismatches))

    now = _utc_timestamp()
    record["status"] = "running"
    record["finished_at"] = None
    record["status_detail"] = None
    record["attempts"].append(
        {"detail": None, "finished_at": None, "outcome": None, "started_at": now}
    )
    atomic_write_json(run_dir / "run.json", record)
    return record


def write_run_jsonl(
    run_path: str | Path,
    relative_path: str | Path,
    records: Iterable[Mapping[str, Any]],
    *,
    sort_key: Callable[[Mapping[str, Any]], Any],
) -> Path:
    run_dir = Path(run_path).resolve()
    record = _read_mutable_run(run_dir)
    if record["status"] != "running":
        raise RunStateError("canonical artifacts can only be written to a running run")
    destination = _safe_artifact_path(run_dir, relative_path)
    if destination.name in {"run.json", COMPLETION_FILE}:
        raise ValueError("run metadata paths are reserved")
    write_canonical_jsonl(destination, records, sort_key=sort_key)
    return destination


def complete_run(run_path: str | Path) -> dict[str, Any]:
    run_dir = Path(run_path).resolve()
    record = _read_run_record(run_dir)
    if record["status"] != "running":
        raise RunStateError("only a running run can be completed")
    if not record["attempts"] or record["attempts"][-1]["outcome"] is not None:
        raise ArtifactFormatError("running run has no open attempt")

    completed_at = _utc_timestamp()
    completed_record = dict(record)
    completed_record["status"] = "completed"
    completed_record["finished_at"] = completed_at
    completed_record["status_detail"] = None
    completed_record["attempts"] = [dict(item) for item in record["attempts"]]
    completed_record["attempts"][-1].update(
        {"detail": None, "finished_at": completed_at, "outcome": "completed"}
    )
    run_bytes = canonical_json_bytes(completed_record)
    files = _inventory_for_completion(run_dir, run_bytes)
    marker = {
        "completed_at": completed_at,
        "files": files,
        "required_features": [],
        "run_id": record["run_id"],
        "schema": COMPLETION_SCHEMA,
        "schema_version": COMPLETION_SCHEMA_VERSION,
    }
    marker_bytes = canonical_json_bytes(marker)

    marker_path = run_dir / COMPLETION_FILE
    run_json_path = run_dir / "run.json"
    marker_temp: Path | None = None
    run_temp: Path | None = None
    try:
        marker_temp = stage_atomic_bytes(marker_path, marker_bytes)
        run_temp = stage_atomic_bytes(run_json_path, run_bytes)
        # Publishing the marker first means a failed second replace is visible only as
        # an invalid, non-completed run. A completed run can never lack its seal.
        os.replace(marker_temp, marker_path)
        os.replace(run_temp, run_json_path)
    finally:
        if marker_temp is not None:
            marker_temp.unlink(missing_ok=True)
        if run_temp is not None:
            run_temp.unlink(missing_ok=True)
    return read_run(run_dir)


def generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(6))
    return f"run-{timestamp}-{suffix}"


def _reserve_run_directory(runs_dir: Path) -> tuple[Path, str]:
    for _ in range(128):
        run_id = generate_run_id()
        if _RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise RuntimeError("generated run ID does not satisfy the run ID contract")
        run_dir = runs_dir / run_id
        try:
            run_dir.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return run_dir, run_id
    raise FileExistsError("could not allocate a unique run ID after 128 attempts")


def _read_mutable_run(run_dir: Path) -> dict[str, Any]:
    record = _read_run_record(run_dir)
    if (run_dir / COMPLETION_FILE).exists():
        raise RunStateError("a sealed or partially finalized run cannot be modified")
    if record["status"] == "completed":
        raise RunStateError("a completed run is immutable")
    return record


def _read_run_record(run_dir: Path) -> dict[str, Any]:
    record = read_json(run_dir / "run.json")
    if not isinstance(record, dict):
        raise ArtifactFormatError("run.json must contain an object")
    _validate_run_record(run_dir, record)
    return record


def _validate_run_record(run_dir: Path, record: dict[str, Any]) -> None:
    require_compatible_schema(record, schema=RUN_SCHEMA, current_version=RUN_SCHEMA_VERSION)
    require_fields(
        record,
        {
            "attempts",
            "case_id",
            "created_at",
            "environment",
            "finished_at",
            "options",
            "parent_run_ids",
            "provenance",
            "random_seed",
            "required_features",
            "run_id",
            "schema",
            "schema_version",
            "source",
            "stage",
            "started_at",
            "status",
            "status_detail",
            "task_id",
            "versions",
        },
    )
    if not isinstance(record["run_id"], str) or _RUN_ID_PATTERN.fullmatch(record["run_id"]) is None:
        raise ArtifactFormatError("invalid run_id")
    if run_dir.name != record["run_id"]:
        raise ArtifactFormatError("run directory name does not match run_id")
    case = read_case(run_dir.parent.parent)
    if record["case_id"] != case["case_id"]:
        raise ArtifactFormatError("run case_id does not match its case directory")
    _validate_stage(record["stage"])
    parents = record["parent_run_ids"]
    if not isinstance(parents, list) or not all(isinstance(item, str) for item in parents):
        raise ArtifactFormatError("parent_run_ids must be a list of strings")
    if len(set(parents)) != len(parents):
        raise ArtifactFormatError("parent_run_ids must be unique")
    if not all(_RUN_ID_PATTERN.fullmatch(item) is not None for item in parents):
        raise ArtifactFormatError("parent_run_ids contains an invalid run ID")
    allowed_parents = _PARENT_STAGES[record["stage"]]
    if not allowed_parents and parents:
        raise ArtifactFormatError(f"{record['stage']} runs cannot have parents")
    if allowed_parents and not parents:
        raise ArtifactFormatError(f"{record['stage']} runs require at least one parent")
    if record["status"] not in RUN_STATUSES:
        raise ArtifactFormatError("invalid run status")
    if not isinstance(record["options"], dict):
        raise ArtifactFormatError("run options must be an object")
    canonical_json_bytes(record["options"])
    _validate_random_seed(record["random_seed"])
    _validate_optional_string(record["task_id"], "task_id")
    for field in ("created_at",):
        if not isinstance(record[field], str):
            raise ArtifactFormatError(f"{field} must be a string")
    for field in ("started_at", "finished_at", "status_detail"):
        if record[field] is not None and not isinstance(record[field], str):
            raise ArtifactFormatError(f"{field} must be a string or null")
    _validate_source(record["source"])
    if record["source"] != {
        "sha256": case["source"]["sha256"],
        "size": case["source"]["size"],
    }:
        raise ArtifactFormatError("run source fingerprint does not match case.json")
    _validate_versions_record(record["versions"])
    _validate_environment(record["environment"])
    _validate_provenance(run_dir, record["provenance"])
    _validate_attempts(record)


def _validate_attempts(record: dict[str, Any]) -> None:
    attempts = record["attempts"]
    if not isinstance(attempts, list):
        raise ArtifactFormatError("attempts must be a list")
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise ArtifactFormatError("attempt must be an object")
        require_fields(attempt, {"detail", "finished_at", "outcome", "started_at"})
        if not isinstance(attempt["started_at"], str):
            raise ArtifactFormatError("attempt started_at must be a string")
        if attempt["finished_at"] is not None and not isinstance(attempt["finished_at"], str):
            raise ArtifactFormatError("attempt finished_at must be a string or null")
        if attempt["detail"] is not None and not isinstance(attempt["detail"], str):
            raise ArtifactFormatError("attempt detail must be a string or null")
        if attempt["outcome"] not in {None, "completed", "interrupted", "error"}:
            raise ArtifactFormatError("invalid attempt outcome")
        if (attempt["outcome"] is None) != (attempt["finished_at"] is None):
            raise ArtifactFormatError("attempt outcome and finished_at must be set together")
    if any(attempt["outcome"] is None for attempt in attempts[:-1]):
        raise ArtifactFormatError("only the final attempt may be open")
    status = record["status"]
    if status == "created" and attempts:
        raise ArtifactFormatError("created run cannot have attempts")
    if status == "created" and (record["started_at"] is not None or record["finished_at"] is not None):
        raise ArtifactFormatError("created run cannot have start or finish timestamps")
    if status == "running":
        if not attempts or attempts[-1]["outcome"] is not None:
            raise ArtifactFormatError("running run must have one open final attempt")
        if record["started_at"] is None or record["finished_at"] is not None:
            raise ArtifactFormatError("running run timestamps are inconsistent")
    if status in {"completed", "interrupted", "error"}:
        if not attempts or attempts[-1]["outcome"] != status:
            raise ArtifactFormatError("terminal run status must match its final attempt")
        if record["started_at"] is None or record["finished_at"] is None:
            raise ArtifactFormatError("terminal run timestamps are incomplete")


def _validate_source(source: Any) -> None:
    if not isinstance(source, dict):
        raise ArtifactFormatError("run source must be an object")
    require_fields(source, {"sha256", "size"})
    if not isinstance(source["sha256"], str) or _SHA256_PATTERN.fullmatch(source["sha256"]) is None:
        raise ArtifactFormatError("invalid run source SHA-256")
    if isinstance(source["size"], bool) or not isinstance(source["size"], int) or source["size"] < 0:
        raise ArtifactFormatError("invalid run source size")


def _validate_versions(
    tool: str,
    engine: str,
    policy: str,
    schema: str,
) -> dict[str, str]:
    record = {"engine": engine, "policy": policy, "schema": schema, "tool": tool}
    _validate_versions_record(record)
    return record


def _validate_versions_record(versions: Any) -> None:
    if not isinstance(versions, dict):
        raise ArtifactFormatError("versions must be an object")
    version_fields = ("tool", "engine", "policy", "schema")
    require_fields(versions, set(version_fields))
    if not all(isinstance(versions[key], str) and versions[key] for key in version_fields):
        raise ArtifactFormatError(
            "tool, engine, policy, and schema versions must be non-empty strings"
        )


def _validate_environment(environment: Any) -> dict[str, Any]:
    if not isinstance(environment, Mapping):
        raise ArtifactFormatError("environment must be an object")
    normalized = dict(environment)
    if not normalized:
        raise ArtifactFormatError("environment must not be empty")
    canonical_json_bytes(normalized)
    return normalized


def _validate_provenance(run_dir: Path, provenance: Any) -> None:
    if not isinstance(provenance, dict):
        raise ArtifactFormatError("provenance must be an object")
    require_fields(provenance, {"dirty", "git_commit", "patch"})
    if not isinstance(provenance["dirty"], bool):
        raise ArtifactFormatError("provenance dirty must be boolean")
    _validate_optional_string(provenance["git_commit"], "git_commit")
    patch = provenance["patch"]
    if not provenance["dirty"]:
        if patch is not None:
            raise ArtifactFormatError("clean provenance cannot reference a patch")
        return
    if not isinstance(patch, dict):
        raise ArtifactFormatError("dirty provenance requires a patch artifact")
    require_fields(patch, {"path", "sha256", "size"})
    if patch["path"] != "provenance/dirty.patch":
        raise ArtifactFormatError("dirty patch path must be provenance/dirty.patch")
    patch_path = run_dir / "provenance" / "dirty.patch"
    if not patch_path.is_file():
        raise ArtifactFormatError("dirty patch artifact is missing")
    if not isinstance(patch["sha256"], str) or _SHA256_PATTERN.fullmatch(patch["sha256"]) is None:
        raise ArtifactFormatError("invalid dirty patch SHA-256")
    if isinstance(patch["size"], bool) or not isinstance(patch["size"], int) or patch["size"] < 1:
        raise ArtifactFormatError("invalid dirty patch size")
    if patch["sha256"] != sha256_file(patch_path) or patch["size"] != patch_path.stat().st_size:
        raise ArtifactFormatError("dirty patch artifact does not match provenance")


def _validate_parent_runs(case_dir: Path, stage: str, parent_ids: list[str]) -> None:
    if len(set(parent_ids)) != len(parent_ids):
        raise ValueError("parent_run_ids must be unique")
    allowed = _PARENT_STAGES[stage]
    if not allowed and parent_ids:
        raise ValueError(f"{stage} runs cannot have parents")
    if allowed and not parent_ids:
        raise ValueError(f"{stage} runs require at least one parent")
    for parent_id in parent_ids:
        if _RUN_ID_PATTERN.fullmatch(parent_id) is None:
            raise ValueError(f"invalid parent run ID: {parent_id}")
        parent = read_run(case_dir / "runs" / parent_id)
        if parent["status"] != "completed":
            raise ValueError(f"parent run is not completed: {parent_id}")
        if parent["stage"] not in allowed:
            raise ValueError(
                f"{stage} run cannot use {parent['stage']} parent {parent_id}"
            )


def _finish_unsuccessful_run(
    run_path: str | Path,
    *,
    status: str,
    detail: str | None,
) -> dict[str, Any]:
    run_dir = Path(run_path).resolve()
    record = _read_mutable_run(run_dir)
    if record["status"] != "running":
        raise RunStateError(f"only a running run can become {status}")
    if detail is not None and not isinstance(detail, str):
        raise TypeError("status detail must be a string or None")
    now = _utc_timestamp()
    record["status"] = status
    record["finished_at"] = now
    record["status_detail"] = detail
    record["attempts"][-1].update(
        {"detail": detail, "finished_at": now, "outcome": status}
    )
    atomic_write_json(run_dir / "run.json", record)
    return record


def _inventory_for_completion(run_dir: Path, completed_run_bytes: bytes) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in run_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(run_dir).as_posix()
        if relative == COMPLETION_FILE:
            continue
        if relative == "run.json":
            continue
        if ".staging" in path.parts or path.name.endswith(".tmp"):
            raise RunStateError(f"cannot complete with staging file present: {relative}")
        if path.is_symlink():
            raise RunStateError(f"cannot seal a symbolic-link artifact: {relative}")
        files.append(
            {"path": relative, "sha256": sha256_file(path), "size": path.stat().st_size}
        )
    files.append(
        {
            "path": "run.json",
            "sha256": sha256_bytes(completed_run_bytes),
            "size": len(completed_run_bytes),
        }
    )
    files.sort(key=lambda item: item["path"])
    return files


def _validate_completion(run_dir: Path, run: dict[str, Any], marker_path: Path) -> None:
    marker = read_json(marker_path)
    if not isinstance(marker, dict):
        raise ArtifactFormatError("completed.json must contain an object")
    require_compatible_schema(
        marker,
        schema=COMPLETION_SCHEMA,
        current_version=COMPLETION_SCHEMA_VERSION,
    )
    require_fields(
        marker,
        {"completed_at", "files", "required_features", "run_id", "schema", "schema_version"},
    )
    if marker["run_id"] != run["run_id"]:
        raise ArtifactFormatError("completion marker run_id mismatch")
    if marker["completed_at"] != run["finished_at"]:
        raise ArtifactFormatError("completion marker timestamp mismatch")
    files = marker["files"]
    if not isinstance(files, list):
        raise ArtifactFormatError("completion files must be a list")
    expected: dict[str, tuple[str, int]] = {}
    for entry in files:
        if not isinstance(entry, dict):
            raise ArtifactFormatError("completion file entry must be an object")
        require_fields(entry, {"path", "sha256", "size"})
        relative = entry["path"]
        if not isinstance(relative, str) or relative in expected:
            raise ArtifactFormatError("completion paths must be unique strings")
        candidate = _safe_artifact_path(run_dir, relative)
        if candidate == marker_path:
            raise ArtifactFormatError("completion marker cannot seal itself")
        sha256 = entry["sha256"]
        size = entry["size"]
        if not isinstance(sha256, str) or _SHA256_PATTERN.fullmatch(sha256) is None:
            raise ArtifactFormatError("invalid completion file SHA-256")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ArtifactFormatError("invalid completion file size")
        expected[relative] = (sha256, size)

    actual_paths = {
        path.relative_to(run_dir).as_posix(): path
        for path in run_dir.rglob("*")
        if path.is_file() and path != marker_path
    }
    if set(actual_paths) != set(expected):
        raise ArtifactFormatError("completed run file set does not match its seal")
    for relative, path in actual_paths.items():
        if path.is_symlink():
            raise ArtifactFormatError(f"completed artifact cannot be a symbolic link: {relative}")
        digest, size = expected[relative]
        if path.stat().st_size != size or sha256_file(path) != digest:
            raise ArtifactFormatError(f"completed artifact does not match its seal: {relative}")


def _safe_artifact_path(run_dir: Path, relative_path: str | Path) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("artifact path must be a non-empty relative path without '..'")
    destination = (run_dir / relative).resolve()
    if not destination.is_relative_to(run_dir):
        raise ValueError("artifact path escapes the run directory")
    return destination


def _validate_stage(stage: Any) -> None:
    if not isinstance(stage, str) or stage not in RUN_STAGES:
        raise ValueError(f"stage must be one of: {', '.join(sorted(RUN_STAGES))}")


def _validate_random_seed(seed: Any) -> None:
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ArtifactFormatError("random_seed must be an integer or null")


def _validate_optional_string(value: Any, name: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ArtifactFormatError(f"{name} must be a string or null")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
