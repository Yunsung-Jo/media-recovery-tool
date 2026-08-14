from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, ClassVar


class DomainValidationError(ValueError):
    """Raised when a forensic value violates the on-disk domain contract."""


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class Provenance(_StringEnum):
    OBSERVED = "observed"
    DECODED = "decoded"
    INFERRED = "inferred"
    GENERATED = "generated"


class MediaType(_StringEnum):
    JPEG = "jpeg"
    AVI = "avi"


class CoordinateSpace(_StringEnum):
    DISK_BYTE = "disk_byte"
    OBJECT_RAW_BYTE = "object_raw_byte"
    RAW_ENTROPY_BIT = "raw_entropy_bit"
    DESTUFFED_BIT = "destuffed_bit"
    VIRTUAL_WORK_BIT = "virtual_work_bit"


class Component(_StringEnum):
    Y = "y"
    CB = "cb"
    CR = "cr"


COMPONENTS = (Component.Y, Component.CB, Component.CR)


class HypothesisKind(_StringEnum):
    BOUNDARY = "boundary"
    HEADER = "header"
    ENTROPY = "entropy"
    PLACEMENT = "placement"


class VirtualEditKind(_StringEnum):
    BYTE_SUBSTITUTION = "byte_substitution"
    BYTE_DELETION = "byte_deletion"
    BYTE_INSERTION = "byte_insertion"
    BIT_RESYNC = "bit_resync"
    DC_RESET = "dc_reset"


class InterventionKind(_StringEnum):
    BYTE_SUBSTITUTION = "byte_substitution"
    BYTE_DELETION = "byte_deletion"
    BYTE_INSERTION = "byte_insertion"
    BIT_RESYNC = "bit_resync"
    DC_RESET = "dc_reset"
    MCU_PLACEMENT = "mcu_placement"


