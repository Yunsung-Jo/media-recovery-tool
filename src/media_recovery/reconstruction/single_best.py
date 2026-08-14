"""Header/normal 경로의 현행 single-best 선택과 action 판정."""
from __future__ import annotations

import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from media_recovery.formats.jpeg import baseline_decoder as jd
from media_recovery.reconstruction import entropy, header_hypotheses, placement
from media_recovery.reconstruction.metrics import (
    gray_fraction,
    undecoded_fraction,
)


_ACTIONS = frozenset({
    "RECOVERED",
    "HEADER_RECOVERED",
    "CLEAN",
    "FAILED",
    "SKIP_UNDECODABLE",
    "ERROR",
})


@dataclass(frozen=True)
class _FrozenList(Sequence[object]):
    """list의 순서와 복원 타입을 보존하는 immutable snapshot."""

    values: tuple[object, ...]

    def __len__(self) -> int:
        return len(self.values)

    def __getitem__(self, index):
        return self.values[index]


@dataclass(frozen=True, eq=False)
class FrozenMapping(Mapping[str, object]):
    """pickle 가능한 insertion-order immutable mapping."""

    _entries: tuple[tuple[str, object], ...]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, key: str) -> object:
        for item_key, value in self._entries:
            if item_key == key:
                return value
        raise KeyError(key)


def _immutable_array(value: np.ndarray) -> np.ndarray:
    """입력과 alias되지 않고 write flag도 되살릴 수 없는 array snapshot."""
    snapshot = np.array(value, copy=True, order="C")
    if snapshot.dtype.hasobject:
        raise TypeError("object dtype array는 immutable snapshot으로 저장할 수 없다")
    return np.frombuffer(
        snapshot.tobytes(order="C"), dtype=snapshot.dtype
    ).reshape(snapshot.shape)


def _freeze(value: Any) -> object:
    if isinstance(value, Mapping):
        entries = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("info mapping key는 문자열이어야 한다")
            entries.append((key, _freeze(item)))
        return FrozenMapping(tuple(entries))
    if isinstance(value, _FrozenList):
        return _FrozenList(tuple(_freeze(item) for item in value))
    if isinstance(value, list):
        return _FrozenList(tuple(_freeze(item) for item in value))
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    if isinstance(value, np.ndarray):
        return _immutable_array(value)
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if value is None or isinstance(
        value, (str, bytes, bool, int, float, np.generic)
    ):
        return value
    raise TypeError(
        f"info에 snapshot할 수 없는 값이 있다: {type(value).__name__}"
    )


def _thaw(value: object) -> object:
    if isinstance(value, FrozenMapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, _FrozenList):
        return [_thaw(item) for item in value.values]
    if isinstance(value, tuple):
        return tuple(_thaw(item) for item in value)
    if isinstance(value, np.ndarray):
        return np.array(value, copy=True)
    return value


@dataclass(frozen=True)
class SegmentSnapshot:
    """현행 `(MCU, source bit, DC)` segment의 immutable 내부 표현."""

    start_mcu: int
    start_bit: int
    dc_predictors: tuple[int, int, int]

    def __post_init__(self) -> None:
        predictors = tuple(int(value) for value in self.dc_predictors)
        if len(predictors) != 3:
            raise ValueError("segment DC predictor는 3개여야 한다")
        object.__setattr__(self, "start_mcu", int(self.start_mcu))
        object.__setattr__(self, "start_bit", int(self.start_bit))
        object.__setattr__(self, "dc_predictors", predictors)

    @classmethod
    def from_legacy(cls, segment) -> "SegmentSnapshot":
        if isinstance(segment, cls):
            return cls(
                segment.start_mcu,
                segment.start_bit,
                segment.dc_predictors,
            )
        start_mcu, start_bit, dc = segment
        return cls(start_mcu, start_bit, dc)


@dataclass(frozen=True)
class SingleBestResult:
    """출력 위치와 무관한 현행 single-best 계산 결과.

    RGB와 info/segment는 생성 시 deep snapshot으로 고정한다. legacy writer는
    `info_copy()`를 통해 기존 mutable dict 반환 계약만 경계 밖에서 복원한다.
    """

    action: str
    source_bytes: bytes = field(repr=False)
    rgb: np.ndarray | None = field(default=None, repr=False, compare=False)
    info: Mapping[str, object] = field(default_factory=dict, compare=False)
    segments: tuple[SegmentSnapshot, ...] | Sequence[object] = field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:
        if self.action not in _ACTIONS:
            raise ValueError(f"알 수 없는 reconstruction action: {self.action}")
        object.__setattr__(self, "source_bytes", bytes(self.source_bytes))
        if self.rgb is not None:
            object.__setattr__(self, "rgb", _immutable_array(self.rgb))
        frozen_info = _freeze(self.info)
        if not isinstance(frozen_info, FrozenMapping):
            raise TypeError("info는 mapping이어야 한다")
        object.__setattr__(self, "info", frozen_info)
        object.__setattr__(self, "segments", tuple(
            SegmentSnapshot.from_legacy(segment)
            for segment in self.segments
        ))

    def info_copy(self) -> dict[str, object]:
        """기존 façade 반환용 mutable deep copy."""
        thawed = _thaw(self.info)
        assert isinstance(thawed, dict)
        return thawed

    def __reduce__(self):
        """pickle round-trip도 constructor의 immutable snapshot을 다시 적용한다."""
        segments = tuple(
            (
                segment.start_mcu,
                segment.start_bit,
                np.asarray(segment.dc_predictors, dtype=np.int64),
            )
            for segment in self.segments
        )
        rgb = None if self.rgb is None else np.array(self.rgb, copy=True)
        return (
            type(self),
            (self.action, self.source_bytes, rgb, self.info_copy(), segments),
        )


