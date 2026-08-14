from __future__ import annotations

import copy
import io
import json
import os
import zipfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from media_recovery.artifacts import (
    ArtifactFormatError,
    complete_run,
    create_run,
    read_candidates_jsonl,
    read_coefficient_npz,
    read_objects_jsonl,
    read_results_jsonl,
    read_run,
    register_case,
    start_run,
    write_candidates_jsonl,
    write_coefficient_npz,
    write_objects_jsonl,
    write_results_jsonl,
    write_run_jsonl,
)
from media_recovery.artifacts import forensics
from media_recovery.artifacts.io import sha256_file
from media_recovery.domain import (
    ArtifactOwner,
    ArtifactStatus,
    CandidateRecord,
    Component,
    CoordinateRange,
    CoordinateSpace,
    DecodeExtent,
    DecodeSegment,
    DomainValidationError,
    ExecutionStatus,
    HeaderBasis,
    Hypothesis,
    HypothesisAssertion,
    HypothesisKind,
    IndexRange,
    InterventionKind,
    MediaType,
    ObjectRecord,
    OwnerKind,
    Placement,
    Provenance,
    ResultRecord,
    SelectionStatus,
    SourceSpan,
    SupportStatus,
    VirtualEdit,
    VirtualEditKind,
    candidate_fingerprint_for,
    candidate_id_for_ordinal,
    object_id_for,
)


VERSIONS = {
    "tool_version": "test-tool",
    "engine_version": "test-engine",
    "policy_version": "test-policy",
    "artifact_schema_version": "test-artifacts-v1",
    "environment": {"platform": "test", "python": "3.12"},
}


def _case(tmp_path: Path) -> Path:
    source = tmp_path / "source.bin"
    source.write_bytes(bytes(0x5000))
    return register_case(source, work_root=tmp_path / "work")


def _run(case: Path, *, stage: str, parents: tuple[str, ...] = ()) -> Path:
    return create_run(
        case,
        stage=stage,
        parent_run_ids=parents,
        options={"profile": "test"},
        random_seed=7,
        task_id="T-0004",
        **VERSIONS,
    )


def _spans(*, disk_offset: int = 0x1000) -> tuple[SourceSpan, SourceSpan]:
    return (
        SourceSpan(
            "span-000000",
            CoordinateRange(CoordinateSpace.DISK_BYTE, disk_offset, disk_offset + 4),
            CoordinateRange(CoordinateSpace.OBJECT_RAW_BYTE, 0, 4),
            CoordinateRange(CoordinateSpace.RAW_ENTROPY_BIT, 0, 32),
            CoordinateRange(CoordinateSpace.DESTUFFED_BIT, 0, 24),
        ),
        SourceSpan(
            "span-000001",
            CoordinateRange(CoordinateSpace.DISK_BYTE, disk_offset + 0x1000, disk_offset + 0x1004),
            CoordinateRange(CoordinateSpace.OBJECT_RAW_BYTE, 4, 8),
            CoordinateRange(CoordinateSpace.RAW_ENTROPY_BIT, 32, 64),
            CoordinateRange(CoordinateSpace.DESTUFFED_BIT, 24, 56),
        ),
    )


def _hypotheses() -> tuple[Hypothesis, Hypothesis]:
    return (
        Hypothesis(
            "hyp-0000",
            HypothesisKind.HEADER,
            (
                HypothesisAssertion("width", 16, Provenance.OBSERVED),
                HypothesisAssertion("dqt", "standard-luma", Provenance.INFERRED),
            ),
            ("span-000000",),
        ),
        Hypothesis(
            "hyp-0001",
            HypothesisKind.BOUNDARY,
            (HypothesisAssertion("exclusive_end", 0x2004, Provenance.OBSERVED),),
            ("span-000001",),
        ),
    )


def _edit() -> VirtualEdit:
    return VirtualEdit(
        "edit-0000",
        VirtualEditKind.BYTE_INSERTION,
        CoordinateRange(CoordinateSpace.DESTUFFED_BIT, 8, 8),
        CoordinateRange(CoordinateSpace.VIRTUAL_WORK_BIT, 8, 16),
        {"inserted_bits": "00000000", "reason": "test assumption"},
    )


def _segment() -> DecodeSegment:
    return DecodeSegment(
        "seg-0000",
        ("span-000000", "span-000001"),
        ("edit-0000",),
        CoordinateRange(CoordinateSpace.VIRTUAL_WORK_BIT, 0, 56),
        IndexRange(0, 1),
        {
            Component.Y: IndexRange(0, 2),
            Component.CB: IndexRange(0, 1),
            Component.CR: IndexRange(0, 1),
        },
    )


def _placements() -> tuple[Placement, ...]:
    return (
        Placement("place-000000", Component.Y, 0, 0, 0),
        Placement("place-000001", Component.Y, 1, 0, 1),
        Placement("place-000002", Component.CB, 0, 0, 0),
    )


def _arrays(*, byte_order: str = "<") -> dict[str, np.ndarray]:
    counts = {"y": 2, "cb": 1, "cr": 1}
    raster = {"y": (1, 2), "cb": (1, 1), "cr": (1, 1)}
    arrays: dict[str, np.ndarray] = {}
    for component, count in counts.items():
        coefficient = np.zeros((count, 8, 8), dtype="<i4")
        validity = np.zeros((count, 8, 8), dtype="u1")
        if component == "y":
            validity[0, :, :] = 1
            validity[1, 0, 0] = 1
        elif component == "cb":
            validity[0, :, :] = 1
        coefficient[validity == 1] = np.arange(1, int(validity.sum()) + 1, dtype=np.int32)
        valid_counts = validity.reshape(count, 64).sum(axis=1)
        block = np.where(valid_counts == 0, 0, np.where(valid_counts == 64, 2, 1)).astype("u1")

        ref_ranges = np.full((count, 8, 8, 2), (-1, 0), dtype="<i8")
        refs: list[int] = []
        for coefficient_index, is_valid in enumerate(validity.reshape(-1)):
            if not is_valid:
                continue
            selected = [0, 1] if component == "y" and coefficient_index == 0 else [1]
            flat = ref_ranges.reshape(-1, 2)
            flat[coefficient_index] = (len(refs), len(selected))
            refs.extend(selected)

        owner = np.full(raster[component], -1, dtype="<i4")
        if component == "y":
            owner[0, 0] = 0
            owner[0, 1] = 1
        elif component == "cb":
            owner[0, 0] = 0

        arrays[f"coef_{component}"] = coefficient
        arrays[f"coefficient_validity_{component}"] = validity
        arrays[f"block_validity_{component}"] = block
        arrays[f"source_span_ref_range_{component}"] = ref_ranges
        arrays[f"source_span_refs_{component}"] = np.asarray(refs, dtype="<i4")
        arrays[f"placement_owner_{component}"] = owner

    if byte_order == ">":
        for name, array in list(arrays.items()):
            if array.dtype.itemsize > 1:
                arrays[name] = array.astype(array.dtype.newbyteorder(">"))
    return arrays


