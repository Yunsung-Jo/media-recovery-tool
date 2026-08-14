"""T-0005에서 책임을 옮기기 전에 고정한 합성 reconstruction 동작."""
from __future__ import annotations

import hashlib
import inspect
import io
import json
import csv
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from media_recovery.cli import reconstruct as reconstruct_cli
from media_recovery.formats.jpeg import baseline_decoder as jd
from media_recovery.reconstruction import engine
from media_recovery.reconstruction import placement


BASELINE_PATH = (
    Path(__file__).parent / "fixtures" / "reconstruction" /
    "t0005-engine-baseline.json"
)


def _encode(rgb: np.ndarray, *, quality: int = 92) -> bytes:
    output = io.BytesIO()
    Image.fromarray(rgb).save(
        output, format="JPEG", quality=quality, subsampling=1
    )
    return output.getvalue()


def _textured_image(height: int, width: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    xx, yy = np.meshgrid(
        np.linspace(0, 255, width), np.linspace(0, 255, height)
    )
    base = np.stack([xx, yy, (xx + yy) / 2], axis=-1)
    base += rng.normal(0, 25, (height, width, 3))
    return np.clip(base, 0, 255).astype(np.uint8)


def _corrupt_entropy(data: bytes, count: int, seed: int) -> bytes:
    header = jd.parse_header(data)
    last_eoi = data.rfind(b"\xff\xd9")
    damaged = bytearray(data)
    rng = np.random.default_rng(seed)
    start = header.scan_start + (last_eoi - header.scan_start) * 2 // 5
    for offset in range(count):
        damaged[start + offset] = int(rng.integers(0, 256))
    return bytes(damaged)


def _strip_dht(data: bytes) -> bytes:
    header = jd.parse_header(data)
    damaged = bytearray(data)
    index = 2
    while index < header.scan_start - 1:
        if damaged[index] == 0xFF and damaged[index + 1] == 0xC4:
            damaged[index + 1] = 0x00
        index += 1
    return bytes(damaged)


def _truncate_for_failed(data: bytes) -> bytes:
    header = jd.parse_header(data)
    last_eoi = data.rfind(b"\xff\xd9")
    return data[
        :header.scan_start + (last_eoi - header.scan_start) * 7 // 10
    ]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {
            key: _stable(item)
            for key, item in sorted(value.items())
            if key != "recover_sec"
        }
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    return value


def _case_snapshot(
    source_dir: Path,
    output_dir: Path,
    name: str,
    data: bytes,
) -> dict:
    source = source_dir / f"{name}.jpg"
    source.write_bytes(data)
    output, action, info = engine.recover_file(
        source, output_dir, quality=95, time_budget=0
    )
    return {
        "action": action,
        "relative_path": output.relative_to(output_dir).as_posix(),
        "output_sha256": _sha256(output.read_bytes()),
        "preserved_source": output.read_bytes() == data,
        "info": _stable(info),
    }


def _build_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    source_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    output_dir.mkdir()

    clean = _encode(_textured_image(96, 128, 100))
    entropy = _corrupt_entropy(
        _encode(_textured_image(96, 128, 4)), 32, 99
    )
    header = _strip_dht(_encode(_textured_image(96, 96, 5)))
    failed = _truncate_for_failed(
        _encode(_textured_image(64, 64, 7))
    )
    undecodable = b"\xff\xd8 not a decodable jpeg \xff\xd9"

    routes = {
        "clean": _case_snapshot(
            source_dir, output_dir, "clean", clean
        ),
        "entropy_recovered": _case_snapshot(
            source_dir, output_dir, "entropy", entropy
        ),
    }

    def spatial_only(_decoder, rgb, *_args, **_kwargs):
        return np.flip(rgb, axis=1).copy(), {
            "shifted": 0,
            "mcu_ins": 1,
            "mcu_drop": 1,
            "row_global_passes": 1,
            "row_global_events": 1,
            "row_global_changes": 1,
            "row_global_plan": ((1, 1),),
        }

    with monkeypatch.context() as scoped:
        scoped.setattr(placement, "_correct_segment_shifts", spatial_only)
        routes["spatial_recovered"] = _case_snapshot(
            source_dir, output_dir, "spatial", clean
        )

    routes.update({
        "header_recovered": _case_snapshot(
            source_dir, output_dir, "header", header
        ),
        "failed": _case_snapshot(
            source_dir, output_dir, "failed", failed
        ),
        "skip_undecodable": _case_snapshot(
            source_dir, output_dir, "skip", undecodable
        ),
    })

    decoder = jd.Decoder(entropy)
    rgb, stats, segments = engine.recover(decoder, time_budget=0)
    recover_api = {
        "rgb_shape": list(rgb.shape),
        "rgb_dtype": str(rgb.dtype),
        "rgb_sha256": _sha256(rgb.tobytes()),
        "stats": _stable(stats),
        "segments": [
            [int(mcu), int(bit), [int(value) for value in dc]]
            for mcu, bit, dc in segments
        ],
    }

    error_source = source_dir / "error.jpg"
    error_bytes = b"\xff\xd8worker-error\xff\xd9"
    error_source.write_bytes(error_bytes)
    with monkeypatch.context() as scoped:
        def fail(*_args, **_kwargs):
            raise RuntimeError("synthetic worker failure")

        scoped.setattr(reconstruct_cli, "recover_file", fail)
        name, action, info, error = reconstruct_cli._work(
            error_source,
            output_dir,
            quality=95,
            time_budget=None,
            near=300000,
            full=True,
        )
    error_output = output_dir / "error" / error_source.name
    worker_error = {
        "filename": name,
        "action": action,
        "info": info,
        "error": error,
        "relative_path": error_output.relative_to(output_dir).as_posix(),
        "output_sha256": _sha256(error_output.read_bytes()),
        "preserved_source": error_output.read_bytes() == error_bytes,
    }

    return {
        "routes": routes,
        "recover_api": recover_api,
        "worker_error": worker_error,
    }


def test_public_facade_signatures_are_characterized():
    assert str(inspect.signature(engine.recover_file)) == (
        "(src_path: 'Path', out_dir: 'Path', quality: 'int' = 95, "
        "time_budget=90.0, resync_near=300000, resync_full=True)"
    )
    assert str(inspect.signature(engine.recover)) == (
        "(dec, maxW=900, max_ops=300, time_budget=90.0, "
        "resync_near=300000, resync_full=True, apply_shift=True)"
    )
    assert str(inspect.signature(engine.recover_bytes)) == "(data: 'bytes')"
    assert str(inspect.signature(engine.gray_fraction)) == (
        "(rgb: 'np.ndarray') -> 'float'"
    )
    assert str(inspect.signature(engine.undecoded_fraction)) == (
        "(rgb: 'np.ndarray') -> 'float'"
    )


def test_t0005_pre_refactor_snapshot(tmp_path, monkeypatch):
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    actual = _build_snapshot(tmp_path, monkeypatch)

    normalized = json.dumps(
        actual, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    assert _sha256(normalized) == baseline["normalized_snapshot_sha256"]
    assert {
        key: value["output_sha256"]
        for key, value in actual["routes"].items()
    } == baseline["output_sha256"]
    assert (
        actual["worker_error"]["output_sha256"]
        == baseline["worker_error_output_sha256"]
    )
    assert {
        key: value["action"] for key, value in actual["routes"].items()
    } == {
        "clean": "CLEAN",
        "entropy_recovered": "RECOVERED",
        "spatial_recovered": "RECOVERED",
        "header_recovered": "HEADER_RECOVERED",
        "failed": "FAILED",
        "skip_undecodable": "SKIP_UNDECODABLE",
    }
    assert actual["worker_error"]["action"] == "ERROR"


def test_cli_single_and_spawn_workers_are_equivalent(tmp_path):
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    clean = _encode(_textured_image(96, 128, 100))
    entropy = _corrupt_entropy(
        _encode(_textured_image(96, 128, 4)), 32, 99
    )
    header = _strip_dht(_encode(_textured_image(96, 96, 5)))
    (input_dir / "clean.jpg").write_bytes(clean)
    (input_dir / "entropy.jpg").write_bytes(entropy)
    (input_dir / "header.jpg").write_bytes(header)

    runs = {}
    for jobs in (1, 2):
        output_dir = tmp_path / f"output-j{jobs}"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "media_recovery",
                "reconstruct",
                str(input_dir),
                "-o",
                str(output_dir),
                "--time-budget",
                "0",
                "-j",
                str(jobs),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        with (output_dir / "report.csv").open(
            newline="", encoding="utf-8"
        ) as report_file:
            rows = list(csv.DictReader(report_file))
        for row in rows:
            row["recover_sec"] = "<normalized>"
        rows.sort(key=lambda row: row["filename"])
        output_hashes = {
            path.relative_to(output_dir).as_posix(): _sha256(
                path.read_bytes()
            )
            for path in output_dir.rglob("*.jpg")
        }
        stable_console = [
            line.strip()
            for line in completed.stdout.splitlines()
            if line.strip().startswith((
                "CLEAN:",
                "ERROR:",
                "FAILED:",
                "HEADER_RECOVERED:",
                "RECOVERED:",
                "SKIP_UNDECODABLE:",
                "MCU 밀림 보정:",
                "헤더 복구:",
                "RECOVERED undec 평균:",
                "악화(",
                "MCU 대역별",
                "<120:",
            ))
        ]
        runs[jobs] = {
            "fieldnames": list(rows[0]),
            "rows": rows,
            "output_hashes": output_hashes,
            "console": stable_console,
        }
        normalized = json.dumps(
            runs[jobs],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        assert _sha256(normalized) == (
            baseline["cli_normalized_snapshot_sha256"]
        )

    assert runs[1] == runs[2]
    assert [row["action"] for row in runs[1]["rows"]] == [
        "CLEAN",
        "RECOVERED",
        "HEADER_RECOVERED",
    ]
    assert any(
        line.startswith("헤더 복구: 1개") for line in runs[1]["console"]
    )


def test_cli_all_action_and_placement_summaries_match_baseline(
    tmp_path, monkeypatch, capsys
):
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    actions = {
        "clean": "CLEAN",
        "error": "ERROR",
        "failed": "FAILED",
        "header": "HEADER_RECOVERED",
        "recovered": "RECOVERED",
        "skip": "SKIP_UNDECODABLE",
    }
    for name in actions:
        (input_dir / f"{name}.jpg").write_bytes(b"synthetic")

    common_info = {
        "gray_before": 0.2,
        "gray_after": 0.1,
        "undec_before": 0.2,
        "undec_after": 0.1,
        "recover_sec": 1.25,
        "ops": 1,
        "sub": 1,
        "dele": 0,
        "ins": 0,
        "resync": 0,
        "hole": 0,
        "mcus": 100,
        "width": 160,
        "height": 80,
        "shifted": 0,
        "mcu_ins": 0,
        "mcu_drop": 0,
        "shift_margin": 0.0,
        "shift_reject": 0,
        "spatial_changed": 0,
        "row_global_passes": 0,
        "row_global_changes": 0,
        "row_local_cuts": 0,
    }

    def work(path, *_args, **_kwargs):
        action = actions[path.stem]
        if action in {"ERROR", "SKIP_UNDECODABLE"}:
            error = "synthetic worker failure" if action == "ERROR" else None
            return path.name, action, {}, error
        info = dict(common_info)
        if action == "CLEAN":
            info.update(ops=0, sub=0)
        elif action == "FAILED":
            info.update(ops=0, sub=0, hole=1)
        elif action == "HEADER_RECOVERED":
            info["header_fix"] = "dht"
        elif action == "RECOVERED":
            info.update(
                spatial_changed=1,
                mcu_ins=5,
                mcu_drop=5,
                row_global_passes=2,
                row_global_changes=3,
            )
        return path.name, action, info, None

    class QuietBar:
        def __init__(self, *_args, **_kwargs):
            pass

        def update(self, *_args):
            pass

        def close(self):
            pass

        @staticmethod
        def write(message):
            print(message)

    monkeypatch.setattr(reconstruct_cli, "_work", work)
    monkeypatch.setattr(reconstruct_cli, "tqdm", QuietBar)
    reconstruct_cli.main([
        str(input_dir),
        "-o",
        str(output_dir),
        "--time-budget",
        "0",
        "-j",
        "1",
    ])

    with (output_dir / "report.csv").open(
        newline="", encoding="utf-8"
    ) as report_file:
        rows = list(csv.DictReader(report_file))
    for row in rows:
        row["recover_sec"] = "<normalized>" if row["recover_sec"] else ""
    console = [line.strip() for line in capsys.readouterr().out.splitlines()]
    stable_prefixes = (
        "[ERROR]",
        "CLEAN:",
        "ERROR:",
        "FAILED:",
        "HEADER_RECOVERED:",
        "RECOVERED:",
        "SKIP_UNDECODABLE:",
        "MCU 밀림 보정:",
        "헤더 복구:",
        "RECOVERED undec 평균:",
        "악화(",
        "MCU 대역별",
        "<120:",
    )
    snapshot = {
        "fieldnames": list(rows[0]),
        "rows": rows,
        "console": [
            line for line in console if line.startswith(stable_prefixes)
        ],
    }
    normalized = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert _sha256(normalized) == (
        baseline["cli_summary_snapshot_sha256"]
    )
    assert {row["action"] for row in rows} == set(actions.values())
    assert any(
        line.startswith("MCU 밀림 보정: 1개 파일")
        for line in snapshot["console"]
    )
