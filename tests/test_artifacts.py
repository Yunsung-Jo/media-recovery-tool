from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from media_recovery.artifacts import (
    ArtifactFormatError,
    CaseConflictError,
    RunCompatibilityError,
    RunStateError,
    complete_run,
    create_run,
    fail_run,
    interrupt_run,
    read_case,
    read_jsonl,
    read_run,
    register_case,
    resolve_work_root,
    resume_run,
    start_run,
    verify_case_source,
    write_run_jsonl,
)
from media_recovery.artifacts import io as artifact_io
from media_recovery.artifacts import runs
from media_recovery.artifacts.io import (
    atomic_write_json,
    canonical_json_bytes,
    strict_json_loads,
    write_canonical_jsonl,
)
from media_recovery.artifacts.schema import (
    CASE_SCHEMA,
    CASE_SCHEMA_VERSION,
    COMPLETION_SCHEMA,
    COMPLETION_SCHEMA_VERSION,
    RUN_SCHEMA,
    RUN_SCHEMA_VERSION,
)


VERSIONS = {
    "tool_version": "test-tool",
    "engine_version": "test-engine",
    "policy_version": "test-policy",
    "artifact_schema_version": "test-artifacts-v1",
    "environment": {"platform": "test-platform", "python": "3.12"},
}


def _case(tmp_path: Path, *, content: bytes = b"synthetic source") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.bin"
    source.write_bytes(content)
    return register_case(source, work_root=tmp_path / "work")


def _run(case: Path, **kwargs) -> Path:
    options = kwargs.pop("options", {"workers": 2})
    return create_run(
        case,
        stage=kwargs.pop("stage", "discovery"),
        options=options,
        **VERSIONS,
        **kwargs,
    )


def _completed_discovery(case: Path) -> Path:
    run = _run(case)
    start_run(run)
    write_run_jsonl(run, "objects.jsonl", [{"offset": 8}], sort_key=lambda row: row["offset"])
    complete_run(run)
    return run


def test_default_and_override_work_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_work_root() == (tmp_path / "work").resolve()
    assert resolve_work_root(tmp_path / "elsewhere") == (tmp_path / "elsewhere").resolve()


def test_register_case_records_absolute_source_without_copying(tmp_path):
    source = tmp_path / "small.bin"
    source.write_bytes(b"abc")
    case = register_case(source, work_root=tmp_path / "workspace", label="synthetic")
    record = read_case(case)

    assert case.name == "case-ba7816bf8f01cfea4141"
    assert record["schema"] == "media-recovery.case"
    assert record["schema_version"] == "1.0"
    assert record["source"] == {
        "path": str(source.resolve()),
        "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "size": 3,
    }
    assert not (case / "source").exists()
    assert (tmp_path / "workspace" / "cache").is_dir()
    assert (tmp_path / "workspace" / "tmp").is_dir()


def test_case_id_is_stable_and_existing_case_is_not_rewritten(tmp_path):
    source = tmp_path / "same.bin"
    source.write_bytes(b"same")
    first = register_case(source, work_root=tmp_path / "work", label="first")
    before = (first / "case.json").read_bytes()
    second = register_case(source, work_root=tmp_path / "work", label="ignored")

    assert first == second
    assert (second / "case.json").read_bytes() == before
    assert read_case(second)["label"] == "first"


def test_case_prefix_collision_compares_full_sha256(tmp_path):
    case = _case(tmp_path)
    metadata_path = case / "case.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    original = metadata["source"]["sha256"]
    metadata["source"]["sha256"] = original[:20] + ("0" if original[20] != "0" else "1") + original[21:]
    atomic_write_json(metadata_path, metadata)

    with pytest.raises(CaseConflictError, match="full SHA-256 differs"):
        register_case(tmp_path / "source.bin", work_root=tmp_path / "work")


def test_case_reader_and_source_verifier_reject_tampering(tmp_path):
    case = _case(tmp_path)
    (tmp_path / "source.bin").write_bytes(b"changed")
    with pytest.raises(ArtifactFormatError, match="size has changed"):
        verify_case_source(case)

    metadata = json.loads((case / "case.json").read_text(encoding="utf-8"))
    metadata["source"]["sha256"] = "0" * 64
    atomic_write_json(case / "case.json", metadata)
    with pytest.raises(ArtifactFormatError, match="case_id does not match"):
        read_case(case)