def _object_record(*, disk_offset: int = 0x1000) -> ObjectRecord:
    return ObjectRecord(
        engine_version="discovery-v1",
        policy_version="discovery-policy-v1",
        object_id=object_id_for(MediaType.JPEG, disk_offset),
        media_type=MediaType.JPEG,
        disk_offset=disk_offset,
        source_spans=_spans(disk_offset=disk_offset),
        hypotheses=_hypotheses(),
    )


def _candidate_semantics():
    object_id = object_id_for(MediaType.JPEG, 0x1000)
    hypotheses = _hypotheses()
    spans = _spans()
    edits = (_edit(),)
    segments = (_segment(),)
    placements = _placements()
    fingerprint = candidate_fingerprint_for(
        object_id,
        hypotheses=hypotheses,
        source_spans=spans,
        decode_segments=segments,
        virtual_edits=edits,
        placements=placements,
    )
    return object_id, fingerprint, hypotheses, spans, segments, edits, placements


def _discovery(case: Path) -> Path:
    run = _run(case, stage="discovery")
    start_run(run)
    write_objects_jsonl(run, [_object_record()])
    complete_run(run)
    return run


def _bundle(tmp_path: Path, *, complete: bool = False):
    case = _case(tmp_path)
    discovery = _discovery(case)
    run = _run(case, stage="reconstruction", parents=(discovery.name,))
    start_run(run)
    object_id, fingerprint, hypotheses, spans, segments, edits, placements = _candidate_semantics()
    owner = ArtifactOwner(OwnerKind.CANDIDATE, object_id, "cand-000", fingerprint)
    manifest = write_coefficient_npz(
        run,
        f"forensic/candidates/{object_id}/cand-000.npz",
        _arrays(),
        owner=owner,
        source_span_ids=tuple(item.span_id for item in spans),
    )
    candidate = CandidateRecord(
        engine_version="reconstruct-v1",
        policy_version="selection-v1",
        object_id=object_id,
        candidate_ordinal=0,
        candidate_id="cand-000",
        candidate_fingerprint=fingerprint,
        hypotheses=hypotheses,
        source_spans=spans,
        decode_segments=segments,
        virtual_edits=edits,
        placements=placements,
        coefficient_manifest=manifest,
    )
    write_candidates_jsonl(run, [candidate])
    result = ResultRecord(
        engine_version="reconstruct-v1",
        policy_version="selection-v1",
        object_id=object_id,
        execution_status=ExecutionStatus.COMPLETED,
        support_status=SupportStatus.SUPPORTED,
        decode_extent=DecodeExtent.PARTIAL,
        selection_status=SelectionStatus.RECONSTRUCTION_CANDIDATE_SELECTED,
        header_basis=HeaderBasis.HYPOTHESIS,
        artifact_status=ArtifactStatus.COMPLETE,
        selected_candidate_id="cand-000",
        selected_candidate_fingerprint=fingerprint,
        candidate_count=1,
        interventions=(VirtualEditKind.BYTE_INSERTION,),
    )
    write_results_jsonl(run, [result])
    if complete:
        complete_run(run)
    return run, candidate, result


def _schema_registry() -> tuple[Registry, dict[str, dict]]:
    root = Path(__file__).parents[1] / "schemas"
    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in root.glob("*.schema.json")
    }
    registry = Registry()
    for schema in schemas.values():
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry, schemas


def test_object_and_candidate_ids_are_stable_and_bounded():
    assert object_id_for("jpeg", 0x42E21000) == "jpeg-0000000042e21000"
    assert object_id_for(MediaType.AVI, 2**64 - 1) == "avi-ffffffffffffffff"
    assert candidate_id_for_ordinal(0) == "cand-000"
    assert candidate_id_for_ordinal(999) == "cand-999"
    with pytest.raises(DomainValidationError):
        object_id_for("jpeg", 2**64)
    with pytest.raises(DomainValidationError):
        candidate_id_for_ordinal(1000)


def test_domain_round_trip_and_fingerprint_ignore_input_collection_order():
    object_id, fingerprint, hypotheses, spans, segments, edits, placements = _candidate_semantics()
    assert fingerprint == candidate_fingerprint_for(
        object_id,
        hypotheses=tuple(reversed(hypotheses)),
        source_spans=tuple(reversed(spans)),
        decode_segments=segments,
        virtual_edits=edits,
        placements=tuple(reversed(placements)),
    )

    without_placement = candidate_fingerprint_for(
        object_id,
        hypotheses=hypotheses,
        source_spans=spans,
        decode_segments=segments,
        virtual_edits=edits,
        placements=(),
    )
    assert without_placement == candidate_fingerprint_for(
        object_id,
        hypotheses=tuple(reversed(hypotheses)),
        source_spans=tuple(reversed(spans)),
        decode_segments=tuple(reversed(segments)),
        virtual_edits=tuple(reversed(edits)),
        placements=(),
    )
    candidate = CandidateRecord.build(
        engine_version="engine-v1",
        policy_version="policy-v1",
        object_id=object_id,
        candidate_ordinal=7,
        hypotheses=tuple(reversed(hypotheses)),
        source_spans=tuple(reversed(spans)),
        decode_segments=segments,
        virtual_edits=edits,
        placements=(),
    )
    assert CandidateRecord.from_dict(candidate.to_dict()) == candidate
    assert ObjectRecord.from_dict(_object_record().to_dict()) == _object_record()


