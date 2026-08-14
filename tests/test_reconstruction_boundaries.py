"""T-0005 reconstruction 책임 경계와 내부 result 계약."""
from __future__ import annotations

import ast
import importlib
import pickle
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from media_recovery.artifacts.io import canonical_json_bytes
from media_recovery.reconstruction import (
    engine,
    entropy,
    legacy_output,
    placement,
    single_best,
)


def test_engine_is_public_facade_without_private_placement_wrappers():
    assert engine.recover is entropy.recover
    assert engine.recover_bytes is entropy.recover_bytes
    assert not hasattr(engine, "_correct_segment_shifts")
    assert not hasattr(engine, "_probe")


def test_single_best_result_is_deep_snapshot_and_pickleable():
    source = bytearray(b"source")
    rgb = np.arange(12, dtype=np.uint8).reshape(2, 2, 3)
    nested_array = np.array([1, 2, 3], dtype=np.int64)
    info = {
        "phase_cuts": [[3, "resync", 1.5]],
        "nested": {"array": nested_array},
    }
    dc = np.array([7, 8, 9], dtype=np.int64)

    result = single_best.SingleBestResult(
        "RECOVERED", source, rgb, info, [(2, 11, dc)]
    )
    source[0] = ord("X")
    rgb[:] = 0
    nested_array[:] = 0
    info["phase_cuts"][0][0] = 99
    dc[:] = 0

    assert result.source_bytes == b"source"
    assert result.rgb is not None
    assert result.rgb.ravel().tolist() == list(range(12))
    assert result.info["phase_cuts"][0][0] == 3
    assert result.info["nested"]["array"].tolist() == [1, 2, 3]
    assert result.segments[0].dc_predictors == (7, 8, 9)
    with pytest.raises(FrozenInstanceError):
        result.action = "FAILED"
    with pytest.raises(ValueError):
        result.rgb[0, 0, 0] = 0
    with pytest.raises(ValueError):
        result.rgb.setflags(write=True)
    with pytest.raises(TypeError):
        result.info["new"] = 1
    with pytest.raises(ValueError):
        result.info["nested"]["array"].setflags(write=True)

    mutable_info = result.info_copy()
    assert isinstance(mutable_info["phase_cuts"], list)
    assert isinstance(mutable_info["nested"], dict)
    mutable_info["phase_cuts"][0][0] = 42
    assert result.info["phase_cuts"][0][0] == 3

    restored = pickle.loads(pickle.dumps(result))
    assert restored.action == result.action
    assert restored.source_bytes == result.source_bytes
    assert np.array_equal(restored.rgb, result.rgb)
    assert restored.segments == result.segments
    assert not restored.rgb.flags.writeable
    assert not restored.info["nested"]["array"].flags.writeable
    with pytest.raises(ValueError):
        restored.rgb.setflags(write=True)
    with pytest.raises(ValueError):
        restored.info["nested"]["array"].setflags(write=True)


def test_single_best_result_refreezes_wrappers_and_mutable_values():
    payload = bytearray(b"payload")
    wrapped_values = []
    wrapped_info = single_best.FrozenMapping((
        ("payload", payload),
        ("values", wrapped_values),
    ))
    dc = [1, 2, 3]
    segment = single_best.SegmentSnapshot(2, 11, dc)

    result = single_best.SingleBestResult(
        "CLEAN", b"source", None, wrapped_info, (segment,)
    )
    payload[0] = ord("X")
    wrapped_values.append("changed")
    dc[0] = 9

    assert result.info["payload"] == b"payload"
    assert list(result.info["values"]) == []
    assert result.segments[0].dc_predictors == (1, 2, 3)
    with pytest.raises(TypeError, match="snapshot"):
        single_best.SingleBestResult(
            "CLEAN", b"source", None, {"mutable": object()}, ()
        )


def test_frozen_info_preserves_mapping_protocol_and_artifact_validation():
    result = single_best.SingleBestResult(
        "CLEAN", b"source", None, {"ops": 0, "nested": {"hole": 1}}, ()
    )

    assert dict(result.info.items()) == {
        "ops": 0,
        "nested": result.info["nested"],
    }
    assert dict(result.info["nested"].items()) == {"hole": 1}
    assert result.info == {"ops": 0, "nested": {"hole": 1}}
    assert canonical_json_bytes(result.info_copy()) == (
        b'{"nested":{"hole":1},"ops":0}\n'
    )