def test_run_id_format_and_directory_collision_regeneration(tmp_path, monkeypatch):
    case = _case(tmp_path)
    ids = iter(
        [
            "run-20260813T010203Z-aaaaaa",
            "run-20260813T010203Z-aaaaaa",
            "run-20260813T010203Z-bbbbbb",
        ]
    )
    monkeypatch.setattr(runs, "generate_run_id", lambda: next(ids))

    first = _run(case)
    second = _run(case)
    assert first.name == "run-20260813T010203Z-aaaaaa"
    assert second.name == "run-20260813T010203Z-bbbbbb"
    assert re.fullmatch(r"run-\d{8}T\d{6}Z-[a-z2-7]{6}", second.name)


def test_parent_lineage_requires_completed_compatible_stage(tmp_path):
    case = _case(tmp_path)
    discovery = _run(case)
    start_run(discovery)

    with pytest.raises(ValueError, match="not completed"):
        _run(case, stage="reconstruction", parent_run_ids=[discovery.name])
    complete_run(discovery)

    reconstruction = _run(case, stage="reconstruction", parent_run_ids=[discovery.name])
    assert read_run(reconstruction)["parent_run_ids"] == [discovery.name]
    with pytest.raises(ValueError, match="cannot use discovery"):
        _run(case, stage="rendering", parent_run_ids=[discovery.name])
    with pytest.raises(ValueError, match="require at least one parent"):
        _run(case, stage="enhancement")
    with pytest.raises(ValueError, match="cannot have parents"):
        _run(case, stage="discovery", parent_run_ids=[discovery.name])


def test_parent_run_must_belong_to_same_case(tmp_path):
    first_case = _case(tmp_path / "one", content=b"one")
    second_case = _case(tmp_path / "two", content=b"two")
    parent = _completed_discovery(first_case)

    with pytest.raises(ArtifactFormatError, match="cannot read JSON"):
        _run(second_case, stage="reconstruction", parent_run_ids=[parent.name])


def test_lifecycle_allows_only_explicit_transitions(tmp_path):
    run = _run(_case(tmp_path))
    assert read_run(run)["status"] == "created"
    started = start_run(run)
    assert started["status"] == "running"
    assert len(started["attempts"]) == 1
    with pytest.raises(RunStateError, match="only a created"):
        start_run(run)

    interrupted = interrupt_run(run, detail="operator stop")
    assert interrupted["status"] == "interrupted"
    assert interrupted["attempts"][-1]["outcome"] == "interrupted"
    with pytest.raises(RunStateError, match="only a running"):
        complete_run(run)


def test_start_rehashes_registered_source_before_running(tmp_path):
    run = _run(_case(tmp_path))
    replacement = b"tampered content"
    assert len(replacement) == (tmp_path / "source.bin").stat().st_size
    (tmp_path / "source.bin").write_bytes(replacement)

    with pytest.raises(ArtifactFormatError, match="SHA-256 has changed"):
        start_run(run)
    assert read_run(run)["status"] == "created"


def test_resume_requires_stage_support_and_identical_metadata(tmp_path):
    run = _run(_case(tmp_path), options={"workers": 4})
    start_run(run)
    fail_run(run, detail="worker failed")

    with pytest.raises(RunCompatibilityError, match="did not declare"):
        resume_run(run, stage_supports_resume=False, options={"workers": 4}, **VERSIONS)
    with pytest.raises(RunCompatibilityError, match="options"):
        resume_run(run, stage_supports_resume=True, options={"workers": 8}, **VERSIONS)
    with pytest.raises(RunCompatibilityError, match="version"):
        resume_run(
            run,
            stage_supports_resume=True,
            options={"workers": 4},
            tool_version="different",
            engine_version=VERSIONS["engine_version"],
            policy_version=VERSIONS["policy_version"],
            artifact_schema_version=VERSIONS["artifact_schema_version"],
            environment=VERSIONS["environment"],
        )
    with pytest.raises(RunCompatibilityError, match="environment"):
        resume_run(
            run,
            stage_supports_resume=True,
            options={"workers": 4},
            **{**VERSIONS, "environment": {"platform": "different"}},
        )

    resumed = resume_run(run, stage_supports_resume=True, options={"workers": 4}, **VERSIONS)
    assert resumed["status"] == "running"
    assert len(resumed["attempts"]) == 2