def test_decode_segment_freezes_source_and_edit_reference_inputs():
    source_span_ids = ["span-000000", "span-000001"]
    virtual_edit_ids = ["edit-0000"]
    segment = replace(
        _segment(),
        source_span_ids=source_span_ids,
        virtual_edit_ids=virtual_edit_ids,
    )

    source_span_ids.append("span-000002")
    virtual_edit_ids.clear()

    assert segment.source_span_ids == ("span-000000", "span-000001")
    assert segment.virtual_edit_ids == ("edit-0000",)
    assert DecodeSegment.from_dict(segment.to_dict()) == segment


def test_source_spans_preserve_discontinuity_and_reject_mixed_provenance():
    record = _object_record()
    assert [item.disk_bytes.start for item in record.source_spans] == [0x1000, 0x2000]
    assert [item.object_raw_bytes.start for item in record.source_spans] == [0, 4]
    round_trip = ObjectRecord.from_dict(record.to_dict())
    assert len(round_trip.source_spans) == 2
    with pytest.raises(DomainValidationError, match="observed provenance"):
        replace(record.source_spans[0], provenance=Provenance.INFERRED)
    with pytest.raises(DomainValidationError, match="equal byte length"):
        replace(
            record.source_spans[0],
            object_raw_bytes=CoordinateRange(CoordinateSpace.OBJECT_RAW_BYTE, 0, 3),
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        (
            "disk_bytes",
            CoordinateRange(CoordinateSpace.DISK_BYTE, 0x1002, 0x1006),
            "disk byte source spans must not overlap",
        ),
        (
            "raw_entropy_bits",
            CoordinateRange(CoordinateSpace.RAW_ENTROPY_BIT, 16, 48),
            "raw entropy source spans must not overlap",
        ),
        (
            "destuffed_bits",
            CoordinateRange(CoordinateSpace.DESTUFFED_BIT, 16, 48),
            "destuffed source spans must not overlap",
        ),
    ],
)
def test_source_spans_reject_reused_observed_coordinates(field, replacement, message):
    record = _object_record()
    spans = list(record.source_spans)
    spans[1] = replace(spans[1], **{field: replacement})
    with pytest.raises(DomainValidationError, match=message):
        replace(record, source_spans=tuple(spans))


def test_candidate_object_id_must_match_its_first_source_anchor():
    _, _, hypotheses, spans, segments, edits, _ = _candidate_semantics()
    with pytest.raises(DomainValidationError, match="object disk offset"):
        CandidateRecord.build(
            engine_version="engine-v1",
            policy_version="policy-v1",
            object_id=object_id_for("jpeg", 0x3000),
            candidate_ordinal=0,
            hypotheses=hypotheses,
            source_spans=spans,
            decode_segments=segments,
            virtual_edits=edits,
            placements=(),
        )


