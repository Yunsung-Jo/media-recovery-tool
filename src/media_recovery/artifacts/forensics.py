from __future__ import annotations

import os
import tempfile
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar

import numpy as np

from media_recovery.artifacts.io import ArtifactFormatError, read_jsonl, sha256_file
from media_recovery.artifacts.runs import (
    _read_mutable_run,
    _safe_artifact_path,
    read_run,
    write_run_jsonl,
)
from media_recovery.artifacts.schema import require_compatible_schema
from media_recovery.domain.forensics import (
    CANDIDATE_SCHEMA,
    COEFFICIENT_MANIFEST_SCHEMA,
    FORENSIC_SCHEMA_VERSION,
    OBJECT_SCHEMA,
    RESULT_SCHEMA,
    ArtifactOwner,
    ArrayDescriptor,
    CandidateRecord,
    CoefficientManifest,
    Component,
    ComponentLayout,
    DomainValidationError,
    ObjectRecord,
    OwnerKind,
    Placement,
    ResultRecord,
)


OBJECTS_FILE = "objects.jsonl"
CANDIDATES_FILE = "candidates.jsonl"
RESULTS_FILE = "results.jsonl"

_COMPONENT_NAMES = ("y", "cb", "cr")
_ARRAY_PREFIX_DTYPES = {
    "coef": np.dtype("<i4"),
    "coefficient_validity": np.dtype("u1"),
    "block_validity": np.dtype("u1"),
    "source_span_ref_range": np.dtype("<i8"),
    "source_span_refs": np.dtype("<i4"),
    "placement_owner": np.dtype("<i4"),
}
EXPECTED_ARRAY_DTYPES = {
    f"{prefix}_{component}": dtype
    for component in _COMPONENT_NAMES
    for prefix, dtype in _ARRAY_PREFIX_DTYPES.items()
}
EXPECTED_ARRAY_NAMES = frozenset(EXPECTED_ARRAY_DTYPES)

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ZIP_EXTERNAL_ATTR = 0o100600 << 16
_ZIP_COMPRESSION_LEVEL = 9

RecordT = TypeVar("RecordT", ObjectRecord, CandidateRecord, ResultRecord)