def test_resume_rehashes_registered_source(tmp_path):
    run = _run(_case(tmp_path))
    start_run(run)
    interrupt_run(run)
    replacement = b"tampered content"
    assert len(replacement) == (tmp_path / "source.bin").stat().st_size
    (tmp_path / "source.bin").write_bytes(replacement)

    with pytest.raises(ArtifactFormatError, match="SHA-256 has changed"):
        resume_run(run, stage_supports_resume=True, options={"workers": 2}, **VERSIONS)


def test_strict_json_rejects_nonfinite_duplicate_keys_and_invalid_utf8():
    with pytest.raises(ArtifactFormatError, match="strict JSON"):
        canonical_json_bytes({"value": float("nan")})
    with pytest.raises(ArtifactFormatError, match="non-finite"):
        strict_json_loads('{"value": Infinity}')
    with pytest.raises(ArtifactFormatError, match="non-finite"):
        strict_json_loads('{"value": 1e9999}')
    with pytest.raises(ArtifactFormatError, match="duplicate"):
        strict_json_loads('{"value": 1, "value": 2}')
    with pytest.raises(ArtifactFormatError, match="UTF-8"):
        strict_json_loads(b"\xff")
    with pytest.raises(ArtifactFormatError, match="keys must be strings"):
        canonical_json_bytes({1: "not strict"})