def test_observed_disk_spans_must_fit_the_case_source_on_write_and_read(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    outside = _object_record(disk_offset=0x5000)

    with pytest.raises(ArtifactFormatError, match="outside the case source"):
        write_objects_jsonl(run, [outside])

    write_run_jsonl(
        run,
        "objects.jsonl",
        [outside.to_dict()],
        sort_key=lambda row: row["object_id"],
    )
    with pytest.raises(ArtifactFormatError, match="outside the case source"):
        read_objects_jsonl(run)

    discovery = _discovery(case)
    reconstruction = _run(case, stage="reconstruction", parents=(discovery.name,))
    start_run(reconstruction)
    spans = list(_spans())
    spans[1] = replace(
        spans[1],
        disk_bytes=CoordinateRange(CoordinateSpace.DISK_BYTE, 0x5000, 0x5004),
    )
    candidate = CandidateRecord.build(
        engine_version="engine-v1",
        policy_version="policy-v1",
        object_id=object_id_for("jpeg", 0x1000),
        candidate_ordinal=0,
        hypotheses=_hypotheses(),
        source_spans=tuple(spans),
        decode_segments=(_segment(),),
        virtual_edits=(_edit(),),
        placements=(),
    )
    with pytest.raises(ArtifactFormatError, match="outside the case source"):
        write_candidates_jsonl(reconstruction, [candidate])


def test_virtual_edit_records_pre_edit_and_work_coordinates():
    edit = _edit()
    assert edit.source_range.space is CoordinateSpace.DESTUFFED_BIT
    assert edit.source_range.start == edit.source_range.end
    assert edit.work_range.space is CoordinateSpace.VIRTUAL_WORK_BIT
    assert VirtualEdit.from_dict(edit.to_dict()) == edit
    with pytest.raises(DomainValidationError, match="pre-edit"):
        replace(edit, source_range=edit.work_range)


@pytest.mark.parametrize(
    "changes",
    [
        {"execution_status": "error", "decode_extent": "complete"},
        {"execution_status": "error", "selection_status": "reconstruction_candidate_selected"},
        {"selection_status": "source_candidate_selected", "header_basis": "hypothesis"},
        {"selected_candidate_id": None},
        {"decode_extent": "partial", "header_basis": "none"},
        {"selection_status": "no_supported_candidate", "artifact_status": "complete"},
        {"interventions": ["byte_insertion", "byte_insertion"]},
        {
            "support_status": "unsupported",
            "decode_extent": "partial",
            "selection_status": "not_applicable",
            "header_basis": "none",
            "artifact_status": "unavailable",
            "selected_candidate_id": None,
            "selected_candidate_fingerprint": None,
        },
    ],
)
def test_result_schema_and_reader_reject_the_same_invalid_state_combinations(changes):
    _, schemas = _schema_registry()
    registry, _ = _schema_registry()
    _, fingerprint, *_ = _candidate_semantics()
    valid = ResultRecord(
        engine_version="engine-v1",
        policy_version="policy-v1",
        object_id=object_id_for("jpeg", 0x1000),
        execution_status="completed",
        support_status="supported",
        decode_extent="partial",
        selection_status="reconstruction_candidate_selected",
        header_basis="hypothesis",
        artifact_status="complete",
        selected_candidate_id="cand-000",
        selected_candidate_fingerprint=fingerprint,
        candidate_count=1,
    ).to_dict()
    invalid = {**valid, **changes}
    schema = schemas["media-recovery.result-1.0.schema.json"]
    assert list(Draft202012Validator(schema, registry=registry).iter_errors(invalid))
    with pytest.raises(DomainValidationError):
        ResultRecord.from_dict(invalid)


def test_result_accepts_unsupported_and_partial_error_states():
    unsupported = ResultRecord(
        "engine-v1",
        "policy-v1",
        object_id_for("avi", 0),
        "completed",
        "unsupported",
        "not_attempted",
        "not_applicable",
        "none",
        "unavailable",
        None,
        None,
        0,
    )
    partial_error = ResultRecord(
        "engine-v1",
        "policy-v1",
        object_id_for("jpeg", 1),
        "error",
        "partially_supported",
        "partial",
        "not_applicable",
        "source_repaired",
        "partial",
        None,
        None,
        0,
    )
    assert ResultRecord.from_dict(unsupported.to_dict()) == unsupported
    assert ResultRecord.from_dict(partial_error.to_dict()) == partial_error
    assert ResultRecord.from_dict(
        replace(partial_error, interventions=(InterventionKind.MCU_PLACEMENT,)).to_dict()
    ).interventions == (InterventionKind.MCU_PLACEMENT,)


def test_result_schema_and_python_validator_accept_exactly_the_same_state_matrix():
    registry, schemas = _schema_registry()
    validator = Draft202012Validator(
        schemas["media-recovery.result-1.0.schema.json"], registry=registry
    )
    _, fingerprint, *_ = _candidate_semantics()
    for execution in ("completed", "interrupted", "error"):
        for support in ("supported", "partially_supported", "unsupported"):
            for decode in ("complete", "partial", "none", "not_attempted"):
                for selection in (
                    "source_candidate_selected",
                    "reconstruction_candidate_selected",
                    "no_supported_candidate",
                    "not_applicable",
                ):
                    for header in (
                        "source",
                        "source_repaired",
                        "standard_assumption",
                        "donor_assumption",
                        "hypothesis",
                        "none",
                    ):
                        for artifact in ("complete", "partial", "unavailable"):
                            selected = selection.endswith("candidate_selected")
                            record = {
                                "artifact_status": artifact,
                                "candidate_count": 1 if selected else 0,
                                "decode_extent": decode,
                                "engine_version": "engine-v1",
                                "execution_status": execution,
                                "header_basis": header,
                                "interventions": [],
                                "object_id": object_id_for("jpeg", 0),
                                "policy_version": "policy-v1",
                                "required_features": [],
                                "schema": "media-recovery.result",
                                "schema_version": "1.0",
                                "selected_candidate_fingerprint": fingerprint if selected else None,
                                "selected_candidate_id": "cand-000" if selected else None,
                                "selection_status": selection,
                                "support_status": support,
                            }
                            schema_accepts = validator.is_valid(record)
                            try:
                                ResultRecord.from_dict(record)
                            except DomainValidationError:
                                python_accepts = False
                            else:
                                python_accepts = True
                            assert schema_accepts == python_accepts, record


def test_distributed_schemas_validate_themselves_and_writer_outputs(tmp_path):
    registry, schemas = _schema_registry()
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)

    run, candidate, result = _bundle(tmp_path)
    object_record = read_objects_jsonl(run.parent / read_run(run)["parent_run_ids"][0])[0]
    samples = {
        "media-recovery.object-1.0.schema.json": object_record.to_dict(),
        "media-recovery.candidate-1.0.schema.json": candidate.to_dict(),
        "media-recovery.result-1.0.schema.json": result.to_dict(),
        "media-recovery.coefficient-manifest-1.0.schema.json": (
            candidate.coefficient_manifest.to_dict()
        ),
    }
    for filename, sample in samples.items():
        Draft202012Validator(schemas[filename], registry=registry).validate(sample)


def test_distributed_schema_versions_use_the_reader_canonical_minor_grammar():
    _, schemas = _schema_registry()
    version_schemas = []
    for schema in schemas.values():
        properties = schema.get("properties", {})
        if "schema_version" in properties:
            version_schemas.append(properties["schema_version"])
    version_schemas.append(
        schemas["media-recovery.forensic-defs-1.0.schema.json"]["$defs"]["schemaVersion"]
    )
    assert len(version_schemas) == 8
    for version_schema in version_schemas:
        validator = Draft202012Validator(version_schema)
        assert validator.is_valid("1.0")
        assert validator.is_valid("1.9")
        assert not validator.is_valid("1.01")


def test_npz_component_shapes_partial_blocks_and_discontinuous_refs_round_trip(tmp_path):
    run, candidate, _ = _bundle(tmp_path)
    arrays = read_coefficient_npz(run, candidate.coefficient_manifest)
    assert arrays["coef_y"].shape == (2, 8, 8)
    assert arrays["block_validity_y"].tolist() == [2, 1]
    assert arrays["block_validity_cr"].tolist() == [0]
    first_start, first_count = arrays["source_span_ref_range_y"][0, 0, 0]
    assert (int(first_start), int(first_count)) == (0, 2)
    assert arrays["source_span_refs_y"][:2].tolist() == [0, 1]
    assert arrays["placement_owner_cr"].tolist() == [[-1]]