def _spatial_changed(stats: Mapping[str, object]) -> bool:
    return bool(
        int(stats.get("shifted", 0))
        or int(stats.get("row_global_passes", 0))
        or int(stats.get("row_local_cuts", 0))
        or int(stats.get("row_shifted", 0))
    )


def reconstruct_single_best(
    data: bytes,
    *,
    time_budget=90.0,
    resync_near=300000,
    resync_full=True,
) -> SingleBestResult:
    """현행 header/normal single-best를 선택하되 어떤 경로에도 쓰지 않는다."""
    source_bytes = bytes(data)

    # Header 후보 비교는 historical unshifted render로 수행하고 선택된 후보만
    # _header_result에서 정확히 한 번 placement한다.
    header_recover = lambda decoder: entropy.recover(
        decoder,
        time_budget=time_budget,
        resync_near=resync_near,
        resync_full=resync_full,
        apply_shift=False,
    )

    def _header_result(record, elapsed: float) -> SingleBestResult:
        decoder, fix, rgb, stats, segments, gray_plain, undec_plain = record
        shift_started = time.monotonic()
        rgb, shift_stats = placement._correct_segment_shifts(
            decoder,
            rgb,
            segments,
            stats.get("frontier", decoder.mcus_x * decoder.mcus_y),
            stats.get("phase_cuts"),
        )
        stats.update(shift_stats)
        elapsed += time.monotonic() - shift_started
        info = {
            "gray_before": gray_plain,
            "gray_after": gray_fraction(rgb),
            "undec_before": undec_plain,
            "undec_after": undecoded_fraction(rgb),
            "recover_sec": elapsed,
            "ops": (
                stats["sub"] + stats["dele"] + stats["ins"]
                + stats["resync"]
            ),
            "width": decoder.h.width,
            "height": decoder.h.height,
            "mcus": decoder.mcus_x * decoder.mcus_y,
            "header_fix": fix,
            **stats,
        }
        return SingleBestResult(
            "HEADER_RECOVERED", source_bytes, rgb, info, segments
        )

    decoder = None
    try:
        decoder = jd.Decoder(source_bytes)
    except Exception:
        pass
    if decoder is None:
        started = time.monotonic()
        record = header_hypotheses.reconstruct(
            source_bytes, header_recover
        )
        if record is not None:
            return _header_result(
                record, time.monotonic() - started
            )
        return SingleBestResult(
            "SKIP_UNDECODABLE", source_bytes, None, {}, ()
        )

    triggered = (
        header_hypotheses.opening_probe(decoder)
        < header_hypotheses.floor_of(decoder.mcus_x * decoder.mcus_y)
    )

    decoder.decode_full()
    plain_rgb = decoder.to_rgb()
    before = gray_fraction(plain_rgb)
    before_undec = undecoded_fraction(plain_rgb)
    started = time.monotonic()
    rgb, stats, segments = entropy.recover(
        decoder,
        time_budget=time_budget,
        resync_near=resync_near,
        resync_full=resync_full,
        apply_shift=False,
    )
    unshifted_after_undec = undecoded_fraction(rgb)

    if triggered:
        header_started = time.monotonic()
        record = header_hypotheses.reconstruct(
            source_bytes, header_recover
        )
        if (
            record is not None
            and undecoded_fraction(record[2])
            < unshifted_after_undec - 0.01
        ):
            return _header_result(
                record, time.monotonic() - header_started
            )

    rgb, shift_stats = placement._correct_segment_shifts(
        decoder,
        rgb,
        segments,
        stats.get("frontier", decoder.mcus_x * decoder.mcus_y),
        stats.get("phase_cuts"),
    )
    stats.update(shift_stats)
    recover_sec = time.monotonic() - started
    after = gray_fraction(rgb)
    after_undec = undecoded_fraction(rgb)

    ops = (
        stats["sub"] + stats["dele"] + stats["ins"] + stats["resync"]
    )
    spatial_changed = _spatial_changed(stats)
    info = {
        "gray_before": before,
        "gray_after": after,
        "undec_before": before_undec,
        "undec_after": after_undec,
        "recover_sec": recover_sec,
        "ops": ops,
        "spatial_changed": int(spatial_changed),
        "width": decoder.h.width,
        "height": decoder.h.height,
        "mcus": decoder.mcus_x * decoder.mcus_y,
        **stats,
    }
    if ops == 0 and not spatial_changed and before < 0.02:
        action = "CLEAN"
    elif ops == 0 and not spatial_changed and stats["hole"] >= 1:
        action = "FAILED"
    else:
        action = "RECOVERED"
    return SingleBestResult(action, source_bytes, rgb, info, segments)