def test_jsonl_is_utf8_lf_and_deterministic_across_input_order(tmp_path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    records = [
        {"offset": 20, "label": "둘"},
        {"offset": 10, "label": "하나"},
        {"offset": 20, "label": "가"},
    ]
    write_canonical_jsonl(first, records, sort_key=lambda row: row["offset"])
    write_canonical_jsonl(second, reversed(records), sort_key=lambda row: row["offset"])

    assert first.read_bytes() == second.read_bytes()
    assert b"\r" not in first.read_bytes()
    assert first.read_bytes().endswith(b"\n")
    assert [row["offset"] for row in read_jsonl(first)] == [10, 20, 20]
    assert "하나" in first.read_text(encoding="utf-8")


def test_jsonl_rejects_nonfinite_sort_key_without_replacing_destination(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_bytes(b'{"old":true}\n')

    with pytest.raises(ArtifactFormatError, match="finite"):
        write_canonical_jsonl(
            path,
            [{"offset": 2}, {"offset": 1}],
            sort_key=lambda _row: float("nan"),
        )

    assert path.read_bytes() == b'{"old":true}\n'
    assert list(tmp_path.glob(".records.jsonl.*.tmp")) == []


def test_run_canonical_results_ignore_worker_completion_order_with_same_seed(tmp_path):
    case = _case(tmp_path)
    first_run = _run(case, random_seed=42)
    second_run = _run(case, random_seed=42)
    start_run(first_run)
    start_run(second_run)
    worker_results = [
        {"object_id": "jpeg-0000000000000020", "rank": 0},
        {"object_id": "jpeg-0000000000000010", "rank": 1},
    ]

    first = write_run_jsonl(
        first_run,
        "objects.jsonl",
        worker_results,
        sort_key=lambda row: (row["object_id"], row["rank"]),
    )
    second = write_run_jsonl(
        second_run,
        "objects.jsonl",
        reversed(worker_results),
        sort_key=lambda row: (row["object_id"], row["rank"]),
    )

    assert first.read_bytes() == second.read_bytes()
    assert read_run(first_run)["random_seed"] == read_run(second_run)["random_seed"] == 42


@pytest.mark.parametrize(
    "content, message",
    [
        (b'{"a":1}\r\n', "LF"),
        (b'{"a":1}', "end each record"),
        (b'{"a":1}\n\n', "blank"),
        (b'[1,2]\n', "not an object"),
    ],
)
def test_jsonl_reader_rejects_noncanonical_input(tmp_path, content, message):
    path = tmp_path / "bad.jsonl"
    path.write_bytes(content)
    with pytest.raises(ArtifactFormatError, match=message):
        read_jsonl(path)


def test_jsonl_writer_failure_does_not_expose_partial_destination(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_bytes(b'{"old":true}\n')

    def failing_records():
        yield {"offset": 1}
        raise RuntimeError("worker collection failed")

    with pytest.raises(RuntimeError, match="worker collection"):
        write_canonical_jsonl(path, failing_records(), sort_key=lambda row: row["offset"])
    assert path.read_bytes() == b'{"old":true}\n'
    assert list(tmp_path.glob(".records.jsonl.*.tmp")) == []


def test_atomic_replace_failure_preserves_existing_destination(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    path.write_bytes(b'{"old":true}\n')
    monkeypatch.setattr(artifact_io.os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("no")))

    with pytest.raises(OSError, match="no"):
        atomic_write_json(path, {"new": True})
    assert path.read_bytes() == b'{"old":true}\n'
    assert list(tmp_path.glob(".record.json.*.tmp")) == []


def test_completion_seals_run_and_all_artifact_files(tmp_path):
    run = _run(_case(tmp_path))
    start_run(run)
    write_run_jsonl(
        run,
        "reports/objects.jsonl",
        [{"offset": 2}, {"offset": 1}],
        sort_key=lambda row: row["offset"],
    )
    completed = complete_run(run)

    assert completed["status"] == "completed"
    marker = json.loads((run / "completed.json").read_text(encoding="utf-8"))
    assert [item["path"] for item in marker["files"]] == [
        "reports/objects.jsonl",
        "run.json",
    ]
    with pytest.raises(RunStateError):
        write_run_jsonl(run, "more.jsonl", [], sort_key=lambda row: row)
    with pytest.raises(RunStateError):
        resume_run(run, stage_supports_resume=True, options={"workers": 2}, **VERSIONS)


@pytest.mark.parametrize("mutation", ["add", "modify", "remove-marker", "status-mismatch"])
def test_completed_run_rejects_file_or_marker_mismatch(tmp_path, mutation):
    run = _run(_case(tmp_path))
    start_run(run)
    artifact = write_run_jsonl(run, "objects.jsonl", [{"offset": 1}], sort_key=lambda row: row["offset"])
    complete_run(run)

    if mutation == "add":
        (run / "late.txt").write_text("late", encoding="utf-8")
    elif mutation == "modify":
        artifact.write_bytes(b'{"offset":2}\n')
    elif mutation == "remove-marker":
        (run / "completed.json").unlink()
    else:
        record = json.loads((run / "run.json").read_text(encoding="utf-8"))
        record["status"] = "running"
        atomic_write_json(run / "run.json", record)

    with pytest.raises(ArtifactFormatError):
        read_run(run)


def test_failed_completion_never_reads_as_completed_and_can_retry(tmp_path, monkeypatch):
    run = _run(_case(tmp_path))
    start_run(run)
    real_replace = runs.os.replace
    replace_count = 0

    def fail_second_replace(source, target):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 2:
            raise OSError("simulated run.json replace failure")
        real_replace(source, target)

    monkeypatch.setattr(runs.os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="simulated"):
        complete_run(run)
    with pytest.raises(ArtifactFormatError, match="non-completed"):
        read_run(run)
    assert json.loads((run / "run.json").read_text(encoding="utf-8"))["status"] == "running"

    monkeypatch.setattr(runs.os, "replace", real_replace)
    assert complete_run(run)["status"] == "completed"


def test_second_completion_staging_failure_cleans_first_temp_and_can_retry(
    tmp_path, monkeypatch
):
    run = _run(_case(tmp_path))
    start_run(run)
    real_stage = runs.stage_atomic_bytes
    stage_count = 0

    def fail_second_stage(path, data):
        nonlocal stage_count
        stage_count += 1
        if stage_count == 2:
            raise OSError("simulated run.json staging failure")
        return real_stage(path, data)

    monkeypatch.setattr(runs, "stage_atomic_bytes", fail_second_stage)
    with pytest.raises(OSError, match="simulated"):
        complete_run(run)

    assert list(run.glob(".*.tmp")) == []
    assert not (run / "completed.json").exists()
    assert read_run(run)["status"] == "running"

    monkeypatch.setattr(runs, "stage_atomic_bytes", real_stage)
    assert complete_run(run)["status"] == "completed"


def test_dirty_run_preserves_actual_patch_and_rejects_tampering(tmp_path):
    case = _case(tmp_path)
    patch = b"diff --git a/example b/example\n+change\n"
    run = _run(case, git_dirty=True, dirty_patch=patch, git_commit="abc123")
    record = read_run(run)

    assert (run / "provenance" / "dirty.patch").read_bytes() == patch
    assert record["provenance"]["patch"]["size"] == len(patch)
    assert record["versions"] == {
        "engine": "test-engine",
        "policy": "test-policy",
        "schema": "test-artifacts-v1",
        "tool": "test-tool",
    }
    assert record["environment"] == VERSIONS["environment"]
    (run / "provenance" / "dirty.patch").write_bytes(b"different")
    with pytest.raises(ArtifactFormatError, match="does not match"):
        read_run(run)


def test_dirty_flag_and_patch_must_be_consistent(tmp_path):
    case = _case(tmp_path)
    with pytest.raises(ValueError, match="require non-empty"):
        _run(case, git_dirty=True)
    with pytest.raises(ValueError, match="only valid"):
        _run(case, dirty_patch=b"patch")


def test_same_major_higher_minor_is_readable_unless_feature_is_unknown(tmp_path):
    case = _case(tmp_path)
    path = case / "case.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["schema_version"] = "1.9"
    metadata["future_optional"] = True
    atomic_write_json(path, metadata)
    assert read_case(case)["future_optional"] is True

    metadata["required_features"] = ["future-required"]
    atomic_write_json(path, metadata)
    with pytest.raises(ArtifactFormatError, match="unknown required_features"):
        read_case(case)


def test_higher_schema_major_is_rejected(tmp_path):
    case = _case(tmp_path)
    path = case / "case.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["schema_version"] = "2.0"
    atomic_write_json(path, metadata)
    with pytest.raises(ArtifactFormatError, match="unsupported.*major"):
        read_case(case)


@pytest.mark.parametrize(
    "filename, schema_name, version",
    [
        ("media-recovery.case-1.0.schema.json", CASE_SCHEMA, CASE_SCHEMA_VERSION),
        ("media-recovery.run-1.0.schema.json", RUN_SCHEMA, RUN_SCHEMA_VERSION),
        (
            "media-recovery.run-completion-1.0.schema.json",
            COMPLETION_SCHEMA,
            COMPLETION_SCHEMA_VERSION,
        ),
    ],
)
def test_distributed_schema_names_versions_and_writer_fields_match(filename, schema_name, version):
    schema_path = Path(__file__).parents[1] / "schemas" / filename
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["properties"]["schema"]["const"] == schema_name
    assert re.fullmatch(schema["properties"]["schema_version"]["pattern"], version)
    assert set(schema["required"]) <= set(schema["properties"])
    Draft202012Validator.check_schema(schema)


def test_writer_records_contain_all_distributed_schema_required_fields(tmp_path):
    case = _case(tmp_path)
    run = _run(case)
    start_run(run)
    complete_run(run)
    root = Path(__file__).parents[1]
    records = [
        (
            json.loads((case / "case.json").read_text(encoding="utf-8")),
            root / "schemas" / "media-recovery.case-1.0.schema.json",
        ),
        (
            json.loads((run / "run.json").read_text(encoding="utf-8")),
            root / "schemas" / "media-recovery.run-1.0.schema.json",
        ),
        (
            json.loads((run / "completed.json").read_text(encoding="utf-8")),
            root / "schemas" / "media-recovery.run-completion-1.0.schema.json",
        ),
    ]
    for record, schema_path in records:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert set(schema["required"]) <= set(record)
        assert record["schema"] == schema["properties"]["schema"]["const"]
        Draft202012Validator(schema).validate(record)


@pytest.mark.parametrize("mutation", ["discovery-parent", "empty-completed", "dirty-without-patch"])
def test_run_schema_rejects_reader_invalid_relationships(tmp_path, mutation):
    run = _run(_case(tmp_path))
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    invalid = copy.deepcopy(record)

    if mutation == "discovery-parent":
        invalid["parent_run_ids"] = ["run-20260813T010203Z-aaaaaa"]
    elif mutation == "empty-completed":
        invalid["status"] = "completed"
        invalid["started_at"] = "2026-08-13T01:02:03Z"
        invalid["finished_at"] = "2026-08-13T01:03:04Z"
    else:
        invalid["provenance"]["dirty"] = True
        invalid["provenance"]["patch"] = None

    schema_path = Path(__file__).parents[1] / "schemas" / "media-recovery.run-1.0.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(invalid))