def test_object_owned_npz_manifest_round_trips_through_objects_jsonl(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    record = _object_record()
    manifest = write_coefficient_npz(
        run,
        "forensic/objects/object.npz",
        _arrays(),
        owner=ArtifactOwner(OwnerKind.OBJECT, record.object_id),
        source_span_ids=tuple(item.span_id for item in record.source_spans),
    )
    record = replace(record, coefficient_manifest=manifest)
    write_objects_jsonl(run, [record])
    assert read_objects_jsonl(run) == [record]


def test_npz_writer_canonicalizes_fixed_width_big_endian_dtypes(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    object_id = object_id_for("jpeg", 0x1000)
    manifest = write_coefficient_npz(
        run,
        "forensic/object.npz",
        _arrays(byte_order=">"),
        owner=ArtifactOwner(OwnerKind.OBJECT, object_id),
        source_span_ids=("span-000000", "span-000001"),
    )
    arrays = read_coefficient_npz(run, manifest)
    assert arrays["coef_y"].dtype.str == "<i4"
    assert arrays["source_span_ref_range_y"].dtype.str == "<i8"
    assert arrays["coefficient_validity_y"].dtype.str == "|u1"
    assert {item.dtype for item in manifest.arrays} == {"<i4", "<i8", "|u1"}


@pytest.mark.parametrize("mutation", ["object", "unknown", "missing"])
def test_npz_writer_rejects_object_unknown_and_missing_arrays(tmp_path, mutation):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    arrays = _arrays()
    if mutation == "object":
        arrays["coef_y"] = arrays["coef_y"].astype(object)
    elif mutation == "unknown":
        arrays["unexpected"] = np.zeros(1, dtype="u1")
    else:
        del arrays["coef_y"]
    with pytest.raises(ArtifactFormatError):
        write_coefficient_npz(
            run,
            "forensic/object.npz",
            arrays,
            owner=ArtifactOwner(OwnerKind.OBJECT, object_id_for("jpeg", 0)),
            source_span_ids=("span-000000", "span-000001"),
        )


def test_npz_writer_rejects_unproven_valid_coefficient_and_component_shape_mismatch(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    owner = ArtifactOwner(OwnerKind.OBJECT, object_id_for("jpeg", 0))
    arrays = _arrays()
    arrays["source_span_ref_range_y"][0, 0, 0] = (-1, 0)
    with pytest.raises(ArtifactFormatError, match="canonical"):
        write_coefficient_npz(
            run,
            "forensic/a.npz",
            arrays,
            owner=owner,
            source_span_ids=("span-000000", "span-000001"),
        )
    arrays = _arrays()
    arrays["coefficient_validity_cb"] = np.zeros((2, 8, 8), dtype="u1")
    with pytest.raises(ArtifactFormatError, match="shape"):
        write_coefficient_npz(
            run,
            "forensic/b.npz",
            arrays,
            owner=owner,
            source_span_ids=("span-000000", "span-000001"),
        )


def test_npz_bytes_and_zip_metadata_are_deterministic_across_mapping_order(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    arrays = _arrays()
    reversed_arrays = dict(reversed(list(arrays.items())))
    owner = ArtifactOwner(OwnerKind.OBJECT, object_id_for("jpeg", 0))
    first = write_coefficient_npz(
        run,
        "forensic/first.npz",
        arrays,
        owner=owner,
        source_span_ids=("span-000000", "span-000001"),
    )
    second = write_coefficient_npz(
        run,
        "forensic/second.npz",
        reversed_arrays,
        owner=owner,
        source_span_ids=("span-000000", "span-000001"),
    )
    first_path = run / first.npz_path
    second_path = run / second.npz_path
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first.npz_sha256 == second.npz_sha256
    with zipfile.ZipFile(first_path) as archive:
        infos = archive.infolist()
        assert [item.filename for item in infos] == sorted(item.filename for item in infos)
        assert {item.date_time for item in infos} == {(1980, 1, 1, 0, 0, 0)}
        assert {item.create_system for item in infos} == {3}
        assert {item.external_attr for item in infos} == {0o100600 << 16}
        assert {item.compress_type for item in infos} == {zipfile.ZIP_DEFLATED}


@pytest.mark.parametrize("field", ["npz_sha256", "npz_size", "shape", "dtype"])
def test_npz_reader_detects_manifest_hash_size_shape_and_dtype_tampering(tmp_path, field):
    run, candidate, _ = _bundle(tmp_path)
    manifest = candidate.coefficient_manifest
    if field == "npz_sha256":
        tampered = replace(manifest, npz_sha256="0" * 64)
    elif field == "npz_size":
        tampered = replace(manifest, npz_size=manifest.npz_size + 1)
    else:
        arrays = list(manifest.arrays)
        index = next(index for index, item in enumerate(arrays) if item.name == "coef_y")
        descriptor = arrays[index]
        arrays[index] = replace(
            descriptor,
            shape=(3, 8, 8) if field == "shape" else descriptor.shape,
            dtype=">i4" if field == "dtype" else descriptor.dtype,
        )
        tampered = replace(manifest, arrays=tuple(arrays))
    with pytest.raises(ArtifactFormatError):
        read_coefficient_npz(run, tampered)


def _rewrite_npz(path: Path, *, remove: str | None = None, add_unknown: bool = False, replace_member=None):
    with zipfile.ZipFile(path, "r") as source:
        members = {item.filename: source.read(item.filename) for item in source.infolist()}
    if remove is not None:
        del members[f"{remove}.npy"]
    if add_unknown:
        members["unexpected.npy"] = next(iter(members.values()))
    if replace_member is not None:
        name, data = replace_member
        members[f"{name}.npy"] = data
    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            archive.writestr(info, members[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


@pytest.mark.parametrize("mutation", ["missing", "unknown", "pickle", "npy-version"])
def test_npz_reader_rejects_missing_unknown_pickle_and_noncanonical_npy_members(
    tmp_path, mutation
):
    run, candidate, _ = _bundle(tmp_path)
    manifest = candidate.coefficient_manifest
    path = run / manifest.npz_path
    descriptors = manifest.arrays
    if mutation == "missing":
        _rewrite_npz(path, remove="coef_y")
    elif mutation == "unknown":
        _rewrite_npz(path, add_unknown=True)
    elif mutation == "pickle":
        buffer = io.BytesIO()
        np.lib.format.write_array(
            buffer,
            np.asarray([[{"pickle": True}]], dtype=object),
            version=(2, 0),
            allow_pickle=True,
        )
        _rewrite_npz(path, replace_member=("coef_y", buffer.getvalue()))
        descriptors = tuple(
            replace(item, dtype="|O", shape=(1, 1)) if item.name == "coef_y" else item
            for item in descriptors
        )
    else:
        for name, array in _arrays().items():
            buffer = io.BytesIO()
            np.lib.format.write_array(
                buffer,
                array,
                version=(1, 0),
                allow_pickle=False,
            )
            _rewrite_npz(path, replace_member=(name, buffer.getvalue()))
    tampered = replace(
        manifest,
        npz_sha256=sha256_file(path),
        npz_size=path.stat().st_size,
        arrays=descriptors,
    )
    with pytest.raises(ArtifactFormatError):
        read_coefficient_npz(run, tampered)


def test_npz_reader_always_calls_numpy_load_with_pickle_disabled(tmp_path, monkeypatch):
    run, candidate, _ = _bundle(tmp_path)
    calls = []
    real_load = forensics.np.load

    def checked_load(*args, **kwargs):
        calls.append(kwargs.get("allow_pickle"))
        return real_load(*args, **kwargs)

    monkeypatch.setattr(forensics.np, "load", checked_load)
    read_coefficient_npz(run, candidate.coefficient_manifest)
    assert calls == [False]


def test_npz_path_traversal_and_run_outside_references_are_rejected(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    owner = ArtifactOwner(OwnerKind.OBJECT, object_id_for("jpeg", 0))
    with pytest.raises(ValueError, match="relative path"):
        write_coefficient_npz(
            run,
            "../outside.npz",
            _arrays(),
            owner=owner,
            source_span_ids=("span-000000", "span-000001"),
        )
    manifest = write_coefficient_npz(
        run,
        "inside.npz",
        _arrays(),
        owner=owner,
        source_span_ids=("span-000000", "span-000001"),
    )
    registry, schemas = _schema_registry()
    validator = Draft202012Validator(
        schemas["media-recovery.coefficient-manifest-1.0.schema.json"],
        registry=registry,
    )
    for unsafe_path in (
        "../outside.npz",
        "nested/../outside.npz",
        "C:/outside.npz",
        "nested//outside.npz",
        "nested/./outside.npz",
        "objects.jsonl:coeff.npz",
        "inside\x00.npz",
        "inside.npz\n",
        "inside\x7f.npz",
    ):
        record = manifest.to_dict()
        record["npz"]["path"] = unsafe_path
        assert not validator.is_valid(record), unsafe_path
        with pytest.raises(DomainValidationError, match="artifact path"):
            replace(manifest, npz_path=unsafe_path)


def test_npz_writer_rejects_ntfs_alternate_data_stream_path(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    owner = ArtifactOwner(OwnerKind.OBJECT, object_id_for("jpeg", 0))

    with pytest.raises(ValueError, match="must not contain ':'"):
        write_coefficient_npz(
            run,
            "objects.jsonl:coeff.npz",
            _arrays(),
            owner=owner,
            source_span_ids=("span-000000", "span-000001"),
        )


def test_npz_reader_rejects_an_in_run_symbolic_link(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    manifest = write_coefficient_npz(
        run,
        "forensic/object.npz",
        _arrays(),
        owner=ArtifactOwner(OwnerKind.OBJECT, object_id_for("jpeg", 0)),
        source_span_ids=("span-000000", "span-000001"),
    )
    target = run / manifest.npz_path
    link = target.with_name("object-link.npz")
    try:
        link.symlink_to(target.name)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    linked_manifest = replace(
        manifest,
        npz_path=link.relative_to(run).as_posix(),
    )
    with pytest.raises(ArtifactFormatError, match="symbolic link"):
        read_coefficient_npz(run, linked_manifest)


def test_npz_writer_rejects_invalid_owner_and_owner_stage_mismatch(tmp_path):
    case = _case(tmp_path)
    discovery = _run(case, stage="discovery")
    start_run(discovery)
    destination = discovery / "forensic" / "invalid.npz"
    with pytest.raises(TypeError, match="ArtifactOwner"):
        write_coefficient_npz(
            discovery,
            destination.relative_to(discovery),
            _arrays(),
            owner=None,
            source_span_ids=("span-000000", "span-000001"),
        )
    assert not destination.exists()

    candidate_owner = ArtifactOwner(
        OwnerKind.CANDIDATE,
        object_id_for("jpeg", 0x1000),
        "cand-000",
        "0" * 64,
    )
    with pytest.raises(ArtifactFormatError, match="reconstruction run"):
        write_coefficient_npz(
            discovery,
            "forensic/candidate.npz",
            _arrays(),
            owner=candidate_owner,
            source_span_ids=("span-000000", "span-000001"),
        )


def test_npz_replace_failure_preserves_destination_and_cleans_staging(tmp_path, monkeypatch):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    owner = ArtifactOwner(OwnerKind.OBJECT, object_id_for("jpeg", 0))
    manifest = write_coefficient_npz(
        run,
        "forensic/object.npz",
        _arrays(),
        owner=owner,
        source_span_ids=("span-000000", "span-000001"),
    )
    destination = run / manifest.npz_path
    before = destination.read_bytes()

    def fail_replace(source, target):
        raise OSError("replace failed")

    monkeypatch.setattr(forensics.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_coefficient_npz(
            run,
            manifest.npz_path,
            _arrays(),
            owner=owner,
            source_span_ids=("span-000000", "span-000001"),
        )
    assert destination.read_bytes() == before
    assert not list(destination.parent.glob(f".{destination.name}.*.tmp"))


def test_npz_staging_failure_preserves_destination_and_cleans_staging(tmp_path, monkeypatch):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    owner = ArtifactOwner(OwnerKind.OBJECT, object_id_for("jpeg", 0))
    manifest = write_coefficient_npz(
        run,
        "forensic/object.npz",
        _arrays(),
        owner=owner,
        source_span_ids=("span-000000", "span-000001"),
    )
    destination = run / manifest.npz_path
    before = destination.read_bytes()

    def fail_write(*args, **kwargs):
        raise OSError("write failed")

    monkeypatch.setattr(forensics.np.lib.format, "write_array", fail_write)
    with pytest.raises(OSError, match="write failed"):
        write_coefficient_npz(
            run,
            manifest.npz_path,
            _arrays(),
            owner=owner,
            source_span_ids=("span-000000", "span-000001"),
        )
    assert destination.read_bytes() == before
    assert not list(destination.parent.glob(f".{destination.name}.*.tmp"))


def test_record_jsonl_is_canonical_across_input_order(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    first = _object_record(disk_offset=0x1000)
    second = _object_record(disk_offset=0x3000)
    path = write_objects_jsonl(run, [second, first])
    before = path.read_bytes()
    write_objects_jsonl(run, [first, second])
    assert path.read_bytes() == before
    assert [item.object_id for item in read_objects_jsonl(run)] == [first.object_id, second.object_id]


def test_object_parent_ids_resolve_in_the_same_jsonl_and_reject_cycles(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    first = _object_record(disk_offset=0x1000)
    second = _object_record(disk_offset=0x3000)

    dangling = replace(first, parent_object_id=second.object_id)
    with pytest.raises(ArtifactFormatError, match="parent object"):
        write_objects_jsonl(run, [dangling])

    child = replace(second, parent_object_id=first.object_id)
    write_objects_jsonl(run, (child, first))
    assert read_objects_jsonl(run) == [first, child]

    cycle = (
        replace(first, parent_object_id=second.object_id),
        replace(second, parent_object_id=first.object_id),
    )
    with pytest.raises(ArtifactFormatError, match="cycle"):
        write_objects_jsonl(run, cycle)

    write_run_jsonl(
        run,
        "objects.jsonl",
        [item.to_dict() for item in cycle],
        sort_key=lambda row: row["object_id"],
    )
    with pytest.raises(ArtifactFormatError, match="cycle"):
        read_objects_jsonl(run)


def test_object_parent_graph_handles_depth_beyond_python_recursion_limit():
    records = [
        _object_record(disk_offset=0x1000 + index * 0x2000)
        for index in range(1100)
    ]
    chain = tuple(
        replace(
            item,
            parent_object_id=(
                records[index + 1].object_id if index + 1 < len(records) else None
            ),
        )
        for index, item in enumerate(records)
    )
    forensics._validate_object_parent_graph(chain)


def test_coefficient_npz_path_is_unique_per_record_owner(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    first = _object_record(disk_offset=0x1000)
    second = _object_record(disk_offset=0x3000)
    manifest = write_coefficient_npz(
        run,
        "forensic/shared.npz",
        _arrays(),
        owner=ArtifactOwner(OwnerKind.OBJECT, first.object_id),
        source_span_ids=tuple(item.span_id for item in first.source_spans),
    )
    records = (
        replace(first, coefficient_manifest=manifest),
        replace(
            second,
            coefficient_manifest=replace(
                manifest,
                owner=ArtifactOwner(OwnerKind.OBJECT, second.object_id),
            ),
        ),
    )

    with pytest.raises(ArtifactFormatError, match="NPZ path"):
        write_objects_jsonl(run, records)

    write_run_jsonl(
        run,
        "objects.jsonl",
        [item.to_dict() for item in records],
        sort_key=lambda row: row["object_id"],
    )
    with pytest.raises(ArtifactFormatError, match="NPZ path"):
        read_objects_jsonl(run)


@pytest.mark.skipif(
    os.path.normcase("A") != os.path.normcase("a"),
    reason="requires a case-insensitive filesystem path model",
)
def test_coefficient_npz_path_case_alias_cannot_be_shared_by_owners(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    first = _object_record(disk_offset=0x1000)
    second = _object_record(disk_offset=0x3000)
    manifest = write_coefficient_npz(
        run,
        "forensic/shared.npz",
        _arrays(),
        owner=ArtifactOwner(OwnerKind.OBJECT, first.object_id),
        source_span_ids=tuple(item.span_id for item in first.source_spans),
    )
    alias = replace(
        manifest,
        npz_path="Forensic/shared.npz",
        owner=ArtifactOwner(OwnerKind.OBJECT, second.object_id),
    )
    assert (run / manifest.npz_path).samefile(run / alias.npz_path)

    with pytest.raises(ArtifactFormatError, match="NPZ path"):
        write_objects_jsonl(
            run,
            (
                replace(first, coefficient_manifest=manifest),
                replace(second, coefficient_manifest=alias),
            ),
        )


def test_candidate_object_id_must_resolve_in_a_parent_discovery_run(tmp_path):
    case = _case(tmp_path)
    discovery = _discovery(case)
    run = _run(case, stage="reconstruction", parents=(discovery.name,))
    start_run(run)
    spans = _spans(disk_offset=0x3000)
    orphan = CandidateRecord.build(
        engine_version="engine-v1",
        policy_version="policy-v1",
        object_id=object_id_for("jpeg", 0x3000),
        candidate_ordinal=0,
        hypotheses=_hypotheses(),
        source_spans=spans,
        decode_segments=(_segment(),),
        virtual_edits=(_edit(),),
        placements=(),
    )

    with pytest.raises(ArtifactFormatError, match="parent discovery object"):
        write_candidates_jsonl(run, [orphan])

    write_run_jsonl(
        run,
        "candidates.jsonl",
        [orphan.to_dict()],
        sort_key=lambda row: row["candidate_id"],
    )
    with pytest.raises(ArtifactFormatError, match="parent discovery object"):
        read_candidates_jsonl(run)


def test_same_major_higher_minor_is_readable_but_unknown_feature_and_major_are_rejected(tmp_path):
    case = _case(tmp_path)
    run = _run(case, stage="discovery")
    start_run(run)
    record = _object_record().to_dict()
    record["schema_version"] = "1.9"
    record["future_optional"] = {"ignored": True}
    write_run_jsonl(run, "objects.jsonl", [record], sort_key=lambda row: row["object_id"])
    loaded = read_objects_jsonl(run)[0]
    assert loaded.schema_version == "1.9"
    assert "future_optional" not in loaded.to_dict()

    record["required_features"] = ["future-required"]
    write_run_jsonl(run, "objects.jsonl", [record], sort_key=lambda row: row["object_id"])
    with pytest.raises(ArtifactFormatError, match="unknown required_features"):
        read_objects_jsonl(run)

    record["required_features"] = []
    record["schema_version"] = "2.0"
    write_run_jsonl(run, "objects.jsonl", [record], sort_key=lambda row: row["object_id"])
    with pytest.raises(ArtifactFormatError, match="major version"):
        read_objects_jsonl(run)

    record["schema_version"] = "1.01"
    write_run_jsonl(run, "objects.jsonl", [record], sort_key=lambda row: row["object_id"])
    with pytest.raises(ArtifactFormatError, match="invalid schema version"):
        read_objects_jsonl(run)


def test_unknown_component_keys_are_reported_as_artifact_format_errors(tmp_path):
    run, candidate, _ = _bundle(tmp_path)
    record = candidate.to_dict()
    record["decode_segments"][0]["component_block_ranges"]["x"] = {
        "start": 0,
        "end": 0,
    }
    write_run_jsonl(
        run,
        "candidates.jsonl",
        [record],
        sort_key=lambda row: row["candidate_id"],
    )
    with pytest.raises(ArtifactFormatError, match="component"):
        read_candidates_jsonl(run)

    manifest = candidate.coefficient_manifest.to_dict()
    manifest["components"]["x"] = manifest["components"]["cr"]
    with pytest.raises(ArtifactFormatError, match="component"):
        read_coefficient_npz(run, manifest)


def test_placement_owner_must_match_gap_and_placement_records(tmp_path):
    run, candidate, _ = _bundle(tmp_path)
    arrays = _arrays()
    arrays["placement_owner_y"][0, 1] = -1
    manifest = write_coefficient_npz(
        run,
        "forensic/candidates/mismatch.npz",
        arrays,
        owner=candidate.coefficient_manifest.owner,
        source_span_ids=candidate.coefficient_manifest.source_span_ids,
    )
    mismatched = replace(candidate, coefficient_manifest=manifest)
    with pytest.raises(ArtifactFormatError, match="placement owner"):
        write_candidates_jsonl(run, [mismatched])


def test_overlap_owner_sentinel_represents_multiple_placement_claims(tmp_path):
    run, _, _ = _bundle(tmp_path)
    object_id, _, hypotheses, spans, segments, edits, _ = _candidate_semantics()
    placements = (
        Placement("place-000000", Component.Y, 0, 0, 0),
        Placement("place-000001", Component.Y, 1, 0, 0),
        Placement("place-000002", Component.CB, 0, 0, 0),
    )
    fingerprint = candidate_fingerprint_for(
        object_id,
        hypotheses=hypotheses,
        source_spans=spans,
        decode_segments=segments,
        virtual_edits=edits,
        placements=placements,
    )
    arrays = _arrays()
    arrays["placement_owner_y"][:] = (-2, -1)
    manifest = write_coefficient_npz(
        run,
        "forensic/candidates/overlap.npz",
        arrays,
        owner=ArtifactOwner(OwnerKind.CANDIDATE, object_id, "cand-000", fingerprint),
        source_span_ids=tuple(item.span_id for item in spans),
    )
    candidate = CandidateRecord(
        "reconstruct-v1",
        "selection-v1",
        object_id,
        0,
        "cand-000",
        fingerprint,
        hypotheses,
        spans,
        segments,
        edits,
        placements,
        manifest,
    )
    write_candidates_jsonl(run, [candidate])
    assert read_candidates_jsonl(run) == [candidate]


def test_result_candidate_count_and_reference_are_checked_against_candidates_jsonl(tmp_path):
    run, _, result = _bundle(tmp_path)
    with pytest.raises(ArtifactFormatError, match="candidate_count"):
        write_results_jsonl(run, [replace(result, candidate_count=2)])
    with pytest.raises(ArtifactFormatError, match="unresolved"):
        write_results_jsonl(
            run,
            [replace(result, selected_candidate_fingerprint="0" * 64)],
        )


def test_result_object_id_must_resolve_in_a_parent_discovery_run(tmp_path):
    case = _case(tmp_path)
    discovery = _discovery(case)
    run = _run(case, stage="reconstruction", parents=(discovery.name,))
    start_run(run)
    orphan = ResultRecord(
        engine_version="engine-v1",
        policy_version="policy-v1",
        object_id=object_id_for("jpeg", 0x3000),
        execution_status=ExecutionStatus.COMPLETED,
        support_status=SupportStatus.UNSUPPORTED,
        decode_extent=DecodeExtent.NOT_ATTEMPTED,
        selection_status=SelectionStatus.NOT_APPLICABLE,
        header_basis=HeaderBasis.NONE,
        artifact_status=ArtifactStatus.UNAVAILABLE,
        selected_candidate_id=None,
        selected_candidate_fingerprint=None,
        candidate_count=0,
    )

    with pytest.raises(ArtifactFormatError, match="parent discovery object"):
        write_results_jsonl(run, [orphan])

    write_run_jsonl(
        run,
        "results.jsonl",
        [orphan.to_dict()],
        sort_key=lambda row: row["object_id"],
    )
    with pytest.raises(ArtifactFormatError, match="parent discovery object"):
        read_results_jsonl(run)


def test_forensic_records_and_npz_read_after_existing_completion_seal(tmp_path):
    run, candidate, result = _bundle(tmp_path, complete=True)
    assert read_run(run)["status"] == "completed"
    assert read_candidates_jsonl(run) == [candidate]
    assert read_results_jsonl(run) == [result]
    arrays = read_coefficient_npz(run, candidate.coefficient_manifest)
    assert arrays["coef_cb"].shape == (1, 8, 8)