def write_coefficient_npz(
    run_path: str | Path,
    relative_path: str | Path,
    arrays: Mapping[str, np.ndarray],
    *,
    owner: ArtifactOwner,
    source_span_ids: Sequence[str],
) -> CoefficientManifest:
    """Write one deterministic object/candidate coefficient NPZ and return its manifest."""
    run_dir = Path(run_path).resolve()
    record = _read_mutable_run(run_dir)
    if record["status"] != "running":
        raise ArtifactFormatError("coefficient artifacts can only be written to a running run")
    if not isinstance(owner, ArtifactOwner):
        raise TypeError("coefficient owner must be an ArtifactOwner")
    _validate_owner_stage(record["stage"], owner)
    destination = _safe_artifact_path(run_dir, relative_path)
    if destination.name in {"run.json", "completed.json"}:
        raise ValueError("run metadata paths are reserved")
    if destination.suffix != ".npz":
        raise ValueError("coefficient artifact path must end with .npz")

    normalized = _canonicalize_arrays(arrays)
    layouts = _component_layouts(normalized)
    _validate_array_semantics(normalized, len(source_span_ids), layouts)
    descriptors = tuple(
        ArrayDescriptor(name, tuple(normalized[name].shape), normalized[name].dtype.str)
        for name in sorted(normalized)
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    manifest: CoefficientManifest | None = None
    try:
        with os.fdopen(descriptor, "w+b") as stream:
            with zipfile.ZipFile(
                stream,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=_ZIP_COMPRESSION_LEVEL,
                strict_timestamps=True,
            ) as archive:
                for name in sorted(normalized):
                    info = zipfile.ZipInfo(f"{name}.npy", date_time=_ZIP_TIMESTAMP)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.create_system = 3
                    info.external_attr = _ZIP_EXTERNAL_ATTR
                    info._compresslevel = _ZIP_COMPRESSION_LEVEL
                    with archive.open(info, mode="w", force_zip64=True) as member:
                        np.lib.format.write_array(
                            member,
                            normalized[name],
                            version=(2, 0),
                            allow_pickle=False,
                        )
            stream.flush()
            os.fsync(stream.fileno())

        manifest = CoefficientManifest(
            owner=owner,
            source_span_ids=tuple(source_span_ids),
            npz_path=destination.relative_to(run_dir).as_posix(),
            npz_sha256=sha256_file(temp_path),
            npz_size=temp_path.stat().st_size,
            arrays=descriptors,
            components=layouts,
        )
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
    assert manifest is not None
    return manifest


def read_coefficient_npz(
    run_path: str | Path,
    manifest: CoefficientManifest | Mapping[str, Any],
) -> dict[str, np.ndarray]:
    """Validate a manifest and load its NPZ without ever enabling pickle."""
    run_dir = Path(run_path).resolve()
    run_record = read_run(run_dir)
    parsed = _coefficient_manifest(manifest)
    require_compatible_schema(
        parsed.to_dict(),
        schema=COEFFICIENT_MANIFEST_SCHEMA,
        current_version=FORENSIC_SCHEMA_VERSION,
    )
    _validate_owner_stage(run_record["stage"], parsed.owner)
    try:
        path = _safe_artifact_path(run_dir, parsed.npz_path)
    except ValueError as exc:
        raise ArtifactFormatError(f"invalid coefficient NPZ path: {exc}") from exc
    if not path.is_file():
        raise ArtifactFormatError("coefficient NPZ artifact is missing")
    if path.is_symlink():
        raise ArtifactFormatError("coefficient NPZ artifact cannot be a symbolic link")
    if path.stat().st_size != parsed.npz_size:
        raise ArtifactFormatError("coefficient NPZ size does not match its manifest")
    if sha256_file(path) != parsed.npz_sha256:
        raise ArtifactFormatError("coefficient NPZ SHA-256 does not match its manifest")

    manifest_descriptors = {item.name: item for item in parsed.arrays}
    if set(manifest_descriptors) != EXPECTED_ARRAY_NAMES:
        raise ArtifactFormatError("coefficient manifest has unknown or missing arrays")

    _validate_zip_contract(path)
    loaded: dict[str, np.ndarray] = {}
    try:
        with np.load(path, allow_pickle=False) as archive:
            if len(archive.files) != len(set(archive.files)):
                raise ArtifactFormatError("coefficient NPZ array names must be unique")
            if set(archive.files) != EXPECTED_ARRAY_NAMES:
                raise ArtifactFormatError("coefficient NPZ has unknown or missing arrays")
            for name in sorted(archive.files):
                array = archive[name]
                if array.dtype.hasobject:
                    raise ArtifactFormatError("object dtype arrays are forbidden")
                descriptor = manifest_descriptors[name]
                if tuple(array.shape) != descriptor.shape:
                    raise ArtifactFormatError(f"array shape disagrees with manifest: {name}")
                if array.dtype.str != descriptor.dtype:
                    raise ArtifactFormatError(f"array dtype disagrees with manifest: {name}")
                expected_dtype = EXPECTED_ARRAY_DTYPES[name]
                if array.dtype.str != expected_dtype.str:
                    raise ArtifactFormatError(f"array dtype is not canonical: {name}")
                loaded[name] = np.ascontiguousarray(array)
    except ArtifactFormatError:
        raise
    except (OSError, ValueError, TypeError, zipfile.BadZipFile) as exc:
        raise ArtifactFormatError(f"cannot load coefficient NPZ safely: {exc}") from exc

    expected_layouts = _component_layouts(loaded)
    if expected_layouts != dict(parsed.components):
        raise ArtifactFormatError("coefficient component layouts disagree with manifest")
    _validate_array_semantics(loaded, len(parsed.source_span_ids), expected_layouts)
    return loaded


def write_objects_jsonl(run_path: str | Path, records: Iterable[ObjectRecord]) -> Path:
    values = _record_list(records, ObjectRecord, "object")
    _require_unique((item.object_id for item in values), "object IDs")
    _validate_object_parent_graph(values)
    run_dir, run_record = _require_run_stage(run_path, "discovery", mutable=True)
    _validate_coefficient_npz_path_uniqueness(run_dir, values)
    _validate_source_bounds(values, run_record["source"]["size"])
    for item in values:
        if item.coefficient_manifest is not None:
            read_coefficient_npz(run_dir, item.coefficient_manifest)
    return write_run_jsonl(
        run_dir,
        OBJECTS_FILE,
        [item.to_dict() for item in values],
        sort_key=lambda row: (row["disk_offset"], row["object_id"]),
    )


def read_objects_jsonl(run_path: str | Path) -> list[ObjectRecord]:
    run_dir, run_record = _require_run_stage(run_path, "discovery", mutable=False)
    values = _read_records(
        run_dir / OBJECTS_FILE,
        schema=OBJECT_SCHEMA,
        record_type=ObjectRecord,
    )
    _require_unique((item.object_id for item in values), "object IDs")
    _validate_object_parent_graph(values)
    _validate_coefficient_npz_path_uniqueness(run_dir, values)
    _validate_source_bounds(values, run_record["source"]["size"])
    for item in values:
        if item.coefficient_manifest is not None:
            read_coefficient_npz(run_dir, item.coefficient_manifest)
    return values


def write_candidates_jsonl(
    run_path: str | Path, records: Iterable[CandidateRecord]
) -> Path:
    values = _record_list(records, CandidateRecord, "candidate")
    _validate_candidate_uniqueness(values)
    run_dir, run_record = _require_run_stage(run_path, "reconstruction", mutable=True)
    _validate_coefficient_npz_path_uniqueness(run_dir, values)
    _validate_source_bounds(values, run_record["source"]["size"])
    _validate_parent_object_references(run_dir, run_record, values)
    for item in values:
        if item.coefficient_manifest is not None:
            arrays = read_coefficient_npz(run_dir, item.coefficient_manifest)
            _validate_placement_owners(item.placements, item.coefficient_manifest, arrays)
    return write_run_jsonl(
        run_dir,
        CANDIDATES_FILE,
        [item.to_dict() for item in values],
        sort_key=lambda row: (
            row["object_id"],
            row["candidate_ordinal"],
            row["candidate_fingerprint"],
        ),
    )


def read_candidates_jsonl(run_path: str | Path) -> list[CandidateRecord]:
    run_dir, run_record = _require_run_stage(run_path, "reconstruction", mutable=False)
    values = _read_records(
        run_dir / CANDIDATES_FILE,
        schema=CANDIDATE_SCHEMA,
        record_type=CandidateRecord,
    )
    _validate_candidate_uniqueness(values)
    _validate_coefficient_npz_path_uniqueness(run_dir, values)
    _validate_source_bounds(values, run_record["source"]["size"])
    _validate_parent_object_references(run_dir, run_record, values)
    for item in values:
        if item.coefficient_manifest is not None:
            arrays = read_coefficient_npz(run_dir, item.coefficient_manifest)
            _validate_placement_owners(item.placements, item.coefficient_manifest, arrays)
    return values


def write_results_jsonl(run_path: str | Path, records: Iterable[ResultRecord]) -> Path:
    values = _record_list(records, ResultRecord, "result")
    _require_unique((item.object_id for item in values), "result object IDs")
    run_dir, run_record = _require_run_stage(run_path, "reconstruction", mutable=True)
    _validate_parent_object_references(run_dir, run_record, values)
    _validate_results_against_candidates(run_dir, values)
    return write_run_jsonl(
        run_dir,
        RESULTS_FILE,
        [item.to_dict() for item in values],
        sort_key=lambda row: row["object_id"],
    )


def read_results_jsonl(run_path: str | Path) -> list[ResultRecord]:
    run_dir, run_record = _require_run_stage(run_path, "reconstruction", mutable=False)
    values = _read_records(
        run_dir / RESULTS_FILE,
        schema=RESULT_SCHEMA,
        record_type=ResultRecord,
    )
    _require_unique((item.object_id for item in values), "result object IDs")
    _validate_parent_object_references(run_dir, run_record, values)
    _validate_results_against_candidates(run_dir, values)
    return values


def _canonicalize_arrays(arrays: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    if not isinstance(arrays, Mapping):
        raise ArtifactFormatError("coefficient arrays must be a mapping")
    if not all(isinstance(name, str) for name in arrays):
        raise ArtifactFormatError("coefficient array names must be strings")
    if set(arrays) != EXPECTED_ARRAY_NAMES:
        unknown = sorted(set(arrays) - EXPECTED_ARRAY_NAMES)
        missing = sorted(EXPECTED_ARRAY_NAMES - set(arrays))
        detail = []
        if unknown:
            detail.append("unknown=" + ",".join(unknown))
        if missing:
            detail.append("missing=" + ",".join(missing))
        raise ArtifactFormatError("coefficient arrays do not match the fixed contract: " + " ".join(detail))

    normalized: dict[str, np.ndarray] = {}
    for name in sorted(arrays):
        array = np.asarray(arrays[name])
        if array.dtype.hasobject:
            raise ArtifactFormatError(f"object dtype arrays are forbidden: {name}")
        expected = EXPECTED_ARRAY_DTYPES[name]
        if array.dtype.kind != expected.kind or array.dtype.itemsize != expected.itemsize:
            raise ArtifactFormatError(
                f"array {name} must have dtype kind/itemsize {expected.kind}{expected.itemsize}"
            )
        normalized[name] = np.ascontiguousarray(array.astype(expected, copy=False))
    return normalized


def _component_layouts(arrays: Mapping[str, np.ndarray]) -> dict[Component, ComponentLayout]:
    layouts: dict[Component, ComponentLayout] = {}
    for component_name in _COMPONENT_NAMES:
        component = Component(component_name)
        coefficient = arrays[f"coef_{component_name}"]
        owner = arrays[f"placement_owner_{component_name}"]
        if coefficient.ndim != 3:
            raise ArtifactFormatError(f"coefficient array must have rank 3: {component_name}")
        if owner.ndim != 2:
            raise ArtifactFormatError(f"placement owner array must have rank 2: {component_name}")
        layouts[component] = ComponentLayout(
            coefficient.shape[0], owner.shape[0], owner.shape[1]
        )
    return layouts


def _validate_array_semantics(
    arrays: Mapping[str, np.ndarray],
    source_span_count: int,
    layouts: Mapping[Component, ComponentLayout],
) -> None:
    if source_span_count < 0:
        raise ArtifactFormatError("source span count cannot be negative")
    for component_name in _COMPONENT_NAMES:
        component = Component(component_name)
        layout = layouts[component]
        count = layout.source_block_count
        coefficient = arrays[f"coef_{component_name}"]
        coefficient_validity = arrays[f"coefficient_validity_{component_name}"]
        block_validity = arrays[f"block_validity_{component_name}"]
        ref_ranges = arrays[f"source_span_ref_range_{component_name}"]
        refs = arrays[f"source_span_refs_{component_name}"]
        owners = arrays[f"placement_owner_{component_name}"]

        expected_shapes = {
            f"coef_{component_name}": (count, 8, 8),
            f"coefficient_validity_{component_name}": (count, 8, 8),
            f"block_validity_{component_name}": (count,),
            f"source_span_ref_range_{component_name}": (count, 8, 8, 2),
            f"source_span_refs_{component_name}": (refs.size,),
            f"placement_owner_{component_name}": (
                layout.raster_block_rows,
                layout.raster_block_columns,
            ),
        }
        for name, shape in expected_shapes.items():
            if arrays[name].shape != shape:
                raise ArtifactFormatError(f"invalid coefficient array shape: {name}")
        if refs.ndim != 1:
            raise ArtifactFormatError(f"source span refs must be one-dimensional: {component_name}")
        if not np.all((coefficient_validity == 0) | (coefficient_validity == 1)):
            raise ArtifactFormatError("coefficient validity enum must be 0 or 1")
        if not np.all((block_validity >= 0) & (block_validity <= 2)):
            raise ArtifactFormatError("block validity enum must be 0, 1, or 2")
        if np.any((coefficient_validity == 0) & (coefficient != 0)):
            raise ArtifactFormatError("missing coefficient values must use zero sentinel")

        valid_counts = coefficient_validity.reshape(count, 64).sum(axis=1, dtype=np.int64)
        expected_block = np.where(valid_counts == 0, 0, np.where(valid_counts == 64, 2, 1))
        if not np.array_equal(block_validity.astype(np.int64), expected_block):
            raise ArtifactFormatError("block validity disagrees with coefficient validity")

        cursor = 0
        flat_validity = coefficient_validity.reshape(-1)
        flat_ranges = ref_ranges.reshape(-1, 2)
        for validity, pair in zip(flat_validity, flat_ranges, strict=True):
            start = int(pair[0])
            ref_count = int(pair[1])
            if validity == 0:
                if start != -1 or ref_count != 0:
                    raise ArtifactFormatError("missing coefficient must use source ref range (-1,0)")
                continue
            if start != cursor or ref_count < 1 or start + ref_count > refs.size:
                raise ArtifactFormatError("source span reference ranges are not canonical")
            selected = refs[start : start + ref_count]
            if np.any(selected < 0) or np.any(selected >= source_span_count):
                raise ArtifactFormatError("source span reference is outside the normalized table")
            if selected.size > 1 and np.any(np.diff(selected.astype(np.int64)) <= 0):
                raise ArtifactFormatError("a coefficient's source span references must be strictly ordered")
            cursor += ref_count
        if cursor != refs.size:
            raise ArtifactFormatError("source span reference array has unreferenced entries")

        allowed_owners = (owners == -1) | (owners == -2) | (
            (owners >= 0) & (owners < count)
        )
        if not np.all(allowed_owners):
            raise ArtifactFormatError("placement owner contains an invalid source index or sentinel")
        positive = owners[owners >= 0]
        if positive.size != np.unique(positive).size:
            raise ArtifactFormatError("a source block cannot own more than one raster slot")


def _validate_zip_contract(path: Path) -> None:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            expected_members = [f"{name}.npy" for name in sorted(EXPECTED_ARRAY_NAMES)]
            if [item.filename for item in infos] != expected_members:
                raise ArtifactFormatError("coefficient NPZ ZIP members are unknown, missing, or unordered")
            if archive.comment:
                raise ArtifactFormatError("coefficient NPZ ZIP comment is forbidden")
            for info in infos:
                if info.date_time != _ZIP_TIMESTAMP:
                    raise ArtifactFormatError("coefficient NPZ ZIP timestamp is not canonical")
                if info.compress_type != zipfile.ZIP_DEFLATED:
                    raise ArtifactFormatError("coefficient NPZ ZIP compression is not canonical")
                if info.create_system != 3 or info.external_attr != _ZIP_EXTERNAL_ATTR:
                    raise ArtifactFormatError("coefficient NPZ ZIP permissions are not canonical")
                with archive.open(info, "r") as member:
                    if np.lib.format.read_magic(member) != (2, 0):
                        raise ArtifactFormatError(
                            "coefficient NPZ members must use NPY format version 2.0"
                        )
    except ArtifactFormatError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ArtifactFormatError(f"invalid coefficient NPZ ZIP container: {exc}") from exc


def _validate_placement_owners(
    placements: Sequence[Placement],
    manifest: CoefficientManifest,
    arrays: Mapping[str, np.ndarray],
) -> None:
    grouped: dict[Component, dict[tuple[int, int], list[int]]] = {
        component: defaultdict(list) for component in Component
    }
    for placement in placements:
        grouped[placement.component][
            (placement.raster_block_row, placement.raster_block_column)
        ].append(placement.source_block_index)
    for component in Component:
        layout = manifest.components[component]
        expected = np.full(
            (layout.raster_block_rows, layout.raster_block_columns),
            -1,
            dtype=np.dtype("<i4"),
        )
        for (row, column), sources in grouped[component].items():
            expected[row, column] = sources[0] if len(sources) == 1 else -2
        if not np.array_equal(expected, arrays[f"placement_owner_{component.value}"]):
            raise ArtifactFormatError("placement owner array disagrees with placement records")


def _coefficient_manifest(
    value: CoefficientManifest | Mapping[str, Any],
) -> CoefficientManifest:
    if isinstance(value, CoefficientManifest):
        return value
    try:
        return CoefficientManifest.from_dict(value)
    except DomainValidationError as exc:
        raise ArtifactFormatError(f"invalid coefficient manifest: {exc}") from exc


def _record_list(
    values: Iterable[RecordT], record_type: type[RecordT], label: str
) -> list[RecordT]:
    result = list(values)
    if not all(isinstance(item, record_type) for item in result):
        raise TypeError(f"{label} writer accepts only {record_type.__name__} values")
    return result


def _validate_owner_stage(stage: str, owner: ArtifactOwner) -> None:
    expected_stage = {
        OwnerKind.OBJECT: "discovery",
        OwnerKind.CANDIDATE: "reconstruction",
    }[owner.kind]
    if stage != expected_stage:
        raise ArtifactFormatError(
            f"{owner.kind.value} coefficient artifacts require a {expected_stage} run"
        )


def _validate_source_bounds(
    values: Sequence[ObjectRecord | CandidateRecord], source_size: int
) -> None:
    for item in values:
        for span in item.source_spans:
            if span.disk_bytes.end > source_size:
                raise ArtifactFormatError(
                    f"observed source span is outside the case source: "
                    f"{item.object_id}/{span.span_id}"
                )


def _validate_object_parent_graph(values: Sequence[ObjectRecord]) -> None:
    by_id = {item.object_id: item for item in values}
    for item in values:
        if item.parent_object_id is not None and item.parent_object_id not in by_id:
            raise ArtifactFormatError(
                f"parent object ID is unresolved in objects.jsonl: {item.parent_object_id}"
            )

    visited: set[str] = set()
    for root_id in sorted(by_id):
        if root_id in visited:
            continue
        path: list[str] = []
        path_ids: set[str] = set()
        current_id: str | None = root_id
        while current_id is not None and current_id not in visited:
            if current_id in path_ids:
                raise ArtifactFormatError("object parent graph contains a cycle")
            path.append(current_id)
            path_ids.add(current_id)
            current_id = by_id[current_id].parent_object_id
        visited.update(path_ids)


def _validate_coefficient_npz_path_uniqueness(
    run_dir: Path,
    values: Sequence[ObjectRecord | CandidateRecord],
) -> None:
    paths: list[str] = []
    for item in values:
        if item.coefficient_manifest is None:
            continue
        try:
            path = _safe_artifact_path(run_dir, item.coefficient_manifest.npz_path)
        except ValueError as exc:
            raise ArtifactFormatError(f"invalid coefficient NPZ path: {exc}") from exc
        paths.append(os.path.normcase(str(path.resolve())))
    _require_unique(paths, "coefficient NPZ paths within a record file")


def _validate_parent_object_references(
    run_dir: Path,
    run_record: Mapping[str, Any],
    values: Sequence[CandidateRecord | ResultRecord],
) -> None:
    requested_ids = {item.object_id for item in values}
    if not requested_ids:
        return

    counts: Counter[str] = Counter()
    for parent_id in run_record["parent_run_ids"]:
        parent_dir = run_dir.parent / parent_id
        parent_record = read_run(parent_dir)
        if parent_record["stage"] != "discovery":
            raise ArtifactFormatError(
                f"reconstruction parent is not a discovery run: {parent_id}"
            )
        counts.update(item.object_id for item in read_objects_jsonl(parent_dir))

    for object_id in sorted(requested_ids):
        if counts[object_id] == 0:
            raise ArtifactFormatError(
                f"object ID does not resolve to a parent discovery object: {object_id}"
            )
        if counts[object_id] > 1:
            raise ArtifactFormatError(
                f"object ID resolves to multiple parent discovery objects: {object_id}"
            )


def _read_records(
    path: Path,
    *,
    schema: str,
    record_type: type[RecordT],
) -> list[RecordT]:
    values: list[RecordT] = []
    for line_number, record in enumerate(read_jsonl(path), start=1):
        try:
            require_compatible_schema(
                record,
                schema=schema,
                current_version=FORENSIC_SCHEMA_VERSION,
            )
            values.append(record_type.from_dict(record))
        except (ArtifactFormatError, DomainValidationError) as exc:
            raise ArtifactFormatError(f"invalid {schema} record at line {line_number}: {exc}") from exc
    return values


def _require_run_stage(
    run_path: str | Path, expected_stage: str, *, mutable: bool
) -> tuple[Path, dict[str, Any]]:
    run_dir = Path(run_path).resolve()
    record = _read_mutable_run(run_dir) if mutable else read_run(run_dir)
    if mutable and record["status"] != "running":
        raise ArtifactFormatError("forensic artifacts can only be written to a running run")
    if record["stage"] != expected_stage:
        raise ArtifactFormatError(
            f"{expected_stage} artifact cannot be used in a {record['stage']} run"
        )
    return run_dir, record


def _validate_candidate_uniqueness(values: Sequence[CandidateRecord]) -> None:
    _require_unique(
        ((item.object_id, item.candidate_id) for item in values),
        "candidate IDs within an object",
    )
    _require_unique(
        ((item.object_id, item.candidate_fingerprint) for item in values),
        "candidate fingerprints within an object",
    )


def _validate_results_against_candidates(
    run_dir: Path, results: Sequence[ResultRecord]
) -> None:
    candidate_path = run_dir / CANDIDATES_FILE
    if candidate_path.is_file():
        candidates = read_candidates_jsonl(run_dir)
    else:
        candidates = []
    by_object: dict[str, list[CandidateRecord]] = defaultdict(list)
    for candidate in candidates:
        by_object[candidate.object_id].append(candidate)
    for result in results:
        available = by_object[result.object_id]
        if result.candidate_count != len(available):
            raise ArtifactFormatError("result candidate_count disagrees with candidates.jsonl")
        if result.selected_candidate_id is not None:
            matches = [
                item
                for item in available
                if item.candidate_id == result.selected_candidate_id
                and item.candidate_fingerprint == result.selected_candidate_fingerprint
            ]
            if len(matches) != 1:
                raise ArtifactFormatError("result selected candidate reference is unresolved")


def _require_unique(values: Iterable[Any], label: str) -> None:
    counts = Counter(values)
    if any(count > 1 for count in counts.values()):
        raise ArtifactFormatError(f"{label} must be unique")