class ExecutionStatus(_StringEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ERROR = "error"


class SupportStatus(_StringEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"


class DecodeExtent(_StringEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NONE = "none"
    NOT_ATTEMPTED = "not_attempted"


class SelectionStatus(_StringEnum):
    SOURCE_CANDIDATE_SELECTED = "source_candidate_selected"
    RECONSTRUCTION_CANDIDATE_SELECTED = "reconstruction_candidate_selected"
    NO_SUPPORTED_CANDIDATE = "no_supported_candidate"
    NOT_APPLICABLE = "not_applicable"


class HeaderBasis(_StringEnum):
    SOURCE = "source"
    SOURCE_REPAIRED = "source_repaired"
    STANDARD_ASSUMPTION = "standard_assumption"
    DONOR_ASSUMPTION = "donor_assumption"
    HYPOTHESIS = "hypothesis"
    NONE = "none"


class ArtifactStatus(_StringEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class OwnerKind(_StringEnum):
    OBJECT = "object"
    CANDIDATE = "candidate"


OBJECT_SCHEMA = "media-recovery.object"
CANDIDATE_SCHEMA = "media-recovery.candidate"
RESULT_SCHEMA = "media-recovery.result"
COEFFICIENT_MANIFEST_SCHEMA = "media-recovery.coefficient-manifest"
FORENSIC_SCHEMA_VERSION = "1.0"
CANDIDATE_FINGERPRINT_SCHEMA = "media-recovery.candidate-fingerprint"
CANDIDATE_FINGERPRINT_VERSION = "1.0"

MAX_DISK_OFFSET = 2**64 - 1
MAX_CANDIDATE_ORDINAL = 999
GAP_OWNER = -1
OVERLAP_OWNER = -2

_OBJECT_ID_PATTERN = re.compile(r"^(jpeg|avi)-[0-9a-f]{16}$")
_CANDIDATE_ID_PATTERN = re.compile(r"^cand-[0-9]{3}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SPAN_ID_PATTERN = re.compile(r"^span-[0-9]{6}$")
_HYPOTHESIS_ID_PATTERN = re.compile(r"^hyp-[0-9]{4}$")
_SEGMENT_ID_PATTERN = re.compile(r"^seg-[0-9]{4}$")
_EDIT_ID_PATTERN = re.compile(r"^edit-[0-9]{4}$")
_PLACEMENT_ID_PATTERN = re.compile(r"^place-[0-9]{6}$")
_ASSERTION_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SCHEMA_VERSION_PATTERN = re.compile(r"^1\.(0|[1-9][0-9]*)$")


def object_id_for(media_type: MediaType | str, disk_offset: int) -> str:
    media = _enum_value(MediaType, media_type, "media_type")
    _require_int(disk_offset, "disk_offset", minimum=0, maximum=MAX_DISK_OFFSET)
    return f"{media.value}-{disk_offset:016x}"


def candidate_id_for_ordinal(ordinal: int) -> str:
    _require_int(
        ordinal,
        "candidate_ordinal",
        minimum=0,
        maximum=MAX_CANDIDATE_ORDINAL,
    )
    return f"cand-{ordinal:03d}"


@dataclass(frozen=True, slots=True)
class CoordinateRange:
    space: CoordinateSpace
    start: int
    end: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "space", _enum_value(CoordinateSpace, self.space, "space"))
        _require_int(self.start, "range start", minimum=0)
        _require_int(self.end, "range end", minimum=self.start)

    def to_dict(self) -> dict[str, Any]:
        return {"end": self.end, "space": self.space.value, "start": self.start}

    @classmethod
    def from_dict(cls, value: Any) -> CoordinateRange:
        record = _mapping(value, "coordinate range")
        _require_keys(record, {"space", "start", "end"})
        return cls(record["space"], record["start"], record["end"])


@dataclass(frozen=True, slots=True)
class IndexRange:
    start: int
    end: int

    def __post_init__(self) -> None:
        _require_int(self.start, "index range start", minimum=0)
        _require_int(self.end, "index range end", minimum=self.start)

    def to_dict(self) -> dict[str, int]:
        return {"end": self.end, "start": self.start}

    @classmethod
    def from_dict(cls, value: Any) -> IndexRange:
        record = _mapping(value, "index range")
        _require_keys(record, {"start", "end"})
        return cls(record["start"], record["end"])


@dataclass(frozen=True, slots=True)
class SourceSpan:
    span_id: str
    disk_bytes: CoordinateRange
    object_raw_bytes: CoordinateRange
    raw_entropy_bits: CoordinateRange | None = None
    destuffed_bits: CoordinateRange | None = None
    provenance: Provenance = Provenance.OBSERVED

    def __post_init__(self) -> None:
        if not isinstance(self.span_id, str) or _SPAN_ID_PATTERN.fullmatch(self.span_id) is None:
            raise DomainValidationError("invalid source span ID")
        if self.disk_bytes.space is not CoordinateSpace.DISK_BYTE:
            raise DomainValidationError("disk_bytes must use disk_byte coordinates")
        if self.object_raw_bytes.space is not CoordinateSpace.OBJECT_RAW_BYTE:
            raise DomainValidationError("object_raw_bytes must use object_raw_byte coordinates")
        if self.disk_bytes.end == self.disk_bytes.start:
            raise DomainValidationError("observed source spans must not be empty")
        if self.disk_bytes.end - self.disk_bytes.start != (
            self.object_raw_bytes.end - self.object_raw_bytes.start
        ):
            raise DomainValidationError("disk and object raw source spans must have equal byte length")
        if self.raw_entropy_bits is not None:
            if self.raw_entropy_bits.space is not CoordinateSpace.RAW_ENTROPY_BIT:
                raise DomainValidationError("raw_entropy_bits must use raw_entropy_bit coordinates")
            if self.raw_entropy_bits.end == self.raw_entropy_bits.start:
                raise DomainValidationError("raw entropy span must not be empty")
        if self.destuffed_bits is not None:
            if self.destuffed_bits.space is not CoordinateSpace.DESTUFFED_BIT:
                raise DomainValidationError("destuffed_bits must use destuffed_bit coordinates")
            if self.destuffed_bits.end == self.destuffed_bits.start:
                raise DomainValidationError("destuffed span must not be empty")
        provenance = _enum_value(Provenance, self.provenance, "source span provenance")
        if provenance is not Provenance.OBSERVED:
            raise DomainValidationError("source spans must have observed provenance")
        object.__setattr__(self, "provenance", provenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "destuffed_bits": None if self.destuffed_bits is None else self.destuffed_bits.to_dict(),
            "disk_bytes": self.disk_bytes.to_dict(),
            "object_raw_bytes": self.object_raw_bytes.to_dict(),
            "provenance": self.provenance.value,
            "raw_entropy_bits": (
                None if self.raw_entropy_bits is None else self.raw_entropy_bits.to_dict()
            ),
            "span_id": self.span_id,
        }

    @classmethod
    def from_dict(cls, value: Any) -> SourceSpan:
        record = _mapping(value, "source span")
        _require_keys(
            record,
            {
                "span_id",
                "disk_bytes",
                "object_raw_bytes",
                "raw_entropy_bits",
                "destuffed_bits",
                "provenance",
            },
        )
        return cls(
            span_id=record["span_id"],
            disk_bytes=CoordinateRange.from_dict(record["disk_bytes"]),
            object_raw_bytes=CoordinateRange.from_dict(record["object_raw_bytes"]),
            raw_entropy_bits=_optional_range(record["raw_entropy_bits"]),
            destuffed_bits=_optional_range(record["destuffed_bits"]),
            provenance=record["provenance"],
        )


@dataclass(frozen=True, slots=True)
class HypothesisAssertion:
    name: str
    value: Any
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _ASSERTION_NAME_PATTERN.fullmatch(self.name) is None:
            raise DomainValidationError("invalid hypothesis assertion name")
        provenance = _enum_value(Provenance, self.provenance, "assertion provenance")
        if provenance not in {Provenance.OBSERVED, Provenance.INFERRED}:
            raise DomainValidationError("hypothesis assertions must be observed or inferred")
        _validate_json_value(self.value)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "value", _freeze_json(self.value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provenance": self.provenance.value,
            "value": _thaw_json(self.value),
        }

    @classmethod
    def from_dict(cls, value: Any) -> HypothesisAssertion:
        record = _mapping(value, "hypothesis assertion")
        _require_keys(record, {"name", "value", "provenance"})
        return cls(record["name"], record["value"], record["provenance"])


@dataclass(frozen=True, slots=True)
class Hypothesis:
    hypothesis_id: str
    kind: HypothesisKind
    assertions: tuple[HypothesisAssertion, ...]
    evidence_span_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.hypothesis_id, str)
            or _HYPOTHESIS_ID_PATTERN.fullmatch(self.hypothesis_id) is None
        ):
            raise DomainValidationError("invalid hypothesis ID")
        object.__setattr__(self, "kind", _enum_value(HypothesisKind, self.kind, "hypothesis kind"))
        assertions = tuple(sorted(self.assertions, key=lambda item: item.name))
        if not assertions:
            raise DomainValidationError("hypothesis must contain at least one assertion")
        if len({item.name for item in assertions}) != len(assertions):
            raise DomainValidationError("hypothesis assertion names must be unique")
        evidence = tuple(sorted(self.evidence_span_ids))
        _validate_ids(evidence, _SPAN_ID_PATTERN, "hypothesis evidence span")
        object.__setattr__(self, "assertions", assertions)
        object.__setattr__(self, "evidence_span_ids", evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertions": [item.to_dict() for item in self.assertions],
            "evidence_span_ids": list(self.evidence_span_ids),
            "hypothesis_id": self.hypothesis_id,
            "kind": self.kind.value,
        }

    @classmethod
    def from_dict(cls, value: Any) -> Hypothesis:
        record = _mapping(value, "hypothesis")
        _require_keys(record, {"hypothesis_id", "kind", "assertions", "evidence_span_ids"})
        return cls(
            record["hypothesis_id"],
            record["kind"],
            tuple(HypothesisAssertion.from_dict(item) for item in _sequence(record["assertions"], "assertions")),
            tuple(_string_sequence(record["evidence_span_ids"], "evidence_span_ids")),
        )


@dataclass(frozen=True, slots=True)
class VirtualEdit:
    edit_id: str
    kind: VirtualEditKind
    source_range: CoordinateRange
    work_range: CoordinateRange
    assumption: Mapping[str, Any]
    provenance: Provenance = Provenance.INFERRED

    def __post_init__(self) -> None:
        if not isinstance(self.edit_id, str) or _EDIT_ID_PATTERN.fullmatch(self.edit_id) is None:
            raise DomainValidationError("invalid virtual edit ID")
        object.__setattr__(self, "kind", _enum_value(VirtualEditKind, self.kind, "virtual edit kind"))
        if self.source_range.space is CoordinateSpace.VIRTUAL_WORK_BIT:
            raise DomainValidationError("virtual edit source range must be a pre-edit coordinate")
        if self.work_range.space is not CoordinateSpace.VIRTUAL_WORK_BIT:
            raise DomainValidationError("virtual edit work range must use virtual_work_bit")
        assumption = dict(_mapping(self.assumption, "virtual edit assumption"))
        if not assumption:
            raise DomainValidationError("virtual edit assumption must not be empty")
        _validate_json_value(assumption)
        provenance = _enum_value(Provenance, self.provenance, "virtual edit provenance")
        if provenance is not Provenance.INFERRED:
            raise DomainValidationError("virtual edits must have inferred provenance")
        object.__setattr__(self, "assumption", _freeze_json(assumption))
        object.__setattr__(self, "provenance", provenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assumption": _thaw_json(self.assumption),
            "edit_id": self.edit_id,
            "kind": self.kind.value,
            "provenance": self.provenance.value,
            "source_range": self.source_range.to_dict(),
            "work_range": self.work_range.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> VirtualEdit:
        record = _mapping(value, "virtual edit")
        _require_keys(
            record,
            {"edit_id", "kind", "source_range", "work_range", "assumption", "provenance"},
        )
        return cls(
            record["edit_id"],
            record["kind"],
            CoordinateRange.from_dict(record["source_range"]),
            CoordinateRange.from_dict(record["work_range"]),
            record["assumption"],
            record["provenance"],
        )


@dataclass(frozen=True, slots=True)
class DecodeSegment:
    segment_id: str
    source_span_ids: tuple[str, ...]
    virtual_edit_ids: tuple[str, ...]
    work_bits: CoordinateRange
    mcu_range: IndexRange
    component_block_ranges: Mapping[Component, IndexRange]
    provenance: Provenance = Provenance.DECODED

    def __post_init__(self) -> None:
        if not isinstance(self.segment_id, str) or _SEGMENT_ID_PATTERN.fullmatch(self.segment_id) is None:
            raise DomainValidationError("invalid decode segment ID")
        source_span_ids = tuple(self.source_span_ids)
        virtual_edit_ids = tuple(self.virtual_edit_ids)
        if not source_span_ids:
            raise DomainValidationError("decode segment must reference at least one source span")
        _validate_ids(source_span_ids, _SPAN_ID_PATTERN, "decode segment source span")
        _validate_ids(virtual_edit_ids, _EDIT_ID_PATTERN, "decode segment virtual edit")
        if self.work_bits.space is not CoordinateSpace.VIRTUAL_WORK_BIT:
            raise DomainValidationError("decode segment work range must use virtual_work_bit")
        ranges = _component_mapping(self.component_block_ranges, "component_block_ranges")
        if any(not isinstance(value, IndexRange) for value in ranges.values()):
            raise DomainValidationError("component block ranges must contain IndexRange values")
        provenance = _enum_value(Provenance, self.provenance, "decode segment provenance")
        if provenance is not Provenance.DECODED:
            raise DomainValidationError("decode segments must have decoded provenance")
        object.__setattr__(self, "source_span_ids", source_span_ids)
        object.__setattr__(self, "virtual_edit_ids", virtual_edit_ids)
        object.__setattr__(self, "component_block_ranges", ranges)
        object.__setattr__(self, "provenance", provenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_block_ranges": {
                component.value: self.component_block_ranges[component].to_dict()
                for component in COMPONENTS
            },
            "mcu_range": self.mcu_range.to_dict(),
            "provenance": self.provenance.value,
            "segment_id": self.segment_id,
            "source_span_ids": list(self.source_span_ids),
            "virtual_edit_ids": list(self.virtual_edit_ids),
            "work_bits": self.work_bits.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> DecodeSegment:
        record = _mapping(value, "decode segment")
        _require_keys(
            record,
            {
                "segment_id",
                "source_span_ids",
                "virtual_edit_ids",
                "work_bits",
                "mcu_range",
                "component_block_ranges",
                "provenance",
            },
        )
        ranges = _mapping(record["component_block_ranges"], "component_block_ranges")
        return cls(
            record["segment_id"],
            tuple(_string_sequence(record["source_span_ids"], "source_span_ids")),
            tuple(_string_sequence(record["virtual_edit_ids"], "virtual_edit_ids")),
            CoordinateRange.from_dict(record["work_bits"]),
            IndexRange.from_dict(record["mcu_range"]),
            {key: IndexRange.from_dict(item) for key, item in ranges.items()},
            record["provenance"],
        )


@dataclass(frozen=True, slots=True)
class Placement:
    placement_id: str
    component: Component
    source_block_index: int
    raster_block_row: int
    raster_block_column: int
    provenance: Provenance = Provenance.INFERRED

    def __post_init__(self) -> None:
        if (
            not isinstance(self.placement_id, str)
            or _PLACEMENT_ID_PATTERN.fullmatch(self.placement_id) is None
        ):
            raise DomainValidationError("invalid placement ID")
        object.__setattr__(self, "component", _enum_value(Component, self.component, "component"))
        _require_int(self.source_block_index, "source_block_index", minimum=0)
        _require_int(self.raster_block_row, "raster_block_row", minimum=0)
        _require_int(self.raster_block_column, "raster_block_column", minimum=0)
        provenance = _enum_value(Provenance, self.provenance, "placement provenance")
        if provenance is not Provenance.INFERRED:
            raise DomainValidationError("placements must have inferred provenance")
        object.__setattr__(self, "provenance", provenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component.value,
            "placement_id": self.placement_id,
            "provenance": self.provenance.value,
            "raster_block_column": self.raster_block_column,
            "raster_block_row": self.raster_block_row,
            "source_block_index": self.source_block_index,
        }

    @classmethod
    def from_dict(cls, value: Any) -> Placement:
        record = _mapping(value, "placement")
        _require_keys(
            record,
            {
                "placement_id",
                "component",
                "source_block_index",
                "raster_block_row",
                "raster_block_column",
                "provenance",
            },
        )
        return cls(
            record["placement_id"],
            record["component"],
            record["source_block_index"],
            record["raster_block_row"],
            record["raster_block_column"],
            record["provenance"],
        )


@dataclass(frozen=True, slots=True)
class ComponentLayout:
    source_block_count: int
    raster_block_rows: int
    raster_block_columns: int

    def __post_init__(self) -> None:
        _require_int(self.source_block_count, "source_block_count", minimum=0)
        _require_int(self.raster_block_rows, "raster_block_rows", minimum=0)
        _require_int(self.raster_block_columns, "raster_block_columns", minimum=0)

    def to_dict(self) -> dict[str, int]:
        return {
            "raster_block_columns": self.raster_block_columns,
            "raster_block_rows": self.raster_block_rows,
            "source_block_count": self.source_block_count,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ComponentLayout:
        record = _mapping(value, "component layout")
        _require_keys(
            record,
            {"source_block_count", "raster_block_rows", "raster_block_columns"},
        )
        return cls(
            record["source_block_count"],
            record["raster_block_rows"],
            record["raster_block_columns"],
        )


@dataclass(frozen=True, slots=True)
class ArrayDescriptor:
    name: str
    shape: tuple[int, ...]
    dtype: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise DomainValidationError("array descriptor name must not be empty")
        if not isinstance(self.shape, tuple):
            object.__setattr__(self, "shape", tuple(self.shape))
        for dimension in self.shape:
            _require_int(dimension, "array dimension", minimum=0)
        if not isinstance(self.dtype, str) or not self.dtype:
            raise DomainValidationError("array descriptor dtype must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {"dtype": self.dtype, "name": self.name, "shape": list(self.shape)}

    @classmethod
    def from_dict(cls, value: Any) -> ArrayDescriptor:
        record = _mapping(value, "array descriptor")
        _require_keys(record, {"name", "shape", "dtype"})
        return cls(
            record["name"],
            tuple(_integer_sequence(record["shape"], "array shape", minimum=0)),
            record["dtype"],
        )


@dataclass(frozen=True, slots=True)
class ArtifactOwner:
    kind: OwnerKind
    object_id: str
    candidate_id: str | None = None
    candidate_fingerprint: str | None = None

    def __post_init__(self) -> None:
        kind = _enum_value(OwnerKind, self.kind, "artifact owner kind")
        _validate_object_id(self.object_id)
        if kind is OwnerKind.OBJECT:
            if self.candidate_id is not None or self.candidate_fingerprint is not None:
                raise DomainValidationError("object artifact owner cannot name a candidate")
        else:
            _validate_candidate_id(self.candidate_id)
            _validate_sha256(self.candidate_fingerprint, "candidate fingerprint")
        object.__setattr__(self, "kind", kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_fingerprint": self.candidate_fingerprint,
            "candidate_id": self.candidate_id,
            "kind": self.kind.value,
            "object_id": self.object_id,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ArtifactOwner:
        record = _mapping(value, "artifact owner")
        _require_keys(
            record,
            {"kind", "object_id", "candidate_id", "candidate_fingerprint"},
        )
        return cls(
            record["kind"],
            record["object_id"],
            record["candidate_id"],
            record["candidate_fingerprint"],
        )


@dataclass(frozen=True, slots=True)
class CoefficientManifest:
    owner: ArtifactOwner
    source_span_ids: tuple[str, ...]
    npz_path: str
    npz_sha256: str
    npz_size: int
    arrays: tuple[ArrayDescriptor, ...]
    components: Mapping[Component, ComponentLayout]
    schema_version: str = FORENSIC_SCHEMA_VERSION
    required_features: tuple[str, ...] = ()
    provenance: Provenance = Provenance.DECODED
    coefficient_basis: str = "quantized_dct_before_dqt"

    schema: ClassVar[str] = COEFFICIENT_MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        required_features = _validate_schema_fields(self.schema_version, self.required_features)
        if not isinstance(self.owner, ArtifactOwner):
            raise DomainValidationError("coefficient manifest owner must be an ArtifactOwner")
        _validate_ids(self.source_span_ids, _SPAN_ID_PATTERN, "manifest source span")
        expected_span_ids = tuple(f"span-{index:06d}" for index in range(len(self.source_span_ids)))
        if self.source_span_ids != expected_span_ids:
            raise DomainValidationError("manifest source span IDs must be normalized ordinals")
        _validate_run_relative_posix_path(self.npz_path)
        if not self.npz_path.endswith(".npz"):
            raise DomainValidationError("coefficient artifact path must end with .npz")
        _validate_sha256(self.npz_sha256, "NPZ SHA-256")
        _require_int(self.npz_size, "NPZ size", minimum=1)
        arrays = tuple(sorted(self.arrays, key=lambda item: item.name))
        if len({item.name for item in arrays}) != len(arrays):
            raise DomainValidationError("manifest array names must be unique")
        components = _component_mapping(self.components, "manifest components")
        if any(not isinstance(value, ComponentLayout) for value in components.values()):
            raise DomainValidationError("manifest components must contain ComponentLayout values")
        provenance = _enum_value(Provenance, self.provenance, "coefficient provenance")
        if provenance is not Provenance.DECODED:
            raise DomainValidationError("canonical coefficient artifacts must have decoded provenance")
        if self.coefficient_basis != "quantized_dct_before_dqt":
            raise DomainValidationError("unsupported coefficient basis")
        object.__setattr__(self, "arrays", arrays)
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "required_features", required_features)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arrays": [item.to_dict() for item in self.arrays],
            "coefficient_basis": self.coefficient_basis,
            "components": {
                component.value: self.components[component].to_dict() for component in COMPONENTS
            },
            "npz": {
                "path": self.npz_path,
                "sha256": self.npz_sha256,
                "size": self.npz_size,
            },
            "owner": self.owner.to_dict(),
            "provenance": self.provenance.value,
            "required_features": list(self.required_features),
            "schema": self.schema,
            "schema_version": self.schema_version,
            "source_span_ids": list(self.source_span_ids),
        }

    @classmethod
    def from_dict(cls, value: Any) -> CoefficientManifest:
        record = _mapping(value, "coefficient manifest")
        _require_keys(
            record,
            {
                "schema",
                "schema_version",
                "required_features",
                "owner",
                "source_span_ids",
                "npz",
                "arrays",
                "components",
                "provenance",
                "coefficient_basis",
            },
        )
        if record["schema"] != cls.schema:
            raise DomainValidationError(f"expected schema {cls.schema!r}")
        npz = _mapping(record["npz"], "manifest npz")
        _require_keys(npz, {"path", "sha256", "size"})
        components = _mapping(record["components"], "manifest components")
        return cls(
            owner=ArtifactOwner.from_dict(record["owner"]),
            source_span_ids=tuple(_string_sequence(record["source_span_ids"], "source_span_ids")),
            npz_path=npz["path"],
            npz_sha256=npz["sha256"],
            npz_size=npz["size"],
            arrays=tuple(
                ArrayDescriptor.from_dict(item)
                for item in _sequence(record["arrays"], "manifest arrays")
            ),
            components={key: ComponentLayout.from_dict(item) for key, item in components.items()},
            schema_version=record["schema_version"],
            required_features=tuple(
                _string_sequence(record["required_features"], "required_features")
            ),
            provenance=record["provenance"],
            coefficient_basis=record["coefficient_basis"],
        )


@dataclass(frozen=True, slots=True)
class ObjectRecord:
    engine_version: str
    policy_version: str
    object_id: str
    media_type: MediaType
    disk_offset: int
    source_spans: tuple[SourceSpan, ...]
    hypotheses: tuple[Hypothesis, ...] = ()
    parent_object_id: str | None = None
    coefficient_manifest: CoefficientManifest | None = None
    schema_version: str = FORENSIC_SCHEMA_VERSION
    required_features: tuple[str, ...] = ()
    provenance: Provenance = Provenance.OBSERVED

    schema: ClassVar[str] = OBJECT_SCHEMA

    def __post_init__(self) -> None:
        required_features = _validate_schema_fields(self.schema_version, self.required_features)
        _validate_nonempty_string(self.engine_version, "engine_version")
        _validate_nonempty_string(self.policy_version, "policy_version")
        media = _enum_value(MediaType, self.media_type, "media_type")
        if self.object_id != object_id_for(media, self.disk_offset):
            raise DomainValidationError("object_id does not match media type and disk offset")
        spans = _canonical_source_spans(self.source_spans)
        if not spans:
            raise DomainValidationError("object record must contain at least one source span")
        if spans[0].disk_bytes.start != self.disk_offset:
            raise DomainValidationError("first object source span must start at the object disk offset")
        if spans[0].object_raw_bytes.start != 0:
            raise DomainValidationError("first object source span must start at object raw byte zero")
        hypotheses = _canonical_entities(self.hypotheses, "hypothesis_id", "hypothesis")
        _validate_hypothesis_refs(hypotheses, spans)
        if self.parent_object_id is not None:
            _validate_object_id(self.parent_object_id)
            if self.parent_object_id == self.object_id:
                raise DomainValidationError("object cannot be its own parent")
        if self.coefficient_manifest is not None:
            owner = self.coefficient_manifest.owner
            if owner.kind is not OwnerKind.OBJECT or owner.object_id != self.object_id:
                raise DomainValidationError("object coefficient manifest owner mismatch")
            _validate_manifest_spans(self.coefficient_manifest, spans)
        provenance = _enum_value(Provenance, self.provenance, "object provenance")
        if provenance is not Provenance.OBSERVED:
            raise DomainValidationError("objects must have observed provenance")
        object.__setattr__(self, "media_type", media)
        object.__setattr__(self, "source_spans", spans)
        object.__setattr__(self, "hypotheses", hypotheses)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "required_features", required_features)

    def to_dict(self) -> dict[str, Any]:
        return {
            "coefficient_manifest": (
                None if self.coefficient_manifest is None else self.coefficient_manifest.to_dict()
            ),
            "disk_offset": self.disk_offset,
            "engine_version": self.engine_version,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "media_type": self.media_type.value,
            "object_id": self.object_id,
            "parent_object_id": self.parent_object_id,
            "policy_version": self.policy_version,
            "provenance": self.provenance.value,
            "required_features": list(self.required_features),
            "schema": self.schema,
            "schema_version": self.schema_version,
            "source_spans": [item.to_dict() for item in self.source_spans],
        }

    @classmethod
    def from_dict(cls, value: Any) -> ObjectRecord:
        record = _mapping(value, "object record")
        _require_keys(
            record,
            {
                "schema",
                "schema_version",
                "required_features",
                "engine_version",
                "policy_version",
                "object_id",
                "media_type",
                "disk_offset",
                "provenance",
                "source_spans",
                "hypotheses",
                "parent_object_id",
                "coefficient_manifest",
            },
        )
        if record["schema"] != cls.schema:
            raise DomainValidationError(f"expected schema {cls.schema!r}")
        manifest = record["coefficient_manifest"]
        return cls(
            engine_version=record["engine_version"],
            policy_version=record["policy_version"],
            object_id=record["object_id"],
            media_type=record["media_type"],
            disk_offset=record["disk_offset"],
            source_spans=tuple(
                SourceSpan.from_dict(item)
                for item in _sequence(record["source_spans"], "source_spans")
            ),
            hypotheses=tuple(
                Hypothesis.from_dict(item)
                for item in _sequence(record["hypotheses"], "hypotheses")
            ),
            parent_object_id=record["parent_object_id"],
            coefficient_manifest=(
                None if manifest is None else CoefficientManifest.from_dict(manifest)
            ),
            schema_version=record["schema_version"],
            required_features=tuple(
                _string_sequence(record["required_features"], "required_features")
            ),
            provenance=record["provenance"],
        )


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    engine_version: str
    policy_version: str
    object_id: str
    candidate_ordinal: int
    candidate_id: str
    candidate_fingerprint: str
    hypotheses: tuple[Hypothesis, ...]
    source_spans: tuple[SourceSpan, ...]
    decode_segments: tuple[DecodeSegment, ...]
    virtual_edits: tuple[VirtualEdit, ...]
    placements: tuple[Placement, ...]
    coefficient_manifest: CoefficientManifest | None
    schema_version: str = FORENSIC_SCHEMA_VERSION
    required_features: tuple[str, ...] = ()

    schema: ClassVar[str] = CANDIDATE_SCHEMA

    def __post_init__(self) -> None:
        required_features = _validate_schema_fields(self.schema_version, self.required_features)
        _validate_nonempty_string(self.engine_version, "engine_version")
        _validate_nonempty_string(self.policy_version, "policy_version")
        _validate_object_id(self.object_id)
        if self.candidate_id != candidate_id_for_ordinal(self.candidate_ordinal):
            raise DomainValidationError("candidate_id does not match candidate_ordinal")
        spans = _canonical_source_spans(self.source_spans)
        if spans and spans[0].disk_bytes.start != _object_disk_offset(self.object_id):
            raise DomainValidationError(
                "first candidate source span must start at the object disk offset"
            )
        hypotheses = _canonical_entities(self.hypotheses, "hypothesis_id", "hypothesis")
        segments = _canonical_entities(self.decode_segments, "segment_id", "decode segment")
        edits = _canonical_entities(self.virtual_edits, "edit_id", "virtual edit")
        placements = _canonical_entities(self.placements, "placement_id", "placement")
        _validate_hypothesis_refs(hypotheses, spans)
        span_ids = {item.span_id for item in spans}
        edit_ids = {item.edit_id for item in edits}
        for segment in segments:
            if not set(segment.source_span_ids) <= span_ids:
                raise DomainValidationError("decode segment references an unknown source span")
            if not set(segment.virtual_edit_ids) <= edit_ids:
                raise DomainValidationError("decode segment references an unknown virtual edit")
        expected_fingerprint = candidate_fingerprint_for(
            self.object_id,
            hypotheses=hypotheses,
            source_spans=spans,
            decode_segments=segments,
            virtual_edits=edits,
            placements=placements,
        )
        if self.candidate_fingerprint != expected_fingerprint:
            raise DomainValidationError("candidate fingerprint does not match canonical candidate input")
        if self.coefficient_manifest is not None:
            owner = self.coefficient_manifest.owner
            if (
                owner.kind is not OwnerKind.CANDIDATE
                or owner.object_id != self.object_id
                or owner.candidate_id != self.candidate_id
                or owner.candidate_fingerprint != self.candidate_fingerprint
            ):
                raise DomainValidationError("candidate coefficient manifest owner mismatch")
            _validate_manifest_spans(self.coefficient_manifest, spans)
            _validate_placements_against_manifest(placements, self.coefficient_manifest)
        elif placements:
            raise DomainValidationError("placements require a coefficient manifest")
        object.__setattr__(self, "hypotheses", hypotheses)
        object.__setattr__(self, "source_spans", spans)
        object.__setattr__(self, "decode_segments", segments)
        object.__setattr__(self, "virtual_edits", edits)
        object.__setattr__(self, "placements", placements)
        object.__setattr__(self, "required_features", required_features)

    @classmethod
    def build(
        cls,
        *,
        engine_version: str,
        policy_version: str,
        object_id: str,
        candidate_ordinal: int,
        hypotheses: Sequence[Hypothesis],
        source_spans: Sequence[SourceSpan],
        decode_segments: Sequence[DecodeSegment],
        virtual_edits: Sequence[VirtualEdit],
        placements: Sequence[Placement],
        coefficient_manifest: CoefficientManifest | None = None,
        schema_version: str = FORENSIC_SCHEMA_VERSION,
        required_features: Sequence[str] = (),
    ) -> CandidateRecord:
        normalized_hypotheses = _canonical_entities(hypotheses, "hypothesis_id", "hypothesis")
        normalized_spans = _canonical_source_spans(source_spans)
        normalized_segments = _canonical_entities(decode_segments, "segment_id", "decode segment")
        normalized_edits = _canonical_entities(virtual_edits, "edit_id", "virtual edit")
        normalized_placements = _canonical_entities(placements, "placement_id", "placement")
        return cls(
            engine_version=engine_version,
            policy_version=policy_version,
            object_id=object_id,
            candidate_ordinal=candidate_ordinal,
            candidate_id=candidate_id_for_ordinal(candidate_ordinal),
            candidate_fingerprint=candidate_fingerprint_for(
                object_id,
                hypotheses=normalized_hypotheses,
                source_spans=normalized_spans,
                decode_segments=normalized_segments,
                virtual_edits=normalized_edits,
                placements=normalized_placements,
            ),
            hypotheses=normalized_hypotheses,
            source_spans=normalized_spans,
            decode_segments=normalized_segments,
            virtual_edits=normalized_edits,
            placements=normalized_placements,
            coefficient_manifest=coefficient_manifest,
            schema_version=schema_version,
            required_features=tuple(required_features),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_fingerprint": self.candidate_fingerprint,
            "candidate_id": self.candidate_id,
            "candidate_ordinal": self.candidate_ordinal,
            "coefficient_manifest": (
                None if self.coefficient_manifest is None else self.coefficient_manifest.to_dict()
            ),
            "decode_segments": [item.to_dict() for item in self.decode_segments],
            "engine_version": self.engine_version,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "object_id": self.object_id,
            "placements": [item.to_dict() for item in self.placements],
            "policy_version": self.policy_version,
            "required_features": list(self.required_features),
            "schema": self.schema,
            "schema_version": self.schema_version,
            "source_spans": [item.to_dict() for item in self.source_spans],
            "virtual_edits": [item.to_dict() for item in self.virtual_edits],
        }

    @classmethod
    def from_dict(cls, value: Any) -> CandidateRecord:
        record = _mapping(value, "candidate record")
        _require_keys(
            record,
            {
                "schema",
                "schema_version",
                "required_features",
                "engine_version",
                "policy_version",
                "object_id",
                "candidate_ordinal",
                "candidate_id",
                "candidate_fingerprint",
                "hypotheses",
                "source_spans",
                "decode_segments",
                "virtual_edits",
                "placements",
                "coefficient_manifest",
            },
        )
        if record["schema"] != cls.schema:
            raise DomainValidationError(f"expected schema {cls.schema!r}")
        manifest = record["coefficient_manifest"]
        return cls(
            engine_version=record["engine_version"],
            policy_version=record["policy_version"],
            object_id=record["object_id"],
            candidate_ordinal=record["candidate_ordinal"],
            candidate_id=record["candidate_id"],
            candidate_fingerprint=record["candidate_fingerprint"],
            hypotheses=tuple(
                Hypothesis.from_dict(item)
                for item in _sequence(record["hypotheses"], "hypotheses")
            ),
            source_spans=tuple(
                SourceSpan.from_dict(item)
                for item in _sequence(record["source_spans"], "source_spans")
            ),
            decode_segments=tuple(
                DecodeSegment.from_dict(item)
                for item in _sequence(record["decode_segments"], "decode_segments")
            ),
            virtual_edits=tuple(
                VirtualEdit.from_dict(item)
                for item in _sequence(record["virtual_edits"], "virtual_edits")
            ),
            placements=tuple(
                Placement.from_dict(item)
                for item in _sequence(record["placements"], "placements")
            ),
            coefficient_manifest=(
                None if manifest is None else CoefficientManifest.from_dict(manifest)
            ),
            schema_version=record["schema_version"],
            required_features=tuple(
                _string_sequence(record["required_features"], "required_features")
            ),
        )


@dataclass(frozen=True, slots=True)
class ResultRecord:
    engine_version: str
    policy_version: str
    object_id: str
    execution_status: ExecutionStatus
    support_status: SupportStatus
    decode_extent: DecodeExtent
    selection_status: SelectionStatus
    header_basis: HeaderBasis
    artifact_status: ArtifactStatus
    selected_candidate_id: str | None
    selected_candidate_fingerprint: str | None
    candidate_count: int
    interventions: tuple[InterventionKind | str, ...] = ()
    schema_version: str = FORENSIC_SCHEMA_VERSION
    required_features: tuple[str, ...] = ()

    schema: ClassVar[str] = RESULT_SCHEMA

    def __post_init__(self) -> None:
        required_features = _validate_schema_fields(self.schema_version, self.required_features)
        _validate_nonempty_string(self.engine_version, "engine_version")
        _validate_nonempty_string(self.policy_version, "policy_version")
        _validate_object_id(self.object_id)
        execution = _enum_value(ExecutionStatus, self.execution_status, "execution_status")
        support = _enum_value(SupportStatus, self.support_status, "support_status")
        decode = _enum_value(DecodeExtent, self.decode_extent, "decode_extent")
        selection = _enum_value(SelectionStatus, self.selection_status, "selection_status")
        header = _enum_value(HeaderBasis, self.header_basis, "header_basis")
        artifact = _enum_value(ArtifactStatus, self.artifact_status, "artifact_status")
        _require_int(self.candidate_count, "candidate_count", minimum=0, maximum=1000)
        interventions = tuple(
            _enum_value(InterventionKind, item, "intervention")
            for item in self.interventions
        )
        if len(set(interventions)) != len(interventions):
            raise DomainValidationError("interventions must be unique")
        interventions = tuple(sorted(interventions, key=lambda item: item.value))
        _validate_result_state(
            execution,
            support,
            decode,
            selection,
            header,
            artifact,
            self.selected_candidate_id,
            self.selected_candidate_fingerprint,
            self.candidate_count,
        )
        object.__setattr__(self, "execution_status", execution)
        object.__setattr__(self, "support_status", support)
        object.__setattr__(self, "decode_extent", decode)
        object.__setattr__(self, "selection_status", selection)
        object.__setattr__(self, "header_basis", header)
        object.__setattr__(self, "artifact_status", artifact)
        object.__setattr__(self, "interventions", interventions)
        object.__setattr__(self, "required_features", required_features)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_status": self.artifact_status.value,
            "candidate_count": self.candidate_count,
            "decode_extent": self.decode_extent.value,
            "engine_version": self.engine_version,
            "execution_status": self.execution_status.value,
            "header_basis": self.header_basis.value,
            "interventions": [item.value for item in self.interventions],
            "object_id": self.object_id,
            "policy_version": self.policy_version,
            "required_features": list(self.required_features),
            "schema": self.schema,
            "schema_version": self.schema_version,
            "selected_candidate_fingerprint": self.selected_candidate_fingerprint,
            "selected_candidate_id": self.selected_candidate_id,
            "selection_status": self.selection_status.value,
            "support_status": self.support_status.value,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ResultRecord:
        record = _mapping(value, "result record")
        _require_keys(
            record,
            {
                "schema",
                "schema_version",
                "required_features",
                "engine_version",
                "policy_version",
                "object_id",
                "execution_status",
                "support_status",
                "decode_extent",
                "selection_status",
                "header_basis",
                "artifact_status",
                "selected_candidate_id",
                "selected_candidate_fingerprint",
                "candidate_count",
                "interventions",
            },
        )
        if record["schema"] != cls.schema:
            raise DomainValidationError(f"expected schema {cls.schema!r}")
        return cls(
            engine_version=record["engine_version"],
            policy_version=record["policy_version"],
            object_id=record["object_id"],
            execution_status=record["execution_status"],
            support_status=record["support_status"],
            decode_extent=record["decode_extent"],
            selection_status=record["selection_status"],
            header_basis=record["header_basis"],
            artifact_status=record["artifact_status"],
            selected_candidate_id=record["selected_candidate_id"],
            selected_candidate_fingerprint=record["selected_candidate_fingerprint"],
            candidate_count=record["candidate_count"],
            interventions=tuple(
                _string_sequence(record["interventions"], "interventions")
            ),
            schema_version=record["schema_version"],
            required_features=tuple(
                _string_sequence(record["required_features"], "required_features")
            ),
        )


def candidate_fingerprint_for(
    object_id: str,
    *,
    hypotheses: Sequence[Hypothesis],
    source_spans: Sequence[SourceSpan],
    decode_segments: Sequence[DecodeSegment],
    virtual_edits: Sequence[VirtualEdit],
    placements: Sequence[Placement],
) -> str:
    _validate_object_id(object_id)
    normalized = {
        "decode_segments": [
            item.to_dict()
            for item in _canonical_entities(decode_segments, "segment_id", "decode segment")
        ],
        "hypotheses": [
            item.to_dict()
            for item in _canonical_entities(hypotheses, "hypothesis_id", "hypothesis")
        ],
        "object_id": object_id,
        "placements": [
            item.to_dict()
            for item in _canonical_entities(placements, "placement_id", "placement")
        ],
        "schema": CANDIDATE_FINGERPRINT_SCHEMA,
        "schema_version": CANDIDATE_FINGERPRINT_VERSION,
        "source_spans": [item.to_dict() for item in _canonical_source_spans(source_spans)],
        "virtual_edits": [
            item.to_dict()
            for item in _canonical_entities(virtual_edits, "edit_id", "virtual edit")
        ],
    }
    return hashlib.sha256(_canonical_json_bytes(normalized)).hexdigest()


def _validate_result_state(
    execution: ExecutionStatus,
    support: SupportStatus,
    decode: DecodeExtent,
    selection: SelectionStatus,
    header: HeaderBasis,
    artifact: ArtifactStatus,
    selected_id: str | None,
    selected_fingerprint: str | None,
    candidate_count: int,
) -> None:
    selected = selection in {
        SelectionStatus.SOURCE_CANDIDATE_SELECTED,
        SelectionStatus.RECONSTRUCTION_CANDIDATE_SELECTED,
    }
    if selected:
        _validate_candidate_id(selected_id)
        _validate_sha256(selected_fingerprint, "selected candidate fingerprint")
        if candidate_count < 1:
            raise DomainValidationError("selected result must have at least one candidate")
    elif selected_id is not None or selected_fingerprint is not None:
        raise DomainValidationError("unselected result cannot reference a candidate")

    if execution is not ExecutionStatus.COMPLETED:
        if decode is DecodeExtent.COMPLETE:
            raise DomainValidationError("interrupted/error execution cannot have complete decode")
        if selection is not SelectionStatus.NOT_APPLICABLE:
            raise DomainValidationError("interrupted/error execution must use not_applicable selection")
        if artifact is ArtifactStatus.COMPLETE:
            raise DomainValidationError("interrupted/error execution cannot have complete artifact")
    if support is SupportStatus.UNSUPPORTED:
        if execution is not ExecutionStatus.COMPLETED:
            raise DomainValidationError("unsupported is a completed support decision")
        if (
            decode is not DecodeExtent.NOT_ATTEMPTED
            or selection is not SelectionStatus.NOT_APPLICABLE
            or header is not HeaderBasis.NONE
            or artifact is not ArtifactStatus.UNAVAILABLE
        ):
            raise DomainValidationError("unsupported result state combination is inconsistent")
    elif execution is ExecutionStatus.COMPLETED and decode is DecodeExtent.NOT_ATTEMPTED:
        raise DomainValidationError("supported completed result cannot use not_attempted decode")

    if decode in {DecodeExtent.COMPLETE, DecodeExtent.PARTIAL} and header is HeaderBasis.NONE:
        raise DomainValidationError("complete/partial decode requires a header basis")
    if decode is DecodeExtent.NOT_ATTEMPTED and header is not HeaderBasis.NONE:
        raise DomainValidationError("not_attempted decode requires header_basis none")

    if selected:
        if execution is not ExecutionStatus.COMPLETED:
            raise DomainValidationError("candidate selection requires completed execution")
        if decode not in {DecodeExtent.COMPLETE, DecodeExtent.PARTIAL}:
            raise DomainValidationError("candidate selection requires complete or partial decode")
        if artifact not in {ArtifactStatus.COMPLETE, ArtifactStatus.PARTIAL}:
            raise DomainValidationError("candidate selection requires an available artifact")
        if (
            selection is SelectionStatus.SOURCE_CANDIDATE_SELECTED
            and header is not HeaderBasis.SOURCE
        ):
            raise DomainValidationError("source candidate selection requires source header basis")
    if selection is SelectionStatus.NO_SUPPORTED_CANDIDATE:
        if execution is not ExecutionStatus.COMPLETED:
            raise DomainValidationError("no_supported_candidate requires completed execution")
        if decode not in {DecodeExtent.NONE, DecodeExtent.PARTIAL}:
            raise DomainValidationError("no_supported_candidate requires none or partial decode")
        if artifact not in {ArtifactStatus.PARTIAL, ArtifactStatus.UNAVAILABLE}:
            raise DomainValidationError("no_supported_candidate cannot have complete artifact")
    if selection is SelectionStatus.NOT_APPLICABLE:
        if execution is ExecutionStatus.COMPLETED and support is not SupportStatus.UNSUPPORTED:
            raise DomainValidationError("completed supported result cannot use not_applicable selection")
    if artifact is ArtifactStatus.COMPLETE and not selected:
        raise DomainValidationError("complete artifact requires a selected candidate")


def _validate_placements_against_manifest(
    placements: Sequence[Placement], manifest: CoefficientManifest
) -> None:
    seen_source: set[tuple[Component, int]] = set()
    for placement in placements:
        layout = manifest.components[placement.component]
        if placement.source_block_index >= layout.source_block_count:
            raise DomainValidationError("placement source block is outside the component layout")
        if (
            placement.raster_block_row >= layout.raster_block_rows
            or placement.raster_block_column >= layout.raster_block_columns
        ):
            raise DomainValidationError("placement raster position is outside the component layout")
        source_key = (placement.component, placement.source_block_index)
        if source_key in seen_source:
            raise DomainValidationError("a source block cannot be placed more than once")
        seen_source.add(source_key)


def _validate_manifest_spans(
    manifest: CoefficientManifest, spans: Sequence[SourceSpan]
) -> None:
    if manifest.source_span_ids != tuple(item.span_id for item in spans):
        raise DomainValidationError("coefficient manifest source span table mismatch")


def _validate_hypothesis_refs(
    hypotheses: Sequence[Hypothesis], spans: Sequence[SourceSpan]
) -> None:
    span_ids = {item.span_id for item in spans}
    for hypothesis in hypotheses:
        if not set(hypothesis.evidence_span_ids) <= span_ids:
            raise DomainValidationError("hypothesis references an unknown evidence span")


def _canonical_source_spans(spans: Sequence[SourceSpan]) -> tuple[SourceSpan, ...]:
    normalized = tuple(sorted(spans, key=lambda item: item.span_id))
    expected = tuple(f"span-{index:06d}" for index in range(len(normalized)))
    if tuple(item.span_id for item in normalized) != expected:
        raise DomainValidationError("source span IDs must be normalized ordinals")
    _validate_nonoverlapping_ranges(
        (span.disk_bytes for span in normalized),
        "disk byte source spans must not overlap",
    )
    _validate_nonoverlapping_ranges(
        (span.object_raw_bytes for span in normalized),
        "object raw source spans must not overlap",
    )
    _validate_nonoverlapping_ranges(
        (span.raw_entropy_bits for span in normalized if span.raw_entropy_bits is not None),
        "raw entropy source spans must not overlap",
    )
    _validate_nonoverlapping_ranges(
        (span.destuffed_bits for span in normalized if span.destuffed_bits is not None),
        "destuffed source spans must not overlap",
    )
    return normalized


def _validate_nonoverlapping_ranges(
    ranges: Iterable[CoordinateRange],
    message: str,
) -> None:
    previous_end = -1
    for coordinate_range in sorted(ranges, key=lambda item: (item.start, item.end)):
        if coordinate_range.start < previous_end:
            raise DomainValidationError(message)
        previous_end = coordinate_range.end


def _canonical_entities(
    values: Sequence[Any], attribute: str, label: str
) -> tuple[Any, ...]:
    normalized = tuple(sorted(values, key=lambda item: getattr(item, attribute)))
    identifiers = [getattr(item, attribute) for item in normalized]
    if len(set(identifiers)) != len(identifiers):
        raise DomainValidationError(f"{label} IDs must be unique")
    return normalized


def _component_mapping(
    value: Mapping[Any, Any], label: str
) -> Mapping[Component, Any]:
    record = _mapping(value, label)
    normalized: dict[Component, Any] = {}
    for key, item in record.items():
        component = _enum_value(Component, key, f"{label} key")
        if component in normalized:
            raise DomainValidationError(f"{label} component keys must be unique")
        normalized[component] = item
    if set(normalized) != set(COMPONENTS):
        raise DomainValidationError(f"{label} must contain exactly y, cb, and cr")
    return MappingProxyType(normalized)


def _validate_schema_fields(
    version: str, required_features: Sequence[str]
) -> tuple[str, ...]:
    if not isinstance(version, str) or _SCHEMA_VERSION_PATTERN.fullmatch(version) is None:
        raise DomainValidationError("forensic schema version must have supported major 1")
    if not isinstance(required_features, tuple):
        required_features = tuple(required_features)
    if not all(isinstance(item, str) for item in required_features):
        raise DomainValidationError("required_features must contain strings")
    if len(set(required_features)) != len(required_features):
        raise DomainValidationError("required_features must be unique")
    if required_features:
        raise DomainValidationError("unknown required_features: " + ", ".join(sorted(required_features)))
    return required_features


def _validate_run_relative_posix_path(value: Any) -> None:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise DomainValidationError("artifact path must be a non-empty POSIX relative path")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise DomainValidationError("artifact path must not contain control characters")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or re.match(r"^[A-Za-z]:", value) is not None
    ):
        raise DomainValidationError("artifact path must stay inside its run")
    if path.as_posix() != value:
        raise DomainValidationError("artifact path must be canonical POSIX syntax")


def _validate_object_id(value: Any) -> None:
    if not isinstance(value, str) or _OBJECT_ID_PATTERN.fullmatch(value) is None:
        raise DomainValidationError("invalid object ID")


def _object_disk_offset(object_id: str) -> int:
    _validate_object_id(object_id)
    return int(object_id.rsplit("-", 1)[1], 16)


def _validate_candidate_id(value: Any) -> None:
    if not isinstance(value, str) or _CANDIDATE_ID_PATTERN.fullmatch(value) is None:
        raise DomainValidationError("invalid candidate ID")


def _validate_sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise DomainValidationError(f"invalid {label}")


def _validate_nonempty_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise DomainValidationError(f"{label} must be a non-empty string")


def _validate_ids(values: Sequence[str], pattern: re.Pattern[str], label: str) -> None:
    if not all(isinstance(item, str) and pattern.fullmatch(item) is not None for item in values):
        raise DomainValidationError(f"invalid {label} ID")
    if len(set(values)) != len(values):
        raise DomainValidationError(f"{label} IDs must be unique")


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DomainValidationError("non-finite JSON values are forbidden")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DomainValidationError("JSON object keys must be strings")
            _validate_json_value(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_value(item)
        return
    raise DomainValidationError(f"value is not strict JSON: {type(value).__name__}")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    _validate_json_value(value)
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _enum_value(enum_type: type[_StringEnum], value: Any, label: str) -> Any:
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"invalid {label}: {value!r}") from exc


def _require_int(
    value: Any,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise DomainValidationError(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise DomainValidationError(f"{label} must be at most {maximum}")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DomainValidationError(f"{label} must be an array")
    return value


def _string_sequence(value: Any, label: str) -> tuple[str, ...]:
    values = tuple(_sequence(value, label))
    if not all(isinstance(item, str) for item in values):
        raise DomainValidationError(f"{label} must contain strings")
    return values


def _integer_sequence(
    value: Any, label: str, *, minimum: int | None = None
) -> tuple[int, ...]:
    values = tuple(_sequence(value, label))
    for item in values:
        _require_int(item, label, minimum=minimum)
    return values


def _require_keys(record: Mapping[str, Any], keys: set[str]) -> None:
    missing = sorted(keys - record.keys())
    if missing:
        raise DomainValidationError("missing required fields: " + ", ".join(missing))


def _optional_range(value: Any) -> CoordinateRange | None:
    return None if value is None else CoordinateRange.from_dict(value)