def test_recover_file_composes_calculation_and_legacy_writer(
    tmp_path, monkeypatch
):
    source = tmp_path / "input.jpg"
    source.write_bytes(b"immutable-input")
    output_root = tmp_path / "output"
    result = single_best.SingleBestResult(
        "CLEAN", b"immutable-input", None, {"ops": 0}, ()
    )
    calls = {}

    def calculate(data, **options):
        calls["data"] = data
        calls["options"] = options
        return result

    def materialize(actual, src_path, out_dir, *, quality):
        calls["materialize"] = (actual, src_path, out_dir, quality)
        return out_dir / "clean" / "input.jpg", "CLEAN", {"ops": 0}

    monkeypatch.setattr(single_best, "reconstruct_single_best", calculate)
    monkeypatch.setattr(legacy_output, "materialize_result", materialize)

    returned = engine.recover_file(
        source,
        output_root,
        quality=87,
        time_budget=None,
        resync_near=1234,
        resync_full=False,
    )

    assert calls["data"] == b"immutable-input"
    assert calls["options"] == {
        "time_budget": None,
        "resync_near": 1234,
        "resync_full": False,
    }
    assert calls["materialize"] == (result, source, output_root, 87)
    assert returned[1:] == ("CLEAN", {"ops": 0})


@pytest.mark.parametrize(
    ("action", "directory", "uses_source"),
    [
        ("RECOVERED", "recovered", False),
        ("HEADER_RECOVERED", "header_recovered", False),
        ("CLEAN", "clean", True),
        ("FAILED", "failed", True),
        ("SKIP_UNDECODABLE", "skip_undecodable", True),
        ("ERROR", "error", True),
    ],
)
def test_legacy_writer_routes_actions_and_preserves_original_bytes(
    tmp_path, action, directory, uses_source
):
    source_bytes = b"original-source-bytes"
    rgb = np.arange(12 * 16 * 3, dtype=np.uint8).reshape(12, 16, 3)
    result = single_best.SingleBestResult(
        action,
        source_bytes,
        rgb if not uses_source else None,
        {"ops": 1},
        (),
    )
    source_path = tmp_path / "input-name.jpeg"

    output, returned_action, info = legacy_output.materialize_result(
        result, source_path, tmp_path / "output", quality=95
    )

    assert output.relative_to(tmp_path / "output").as_posix() == (
        f"{directory}/input-name.jpg"
    )
    assert returned_action == action
    assert info == {"ops": 1}
    assert (output.read_bytes() == source_bytes) is uses_source


def test_selected_header_path_applies_placement_exactly_once(monkeypatch):
    decoder = SimpleNamespace(
        mcus_x=2,
        mcus_y=2,
        h=SimpleNamespace(width=16, height=16),
    )
    rgb = np.full((16, 16, 3), 64, dtype=np.uint8)
    stats = {
        "sub": 0,
        "dele": 0,
        "ins": 0,
        "resync": 0,
        "hole": 0,
        "frontier": 4,
        "phase_cuts": [],
    }
    record = (
        decoder,
        "dht",
        rgb,
        stats,
        [(0, 0, np.zeros(3, dtype=np.int64))],
        0.0,
        0.0,
    )
    calls = {"placement": 0, "callback": None}

    def no_decoder(_data):
        raise ValueError("synthetic header failure")

    def reconstruct(_data, recover_fn):
        calls["callback"] = recover_fn
        return record

    def place(_decoder, image, *_args):
        calls["placement"] += 1
        return image, {
            "shifted": 0,
            "mcu_ins": 0,
            "mcu_drop": 0,
            "row_global_passes": 0,
            "row_local_cuts": 0,
            "row_shifted": 0,
        }

    monkeypatch.setattr(single_best.jd, "Decoder", no_decoder)
    monkeypatch.setattr(
        single_best.header_hypotheses, "reconstruct", reconstruct
    )
    monkeypatch.setattr(placement, "_correct_segment_shifts", place)

    result = single_best.reconstruct_single_best(b"header-damaged")

    assert result.action == "HEADER_RECOVERED"
    assert callable(calls["callback"])
    assert calls["placement"] == 1


def test_reconstruction_modules_have_no_internal_import_cycle():
    package_dir = Path(engine.__file__).parent
    module_names = {
        "engine",
        "entropy",
        "header_hypotheses",
        "legacy_output",
        "metrics",
        "placement",
        "single_best",
    }
    graph = {name: set() for name in module_names}
    prefix = "media_recovery.reconstruction"
    for name in module_names:
        module = importlib.import_module(f"{prefix}.{name}")
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module == prefix:
                graph[name].update(
                    alias.name for alias in node.names
                    if alias.name in module_names
                )
            elif node.module and node.module.startswith(prefix + "."):
                target = node.module.rsplit(".", 1)[-1]
                if target in module_names:
                    graph[name].add(target)

    visited = set()
    active = set()

    def visit(name):
        if name in active:
            raise AssertionError(f"reconstruction import cycle: {name}")
        if name in visited:
            return
        active.add(name)
        for dependency in graph[name]:
            visit(dependency)
        active.remove(name)
        visited.add(name)

    for module_name in sorted(module_names):
        visit(module_name)

    decoder_source = Path(
        importlib.import_module(
            "media_recovery.formats.jpeg.baseline_decoder"
        ).__file__
    ).read_text(encoding="utf-8")
    decoder_tree = ast.parse(decoder_source)
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith(prefix)
        for node in ast.walk(decoder_tree)
    )
