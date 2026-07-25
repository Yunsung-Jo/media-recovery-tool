"""바이트 오라클 + 세그먼트 resync 기반 JPEG 복구 엔진.

손상된 baseline JPEG의 엔트로피 스트림은 바이트 손상으로 디싱크되어, 표준 디코더는
손상 지점에서 회색(스캔 중단) 또는 깨진(어긋난 채 진행) 출력을 낸다. 이 엔진은
`carver.jpegdecode`의 비트 단위 디코더로 디싱크 지점을 정확히 짚고, 각 지점에서:

 1) 바이트 편집(치환/삭제/삽입): 단일바이트 손상을 복구(정렬 보존, 밀림 없음).
 2) resync-skip: 재개 비트위치를 넓게 탐색해 다중바이트 손상/구멍을 건너뜀. db≈0인
    masking(가짜 복구)은 거부한다.
 3) MCU 위상 보정: 최종 선택된 재동기 세그먼트를 고정 크기 캔버스에서 삽입·삭제해
    행 밀림을 맞춘다. DC 재설정으로 생긴 밝기·색 캐스트는 별도 한계로 남는다.

좌→우로 처리하므로 편집·세그먼트는 항상 현재 지점 이후에만 일어나 이전 비트위치가 안전하다.
복구 가능한 영역만 복구하고, 물리적으로 소실된 영역은 회색으로 남긴다(가짜 채움 금지).
"""
from __future__ import annotations
import io
import itertools
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numba import njit

from carver import jpegdecode as jd

DC_BOUND, AC_BOUND = 1400, 6000   # 계수 dequant 오버플로 경계(디싱크 탐지)
_ZZ = jd.ZIGZAG


def gray_fraction(rgb: np.ndarray) -> float:
    """평탄+무채색(디코더가 채운 회색) 픽셀 비율."""
    a = rgb.astype(np.int16)
    h, w, _ = a.shape
    if h == 0 or w == 0:
        return 1.0
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    achroma = (np.abs(r - g) < 10) & (np.abs(g - b) < 10) & (np.abs(r - b) < 10)
    flat = np.ones((h, w), bool)
    flat[:, :-1] &= np.abs(np.diff(a, axis=1)).sum(2) < 6
    flat[:-1, :] &= np.abs(np.diff(a, axis=0)).sum(2) < 6
    return float((achroma & flat).mean())


def undecoded_fraction(rgb: np.ndarray) -> float:
    """디코더가 채우지 못한 미복구 회색(RGB≈128 + 평탄) 픽셀 비율.
    gray_fraction과 달리 재동기된 무채색(Cb/Cr DC=0) 콘텐츠를 회색으로 세지 않으므로,
    DC=0 리셋 복구의 '진짜' 복구율을 잰다(gray_fraction은 무채색을 회색으로 과다 집계)."""
    a = rgb.astype(np.int16)
    h, w, _ = a.shape
    if h == 0 or w == 0:
        return 1.0
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    near = (np.abs(r - 128) < 6) & (np.abs(g - 128) < 6) & (np.abs(b - 128) < 6)
    flat = np.ones((h, w), bool)
    flat[:, :-1] &= np.abs(np.diff(a, axis=1)).sum(2) < 6
    flat[:-1, :] &= np.abs(np.diff(a, axis=0)).sum(2) < 6
    return float((near & flat).mean())


def _robust_z(values: np.ndarray, scale_floor: float) -> np.ndarray:
    """Median/MAD z-score with a floor and cap for nearly-flat samples."""
    values = np.asarray(values, dtype=np.float64)
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med)))
    scale = max(1.4826 * mad, scale_floor)
    return np.clip((values - med) / scale, -20.0, 20.0)


def _mcu_green_tiles(rgb: np.ndarray, mcus_x: int, mcus_y: int,
                     mcu_w: int, mcu_h: int) -> np.ndarray:
    """Return edge-extended green MCU tiles in flat scan order."""
    plane = np.full((mcus_y * mcu_h, mcus_x * mcu_w), 128, np.uint8)
    h = min(rgb.shape[0], plane.shape[0])
    w = min(rgb.shape[1], plane.shape[1])
    plane[:h, :w] = rgb[:h, :w, 1]
    if h and w < plane.shape[1]:
        plane[:h, w:] = plane[:h, w - 1:w]
    if h and h < plane.shape[0]:
        plane[h:] = plane[h - 1:h]
    return np.ascontiguousarray(
        plane.reshape(mcus_y, mcu_h, mcus_x, mcu_w)
        .transpose(0, 2, 1, 3)).reshape(mcus_x * mcus_y, mcu_h, mcu_w)


def _mcu_edge_arrays(rgb: np.ndarray, mcus_x: int, mcus_y: int,
                     mcu_w: int, mcu_h: int):
    """Return left/right green-channel edge strips for scan-ordered MCU tiles.

    A single channel is deliberate: a resync DC reset can add a large RGB color
    offset, while within-segment edge shape remains useful.  Partial right and
    bottom MCUs use edge extension for scoring; decoder-gray padding would
    otherwise manufacture a strong false wrap at column zero.
    """
    edge_w = min(2, mcu_w)
    tiles = _mcu_green_tiles(rgb, mcus_x, mcus_y, mcu_w, mcu_h)
    left = np.ascontiguousarray(tiles[:, :, :edge_w])
    right = np.ascontiguousarray(tiles[:, :, -edge_w:])
    return left, right


def _estimate_mcu_phase(left: np.ndarray, right: np.ndarray,
                        start: int, end: int, mcus_x: int,
                        min_pairs: int = 5):
    """Estimate the original scan-row phase of one packed resync segment.

    Consecutive decoded MCUs are grouped by the *current* boundary column.  In
    the correct source raster, exactly one residue class is a row wrap rather
    than a horizontal neighbor.  Its edge profiles are repeatedly unrelated;
    the returned phase is the flat-MCU offset which moves that class back to
    the outer row boundary.

    Absolute brightness steps are removed per pair, and vertical-gradient
    decorrelation supplies a second DC-insensitive signal.  ``None`` means the
    segment is too short or the phase is not sufficiently distinct from zero.
    """
    if end - start <= mcus_x * min_pairs:
        return None
    source = np.arange(start, end - 1, dtype=np.int64)

    l_edge = right[source].astype(np.int16)
    r_edge = left[source + 1].astype(np.int16)
    delta = r_edge - l_edge
    raw_step = np.mean(np.abs(delta), axis=(1, 2))
    center = np.median(delta, axis=(1, 2))
    residual = np.median(
        np.abs(delta - center[:, np.newaxis, np.newaxis]), axis=(1, 2))

    l_grad = np.diff(l_edge.mean(axis=2, dtype=np.float32), axis=1)
    r_grad = np.diff(r_edge.mean(axis=2, dtype=np.float32), axis=1)
    numer = np.sum(l_grad * r_grad, axis=1)
    denom = np.sqrt(np.sum(l_grad * l_grad, axis=1)
                    * np.sum(r_grad * r_grad, axis=1))
    corr = np.zeros_like(numer, dtype=np.float32)
    valid_denom = denom > 1e-6
    corr[valid_denom] = numer[valid_denom] / denom[valid_denom]
    both_flat = (~valid_denom
                 & (np.max(np.abs(l_grad), axis=1) < 1e-6)
                 & (np.max(np.abs(r_grad), axis=1) < 1e-6))
    corr[both_flat] = 1.0
    decor = 1.0 - corr

    pair_count = source.size
    first_group = (start + 1) % mcus_x
    counts = np.zeros(mcus_x, dtype=np.int64)
    resid_group = np.zeros(mcus_x, dtype=np.float64)
    decor_group = np.zeros(mcus_x, dtype=np.float64)
    raw_group = np.zeros(mcus_x, dtype=np.float64)
    for column in range(mcus_x):
        first = (column - first_group) % mcus_x
        if first >= pair_count:
            continue
        column_raw = raw_step[first::mcus_x]
        counts[column] = column_raw.size
        if column_raw.size < min_pairs:
            continue
        raw_group[column] = float(np.median(column_raw))
        resid_group[column] = float(np.median(residual[first::mcus_x]))
        decor_group[column] = float(np.median(decor[first::mcus_x]))
    usable = counts >= min_pairs
    if usable.sum() < 3 or not usable[0]:
        return None

    score = np.full(mcus_x, -np.inf, dtype=np.float64)
    score[usable] = (
        _robust_z(resid_group[usable], 0.25)
        + _robust_z(decor_group[usable], 0.02)
    )
    best = int(np.argmax(score))

    columns = np.arange(mcus_x)
    circular_distance = np.minimum(
        (columns - best) % mcus_x, (best - columns) % mcus_x)
    competitors = usable & (circular_distance > 2)
    competitor = (float(np.max(score[competitors]))
                  if competitors.any() else float('-inf'))
    best_score = float(score[best])
    zero_score = float(score[0])
    margin = (best_score - competitor if best == 0 else
              min(best_score - zero_score, best_score - competitor))

    raw_median = float(np.median(raw_group[usable]))
    raw_baseline = raw_median if best == 0 else max(raw_median, raw_group[0])
    raw_distinct = (raw_group[best] >= raw_baseline * 1.5
                    and raw_group[best] - raw_baseline >= 2.0)

    # Movement is intentionally aggressive (shift removal is the primary
    # goal), but a natural vertical edge must not masquerade as a repeated row
    # wrap.  Require absolute edge separation in addition to the two
    # DC-insensitive signals.  A flat zero-phase segment therefore inherits the
    # previous trusted phase instead of spuriously resetting it.
    confident = (
        best_score >= 3.0
        and margin >= 0.5
        and decor_group[best] >= 0.45
        and raw_distinct
    )
    return {
        'phase': int((-best) % mcus_x),
        'wrap_column': best,
        'score': best_score,
        'margin': float(margin),
        'raw_ratio': float(raw_group[best] / max(raw_baseline, 1e-6)),
        'pairs': int(counts[best]),
        'confident': bool(confident),
    }


def _unwrap_phase(phase: int, previous: int, period: int) -> int:
    """Choose the whole-row-equivalent offset nearest the previous segment."""
    q = (previous - phase) // period
    candidates = (phase + (q - 1) * period,
                  phase + q * period,
                  phase + (q + 1) * period,
                  phase + (q + 2) * period)
    return min(candidates, key=lambda value: (
        abs(value - previous), abs(value), value))


def _adaptive_phase_estimate(left: np.ndarray, right: np.ndarray,
                             start: int, end: int, mcus_x: int):
    """Estimate even short repair bands without pretending they are anchors.

    Five repeated rows remain the preferred evidence.  When an image contains
    many closely spaced repairs, however, a one-to-four-row band can still
    carry the only usable left/right phase clue.  Such estimates are returned
    as relaxed evidence; ``_correct_segment_shifts`` never lets them choose the
    whole-row representative used by later strict bands.
    """
    repeats = (max(0, end - start) - 1) // mcus_x
    if repeats < 1:
        return None
    return _estimate_mcu_phase(
        left, right, start, end, mcus_x,
        min_pairs=max(1, min(5, repeats)))


def _boundary_signature(left: np.ndarray, right: np.ndarray,
                        start: int, end: int, mcus_x: int,
                        min_support: int = 5):
    """Return a DC-insensitive, per-boundary-column span signature.

    Unlike the single strongest wrap column, the complete width profile can
    distinguish a true cyclic left/right displacement from one persistent
    natural vertical edge.  Each feature is standardized across the image
    width, so circular dot products are correlations.
    """
    if end - start <= mcus_x * min_support:
        return None
    source = np.arange(start, end - 1, dtype=np.int64)
    groups = (source + 1) % mcus_x
    counts = np.bincount(groups, minlength=mcus_x)
    support = int(np.min(counts))
    if support < min_support:
        return None

    first = right[source].astype(np.int16)
    second = left[source + 1].astype(np.int16)
    delta = second - first
    raw = np.mean(np.abs(delta), axis=(1, 2))
    center = np.median(delta, axis=(1, 2))
    residual = np.median(
        np.abs(delta - center[:, np.newaxis, np.newaxis]), axis=(1, 2))

    first_grad = np.diff(first.mean(axis=2, dtype=np.float32), axis=1)
    second_grad = np.diff(second.mean(axis=2, dtype=np.float32), axis=1)
    numer = np.sum(first_grad * second_grad, axis=1)
    denom = np.sqrt(np.sum(first_grad * first_grad, axis=1)
                    * np.sum(second_grad * second_grad, axis=1))
    corr = np.zeros_like(numer, dtype=np.float32)
    valid = denom > 1e-6
    corr[valid] = numer[valid] / denom[valid]
    both_flat = (~valid
                 & (np.max(np.abs(first_grad), axis=1) < 1e-6)
                 & (np.max(np.abs(second_grad), axis=1) < 1e-6))
    corr[both_flat] = 1.0
    decor = 1.0 - corr

    features = np.empty((mcus_x, 3), dtype=np.float64)
    for column in range(mcus_x):
        select = groups == column
        features[column] = (
            np.log1p(float(np.median(raw[select]))),
            float(np.median(residual[select])),
            float(np.median(decor[select])),
        )
    mean = features.mean(axis=0)
    scale = np.maximum(features.std(axis=0), 1e-6)
    return {
        'profile': (features - mean) / scale,
        'support': support,
    }


def _signature_correlation(first, second, mcus_x: int,
                           lost_mcu_hint=None):
    """Correlate two complete boundary profiles over every cyclic phase."""
    a = first['profile']
    b = second['profile']
    scores = np.asarray([
        np.mean(a * np.roll(b, delta, axis=0))
        for delta in range(mcus_x)
    ], dtype=np.float64)
    peak_delta = int(np.argmax(scores))
    peak = float(scores[peak_delta])
    columns = np.arange(mcus_x)
    distance = np.minimum(
        (columns - peak_delta) % mcus_x,
        (peak_delta - columns) % mcus_x)
    outside = distance > 2
    competitor = float(np.max(scores[outside])) if outside.any() else peak
    margin = peak - competitor

    selected = peak_delta
    hint_used = False
    if lost_mcu_hint is not None and np.isfinite(lost_mcu_hint):
        near_peak = np.flatnonzero(scores >= peak - 0.03)
        target = float(lost_mcu_hint) % mcus_x

        def hint_distance(delta):
            direct = abs(float(delta) - target)
            return min(direct, mcus_x - direct)

        hinted = min(near_peak, key=lambda delta: (
            hint_distance(delta), -scores[delta], int(delta)))
        if hint_distance(hinted) <= 2.0:
            selected = int(hinted)
            hint_used = selected != peak_delta

    signed = selected if selected <= mcus_x // 2 else selected - mcus_x
    support = min(int(first['support']), int(second['support']))
    strong = (
        support >= 20 and peak >= 0.20 and margin >= 0.05
    ) or (
        support >= 20 and peak >= 0.30 and hint_used
        and float(scores[selected]) >= peak - 0.03
    )
    auxiliary = (
        8 <= support < 20 and peak >= 0.60 and margin >= 0.05
    )
    return {
        'delta': int(signed),
        'phase_delta': int(selected),
        'score': float(scores[selected]),
        'peak': peak,
        'margin': float(margin),
        'support': support,
        'strong': bool(strong),
        'auxiliary': bool(auxiliary),
        'hint_used': bool(hint_used),
    }


def _scatter_mcu_segments(rgb: np.ndarray, mcus_x: int, mcus_y: int,
                          mcu_w: int, mcu_h: int,
                          spans: list[tuple[int, int]], offsets: list):
    """Scatter scan-ordered MCU spans to corrected flat positions.

    Later spans win overlaps, so a negative offset deletes the earlier span's
    tail nearest the resync boundary.  Positive offsets leave decoder-gray MCU
    gaps.  The canvas size never changes and source MCU order is never reversed
    or duplicated.
    """
    total = mcus_x * mcus_y
    padded_shape = (mcus_y * mcu_h, mcus_x * mcu_w, 3)
    if rgb.shape == padded_shape:
        padded = rgb
    else:
        padded = np.full(padded_shape, 128, np.uint8)
        h = min(rgb.shape[0], padded.shape[0])
        w = min(rgb.shape[1], padded.shape[1])
        padded[:h, :w] = rgb[:h, :w]
    source_tiles = padded.reshape(
        mcus_y, mcu_h, mcus_x, mcu_w, 3).transpose(0, 2, 1, 3, 4)

    output = np.full_like(padded, 128)
    output_tiles = output.reshape(
        mcus_y, mcu_h, mcus_x, mcu_w, 3).transpose(0, 2, 1, 3, 4)
    inserted, dropped = _mcu_placement_stats(total, spans, offsets)
    for (start, end), offset in zip(spans, offsets):
        if offset is None:
            continue
        # Bound the temporary advanced-indexing copy for large photographs.
        for chunk_start in range(start, end, 4096):
            source = np.arange(
                chunk_start, min(end, chunk_start + 4096), dtype=np.int64)
            target = source + int(offset)
            inside = (target >= 0) & (target < total)
            source = source[inside]
            target = target[inside]
            if source.size == 0:
                continue
            sy, sx = np.divmod(source, mcus_x)
            ty, tx = np.divmod(target, mcus_x)
            output_tiles[ty, tx] = source_tiles[sy, sx]
    return output[:rgb.shape[0], :rgb.shape[1]], inserted, dropped


def _profile_gradient_correlation(first: np.ndarray,
                                  second: np.ndarray) -> tuple[float, int]:
    """Correlate active horizontal gradients without trusting flat pixels."""
    first = np.asarray(first, dtype=np.float32)
    second = np.asarray(second, dtype=np.float32)
    active = (np.abs(first) + np.abs(second)) > 8.0
    support = int(np.count_nonzero(active))
    if support < 64:
        return -1.0, support
    first = first[active]
    second = second[active]
    first -= np.mean(first)
    second -= np.mean(second)
    denominator = float(np.sqrt(
        np.dot(first, first) * np.dot(second, second)))
    if denominator <= 1e-6:
        return -1.0, support
    return float(np.dot(first, second) / denominator), support


@njit(cache=True)
def _gradient_phase_scores(first: np.ndarray, second: np.ndarray,
                           phases: np.ndarray, stride: int):
    """Evaluate all exact active-gradient correlations without roll copies."""
    length, channels = first.shape
    scores = np.full(phases.size, -1.0, np.float32)
    supports = np.zeros(phases.size, np.int32)
    for phase_index in range(phases.size):
        shift = int(phases[phase_index]) * stride
        support = 0
        first_sum = 0.0
        second_sum = 0.0
        for x in range(length):
            shifted_x = (x - shift) % length
            for channel in range(channels):
                a = first[x, channel]
                b = second[shifted_x, channel]
                if abs(a) + abs(b) > 8.0:
                    support += 1
                    first_sum += a
                    second_sum += b
        supports[phase_index] = support
        if support < 64:
            continue
        first_mean = first_sum / support
        second_mean = second_sum / support
        numerator = 0.0
        first_square = 0.0
        second_square = 0.0
        for x in range(length):
            shifted_x = (x - shift) % length
            for channel in range(channels):
                a = first[x, channel]
                b = second[shifted_x, channel]
                if abs(a) + abs(b) > 8.0:
                    a -= first_mean
                    b -= second_mean
                    numerator += a * b
                    first_square += a * a
                    second_square += b * b
        denominator = np.sqrt(first_square * second_square)
        if denominator > 1e-6:
            scores[phase_index] = numerator / denominator
    return scores, supports


def _canonical_row_phases(phases: np.ndarray, mcus_x: int) -> np.ndarray:
    """Choose the minimum-loss representative of horizontal row phases."""
    half = mcus_x // 2
    return ((np.asarray(phases, dtype=np.int32) + half) % mcus_x - half)


def _infer_cyclic_mcu_row_plan(rgb: np.ndarray, mcus_x: int,
                               mcu_w: int, mcu_h: int,
                               row_multiple: int = 1,
                               max_passes: int = 3):
    """Infer per-row phases while using cyclic movement only as a probe view.

    The returned view is for a following inference stage, never for output.
    Final pixels are placed in flat JPEG scan order by
    :func:`_apply_mcu_row_plan`, so an edge fragment cannot wrap to the wrong
    side of its own row.
    """
    rows = (rgb.shape[0] + mcu_h - 1) // mcu_h
    net_phases = np.zeros(rows, dtype=np.int32)
    strip_h = row_multiple * mcu_h
    phases = np.arange(
        -(mcus_x // 2), (mcus_x + 1) // 2, dtype=np.int32)
    output = rgb
    events = []
    changed_passes = 0

    for pass_index in range(max_passes):
        source = output
        pass_output = source.copy()
        pass_events = []
        state = 0
        for y in range(strip_h, source.shape[0], strip_h):
            block = source[y:min(y + strip_h, source.shape[0])]
            profile_h = min(4, y, block.shape[0])
            if profile_h < 1:
                break
            previous = pass_output[y - profile_h:y, :, 1].mean(
                axis=0, dtype=np.float32)
            current = block[:profile_h, :, 1].mean(
                axis=0, dtype=np.float32)
            previous_gradient = np.diff(previous)
            current_gradient = np.diff(current)

            scores, _supports = _gradient_phase_scores(
                np.ascontiguousarray(previous_gradient[:, None]),
                np.ascontiguousarray(current_gradient[:, None]),
                phases, mcu_w)

            best_index = int(np.argmax(scores))
            best_phase = int(phases[best_index])
            best_score = float(scores[best_index])
            state_index = int(np.flatnonzero(phases == state)[0])
            inherited_score = float(scores[state_index])
            distance = np.minimum(
                (phases - best_phase) % mcus_x,
                (best_phase - phases) % mcus_x)
            outside = distance > 2
            competitor = (float(np.max(scores[outside]))
                          if outside.any() else best_score)
            gain = best_score - inherited_score
            margin = best_score - competitor

            if (best_phase != state and best_score >= 0.18
                    and gain >= 0.15 and margin >= 0.10):
                state = best_phase
                event = (pass_index, y, best_phase, gain, margin, best_score)
                pass_events.append(event)

            row_start = y // mcu_h
            row_end = min(rows, (y + block.shape[0] + mcu_h - 1) // mcu_h)
            if state:
                pass_output[y:y + block.shape[0]] = np.roll(
                    block, state * mcu_w, axis=1)
                net_phases[row_start:row_end] += state

        if not pass_events:
            break
        output = pass_output
        events.extend(pass_events)
        changed_passes += 1

    return output, _canonical_row_phases(net_phases, mcus_x), {
        'events': events,
        'passes': changed_passes,
    }


def _build_mcu_row_plan(rgb: np.ndarray, mcus_x: int,
                        mcu_w: int, mcu_h: int,
                        plan: tuple[int, ...]):
    """Run a fixed multi-scale inference plan from one common pre-row RGB."""
    rows = (rgb.shape[0] + mcu_h - 1) // mcu_h
    net_phases = np.zeros(rows, dtype=np.int32)
    view = rgb
    events = []
    changed_passes = 0
    for row_multiple in plan:
        view, delta, stage = _infer_cyclic_mcu_row_plan(
            view, mcus_x, mcu_w, mcu_h, row_multiple=row_multiple)
        net_phases += delta
        events.extend(stage['events'])
        changed_passes += int(stage['passes'])
    return _canonical_row_phases(net_phases, mcus_x), {
        'events': events,
        'passes': changed_passes,
    }


def _build_residual_chain_plan(rgb: np.ndarray, mcus_x: int,
                               mcu_w: int, mcu_h: int,
                               seed: dict | list[dict] | tuple[dict, ...],
                               max_waves: int = 4):
    """Expose and repair a short chain hidden behind one residual boundary.

    This is intentionally a refinement-only plan.  Each wave is inferred from
    a fresh flat-scatter view of the same input, and the completed chain still
    has to pass the normal local and original-image audits.
    """
    rows = (rgb.shape[0] + mcu_h - 1) // mcu_h
    total = rows * mcus_x
    phases = np.zeros(rows, dtype=np.int32)
    identity_owner = np.arange(total, dtype=np.int64)
    events = []
    pending = list(seed) if isinstance(seed, (list, tuple)) else [seed]
    seen = set()
    waves = 0
    for wave in range(max_waves):
        fresh = []
        for event in pending:
            marker = (int(event['y']), int(event['phase']))
            if marker in seen:
                continue
            seen.add(marker)
            fresh.append(event)
            phases[int(event['y']) // mcu_h:] += int(event['phase'])
            events.append((
                wave, int(event['y']), int(event['phase']),
                float(event['gain']), float(event['margin']),
                float(event['score'])))
        if not fresh:
            break
        waves += 1
        phases = _canonical_row_phases(phases, mcus_x)
        view, _owner = _apply_mcu_row_plan(
            rgb, mcus_x, mcu_w, mcu_h, phases, identity_owner)
        pending = _residual_chain_events(
            view, mcus_x, mcu_w, mcu_h)
        if not pending:
            break
    return phases, {'events': events, 'passes': waves}


def _apply_mcu_row_plan(rgb: np.ndarray, mcus_x: int,
                        mcu_w: int, mcu_h: int,
                        row_phases: np.ndarray,
                        base_owner: np.ndarray):
    """Apply row phases once in flat scan order and compose source ownership."""
    mcus_y = len(row_phases)
    total = mcus_x * mcus_y
    spans = [
        (row * mcus_x, min(total, (row + 1) * mcus_x))
        for row in range(mcus_y)
    ]
    offsets = [int(phase) for phase in row_phases]
    corrected, _row_ins, _row_drop = _scatter_mcu_segments(
        rgb, mcus_x, mcus_y, mcu_w, mcu_h, spans, offsets)
    row_owner, _labels = _mcu_owner_map(total, spans, offsets)
    final_owner = np.full(total, -1, dtype=np.int64)
    placed = row_owner >= 0
    final_owner[placed] = base_owner[row_owner[placed]]
    return corrected, final_owner


def _detect_residual_row_seams(rgb: np.ndarray, mcus_x: int,
                               mcu_w: int, mcu_h: int,
                               step_rows: int = 1,
                               strip_h: int | None = None,
                               min_gain: float = 0.15,
                               min_score: float = 0.18,
                               min_margin: float = 0.08):
    """Audit row registration with RGB signed gradients, independent of fit."""
    if strip_h is None:
        strip_h = max(1, min(4, mcu_h // 2))
    step_h = max(1, step_rows) * mcu_h
    phases = np.arange(
        -(mcus_x // 2), (mcus_x + 1) // 2, dtype=np.int32)
    zero_index = int(np.flatnonzero(phases == 0)[0])
    events = []
    valid = set()

    for y in range(step_h, rgb.shape[0], step_h):
        if y < strip_h or y + strip_h > rgb.shape[0]:
            continue
        previous = rgb[y - strip_h:y].mean(axis=0, dtype=np.float32)
        current = rgb[y:y + strip_h].mean(axis=0, dtype=np.float32)
        previous_gradient = np.diff(previous, axis=0)
        current_gradient = np.diff(current, axis=0)
        scores, supports = _gradient_phase_scores(
            np.ascontiguousarray(previous_gradient),
            np.ascontiguousarray(current_gradient), phases, mcu_w)
        best_index = int(np.argmax(scores))
        best_phase = int(phases[best_index])
        best_score = float(scores[best_index])
        support = int(supports[best_index])
        if support >= 64:
            valid.add(y)
        zero_score = float(scores[zero_index])
        distance = np.minimum(
            (phases - best_phase) % mcus_x,
            (best_phase - phases) % mcus_x)
        outside = distance > 2
        competitor = (float(np.max(scores[outside]))
                      if outside.any() else best_score)
        gain = best_score - zero_score
        margin = best_score - competitor
        if (best_phase != 0 and best_score >= min_score
                and gain >= min_gain and margin >= min_margin):
            events.append({
                'y': y, 'phase': best_phase, 'gain': gain,
                'score': best_score, 'margin': margin,
                'support': support,
            })
    return {'events': events, 'valid': valid, 'step_h': step_h}


def _row_phase_distance(first: int, second: int, mcus_x: int) -> int:
    """Return circular MCU distance between two row-registration phases."""
    delta = abs(int(first) - int(second)) % mcus_x
    return int(min(delta, mcus_x - delta))


def _detect_multistrip_row_seams(rgb: np.ndarray, mcus_x: int,
                                 mcu_w: int, mcu_h: int):
    """Detect narrow residuals that one averaged strip can hide.

    A candidate must survive the 1, 2, 3, and 4 pixel boundary views with
    phases agreeing within two MCUs.  This rejects broad-strip-only natural
    transitions while retaining short displaced bands.  Tiny one-to-three MCU
    peaks remain excluded here; structural-cut alignment handles those with
    stronger ownership evidence.
    """
    strip_heights = tuple(
        height for height in (1, 2, 3, 4)
        if height <= mcu_h and height <= rgb.shape[0] // 2)
    if len(strip_heights) < 3:
        return {'events': [], 'valid': set(), 'step_h': mcu_h}

    audits = {
        height: _detect_residual_row_seams(
            rgb, mcus_x, mcu_w, mcu_h, strip_h=height,
            min_gain=0.08, min_score=0.14, min_margin=0.055)
        for height in strip_heights
    }
    by_height = {
        height: {int(event['y']): event for event in audit['events']}
        for height, audit in audits.items()
    }
    common_y = set.intersection(*(
        set(events) for events in by_height.values()))
    valid = set.intersection(*(
        set(audit['valid']) for audit in audits.values()))
    events = []
    for y in sorted(common_y):
        samples = [by_height[height][y] for height in strip_heights]
        phases = [int(sample['phase']) for sample in samples]
        phase = min(
            phases,
            key=lambda candidate: (
                sum(_row_phase_distance(candidate, other, mcus_x)
                    for other in phases),
                abs(candidate), candidate))
        agreeing = [
            sample for sample in samples
            if _row_phase_distance(phase, int(sample['phase']), mcus_x) <= 2
        ]
        if len(agreeing) != len(samples) or abs(int(phase)) < 4:
            continue
        events.append({
            'y': int(y), 'phase': int(phase),
            'gain': float(min(sample['gain'] for sample in agreeing)),
            'score': float(min(sample['score'] for sample in agreeing)),
            'margin': float(min(sample['margin'] for sample in agreeing)),
            'support': int(min(sample['support'] for sample in agreeing)),
            'strip_heights': strip_heights,
        })
    return {'events': events, 'valid': valid, 'step_h': mcu_h}


def _detect_relaxed_consensus_row_seams(
        rgb: np.ndarray, mcus_x: int, mcu_w: int, mcu_h: int):
    """Find residual row phases supported by most narrow strip views.

    The first global pass requires every 1/2/3/4-pixel view to independently
    choose one phase.  Once that pass exposes short bands, this relaxed audit
    scores every phase jointly and uses the lower quartile (the third-best of
    four views).  One broad-average coincidence cannot seed a move, while a
    boundary weak in only one narrow view can still finish the chain.
    """
    rows = (rgb.shape[0] + mcu_h - 1) // mcu_h
    phases = np.arange(
        -(mcus_x // 2), (mcus_x + 1) // 2, dtype=np.int32)
    zero_index = int(np.flatnonzero(phases == 0)[0])
    strip_heights = tuple(range(1, min(4, mcu_h) + 1))
    if rows < 2 or len(strip_heights) < 4:
        return {'events': [], 'valid': set(), 'step_h': mcu_h}

    scores = np.full(
        (len(strip_heights), rows - 1, phases.size),
        -1.0, dtype=np.float32)
    supports = np.zeros(scores.shape, dtype=np.int32)
    for view_index, strip_h in enumerate(strip_heights):
        for row in range(1, rows):
            y = row * mcu_h
            if y < strip_h or y + strip_h > rgb.shape[0]:
                continue
            previous = rgb[y - strip_h:y].mean(
                axis=0, dtype=np.float32)
            current = rgb[y:y + strip_h].mean(
                axis=0, dtype=np.float32)
            view_scores, view_supports = _gradient_phase_scores(
                np.ascontiguousarray(np.diff(previous, axis=0)),
                np.ascontiguousarray(np.diff(current, axis=0)),
                phases, mcu_w)
            scores[view_index, row - 1] = view_scores
            supports[view_index, row - 1] = view_supports

    usable = supports >= 64
    # Sorting ascending makes index 1 the third-best of four views.
    consensus = np.sort(np.where(usable, scores, -1.0), axis=0)[1]
    valid = set()
    events = []
    for boundary, phase_scores in enumerate(consensus):
        y = (boundary + 1) * mcu_h
        if np.count_nonzero(
                np.max(supports[:, boundary], axis=1) >= 64) >= 3:
            valid.add(y)
        best_index = int(np.argmax(phase_scores))
        best_phase = int(phases[best_index])
        best_score = float(phase_scores[best_index])
        zero_score = float(phase_scores[zero_index])
        distance = np.minimum(
            (phases - best_phase) % mcus_x,
            (best_phase - phases) % mcus_x)
        outside = distance > 2
        competitor = (float(np.max(phase_scores[outside]))
                      if outside.any() else best_score)
        gain = best_score - zero_score
        margin = best_score - competitor
        phase_supports = supports[:, boundary, best_index]
        usable_supports = phase_supports[phase_supports >= 64]
        support = (int(np.min(usable_supports))
                   if usable_supports.size >= 3 else 0)
        if (best_phase != 0 and support >= 64
                and best_score >= 0.10
                and gain >= 0.055 and margin >= 0.025):
            events.append({
                'y': int(y), 'phase': best_phase,
                'gain': float(gain), 'score': best_score,
                'margin': float(margin), 'support': support,
                'strip_heights': strip_heights,
            })
    return {'events': events, 'valid': valid, 'step_h': mcu_h}


def _row_phase_run_spans(row_phases: np.ndarray, mcus_x: int):
    """Compress equal adjacent row offsets into equivalent flat spans."""
    phases = np.asarray(row_phases, dtype=np.int32)
    if phases.size == 0:
        return [], []
    spans = []
    offsets = []
    start = 0
    state = int(phases[0])
    for row in range(1, phases.size):
        current = int(phases[row])
        if current == state:
            continue
        spans.append((start * mcus_x, row * mcus_x))
        offsets.append(state)
        start = row
        state = current
    spans.append((start * mcus_x, phases.size * mcus_x))
    offsets.append(state)
    return spans, offsets


def _residual_chain_events(rgb: np.ndarray, mcus_x: int,
                           mcu_w: int, mcu_h: int):
    """Return independently corroborated events for one chain wave."""
    strict = _detect_residual_row_seams(
        rgb, mcus_x, mcu_w, mcu_h)
    narrow = _detect_multistrip_row_seams(
        rgb, mcus_x, mcu_w, mcu_h)
    soft = _detect_residual_row_seams(
        rgb, mcus_x, mcu_w, mcu_h,
        min_gain=0.10, min_score=0.15, min_margin=0.06)
    coarse = _detect_residual_row_seams(
        rgb, mcus_x, mcu_w, mcu_h, step_rows=2, strip_h=mcu_h,
        min_gain=0.12)

    events = []
    markers = set()

    def append(event):
        marker = (int(event['y']), int(event['phase']))
        if marker not in markers:
            markers.add(marker)
            events.append(event)

    for event in strict['events']:
        append(event)
    for event in narrow['events']:
        append(event)
    # A wide average alone produced repeatable false positives in the hard
    # samples.  Admit it only when the normal four-pixel view independently
    # reports the same phase at the same boundary (within one MCU row).
    for event in coarse['events']:
        corroborated = any(
            abs(int(event['y']) - int(candidate['y'])) <= mcu_h
            and int(event['phase']) == int(candidate['phase'])
            for candidate in soft['events'])
        if corroborated:
            append(event)
    return events


def _row_residual_summary(audit):
    gains = [float(event['gain']) for event in audit['events']]
    return {
        'n': len(gains),
        'score': float(sum(gains)),
        'maximum': float(max(gains)) if gains else 0.0,
    }


def _owner_placement_loss(final_owner: np.ndarray,
                          valid_target_slots: np.ndarray,
                          source_count: int) -> tuple[int, int]:
    """Count final blank valid slots and original sources no longer retained."""
    occupied = np.asarray(final_owner) >= 0
    valid_target_slots = np.asarray(valid_target_slots, dtype=bool)
    inserted = int(np.count_nonzero(valid_target_slots & ~occupied))
    dropped = int(source_count - np.count_nonzero(occupied))
    return inserted, dropped


def _unmatched_row_events(before, after, tolerance: int):
    """Greedily match strongest after-events to one nearby baseline event."""
    used = set()
    unmatched = []
    for event in sorted(after, key=lambda item: -float(item['gain'])):
        choices = [
            (abs(int(event['y']) - int(base['y'])), index)
            for index, base in enumerate(before)
            if index not in used
            and abs(int(event['y']) - int(base['y'])) <= tolerance
        ]
        if not choices:
            unmatched.append(event)
            continue
        _distance, index = min(choices)
        used.add(index)
    return unmatched


def _row_candidate_safe(before_audit, after_audit,
                        *, require_reduction: bool = True) -> bool:
    before = _row_residual_summary(before_audit)
    after = _row_residual_summary(after_audit)
    if (require_reduction and (before['score'] <= 0.0
                               or after['score'] > before['score'] * 0.80)):
        return False
    if (not require_reduction
            and (after['score'] > before['score'] * 1.05 + 0.02
                 or after['maximum'] > max(
                     before['maximum'] * 1.20,
                     before['maximum'] + 0.05))):
        return False
    if any(int(event['y']) not in after_audit['valid']
           for event in before_audit['events']):
        return False
    unmatched = _unmatched_row_events(
        before_audit['events'], after_audit['events'],
        int(before_audit['step_h']))
    tolerance = int(before_audit['step_h'])
    for event in after_audit['events']:
        nearby = [
            base for base in before_audit['events']
            if abs(int(event['y']) - int(base['y'])) <= tolerance
        ]
        if not nearby:
            continue
        previous_gain = max(float(base['gain']) for base in nearby)
        if (float(event['gain']) >= max(0.30, previous_gain * 1.25)
                and float(event['gain']) > previous_gain + 0.05):
            return False
    threshold = (min(0.30, 0.75 * before['maximum'])
                 if before['maximum'] > 0.0 else 0.0)
    return not any(float(event['gain']) >= threshold for event in unmatched)


def _global_row_audits(rgb: np.ndarray, mcus_x: int,
                       mcu_w: int, mcu_h: int):
    return {
        'exact': _detect_residual_row_seams(
            rgb, mcus_x, mcu_w, mcu_h),
        'soft': _detect_residual_row_seams(
            rgb, mcus_x, mcu_w, mcu_h,
            min_gain=0.10, min_score=0.15, min_margin=0.06),
        'multistrip': _detect_multistrip_row_seams(
            rgb, mcus_x, mcu_w, mcu_h),
    }


def _global_row_audits_safe(candidate, reference, mcus_x: int) -> bool:
    """Keep every independent row audit within one trusted reference."""
    for level in ('exact', 'soft', 'multistrip'):
        if not _row_candidate_safe(
                reference[level], candidate[level],
                require_reduction=False):
            return False
        tolerance = int(reference[level]['step_h'])
        for event in candidate[level]['events']:
            nearby = [
                base for base in reference[level]['events']
                if abs(int(event['y']) - int(base['y'])) <= tolerance
            ]
            if (nearby and all(_row_phase_distance(
                    int(event['phase']), int(base['phase']), mcus_x) > 2
                    for base in nearby)):
                return False
    return True


def _global_row_candidate_key(audits, inserted: int, dropped: int,
                              phases: np.ndarray, relaxed):
    """Rank returnable states by alignment first, then retained material."""
    summaries = {
        name: _row_residual_summary(audits[name])
        for name in ('exact', 'multistrip', 'soft')
    }
    transitions = int(np.count_nonzero(np.diff(phases)))
    return (
        summaries['exact']['score'],
        summaries['multistrip']['score'],
        summaries['soft']['score'],
        _row_residual_summary(relaxed)['score'],
        max(int(inserted), int(dropped)),
        int(inserted) + int(dropped),
        transitions,
    )


def _global_owner_order_safe(owner: np.ndarray) -> bool:
    """A flat correction may delete sources, but never duplicate/reorder."""
    placed = np.asarray(owner, dtype=np.int64)
    placed = placed[placed >= 0]
    if placed.size == 0:
        return False
    return bool(
        np.unique(placed).size == placed.size
        and np.all(np.diff(placed) > 0))


def _correct_global_row_shifts(
        rgb: np.ndarray, mcus_x: int, mcu_w: int, mcu_h: int,
        *, base_owner: np.ndarray, valid_sources: np.ndarray,
        source_count: int, loss_budget: int, max_passes: int = 5):
    """Fit one top-anchored absolute row-phase chain over the whole image.

    Pass one applies every strict 1/2/3/4-view consensus event together.
    Later passes use relaxed consensus only on the newly rendered view.  The
    accepted deltas accumulate into one absolute state vector, and every
    candidate is re-rendered from ``rgb`` and ``base_owner`` once.  Thus the
    owner budget is cumulative across segment and row correction and cannot
    drift through sequential image edits.
    """
    zero = dict(
        row_global_passes=0, row_global_events=0,
        row_global_explored=0, row_global_unsafe_passes=0,
        row_global_changes=0, row_global_gain=0.0,
        row_global_margin=0.0,
        row_global_residual_before=0.0,
        row_global_residual_after=0.0,
        row_global_relaxed_after=0.0,
        row_global_veto=0, row_global_plan=(), row_global_trace=())
    if (rgb.ndim != 3 or rgb.shape[2] < 2 or mcus_x < 4
            or mcu_w <= 0 or mcu_h < 4
            or rgb.shape[1] != mcus_x * mcu_w):
        return rgb, base_owner, zero
    rows = (rgb.shape[0] + mcu_h - 1) // mcu_h
    total = rows * mcus_x
    base_owner = np.asarray(base_owner, dtype=np.int64)
    valid_sources = np.asarray(valid_sources, dtype=bool)
    if (rows < 2 or base_owner.shape != (total,)
            or valid_sources.shape != (total,)):
        return rgb, base_owner, zero

    baseline_audits = _global_row_audits(
        rgb, mcus_x, mcu_w, mcu_h)
    pending = baseline_audits['multistrip']
    strict_summary = _row_residual_summary(pending)
    stats = dict(zero)
    stats.update(
        row_global_residual_before=strict_summary['score'],
        row_global_residual_after=strict_summary['score'])
    if not pending['events']:
        return rgb, base_owner, stats

    phases = np.zeros(rows, dtype=np.int32)
    safe_phases = None
    safe_rgb = rgb
    safe_owner = base_owner
    safe_audits = None
    safe_relaxed = None
    safe_key = None
    explored_gains = []
    explored_margins = []
    explored_events = 0
    safe_gains = []
    safe_margins = []
    safe_events = 0
    safe_passes = 0
    trace = []
    vetoed = 0

    for pass_index in range(max(1, int(max_passes))):
        events = list(pending['events'])
        if not events:
            break
        # Cyclic evidence cannot choose the physical direction at exactly
        # half an even-width row.  Do not guess which edge to destroy.
        if (mcus_x % 2 == 0
                and any(abs(int(event['phase'])) == mcus_x // 2
                        for event in events)):
            vetoed += 1
            break

        delta = np.zeros(rows, dtype=np.int32)
        for event in events:
            row = int(event['y']) // mcu_h
            if 0 < row < rows:
                delta[row:] += int(event['phase'])
        candidate_phases = _canonical_row_phases(
            phases + delta, mcus_x)
        if int(candidate_phases[0]) != 0:
            vetoed += 1
            break
        candidate_rgb, candidate_owner = _apply_mcu_row_plan(
            rgb, mcus_x, mcu_w, mcu_h,
            candidate_phases, base_owner)
        inserted, dropped = _owner_placement_loss(
            candidate_owner, valid_sources, source_count)
        if (max(inserted, dropped) > loss_budget
                or not _global_owner_order_safe(candidate_owner)):
            vetoed += 1
            break

        candidate_audits = _global_row_audits(
            candidate_rgb, mcus_x, mcu_w, mcu_h)
        after_objective = (
            candidate_audits['multistrip'] if pass_index == 0
            else _detect_relaxed_consensus_row_seams(
                candidate_rgb, mcus_x, mcu_w, mcu_h))
        before_summary = _row_residual_summary(pending)
        after_summary = _row_residual_summary(after_objective)
        if (before_summary['score'] <= 0.0
                or after_summary['score'] > before_summary['score'] * 0.85):
            vetoed += 1
            break

        phases = candidate_phases
        selected_relaxed = (
            after_objective if pass_index > 0
            else _detect_relaxed_consensus_row_seams(
                candidate_rgb, mcus_x, mcu_w, mcu_h))
        explored_events += len(events)
        explored_gains.extend(float(event['gain']) for event in events)
        explored_margins.extend(float(event['margin']) for event in events)
        stats['row_global_explored'] += 1
        inserted, dropped = _owner_placement_loss(
            candidate_owner, valid_sources, source_count)
        globally_safe = _global_row_audits_safe(
            candidate_audits, baseline_audits, mcus_x)
        if globally_safe and safe_audits is not None:
            globally_safe = _global_row_audits_safe(
                candidate_audits, safe_audits, mcus_x)
        candidate_key = _global_row_candidate_key(
            candidate_audits, inserted, dropped, candidate_phases,
            selected_relaxed)
        selected_safe = bool(
            globally_safe and (safe_key is None or candidate_key < safe_key))
        trace.append({
            'pass': pass_index + 1,
            'before': before_summary['score'],
            'after': after_summary['score'],
            'exact': _row_residual_summary(
                candidate_audits['exact'])['score'],
            'multistrip': _row_residual_summary(
                candidate_audits['multistrip'])['score'],
            'soft': _row_residual_summary(
                candidate_audits['soft'])['score'],
            'safe': bool(globally_safe),
            'selected': selected_safe,
        })
        if selected_safe:
            safe_phases = phases.copy()
            safe_rgb = candidate_rgb
            safe_owner = candidate_owner
            safe_audits = candidate_audits
            safe_relaxed = selected_relaxed
            safe_key = candidate_key
            safe_gains = list(explored_gains)
            safe_margins = list(explored_margins)
            safe_events = explored_events
            safe_passes = pass_index + 1
        elif not globally_safe:
            stats['row_global_unsafe_passes'] += 1
        pending = selected_relaxed

    stats['row_global_veto'] = vetoed
    stats['row_global_trace'] = tuple(trace)
    if safe_phases is None:
        return rgb, base_owner, stats

    transitions = []
    previous = int(safe_phases[0])
    for row, phase in enumerate(safe_phases[1:], start=1):
        phase = int(phase)
        if phase != previous:
            transitions.append((row, phase))
            previous = phase
    inserted, dropped = _owner_placement_loss(
        safe_owner, valid_sources, source_count)
    assert safe_audits is not None and safe_relaxed is not None
    stats.update(
        row_global_passes=safe_passes,
        row_global_events=safe_events,
        row_global_changes=len(transitions),
        row_global_gain=(min(safe_gains)
                         if safe_gains else 0.0),
        row_global_margin=(min(safe_margins)
                           if safe_margins else 0.0),
        row_global_residual_after=_row_residual_summary(
            safe_audits['multistrip'])['score'],
        row_global_relaxed_after=_row_residual_summary(
            safe_relaxed)['score'],
        row_global_plan=tuple(transitions),
        _final_mcu_ins=int(inserted),
        _final_mcu_drop=int(dropped))
    return safe_rgb, safe_owner, stats


@dataclass(frozen=True)
class _StructuralRowCut:
    span_index: int
    row: int
    mode: str
    repeat_side: str | None
    adjacent: dict
    repeat: dict | None
    zero_before: int
    zero_after: int


@dataclass(frozen=True)
class _StructuralRowCandidate:
    cut: _StructuralRowCut
    side: str
    lo: int
    hi: int
    phase: int
    target: dict
    far: dict | None
    inserted: int
    dropped: int
    key: tuple


_STRUCTURAL_ROW_LEVELS = {
    'exact': (0.180, 0.150, 0.080),
    'soft': (0.120, 0.075, 0.060),
    'relaxed': (0.050, 0.040, 0.010),
}


def _structural_canonical_phase(phase: int, mcus_x: int) -> int:
    return int((int(phase) + mcus_x // 2) % mcus_x - mcus_x // 2)


def _structural_candidate_phases(mcus_x: int) -> tuple[int, ...]:
    phases = list(range(-(mcus_x // 2), (mcus_x + 1) // 2))
    # Cyclic evidence cannot distinguish the two flat directions at exactly
    # half an even-width row.  Keep both physical layouts for loss/edge
    # comparison; an exact tie is removed below.
    if mcus_x % 2 == 0:
        phases.append(mcus_x // 2)
    return tuple(phase for phase in phases if phase != 0)


def _row_boundary_metric(rgb: np.ndarray, y: int, mcus_x: int,
                         mcu_w: int, mcu_h: int) -> dict | None:
    strip_h = max(1, min(4, mcu_h // 2))
    if y < strip_h or y + strip_h > rgb.shape[0]:
        return None
    phases = np.arange(
        -(mcus_x // 2), (mcus_x + 1) // 2, dtype=np.int32)
    previous = rgb[y - strip_h:y].mean(axis=0, dtype=np.float32)
    current = rgb[y:y + strip_h].mean(axis=0, dtype=np.float32)
    scores, supports = _gradient_phase_scores(
        np.ascontiguousarray(np.diff(previous, axis=0)),
        np.ascontiguousarray(np.diff(current, axis=0)),
        phases, mcu_w)
    best_index = int(np.argmax(scores))
    best_phase = int(phases[best_index])
    best_score = float(scores[best_index])
    zero_index = int(np.flatnonzero(phases == 0)[0])
    distance = np.minimum(
        (phases - best_phase) % mcus_x,
        (best_phase - phases) % mcus_x)
    outside = distance > 2
    competitor = (float(np.max(scores[outside]))
                  if outside.any() else best_score)
    return {
        'phase': best_phase,
        'score': best_score,
        'gain': best_score - float(scores[zero_index]),
        'margin': best_score - competitor,
        'support': int(supports[best_index]),
    }


def _structural_level_event(metric: dict | None, level: str) -> bool:
    if metric is None or int(metric['phase']) == 0:
        return False
    score, gain, margin = _STRUCTURAL_ROW_LEVELS[level]
    return bool(
        float(metric['score']) >= score
        and float(metric['gain']) >= gain
        and float(metric['margin']) >= margin)


def _structural_target_mode(metric: dict | None) -> int | None:
    if metric is None:
        return None
    phase = int(metric['phase'])
    if phase == 0:
        return 0
    if (abs(phase) <= 2
            and (float(metric['gain']) < 0.040
                 or float(metric['margin']) < 0.010)):
        return 1
    return None


def _structural_aligned_count(estimates: list, mcus_x: int) -> int:
    return sum(
        estimate is not None and bool(estimate['confident'])
        and abs(_structural_canonical_phase(
            int(estimate['phase']), mcus_x)) <= 2
        for estimate in estimates)


def _structural_strong_repeat(estimate: dict | None,
                              mcus_x: int) -> bool:
    return bool(
        estimate is not None and estimate['confident']
        and abs(_structural_canonical_phase(
            int(estimate['phase']), mcus_x)) >= 4
        and int(estimate['pairs']) >= 8
        and float(estimate['score']) >= 8.0
        and float(estimate['margin']) >= 1.0
        and float(estimate['raw_ratio']) >= 1.6)


def _structural_repeat_estimates(left: np.ndarray, right: np.ndarray,
                                 mcus_x: int, total_rows: int,
                                 row: int, before: bool) -> list:
    estimates = []
    for rows in (8, 12, 16):
        if rows > total_rows:
            continue
        start = row - rows if before else row
        start = max(0, min(start, total_rows - rows))
        estimates.append(_estimate_mcu_phase(
            left, right, start * mcus_x, (start + rows) * mcus_x,
            mcus_x, min_pairs=5))
    return estimates


def _placed_structural_rows(spans: list[tuple[int, int]], offsets: list,
                            mcus_x: int,
                            total_rows: int) -> tuple[list[tuple[int, int]],
                                                      list[int]]:
    total = mcus_x * total_rows
    placed = []
    rows = {0, total_rows}
    for index in range(1, min(len(spans), len(offsets))):
        offset = offsets[index]
        if offset is None:
            continue
        start, end = spans[index]
        offset = int(offset)
        source_start = max(int(start), -offset)
        source_end = min(int(end), total - offset)
        if source_end <= source_start:
            continue
        target_start = source_start + offset
        row = max(1, min(total_rows - 1,
                         (target_start + mcus_x - 1) // mcus_x))
        placed.append((index, row))
        rows.add(row)
    return placed, sorted(rows)


def _discover_structural_row_cuts(
        rgb: np.ndarray, spans: list[tuple[int, int]], offsets: list,
        mcus_x: int, total_rows: int, mcu_w: int,
        mcu_h: int) -> tuple[list[_StructuralRowCut], list[int]]:
    placed, structural_rows = _placed_structural_rows(
        spans, offsets, mcus_x, total_rows)
    if not placed:
        return [], structural_rows
    left, right = _mcu_edge_arrays(
        rgb, mcus_x, total_rows, mcu_w, mcu_h)
    per_row: dict[int, list[_StructuralRowCut]] = {}
    for index, row in placed:
        adjacent = _row_boundary_metric(
            rgb, row * mcu_h, mcus_x, mcu_w, mcu_h)
        if adjacent is None or int(adjacent['phase']) == 0:
            continue
        before = _structural_repeat_estimates(
            left, right, mcus_x, total_rows, row, True)
        after = _structural_repeat_estimates(
            left, right, mcus_x, total_rows, row, False)
        zero_before = _structural_aligned_count(before, mcus_x)
        zero_after = _structural_aligned_count(after, mcus_x)

        repeat_side = None
        repeat = None
        if (before and _structural_strong_repeat(before[0], mcus_x)
                and zero_after >= 2):
            repeat_side, repeat = 'before', before[0]
        elif (after and _structural_strong_repeat(after[0], mcus_x)
              and zero_before >= 2):
            repeat_side, repeat = 'after', after[0]
        repeat_mode = bool(
            repeat_side is not None
            and float(adjacent['gain']) >= 0.060
            and float(adjacent['margin']) >= 0.015)
        anchor_mode = bool(
            abs(int(adjacent['phase'])) <= max(2, mcus_x // 12)
            and float(adjacent['score']) >= 0.120
            and float(adjacent['gain']) >= 0.075
            and float(adjacent['margin']) >= 0.060
            and zero_before >= 2 and zero_after >= 2)
        if not (repeat_mode or anchor_mode):
            continue
        evidence = _StructuralRowCut(
            index, row, 'repeat' if repeat_mode else 'anchor',
            repeat_side, adjacent, repeat, zero_before, zero_after)
        per_row.setdefault(row, []).append(evidence)

    cuts = [
        max(alternatives, key=lambda item: (
            item.mode == 'repeat',
            float(item.adjacent['margin']),
            float(item.adjacent['gain']),
            float(item.adjacent['score']),
            -item.span_index))
        for alternatives in per_row.values()
    ]
    return sorted(cuts, key=lambda item: item.row), structural_rows


def _structural_rgb_tiles(rgb: np.ndarray, mcus_x: int,
                          total_rows: int, mcu_w: int,
                          mcu_h: int) -> np.ndarray:
    padded = np.full(
        (total_rows * mcu_h, mcus_x * mcu_w, 3), 128, np.uint8)
    h = min(rgb.shape[0], padded.shape[0])
    w = min(rgb.shape[1], padded.shape[1])
    padded[:h, :w] = rgb[:h, :w]
    return padded.reshape(
        total_rows, mcu_h, mcus_x, mcu_w, 3
    ).transpose(0, 2, 1, 3, 4).reshape(
        total_rows * mcus_x, mcu_h, mcu_w, 3)


def _structural_row_owner(total_rows: int, mcus_x: int,
                          phases: np.ndarray) -> np.ndarray:
    total = total_rows * mcus_x
    row_spans = [
        (row * mcus_x, (row + 1) * mcus_x)
        for row in range(total_rows)
    ]
    owner, _labels = _mcu_owner_map(
        total, row_spans, [int(value) for value in phases])
    return owner


def _structural_boundary_from_owner(
        tiles: np.ndarray, row_owner: np.ndarray, row: int,
        mcus_x: int, mcu_w: int, mcu_h: int) -> dict | None:
    total_rows = row_owner.size // mcus_x
    if row <= 0 or row >= total_rows:
        return None
    targets = np.arange(
        (row - 1) * mcus_x, (row + 1) * mcus_x, dtype=np.int64)
    owners = row_owner[targets]
    selected = np.full(
        (2 * mcus_x, mcu_h, mcu_w, 3), 128, dtype=np.uint8)
    valid = owners >= 0
    selected[valid] = tiles[owners[valid]]
    image = selected.reshape(
        2, mcus_x, mcu_h, mcu_w, 3
    ).transpose(0, 2, 1, 3, 4).reshape(
        2 * mcu_h, mcus_x * mcu_w, 3)
    return _row_boundary_metric(
        image, mcu_h, mcus_x, mcu_w, mcu_h)


def _structural_compose_owner(row_owner: np.ndarray,
                              base_owner: np.ndarray) -> np.ndarray:
    final_owner = np.full(row_owner.shape, -1, dtype=np.int64)
    valid = row_owner >= 0
    final_owner[valid] = base_owner[row_owner[valid]]
    return final_owner


def _structural_audit(rgb: np.ndarray, mcus_x: int, mcu_w: int,
                      mcu_h: int, level: str) -> dict:
    score, gain, margin = _STRUCTURAL_ROW_LEVELS[level]
    return _detect_residual_row_seams(
        rgb, mcus_x, mcu_w, mcu_h,
        min_score=score, min_gain=gain, min_margin=margin)


def _structural_phase_unmatched(before: list[dict], after: list[dict],
                                mcus_x: int, tolerance: int) -> list[dict]:
    used = set()
    unmatched = []
    for event in sorted(after, key=lambda item: -float(item['gain'])):
        choices = [
            (abs(int(event['y']) - int(base['y'])), index)
            for index, base in enumerate(before)
            if index not in used
            and abs(int(event['y']) - int(base['y'])) <= tolerance
            and _row_phase_distance(
                int(event['phase']), int(base['phase']), mcus_x) <= 2
        ]
        if not choices:
            unmatched.append(event)
            continue
        _distance, index = min(choices)
        used.add(index)
    return unmatched


def _structural_audit_safe(before: dict, after: dict,
                           mcus_x: int) -> bool:
    before_summary = _row_residual_summary(before)
    after_summary = _row_residual_summary(after)
    if after_summary['score'] > before_summary['score'] + 1e-6:
        return False
    allowance = 0.010
    if after_summary['maximum'] > before_summary['maximum'] + allowance:
        return False
    unmatched = _structural_phase_unmatched(
        before['events'], after['events'], mcus_x,
        int(before['step_h']))
    limit = before_summary['maximum'] + allowance
    return not any(float(item['gain']) > limit for item in unmatched)


def _drop_ambiguous_half_candidates(
        candidates: list[_StructuralRowCandidate],
        mcus_x: int) -> list[_StructuralRowCandidate]:
    if mcus_x % 2:
        return candidates
    half = mcus_x // 2
    rejected = set()
    for side in ('before', 'after'):
        pair = [
            item for item in candidates
            if item.side == side and abs(item.phase) == half
        ]
        if len(pair) == 2 and pair[0].key == pair[1].key:
            rejected.update(id(item) for item in pair)
    return [item for item in candidates if id(item) not in rejected]


def _enumerate_structural_row_candidates(
        cut: _StructuralRowCut, structural_rows: list[int],
        tiles: np.ndarray, base_owner: np.ndarray,
        valid_sources: np.ndarray, source_count: int,
        mcus_x: int, total_rows: int, mcu_w: int, mcu_h: int,
        loss_budget: int,
        baseline_metrics: dict[int, dict | None]
        ) -> list[_StructuralRowCandidate]:
    position = structural_rows.index(cut.row)
    intervals = (
        ('before', structural_rows[position - 1], cut.row),
        ('after', cut.row, structural_rows[position + 1]),
    )
    zero_phases = np.zeros(total_rows, dtype=np.int32)
    baseline_inserted, baseline_dropped = _owner_placement_loss(
        base_owner, valid_sources, source_count)
    candidates = []
    for side, lo, hi in intervals:
        if hi <= lo:
            continue
        far_row = lo if side == 'before' else hi
        far_before = baseline_metrics.get(far_row)
        for phase in _structural_candidate_phases(mcus_x):
            phases = zero_phases.copy()
            phases[lo:hi] = phase
            row_owner = _structural_row_owner(
                total_rows, mcus_x, phases)
            target = _structural_boundary_from_owner(
                tiles, row_owner, cut.row, mcus_x, mcu_w, mcu_h)
            mode = _structural_target_mode(target)
            if mode is None:
                continue
            far = _structural_boundary_from_owner(
                tiles, row_owner, far_row, mcus_x, mcu_w, mcu_h)
            final_owner = _structural_compose_owner(
                row_owner, base_owner)
            inserted, dropped = _owner_placement_loss(
                final_owner, valid_sources, source_count)
            if max(inserted, dropped) > loss_budget:
                continue
            strict_new = (
                _structural_level_event(far, 'exact')
                and not _structural_level_event(far_before, 'exact'))
            if strict_new:
                continue
            soft_new = (
                _structural_level_event(far, 'soft')
                and not _structural_level_event(far_before, 'soft'))
            relaxed_gain = (
                float(far['gain'])
                if _structural_level_event(far, 'relaxed') else 0.0)

            # Repeat evidence can identify the actually displaced side.  An
            # anchor has no such direction evidence, so the usually-clean
            # prefix is only a ranking prior; the opposite side remains a
            # candidate and can win on stronger edge/residual/loss evidence.
            side_penalty = int(
                (cut.repeat_side is not None
                 and side != cut.repeat_side)
                or (cut.mode == 'anchor' and side == 'before'))
            adjacent_phase = _structural_canonical_phase(
                int(cut.adjacent['phase']), mcus_x)
            expected_phase = (
                adjacent_phase if side == 'after' else -adjacent_phase)
            half_ambiguous = (
                mcus_x % 2 == 0
                and abs(phase) == mcus_x // 2
                and abs(adjacent_phase) == mcus_x // 2)
            orientation_penalty = int(
                cut.mode == 'anchor'
                and not half_ambiguous
                and phase != expected_phase)
            loss_delta = max(
                inserted - baseline_inserted,
                dropped - baseline_dropped)
            key = (
                mode, int(soft_new), side_penalty,
                orientation_penalty, relaxed_gain, loss_delta,
                -float(target['score']), abs(phase),
                int(side == 'before'))
            candidates.append(_StructuralRowCandidate(
                cut, side, lo, hi, int(phase), target, far,
                inserted, dropped, key))

    # Do not choose an arbitrary flat direction when the cyclic half-width
    # evidence and every physical tiebreak are identical.
    candidates = _drop_ambiguous_half_candidates(candidates, mcus_x)

    candidates.sort(key=lambda item: item.key)
    exact = [item for item in candidates if item.key[0] == 0]
    return (exact if exact else candidates)[:4]


def _merge_structural_row_candidates(
        candidates: tuple[_StructuralRowCandidate, ...],
        total_rows: int) -> np.ndarray | None:
    phases = np.zeros(total_rows, dtype=np.int32)
    for candidate in candidates:
        current = phases[candidate.lo:candidate.hi]
        if np.any((current != 0) & (current != candidate.phase)):
            return None
        current[current == 0] = candidate.phase
    return phases


def _correct_structural_row_shifts(
        rgb: np.ndarray, mcus_x: int, mcu_w: int, mcu_h: int,
        spans: list[tuple[int, int]], offsets: list,
        base_owner: np.ndarray, valid_sources: np.ndarray,
        source_count: int, loss_budget: int):
    """Repair residual phases only inside final placed segment intervals."""
    zero = dict(
        row_local_cuts=0, row_local_intervals=0,
        row_local_exact=0, row_local_soft=0,
        row_local_veto=0, row_local_plan=())
    if (rgb.ndim != 3 or rgb.shape[2] < 2 or mcus_x < 4
            or mcu_w <= 0 or mcu_h <= 0
            or rgb.shape[1] != mcus_x * mcu_w):
        return rgb, base_owner, zero
    total_rows = (rgb.shape[0] + mcu_h - 1) // mcu_h
    total = total_rows * mcus_x
    base_owner = np.asarray(base_owner, dtype=np.int64)
    valid_sources = np.asarray(valid_sources, dtype=bool)
    if (base_owner.shape != (total,)
            or valid_sources.shape != (total,)):
        return rgb, base_owner, zero

    cuts, structural_rows = _discover_structural_row_cuts(
        rgb, spans, offsets, mcus_x, total_rows, mcu_w, mcu_h)
    if not cuts:
        return rgb, base_owner, zero
    tiles = _structural_rgb_tiles(
        rgb, mcus_x, total_rows, mcu_w, mcu_h)
    baseline_metrics = {
        row: _row_boundary_metric(
            rgb, row * mcu_h, mcus_x, mcu_w, mcu_h)
        for row in structural_rows if 0 < row < total_rows
    }
    exact_before = _structural_audit(
        rgb, mcus_x, mcu_w, mcu_h, 'exact')
    soft_before = _structural_audit(
        rgb, mcus_x, mcu_w, mcu_h, 'soft')
    relaxed_before = _structural_audit(
        rgb, mcus_x, mcu_w, mcu_h, 'relaxed')

    local_lists = []
    for cut in cuts:
        local = _enumerate_structural_row_candidates(
            cut, structural_rows, tiles, base_owner,
            valid_sources, source_count, mcus_x, total_rows,
            mcu_w, mcu_h, loss_budget, baseline_metrics)
        if not local:
            rejected = dict(zero)
            rejected['row_local_veto'] = 1
            return rgb, base_owner, rejected
        local_lists.append(local)

    merged = []
    overlap_conflicts = 0
    for choice in itertools.product(*local_lists):
        phases = _merge_structural_row_candidates(choice, total_rows)
        if phases is None:
            overlap_conflicts += 1
            continue
        row_owner = _structural_row_owner(
            total_rows, mcus_x, phases)
        targets = [
            _structural_boundary_from_owner(
                tiles, row_owner, cut.row, mcus_x, mcu_w, mcu_h)
            for cut in cuts
        ]
        modes = [_structural_target_mode(metric) for metric in targets]
        if any(mode is None for mode in modes):
            continue
        final_owner = _structural_compose_owner(row_owner, base_owner)
        inserted, dropped = _owner_placement_loss(
            final_owner, valid_sources, source_count)
        if max(inserted, dropped) > loss_budget:
            continue
        key = (
            max(int(mode) for mode in modes),
            sum(int(mode) for mode in modes),
            sum(int(candidate.key[1]) for candidate in choice),
            sum(int(candidate.key[2]) for candidate in choice),
            sum(int(candidate.key[3]) for candidate in choice),
            sum(float(candidate.key[4]) for candidate in choice),
            -sum(float(metric['score']) for metric in targets),
            max(inserted, dropped),
            sum(abs(int(candidate.phase)) for candidate in choice),
        )
        merged.append((key, choice, phases, inserted, dropped, targets))
    merged.sort(key=lambda item: item[0])

    selected = None
    vetoed = overlap_conflicts
    for key, choice, phases, inserted, dropped, targets in merged:
        dominance_prefix = key[:7]
        if selected is not None and dominance_prefix > selected[0][0]:
            break
        corrected, final_owner = _apply_mcu_row_plan(
            rgb, mcus_x, mcu_w, mcu_h, phases, base_owner)
        inserted, dropped = _owner_placement_loss(
            final_owner, valid_sources, source_count)
        if max(inserted, dropped) > loss_budget:
            vetoed += 1
            continue
        exact_after = _structural_audit(
            corrected, mcus_x, mcu_w, mcu_h, 'exact')
        if (not _row_candidate_safe(
                    exact_before, exact_after, require_reduction=False)
                or not _structural_audit_safe(
                    exact_before, exact_after, mcus_x)):
            vetoed += 1
            continue
        soft_after = _structural_audit(
            corrected, mcus_x, mcu_w, mcu_h, 'soft')
        if not _structural_audit_safe(
                soft_before, soft_after, mcus_x):
            vetoed += 1
            continue
        relaxed_after = _structural_audit(
            corrected, mcus_x, mcu_w, mcu_h, 'relaxed')
        if not _structural_audit_safe(
                relaxed_before, relaxed_after, mcus_x):
            vetoed += 1
            continue
        exact_summary = _row_residual_summary(exact_after)
        exact_base = _row_residual_summary(exact_before)
        soft_summary = _row_residual_summary(soft_after)
        soft_base = _row_residual_summary(soft_before)
        relaxed_summary = _row_residual_summary(relaxed_after)
        relaxed_base = _row_residual_summary(relaxed_before)
        regression = (
            max(0.0, exact_summary['score'] - exact_base['score']),
            max(0.0, exact_summary['maximum'] - exact_base['maximum']),
            max(0.0, soft_summary['score'] - soft_base['score']),
            max(0.0, soft_summary['maximum'] - soft_base['maximum']),
            max(0.0, relaxed_summary['score'] - relaxed_base['score']),
            max(0.0, relaxed_summary['maximum']
                - relaxed_base['maximum']),
        )
        global_key = (key[:7], regression, key[7:])
        item = (
            global_key, choice, phases, targets, corrected,
            final_owner, inserted, dropped)
        if selected is None or global_key < selected[0]:
            selected = item

    if selected is None:
        rejected = dict(zero)
        rejected['row_local_veto'] = vetoed
        return rgb, base_owner, rejected
    (_global_key, choice, phases, targets, corrected,
     final_owner, inserted, dropped) = selected
    nonzero = phases != 0
    intervals = int(np.count_nonzero(
        nonzero & np.r_[True, phases[1:] != phases[:-1]]))
    stats = dict(
        row_local_cuts=len(choice),
        row_local_intervals=intervals,
        row_local_exact=sum(
            _structural_target_mode(metric) == 0 for metric in targets),
        row_local_soft=sum(
            _structural_target_mode(metric) == 1 for metric in targets),
        row_local_veto=vetoed,
        row_local_plan=tuple(
            (int(candidate.cut.row), int(candidate.lo),
             int(candidate.hi), int(candidate.phase))
            for candidate in choice),
        _final_mcu_ins=int(inserted),
        _final_mcu_drop=int(dropped),
    )
    return corrected, final_owner, stats


def _stitch_mcu_row_bands(rgb: np.ndarray, mcus_x: int,
                          mcu_w: int, mcu_h: int,
                          *, base_owner: np.ndarray | None = None,
                          valid_sources: np.ndarray | None = None,
                          source_count: int | None = None,
                          loss_budget: int | None = None,
                          _rounds_remaining: int = 2,
                          _global_fine_before=None,
                          _global_coarse_before=None,
                          _global_soft_before=None,
                          _global_soft_coarse_before=None,
                          _global_multistrip_before=None):
    """Select a safe residual row plan and apply it without side wrapping."""
    zero = dict(
        row_shift_plan=0, row_shift_rounds=0,
        row_shifted=0, row_shift_passes=0,
        row_shift_gain=0.0, row_shift_margin=0.0,
        row_residual_before=0.0, row_residual_after=0.0,
        row_residual_n_before=0, row_residual_n_after=0,
        row_shift_veto=0)
    if (rgb.ndim != 3 or rgb.shape[2] < 2 or mcus_x < 4
            or mcu_w <= 0 or mcu_h <= 0
            or rgb.shape[1] != mcus_x * mcu_w):
        return rgb, zero
    mcus_y = (rgb.shape[0] + mcu_h - 1) // mcu_h
    total = mcus_x * mcus_y
    if mcus_y < 2:
        return rgb, zero
    if base_owner is None:
        base_owner = np.arange(total, dtype=np.int64)
    else:
        base_owner = np.asarray(base_owner, dtype=np.int64)
    if base_owner.shape != (total,):
        return rgb, zero
    if valid_sources is None:
        valid_sources = np.ones(total, dtype=bool)
    else:
        valid_sources = np.asarray(valid_sources, dtype=bool)
    if valid_sources.shape != (total,):
        return rgb, zero
    if source_count is None:
        source_count = int(np.count_nonzero(valid_sources))
    if loss_budget is None:
        loss_budget = max(mcus_x, int(np.ceil(total * 0.05)))

    fine_before = _detect_residual_row_seams(
        rgb, mcus_x, mcu_w, mcu_h)
    fine_summary = _row_residual_summary(fine_before)
    multistrip_before = _detect_multistrip_row_seams(
        rgb, mcus_x, mcu_w, mcu_h)
    multistrip_summary = _row_residual_summary(multistrip_before)
    base_stats = dict(zero)
    base_stats.update(
        row_residual_before=fine_summary['score'],
        row_residual_after=fine_summary['score'],
        row_residual_n_before=fine_summary['n'],
        row_residual_n_after=fine_summary['n'])
    if (fine_summary['score'] <= 0.0
            and multistrip_summary['score'] <= 0.0):
        return rgb, base_stats
    coarse_before = _detect_residual_row_seams(
        rgb, mcus_x, mcu_w, mcu_h, step_rows=2,
        strip_h=mcu_h, min_gain=0.12)
    soft_before = _detect_residual_row_seams(
        rgb, mcus_x, mcu_w, mcu_h,
        min_gain=0.10, min_score=0.15, min_margin=0.06)
    soft_coarse_before = _detect_residual_row_seams(
        rgb, mcus_x, mcu_w, mcu_h, step_rows=2, strip_h=mcu_h,
        min_gain=0.10, min_score=0.15, min_margin=0.06)
    if _global_fine_before is None:
        _global_fine_before = fine_before
    if _global_coarse_before is None:
        _global_coarse_before = coarse_before
    if _global_soft_before is None:
        _global_soft_before = soft_before
    if _global_soft_coarse_before is None:
        _global_soft_coarse_before = soft_coarse_before
    if _global_multistrip_before is None:
        _global_multistrip_before = multistrip_before

    best_candidate = None
    vetoed = 0

    def consider(rank, row_phases, generator):
        nonlocal best_candidate, vetoed
        events = generator['events']
        if not events or not np.any(row_phases):
            return
        # A cyclic score cannot distinguish the two flat-scan directions at
        # exactly half an even-width row.  Do not choose an arbitrary side.
        if (mcus_x % 2 == 0
                and np.any(np.abs(row_phases) == mcus_x // 2)):
            vetoed += 1
            return
        candidate, final_owner = _apply_mcu_row_plan(
            rgb, mcus_x, mcu_w, mcu_h, row_phases, base_owner)
        inserted, dropped = _owner_placement_loss(
            final_owner, valid_sources, source_count)
        if max(inserted, dropped) > loss_budget:
            vetoed += 1
            return
        fine_after = _detect_residual_row_seams(
            candidate, mcus_x, mcu_w, mcu_h)
        coarse_after = _detect_residual_row_seams(
            candidate, mcus_x, mcu_w, mcu_h, step_rows=2,
            strip_h=mcu_h, min_gain=0.12)
        soft_after = _detect_residual_row_seams(
            candidate, mcus_x, mcu_w, mcu_h,
            min_gain=0.10, min_score=0.15, min_margin=0.06)
        soft_coarse_after = _detect_residual_row_seams(
            candidate, mcus_x, mcu_w, mcu_h, step_rows=2, strip_h=mcu_h,
            min_gain=0.10, min_score=0.15, min_margin=0.06)
        multistrip_after = _detect_multistrip_row_seams(
            candidate, mcus_x, mcu_w, mcu_h)
        if (not _row_candidate_safe(
                    fine_before, fine_after,
                    require_reduction=fine_summary['score'] > 0.0)
                or not _row_candidate_safe(
                    multistrip_before, multistrip_after,
                    require_reduction=multistrip_summary['score'] > 0.0)
                or not _row_candidate_safe(
                    coarse_before, coarse_after, require_reduction=False)
                or not _row_candidate_safe(
                    _global_fine_before, fine_after,
                    require_reduction=_row_residual_summary(
                        _global_fine_before)['score'] > 0.0)
                or not _row_candidate_safe(
                    _global_multistrip_before, multistrip_after,
                    require_reduction=_row_residual_summary(
                        _global_multistrip_before)['score'] > 0.0)
                or not _row_candidate_safe(
                    _global_coarse_before, coarse_after,
                    require_reduction=False)):
            vetoed += 1
            return
        if (rank >= 3
                and (not _row_candidate_safe(
                         soft_before, soft_after,
                         require_reduction=_row_residual_summary(
                             soft_before)['score'] > 0.0)
                     or not _row_candidate_safe(
                         soft_coarse_before, soft_coarse_after,
                         require_reduction=False)
                     or not _row_candidate_safe(
                         _global_soft_before, soft_after,
                         require_reduction=_row_residual_summary(
                             _global_soft_before)['score'] > 0.0)
                     or not _row_candidate_safe(
                         _global_soft_coarse_before, soft_coarse_after,
                         require_reduction=False))):
            vetoed += 1
            return
        summary = _row_residual_summary(fine_after)
        coarse_summary = _row_residual_summary(coarse_after)
        soft_summary = _row_residual_summary(soft_after)
        soft_coarse_summary = _row_residual_summary(soft_coarse_after)
        multistrip_after_summary = _row_residual_summary(multistrip_after)
        key = (summary['score'], multistrip_after_summary['score'],
               coarse_summary['score'], soft_summary['score'],
               soft_coarse_summary['score'],
               summary['n'], coarse_summary['n'],
               summary['maximum'], coarse_summary['maximum'],
               max(inserted, dropped), rank)
        item = (key, rank, candidate, generator, summary,
                multistrip_after_summary,
                inserted, dropped, final_owner)
        if best_candidate is None or key < best_candidate[0]:
            best_candidate = item
        return key

    for rank, plan in ((1, (1,)), (2, (2, 1))):
        row_phases, generator = _build_mcu_row_plan(
            rgb, mcus_x, mcu_w, mcu_h, plan)
        consider(rank, row_phases, generator)

    # Once a normal A/B round has exposed only a few residual boundaries, try
    # a bounded direct chain.  It is disabled on the initial image so a lone
    # natural seam cannot bootstrap its own correction evidence.
    if (_rounds_remaining == 1
            and fine_summary['n'] <= 3
            and len(coarse_before['events']) <= 3):
        direct_complete = False
        # Several adjacent narrow boundaries can be the two ends of displaced
        # one-row bands.  Seed them together so suffix phases compose once;
        # trying only one first can move the other boundary and create a
        # different, lossier chain.
        if multistrip_before['events']:
            row_phases, generator = _build_residual_chain_plan(
                rgb, mcus_x, mcu_w, mcu_h,
                multistrip_before['events'])
            key = consider(3, row_phases, generator)
            direct_complete = bool(
                key is not None
                and all(value <= 0.0 for value in key[:4]))
        seeds = []
        markers = set()
        seed_pool = sorted(
            fine_before['events'] + coarse_before['events']
            + soft_before['events'] + soft_coarse_before['events']
            + multistrip_before['events'],
            key=lambda event: -float(event['gain']))
        for seed in seed_pool:
            marker = (int(seed['y']), int(seed['phase']))
            if marker in markers:
                continue
            markers.add(marker)
            seeds.append(seed)
            if len(seeds) >= 6:
                break
        if not direct_complete:
            for seed in seeds:
                row_phases, generator = _build_residual_chain_plan(
                    rgb, mcus_x, mcu_w, mcu_h, seed)
                key = consider(3, row_phases, generator)
                if (key is not None
                        and all(value <= 0.0 for value in key[:4])):
                    break

    if best_candidate is None:
        base_stats['row_shift_veto'] = vetoed
        return rgb, base_stats
    (_key, rank, selected, generator, summary, selected_multistrip,
     inserted, dropped, final_owner) = best_candidate
    events = generator['events']
    selected_stats = {
        'row_shift_plan': rank,
        'row_shift_rounds': 1,
        'row_shifted': len(events),
        'row_shift_passes': int(generator['passes']),
        'row_shift_gain': float(min(event[3] for event in events)),
        'row_shift_margin': float(min(event[4] for event in events)),
        'row_residual_before': fine_summary['score'],
        'row_residual_after': summary['score'],
        'row_residual_n_before': fine_summary['n'],
        'row_residual_n_after': summary['n'],
        'row_shift_veto': vetoed,
        '_final_mcu_ins': inserted,
        '_final_mcu_drop': dropped,
        '_final_owner': final_owner,
    }
    if (_rounds_remaining <= 1
            or (summary['score'] <= 0.0
                and selected_multistrip['score'] <= 0.0)):
        return selected, selected_stats

    refined, refinement = _stitch_mcu_row_bands(
        selected, mcus_x, mcu_w, mcu_h,
        base_owner=final_owner, valid_sources=valid_sources,
        source_count=source_count, loss_budget=loss_budget,
        _rounds_remaining=_rounds_remaining - 1,
        _global_fine_before=_global_fine_before,
        _global_coarse_before=_global_coarse_before,
        _global_soft_before=_global_soft_before,
        _global_soft_coarse_before=_global_soft_coarse_before,
        _global_multistrip_before=_global_multistrip_before)
    selected_stats['row_shift_veto'] += int(
        refinement.get('row_shift_veto', 0))
    if not refinement.get('row_shift_plan'):
        return selected, selected_stats

    # A second locally-safe round must also remain safe against the original
    # pre-row image; otherwise weak newly-created seams could ratchet upward.
    fine_refined = _detect_residual_row_seams(
        refined, mcus_x, mcu_w, mcu_h)
    coarse_refined = _detect_residual_row_seams(
        refined, mcus_x, mcu_w, mcu_h, step_rows=2,
        strip_h=mcu_h, min_gain=0.12)
    multistrip_refined = _detect_multistrip_row_seams(
        refined, mcus_x, mcu_w, mcu_h)
    if (not _row_candidate_safe(
                fine_before, fine_refined,
                require_reduction=fine_summary['score'] > 0.0)
            or not _row_candidate_safe(
                multistrip_before, multistrip_refined,
                require_reduction=multistrip_summary['score'] > 0.0)
            or not _row_candidate_safe(
                coarse_before, coarse_refined, require_reduction=False)):
        selected_stats['row_shift_veto'] += 1
        return selected, selected_stats

    refined_summary = _row_residual_summary(fine_refined)
    selected_stats.update(
        row_shift_rounds=(1 + int(refinement['row_shift_rounds'])),
        row_shifted=(selected_stats['row_shifted']
                     + int(refinement['row_shifted'])),
        row_shift_passes=(selected_stats['row_shift_passes']
                          + int(refinement['row_shift_passes'])),
        row_shift_gain=min(selected_stats['row_shift_gain'],
                           float(refinement['row_shift_gain'])),
        row_shift_margin=min(selected_stats['row_shift_margin'],
                             float(refinement['row_shift_margin'])),
        row_residual_after=refined_summary['score'],
        row_residual_n_after=refined_summary['n'],
        _final_mcu_ins=int(refinement['_final_mcu_ins']),
        _final_mcu_drop=int(refinement['_final_mcu_drop']),
        _final_owner=refinement['_final_owner'])
    return refined, selected_stats


def _mcu_placement_stats(total: int, spans: list[tuple[int, int]],
                         offsets: list) -> tuple[int, int]:
    """Count blank original slots and discarded sources without RGB copies.

    ``None`` deliberately discards a whole unreliable band.  It still counts
    as valid source material for both insertion and loss accounting.
    """
    occupied = np.zeros(total, dtype=bool)
    originally_valid = np.zeros(total, dtype=bool)
    source_count = 0
    for (start, end), offset in zip(spans, offsets):
        originally_valid[start:end] = True
        source_count += end - start
        if offset is None:
            continue
        target_start = max(0, start + int(offset))
        target_end = min(total, end + int(offset))
        if target_end > target_start:
            occupied[target_start:target_end] = True
    inserted = int(np.count_nonzero(originally_valid & ~occupied))
    dropped = int(source_count - np.count_nonzero(occupied))
    return inserted, dropped


def _mcu_owner_map(total: int, spans: list[tuple[int, int]],
                   offsets: list) -> tuple[np.ndarray, np.ndarray]:
    """Map target MCU slots to source indices and source-segment labels."""
    owner = np.full(total, -1, dtype=np.int64)
    labels = np.full(total, -1, dtype=np.int32)
    for label, ((start, end), offset) in enumerate(zip(spans, offsets)):
        if offset is None:
            continue
        offset = int(offset)
        source_start = max(start, -offset)
        source_end = min(end, total - offset)
        if source_end <= source_start:
            continue
        target_start = source_start + offset
        target_end = source_end + offset
        owner[target_start:target_end] = np.arange(
            source_start, source_end, dtype=np.int64)
        labels[target_start:target_end] = label
    return owner, labels


def _edge_pair_cost(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Mean absolute green-channel step for corresponding MCU edge strips."""
    return np.mean(
        np.abs(first.astype(np.int16) - second.astype(np.int16)),
        axis=tuple(range(1, first.ndim)), dtype=np.float64)


def _layout_edge_costs(owner: np.ndarray, mcus_x: int,
                       left: np.ndarray, right: np.ndarray,
                       top: np.ndarray, bottom: np.ndarray) -> np.ndarray:
    """Return all valid horizontal and vertical MCU-boundary costs."""
    grid = owner.reshape(-1, mcus_x)
    h_first = grid[:, :-1].ravel()
    h_second = grid[:, 1:].ravel()
    h_valid = (h_first >= 0) & (h_second >= 0)
    v_first = grid[:-1].ravel()
    v_second = grid[1:].ravel()
    v_valid = (v_first >= 0) & (v_second >= 0)
    costs = []
    if h_valid.any():
        costs.append(_edge_pair_cost(
            right[h_first[h_valid]], left[h_second[h_valid]]))
    if v_valid.any():
        costs.append(_edge_pair_cost(
            bottom[v_first[v_valid]], top[v_second[v_valid]]))
    return np.concatenate(costs) if costs else np.empty(0, np.float64)


def _vertical_transition_metrics(total: int,
                                 spans: list[tuple[int, int]],
                                 offsets: list[int], boundary: int,
                                 mcus_x: int, top: np.ndarray,
                                 bottom: np.ndarray):
    """Return DC-insensitive continuity metrics across one resync cut."""
    owner, labels = _mcu_owner_map(total, spans, offsets)
    grid_owner = owner.reshape(-1, mcus_x)
    grid_labels = labels.reshape(-1, mcus_x)
    first = grid_owner[:-1].ravel()
    second = grid_owner[1:].ravel()
    first_label = grid_labels[:-1].ravel()
    second_label = grid_labels[1:].ravel()
    valid = (
        (first >= 0) & (second >= 0)
        & (first_label != second_label)
        & ((first_label == boundary) | (second_label == boundary))
    )
    count = int(np.count_nonzero(valid))
    if count == 0:
        return None

    first_edge = bottom[first[valid]].astype(np.int16)
    second_edge = top[second[valid]].astype(np.int16)
    delta = second_edge - first_edge
    center = np.median(delta, axis=(1, 2))
    residual = np.median(
        np.abs(delta - center[:, np.newaxis, np.newaxis]), axis=(1, 2))

    first_grad = np.diff(first_edge.mean(axis=1, dtype=np.float32), axis=1)
    second_grad = np.diff(second_edge.mean(axis=1, dtype=np.float32), axis=1)
    numer = np.sum(first_grad * second_grad, axis=1)
    denom = np.sqrt(np.sum(first_grad * first_grad, axis=1)
                    * np.sum(second_grad * second_grad, axis=1))
    corr = np.zeros_like(numer, dtype=np.float32)
    nonflat = denom > 1e-6
    corr[nonflat] = numer[nonflat] / denom[nonflat]
    both_flat = (~nonflat
                 & (np.max(np.abs(first_grad), axis=1) < 1e-6)
                 & (np.max(np.abs(second_grad), axis=1) < 1e-6))
    corr[both_flat] = 1.0
    decor = 1.0 - corr
    return float(np.median(residual)), float(np.median(decor)), count


def _transition_quality_safe(total: int, spans: list[tuple[int, int]],
                             previous_offsets: list[int], candidate: int,
                             inherited: int, boundary: int, mcus_x: int,
                             top: np.ndarray, bottom: np.ndarray) -> bool:
    """Veto only a jointly and substantially worse resync-cut alignment.

    Some genuine corrections sacrifice the damaged local cut to align a much
    longer suffix, so ordinary worsening is allowed.  A large simultaneous
    rise in centered residual and gradient decorrelation is strong evidence of
    a persistent natural vertical seam rather than a raster wrap.
    """
    prefix_spans = spans[:boundary + 1]
    inherited_metrics = _vertical_transition_metrics(
        total, prefix_spans, previous_offsets + [inherited], boundary,
        mcus_x, top, bottom)
    candidate_metrics = _vertical_transition_metrics(
        total, prefix_spans, previous_offsets + [candidate], boundary,
        mcus_x, top, bottom)
    if inherited_metrics is None or candidate_metrics is None:
        return True
    inherited_residual, inherited_decor, inherited_count = inherited_metrics
    candidate_residual, candidate_decor, candidate_count = candidate_metrics
    min_pairs = max(24, mcus_x // 2)
    if min(inherited_count, candidate_count) < min_pairs:
        return True
    residual_worse = (
        candidate_residual - inherited_residual
        >= max(2.0, inherited_residual * 0.25))
    decor_worse = candidate_decor - inherited_decor >= 0.10
    return not (residual_worse and decor_worse)


def _layout_quality_metrics(before: np.ndarray, after: np.ndarray):
    """Return raw, winsorized, and high-tail layout edge statistics."""
    if before.size == 0 or after.size == 0:
        return None
    before_mean = float(np.mean(before))
    after_mean = float(np.mean(after))
    median = float(np.median(before))
    mad = float(np.median(np.abs(before - median)))
    high_threshold = max(12.0, median + 3.0 * 1.4826 * mad)
    before_capped = float(np.mean(np.minimum(before, high_threshold)))
    after_capped = float(np.mean(np.minimum(after, high_threshold)))
    before_high = float(np.mean(before >= high_threshold))
    after_high = float(np.mean(after >= high_threshold))
    return (before_mean, after_mean, before_capped, after_capped,
            before_high, after_high)


def _layout_quality_safe(before: np.ndarray, after: np.ndarray) -> bool:
    """Reject a shift layout that materially adds global MCU seams."""
    metrics = _layout_quality_metrics(before, after)
    if metrics is None:
        return False
    before_mean, after_mean, _before_capped, _after_capped, \
        before_high, after_high = metrics
    return (after_mean <= before_mean * 1.005
            and after_high <= before_high + 0.005)


def _materialize_phase_offsets(spans, estimates, deleted, mcus_x: int,
                               forced=None, relaxed_updates: bool = False):
    """Choose whole-row representatives without relaxed-anchor drift."""
    forced = forced or {}
    offsets = []
    trusted = 0
    previous = 0
    for index, _span in enumerate(spans):
        if index in deleted:
            offsets.append(None)
            continue
        if index in forced:
            offset, updates_anchor = forced[index]
            offset = int(offset)
            if updates_anchor:
                trusted = offset
        else:
            estimate = estimates[index]
            if estimate is None:
                offset = previous
            else:
                reference = previous if relaxed_updates else trusted
                offset = _unwrap_phase(
                    int(estimate['phase']), reference, mcus_x)
                if estimate['confident'] or relaxed_updates:
                    trusted = offset
        offsets.append(offset)
        previous = offset
    return offsets


def _layout_for_offsets(total, spans, offsets, mcus_x,
                        left, right, top, bottom):
    """Return placement loss, edge vector, and mean for a candidate layout."""
    inserted, dropped = _mcu_placement_stats(total, spans, offsets)
    owner, _labels = _mcu_owner_map(total, spans, offsets)
    costs = _layout_edge_costs(
        owner, mcus_x, left, right, top, bottom)
    mean = float(np.mean(costs)) if costs.size else float('inf')
    return inserted, dropped, costs, mean


def _loss_key(inserted: int, dropped: int):
    return max(inserted, dropped), inserted + dropped


def _layout_after_metrics(before: np.ndarray, after: np.ndarray):
    """Return comparable raw, capped, and high-tail values for one layout."""
    metrics = _layout_quality_metrics(before, after)
    if metrics is None:
        return None
    return metrics[1], metrics[3], metrics[5]


def _layout_dominates(first, second) -> bool:
    """Whether one layout is materially better without regressing any axis."""
    if first is None or second is None:
        return False
    raw, capped, high = first
    other_raw, other_capped, other_high = second
    no_regression = (
        raw <= other_raw * 1.001
        and capped <= other_capped * 1.001
        and high <= other_high + 0.001)
    material = (
        raw <= other_raw * 0.998
        or capped <= other_capped * 0.998
        or high <= other_high - 0.001)
    return bool(no_regression and material)


def _two_band_top_offsets(offsets, top_estimate, mcus_x: int):
    """Build a common-rotation candidate instead of pinning the top at zero."""
    if (len(offsets) != 2 or offsets[0] is None or offsets[1] is None
            or top_estimate is None
            or int(top_estimate.get('pairs', 0)) < 2):
        return None
    top = _unwrap_phase(int(top_estimate['phase']), 0, mcus_x)
    if top == int(offsets[0]):
        return None
    lower = _unwrap_phase(int(offsets[1]) % mcus_x, top, mcus_x)
    return [top, lower]


def _correct_segment_shifts(dec, rgb: np.ndarray, segments, frontier: int,
                            phase_cuts=None):
    """Align every repair band by inserting, deleting, or discarding MCUs.

    Local repeated-wrap evidence is primary.  Full-width left/right boundary
    signatures may replace it only when they form a well-supported relation
    and also reduce placement loss without worsening the whole layout.  This
    avoids mistaking a natural internal seam for the row wrap.  The first band
    remains an absolute MCU-zero anchor unless recovery actually resumed MCU 0
    at a nonzero bit position; later damage cannot move an already decoded
    prefix retroactively.
    """
    zero = dict(
        shifted=0, top_shifted=0, mcu_ins=0, mcu_drop=0,
        shift_margin=0.0, shift_reject=0,
        row_shift_plan=0, row_shift_rounds=0,
        row_shifted=0, row_shift_passes=0,
        row_shift_gain=0.0, row_shift_margin=0.0,
        row_residual_before=0.0, row_residual_after=0.0,
        row_residual_n_before=0, row_residual_n_after=0,
        row_shift_veto=0,
        row_global_passes=0, row_global_events=0,
        row_global_explored=0, row_global_unsafe_passes=0,
        row_global_changes=0, row_global_gain=0.0,
        row_global_margin=0.0,
        row_global_residual_before=0.0,
        row_global_residual_after=0.0,
        row_global_relaxed_after=0.0,
        row_global_veto=0, row_global_plan=(), row_global_trace=(),
        row_local_cuts=0, row_local_intervals=0,
        row_local_exact=0, row_local_soft=0,
        row_local_veto=0, row_local_plan=())
    # An explicitly empty cut trace is positive evidence that recovery made no
    # edit or resync transition.  Do not let image-only row detectors invent a
    # spatial correction in that case.  ``None`` remains the legacy API that
    # derives coarse resync boundaries from ``segments`` below.
    if phase_cuts is not None and len(phase_cuts) == 0:
        return rgb, zero
    ordered = sorted(segments, key=lambda seg: (int(seg[0]), int(seg[1])))
    total = dec.mcus_x * dec.mcus_y
    frontier = max(0, min(int(frontier), total))
    if not ordered or frontier <= 0:
        return rgb, zero

    cut_kinds = {}
    cut_hints = {}
    zero_resync_cut = False
    if phase_cuts is None:
        for start, bit, _dc in ordered:
            start = max(0, min(int(start), frontier))
            kind = 'anchor' if start == 0 and int(bit) == 0 else 'resync'
            cut_kinds[start] = kind
            cut_hints[start] = None
    else:
        cut_kinds = {0: 'anchor'}
        cut_hints = {0: None}
        for item in phase_cuts:
            start, kind = item[:2]
            hint = item[2] if len(item) > 2 else None
            raw_start = int(start)
            start = max(0, min(raw_start, frontier))
            kind = str(kind)
            zero_resync_cut = zero_resync_cut or (
                raw_start == 0 and kind == 'resync')
            cut_kinds[start] = kind
            cut_hints[start] = hint
    starts = sorted(start for start in cut_kinds if start < frontier)
    if not starts:
        return rgb, zero
    spans = [(start, starts[index + 1] if index + 1 < len(starts) else frontier)
             for index, start in enumerate(starts)]

    coarse_starts = {
        max(0, min(int(segment[0]), frontier))
        for segment in ordered if int(segment[0]) < frontier
    }

    def reject_or_coarse():
        """Fall back to the proven resync-only layout if fine cuts are unsafe."""
        if phase_cuts is not None and len(coarse_starts) < len(starts):
            coarse_rgb, coarse_stats = _correct_segment_shifts(
                dec, rgb, segments, frontier, phase_cuts=None)
            if (not coarse_stats['shifted']
                    and not coarse_stats['shift_reject']):
                coarse_stats = dict(coarse_stats)
                coarse_stats['shift_reject'] = 1
            return coarse_rgb, coarse_stats
        rejected = dict(zero)
        rejected['shift_reject'] = 1
        return rgb, rejected

    mcu_w = 8 * int(dec.hmax)
    mcu_h = 8 * int(dec.vmax)
    padded_shape = (dec.mcus_y * mcu_h, dec.mcus_x * mcu_w, 3)
    working_rgb = rgb
    if rgb.shape != padded_shape:
        working_rgb = dec.to_rgb(crop=False)
    edge_w = min(2, mcu_w)
    edge_h = min(2, mcu_h)
    green_tiles = _mcu_green_tiles(
        working_rgb, dec.mcus_x, dec.mcus_y, mcu_w, mcu_h)
    left = np.ascontiguousarray(green_tiles[:, :, :edge_w])
    right = np.ascontiguousarray(green_tiles[:, :, -edge_w:])
    top = np.ascontiguousarray(green_tiles[:, :edge_h, :])
    bottom = np.ascontiguousarray(green_tiles[:, -edge_h:, :])
    del green_tiles

    estimates = [
        _adaptive_phase_estimate(left, right, start, end, dec.mcus_x)
        for start, end in spans
    ]

    # Discard sub-three-row internal bands only when their aggregate loss is
    # small.  This removes unstable fragments in sparse-repair files while a
    # densely damaged file keeps and estimates every short band.
    short = {
        index for index, (start, end) in enumerate(spans)
        if 0 < index < len(spans) - 1 and end - start < 3 * dec.mcus_x
    }
    short_total = sum(spans[index][1] - spans[index][0] for index in short)
    short_budget = max(dec.mcus_x, int(np.ceil(frontier * 0.03)))
    deleted = short if short_total <= short_budget else set()

    top_estimate = estimates[0]
    zero_resync_segment = any(
        int(start) == 0 and int(bit) != 0
        for start, bit, _dc in ordered)
    # Both records are required.  A phase cut without a nonzero-bit segment is
    # incomplete metadata, while a duplicate segment alone may be a legacy or
    # synthetic caller that did not prove an accepted MCU-zero resync.
    real_zero_resync = bool(zero_resync_cut and zero_resync_segment)
    top_movable = real_zero_resync
    top_is_short = spans[0][1] - spans[0][0] < 3 * dec.mcus_x
    base_forced = {} if top_movable else {0: (0, True)}
    strict_offsets = _materialize_phase_offsets(
        spans, estimates, deleted, dec.mcus_x, base_forced)
    previous_offsets = _materialize_phase_offsets(
        spans, estimates, deleted, dec.mcus_x, base_forced,
        relaxed_updates=True)
    # Relaxed evidence is allowed to guide its immediate neighbor (essential
    # for dense repair traces), but not to accumulate an entire-row vertical
    # branch drift.  If that happens, fall back to last-strict representatives;
    # modulo-W left/right phases remain identical.
    previous_runaway = any(
        offset is not None and abs(int(offset)) >= dec.mcus_x
        for offset in previous_offsets)
    offsets = strict_offsets if previous_runaway else previous_offsets
    relaxed_updates = not previous_runaway
    selected_inserted, selected_dropped, selected_costs, selected_mean = (
        _layout_for_offsets(
            total, spans, offsets, dec.mcus_x,
            left, right, top, bottom))

    # Build exact boundary signatures for medium/long bands.  Correlate only
    # consecutive eligible bands, bridging bands too short to form a profile.
    signatures = {}
    for index, (start, end) in enumerate(spans):
        if index in deleted:
            continue
        signature = _boundary_signature(
            left, right, start, end, dec.mcus_x)
        if signature is not None:
            signatures[index] = signature
    eligible = sorted(signatures)
    signature_edges = []
    for first_index, second_index in zip(eligible, eligible[1:]):
        start = spans[second_index][0]
        hint = (cut_hints.get(start)
                if cut_kinds.get(start) == 'resync' else None)
        relation = _signature_correlation(
            signatures[first_index], signatures[second_index],
            dec.mcus_x, hint)
        relation.update(first=first_index, second=second_index)
        signature_edges.append(relation)

    # Reliable edges form small relative-phase components.  Sweep their common
    # rotation unless the component starts at the absolute MCU-zero anchor.
    # Placement loss decides first; full-layout edge quality breaks ties and
    # vetoes regressions.
    components = []
    nodes = []
    component_edges = []
    for relation in signature_edges:
        if relation['strong']:
            if component_edges and nodes[-1] != relation['first']:
                components.append((nodes, component_edges))
                nodes, component_edges = [], []
            if not component_edges:
                nodes = [relation['first']]
            nodes.append(relation['second'])
            component_edges.append(relation)
        elif component_edges:
            components.append((nodes, component_edges))
            nodes, component_edges = [], []
    if component_edges:
        components.append((nodes, component_edges))

    strong_forced = dict(base_forced)
    accepted_signature_margins = []
    for component_nodes, relations in components:
        if len(relations) < 2:
            continue
        first_index = component_nodes[0]
        reference = offsets[first_index]
        reference = int(reference) if reference is not None else 0
        best = None
        best_loss = None
        tied = []
        rotations = ((0,) if not top_movable and first_index == 0
                     else range(dec.mcus_x))
        for rotation in rotations:
            absolute = (0 if not top_movable and first_index == 0
                        else _unwrap_phase(rotation, reference, dec.mcus_x))
            proposal = dict(strong_forced)
            proposal[first_index] = (absolute, True)
            for relation in relations:
                for bridge in range(
                        relation['first'] + 1, relation['second']):
                    bridge_estimate = estimates[bridge]
                    if (bridge not in signatures
                            and (bridge_estimate is None
                                 or not bridge_estimate['confident'])):
                        proposal[bridge] = (absolute, False)
                absolute += int(relation['delta'])
                proposal[relation['second']] = (absolute, True)
            bridge = component_nodes[-1] + 1
            while bridge < len(spans) and bridge not in signatures:
                bridge_estimate = estimates[bridge]
                if (bridge_estimate is not None
                        and bridge_estimate['confident']):
                    break
                proposal[bridge] = (absolute, False)
                bridge += 1
            candidate = _materialize_phase_offsets(
                spans, estimates, deleted, dec.mcus_x, proposal,
                relaxed_updates=relaxed_updates)
            inserted, dropped = _mcu_placement_stats(total, spans, candidate)
            key = _loss_key(inserted, dropped)
            if best_loss is None or key < best_loss:
                best_loss = key
                tied = [(candidate, proposal, inserted, dropped)]
            elif key == best_loss:
                tied.append((candidate, proposal, inserted, dropped))
        for candidate, proposal, inserted, dropped in tied:
            _ins, _drop, costs, mean = _layout_for_offsets(
                total, spans, candidate, dec.mcus_x,
                left, right, top, bottom)
            key = (mean, tuple(0 if value is None else abs(value)
                               for value in candidate))
            if best is None or key < best[0]:
                best = (key, candidate, proposal, inserted, dropped,
                        costs, mean)

        if best is None:
            continue
        (_key, candidate, proposal, inserted, dropped,
         candidate_costs, candidate_mean) = best
        current_loss = _loss_key(selected_inserted, selected_dropped)
        candidate_loss = _loss_key(inserted, dropped)
        improves = (candidate_loss < current_loss
                    or (candidate_loss == current_loss
                        and candidate_mean < selected_mean))
        if improves and _layout_quality_safe(
                selected_costs, candidate_costs):
            offsets = candidate
            strong_forced = proposal
            selected_inserted, selected_dropped = inserted, dropped
            selected_costs, selected_mean = candidate_costs, candidate_mean
            accepted_signature_margins.extend(
                relation['margin'] for relation in relations)

    # A very strong medium-support signature may repair one relaxed local
    # phase.  It cannot override a strict estimate or become a later anchor,
    # and is accepted only when it lowers loss and passes the layout guard.
    auxiliary_anchors = tuple(offsets)
    for relation in signature_edges:
        second_index = relation['second']
        estimate = estimates[second_index]
        if (not relation['auxiliary'] or second_index in strong_forced
                or estimate is None or estimate['confident']
                or int(estimate.get('pairs', 0)) < 10
                or auxiliary_anchors[relation['first']] is None
                or offsets[second_index] is None):
            continue
        candidate = list(offsets)
        candidate[second_index] = (
            int(auxiliary_anchors[relation['first']])
            + int(relation['delta']))
        inserted, dropped, costs, mean = _layout_for_offsets(
            total, spans, candidate, dec.mcus_x,
            left, right, top, bottom)
        if (_loss_key(inserted, dropped)
                < _loss_key(selected_inserted, selected_dropped)
                and _layout_quality_safe(selected_costs, costs)):
            offsets = candidate
            selected_inserted, selected_dropped = inserted, dropped
            selected_costs, selected_mean = costs, mean
            accepted_signature_margins.append(relation['margin'])

    # Two-band files whose MCU-zero anchor was genuinely lost may need an
    # explicit competition with a common rotation suggested by the top band.
    # Applying the lower absolute phase alone is unsafe when both bands share
    # the same nonzero phase; conversely, always moving the top breaks files
    # whose top estimate is a natural-edge false positive.
    loss_budget = int(np.ceil(total * 0.05))
    before_owner, _before_labels = _mcu_owner_map(
        total, spans, [0] * len(spans))
    before_costs = _layout_edge_costs(
        before_owner, dec.mcus_x, left, right, top, bottom)
    top_candidate = None
    if (len(spans) == 2 and top_movable
            and offsets[0] is not None and int(offsets[0]) == 0):
        top_candidate = _two_band_top_offsets(
            offsets, top_estimate, dec.mcus_x)
    if top_candidate is not None:
        cand_ins, cand_drop, cand_costs, cand_mean = _layout_for_offsets(
            total, spans, top_candidate, dec.mcus_x,
            left, right, top, bottom)

        def transition_safe(candidate):
            if int(candidate[0]) == int(candidate[1]):
                return True
            return _transition_quality_safe(
                total, spans, [int(candidate[0])], int(candidate[1]),
                int(candidate[0]), 1, dec.mcus_x, top, bottom)

        current_safe = (
            max(selected_inserted, selected_dropped) <= loss_budget
            and transition_safe(offsets)
            and _layout_quality_safe(before_costs, selected_costs))
        candidate_safe = (
            max(cand_ins, cand_drop) <= loss_budget
            and transition_safe(top_candidate)
            and _layout_quality_safe(before_costs, cand_costs))
        current_metrics = _layout_after_metrics(
            before_costs, selected_costs)
        candidate_metrics = _layout_after_metrics(
            before_costs, cand_costs)
        baseline_metrics = _layout_after_metrics(
            before_costs, before_costs)
        top_pairs = int(top_estimate.get('pairs', 0))
        strong_top = (
            bool(top_estimate.get('confident'))
            or (top_pairs >= max(8, dec.mcus_x // 8)
                and float(top_estimate.get('score', 0.0)) >= 6.0
                and float(top_estimate.get('margin', 0.0)) >= 2.0
                and float(top_estimate.get('raw_ratio', 0.0)) >= 2.0))
        rescue = False
        if candidate_metrics is not None and baseline_metrics is not None:
            cand_raw, cand_capped, cand_high = candidate_metrics
            base_raw, base_capped, base_high = baseline_metrics
            rescue = (
                cand_high <= base_high + 0.001
                and (cand_raw <= base_raw * 0.995
                     or cand_capped <= base_capped * 0.995
                     or cand_high <= base_high - 0.001))

        # A confident top estimate can still be a persistent natural vertical
        # edge when it is supported by only a few row pairs.  Global layout
        # scores do not separate that case: the false common rotation can look
        # even better than a real repair.  Keep the normal path at five pairs;
        # the deliberately narrow low-support rescue requires a real resync,
        # two independently agreeing short bands, and no discarded source MCU.
        regular_evidence = top_pairs >= 5 and (strong_top or rescue)
        lower_start = starts[1]
        lower_estimate = estimates[1]
        hint = cut_hints.get(lower_start)
        low_support_common_rescue = (
            2 <= top_pairs < 5
            and phase_cuts is not None
            and cut_kinds.get(lower_start) == 'resync'
            and hint is not None
            and np.isfinite(float(hint))
            and float(hint) > 0.0
            and top_is_short
            and spans[1][1] - spans[1][0] < 3 * dec.mcus_x
            and lower_estimate is not None
            and bool(lower_estimate.get('confident'))
            and int(lower_estimate.get('pairs', 0)) >= 2
            and int(lower_estimate['phase']) == int(top_estimate['phase'])
            and int(top_candidate[0]) == int(top_candidate[1])
            and cand_drop == 0
            and not current_safe
            and candidate_safe
        )
        candidate_evidence = (
            regular_evidence or low_support_common_rescue)

        choose_candidate = False
        if candidate_safe and candidate_evidence:
            if not current_safe:
                choose_candidate = True
            elif _layout_dominates(candidate_metrics, current_metrics):
                choose_candidate = True
            elif not _layout_dominates(current_metrics, candidate_metrics):
                cand_key = (
                    _loss_key(cand_ins, cand_drop),
                    candidate_metrics[1], candidate_metrics[2],
                    candidate_metrics[0], abs(int(top_candidate[0])))
                current_key = (
                    _loss_key(selected_inserted, selected_dropped),
                    current_metrics[1], current_metrics[2],
                    current_metrics[0], abs(int(offsets[0])))
                choose_candidate = cand_key < current_key
        if choose_candidate:
            offsets = top_candidate
            selected_inserted, selected_dropped = cand_ins, cand_drop
            selected_costs, selected_mean = cand_costs, cand_mean

    if not top_movable:
        assert offsets[0] is not None and int(offsets[0]) == 0

    has_nonzero_phase = any(
        offset is not None and int(offset) != 0 for offset in offsets)
    if not has_nonzero_phase:
        # A discarded short fragment is not by itself evidence of horizontal
        # drift.  Preserve every span for the global/structural detectors.
        offsets = [0] * len(spans)
        (selected_inserted, selected_dropped,
         selected_costs, selected_mean) = _layout_for_offsets(
             total, spans, offsets, dec.mcus_x,
             left, right, top, bottom)
    shifted = sum(offset is None or int(offset) != 0 for offset in offsets)

    # A fixed-size canvas turns every clipped/overlapped source MCU into one
    # additional blank slot.  The cumulative segment+row layout may lose at
    # most ceil(total MCU * 5%); small images receive no one-row exemption.
    inserted, dropped = selected_inserted, selected_dropped
    if max(inserted, dropped) > loss_budget:
        return reject_or_coarse()
    active = [index for index, offset in enumerate(offsets)
              if offset is not None]
    if (len(spans) == 2 and len(active) == 2
            and int(offsets[active[0]]) != int(offsets[active[1]])
            and not _transition_quality_safe(
                total, spans,
                [int(offsets[active[0]])], int(offsets[active[1]]),
                int(offsets[active[0]]), active[1], dec.mcus_x,
                top, bottom)):
        return reject_or_coarse()
    after_owner, _after_labels = _mcu_owner_map(total, spans, offsets)
    after_costs = _layout_edge_costs(
        after_owner, dec.mcus_x, left, right, top, bottom)
    if not _layout_quality_safe(before_costs, after_costs):
        metrics = _layout_quality_metrics(before_costs, after_costs)
        trusted_local = sum(
            estimate is not None and estimate['confident']
            and offset is not None and int(offset) != 0
            for estimate, offset in zip(estimates, offsets))
        trusted_signature_chain = len(accepted_signature_margins) >= 2
        shift_first_safe = False
        if metrics is not None:
            (_before_mean, _after_mean, before_capped, after_capped,
             before_high, after_high) = metrics
            # Raw means can be dominated by a handful of damaged cross-band
            # cuts even when every long band becomes better aligned.  With at
            # least three active bands and independent phase evidence, cap
            # those same outliers at the already-accounted high-edge threshold.
            # The high-tail guard remains strict, so this is not a blanket
            # relaxation for natural vertical seams or two-band guesses.
            shift_first_safe = (
                len(active) >= 3
                and (trusted_local >= 1 or trusted_signature_chain)
                and after_capped <= before_capped * 1.005
                and after_high <= before_high + 0.005
            )
        if not shift_first_safe:
            return reject_or_coarse()
    del left, right, top, bottom, before_costs, after_costs, selected_costs
    corrected, _inserted, _dropped = _scatter_mcu_segments(
        working_rgb, dec.mcus_x, dec.mcus_y, mcu_w, mcu_h, spans, offsets)
    assert (inserted, dropped) == (_inserted, _dropped)
    valid_sources = np.zeros(total, dtype=bool)
    for start, end in spans:
        valid_sources[start:end] = True
    source_count = sum(end - start for start, end in spans)
    corrected, current_owner, global_stats = _correct_global_row_shifts(
        corrected, dec.mcus_x, mcu_w, mcu_h,
        base_owner=after_owner, valid_sources=valid_sources,
        source_count=source_count, loss_budget=loss_budget,
        max_passes=5)
    global_changed = int(global_stats.get('row_global_passes', 0)) > 0
    if global_changed:
        inserted = int(global_stats.pop('_final_mcu_ins', inserted))
        dropped = int(global_stats.pop('_final_mcu_drop', dropped))
        # A successful global fit already includes all strict and exposed
        # residual boundaries.  Running the structural selector afterwards
        # can re-fit a cut already represented in the absolute phase chain.
        local_stats = dict(
            row_local_cuts=0, row_local_intervals=0,
            row_local_exact=0, row_local_soft=0,
            row_local_veto=0, row_local_plan=())
        row_stats = dict(
            row_shift_plan=0, row_shift_rounds=0,
            row_shifted=0, row_shift_passes=0,
            row_shift_gain=0.0, row_shift_margin=0.0,
            row_residual_before=0.0, row_residual_after=0.0,
            row_residual_n_before=0, row_residual_n_after=0,
            row_shift_veto=0)
    else:
        corrected, current_owner, local_stats = (
            _correct_structural_row_shifts(
                corrected, dec.mcus_x, mcu_w, mcu_h, spans, offsets,
                after_owner, valid_sources, source_count, loss_budget))
        inserted = int(local_stats.pop('_final_mcu_ins', inserted))
        dropped = int(local_stats.pop('_final_mcu_drop', dropped))
        # This proven conservative stitch remains the fallback for files whose
        # strict global seed was absent or vetoed, including structural-only
        # short cut repairs.
        corrected, row_stats = _stitch_mcu_row_bands(
            corrected, dec.mcus_x, mcu_w, mcu_h,
            base_owner=current_owner, valid_sources=valid_sources,
            source_count=source_count, loss_budget=loss_budget)
        inserted = int(row_stats.pop('_final_mcu_ins', inserted))
        dropped = int(row_stats.pop('_final_mcu_drop', dropped))
        row_stats.pop('_final_owner', None)
    corrected = corrected[:int(dec.h.height), :int(dec.h.width)].copy()
    margins = [
        float(estimate['margin'])
        for estimate, offset in zip(estimates, offsets)
        if estimate is not None and estimate['confident']
        and offset is not None and int(offset) != 0
    ] + accepted_signature_margins
    return corrected, {
        'shifted': int(shifted),
        'top_shifted': int(offsets[0] is None or int(offsets[0]) != 0),
        'mcu_ins': inserted,
        'mcu_drop': dropped,
        'shift_margin': float(min(margins)) if margins else 0.0,
        'shift_reject': 0,
        **global_stats,
        **local_stats,
        **row_stats,
    }


def _probe(dec, buf, bit, mcu, dc, maxW, rate):
    """(bit, mcu, dc)에서 디코딩이 plausible하게 이어지는 MCU 수(clean run)."""
    return jd.decode_probe(buf, buf.size * 8, int(bit), int(dc[0]), int(dc[1]), int(dc[2]),
                           dec.hl, dec.hs, dec.dc_idx, dec.ac_idx, dec.qmat,
                           dec.hsamp, dec.vsamp, dec.mcus_x, dec.mcus_y, int(mcu),
                           maxW, _ZZ, DC_BOUND, AC_BOUND, rate)[0]


def _probe_stop(dec, buf, bit, mcu, dc, maxW, rate):
    """_probe + 정지 사유. 반환 (run, stop) — stop: 0=maxW 도달, 1=무효코드,
    2=계수오버플로, 3=버퍼끝(데이터 소진), 4=비트레이트."""
    r = jd.decode_probe(buf, buf.size * 8, int(bit), int(dc[0]), int(dc[1]), int(dc[2]),
                        dec.hl, dec.hs, dec.dc_idx, dec.ac_idx, dec.qmat,
                        dec.hsamp, dec.vsamp, dec.mcus_x, dec.mcus_y, int(mcu),
                        maxW, _ZZ, DC_BOUND, AC_BOUND, rate)
    return r[0], r[2]


def _decode_traj(dec, buf, segments, rate, stop=True):
    """세그먼트별로 디코드해 mb/dcr(절대 인덱스) + coef 그리드를 채운다.
    stop=True면 각 세그먼트가 첫 디싱크에서 멈춰 mb/dcr가 frontier까지 항상 유효.
    반환: (mb, dcr, frontier=마지막 세그먼트 정지 MCU)."""
    total = dec.mcus_x * dec.mcus_y
    dec.buf = buf
    dec.nbits = buf.size * 8
    dec.cy[:] = 0; dec.cb[:] = 0; dec.cr[:] = 0
    mb = np.zeros(total + 1, np.int64)
    dcr = np.zeros((total + 1, 3), np.int64)
    dcb, acb, rt = (DC_BOUND, AC_BOUND, rate) if stop else (jd.DISABLE, jd.DISABLE, jd.DISABLE)
    segs = sorted(segments) + [(total, 0, None)]
    frontier = 0
    for i in range(len(segs) - 1):
        sm, sbit, sdc = segs[i]
        em = segs[i + 1][0]
        if em <= sm:
            continue
        done, _eb, _err = jd.decode_range(
            buf, buf.size * 8, int(sbit), int(sdc[0]), int(sdc[1]), int(sdc[2]),
            dec.hl, dec.hs, dec.dc_idx, dec.ac_idx, dec.qmat,
            dec.hsamp, dec.vsamp, dec.hmax, dec.vmax, dec.mcus_x, dec.mcus_y,
            sm, em - sm, dec.cy, dec.cb, dec.cr, mb, _ZZ, dcr, dcb, acb, rt)
        frontier = sm + done
        if stop and done < em - sm:
            # 이 세그먼트가 목표 범위 전에 디싱크 → 여기가 실제 frontier(이후 세그먼트 무시)
            break
    return mb, dcr, frontier


def _best_edit(dec, buf, m_d, mb, dcr, rate, back=4, win_lo=16, win_hi=6, maxW=900):
    """디싱크 지점 부근 바이트 1개를 치환/삭제/삽입해 clean run을 최대화하는 편집 탐색.
    반환 (kind, pos, val, run). kind: 'sub'/'del'/'ins'/None."""
    m_s = max(0, m_d - back)
    sb = int(mb[m_s]); sdc = dcr[m_s].copy()
    base = _probe(dec, buf, sb, m_s, sdc, maxW, rate)
    byte_d = int(mb[m_d]) // 8
    lo = max(sb // 8, byte_d - win_lo)
    hi = min(buf.size - 2, byte_d + win_hi)
    best = (None, -1, -1, base)
    for p in range(lo, hi + 1):                       # 치환
        orig = int(buf[p])
        for v in range(256):
            if v == orig:
                continue
            buf[p] = v
            r = _probe(dec, buf, sb, m_s, sdc, maxW, rate)
            if r > best[3]:
                best = ('sub', p, v, r)
                if r >= maxW:
                    buf[p] = orig
                    return best
        buf[p] = orig
    for p in range(lo, hi + 1):                       # 삭제
        r = _probe(dec, np.delete(buf, p), sb, m_s, sdc, maxW, rate)
        if r > best[3]:
            best = ('del', p, -1, r)
            if r >= maxW:
                return best
    for p in range(lo, hi + 1):                       # 삽입(위치당 버퍼 1회 생성, 값만 교체)
        work = np.insert(buf, p, 0)                    # np.insert를 p당 1회로: 무익한 전체복사 제거
        for v in range(256):
            work[p] = v
            r = _probe(dec, work, sb, m_s, sdc, maxW, rate)
            if r > best[3]:
                best = ('ins', p, v, r)
                if r >= maxW:
                    return best
    return best


def _resync_skip(dec, buf, m_d, mb, dcr, rate, near=300000, full=True, maxW=900):
    """재개 비트위치를 탐색해 손상 클러스터/구멍을 건너뛴다.
    db≈0(masking, 가짜복구)은 거부. 반환 (resume_bit, dc, run) 또는 None.

    maxW는 호출자(recover)가 잔여 MCU로 캡한 probe 창이다. 수락 임계는 창에 비례한
    `max(30, 0.35·maxW)` — 절대 임계(450)는 총 MCU<450 소형·연속 손상 간격<450 파일의
    재동기를 산술적으로 불가능하게 했다(임계 잠금). 예외로 **버퍼 끝까지 완주한 후보**
    (stop=3, 절단 파일의 남은 데이터 전체가 이어지는 경우)는 데이터가 소진돼 그 뒤에
    가릴(masking) 내용 자체가 없으므로 run ≥ 30이면 수락한다.

    각 후보 위치에서 DC 예측을 [직전값 캐리, 전체 0 리셋] 둘 다 시도해 clean run이 긴 쪽을
    채택한다. 캐리만으로는 재동기 불가한 hole에서, DC=0 리셋이 재개 지점을 살려 복구율을 크게
    높인다(Cb/Cr DC도 재동기에 기여하므로 Y만이 아닌 전체를 리셋한다). DC=0은 Cb/Cr 절대
    오프셋을 잃어 무채색 캐스트를 만들지만 — 진짜 복구율(디코드된 영역)에는 영향이 없고 색
    보정은 별도 과제다. 제자리 리셋(|db|<24)은 masking이므로 후보에서 제외한다.

    near비트 내를 byte 정렬(8비트 간격)로 먼저 훑고, full=True면 못 찾을 때
    남은 스트림 전체를 거칠게(64비트 간격) 훑어 더 먼 구멍도 건너뛴다(철저 모드).
    full=False면 near까지만(빠른 모드) — 손상 심한 파일에서 비용 폭발을 막는다."""
    base = int(mb[m_d]); dc = dcr[m_d].copy(); nbits = buf.size * 8
    floor_bit = int(mb[m_d - 1]) + 1 if m_d > 0 else 0   # 이전 MCU를 침범하지 않는 하한
    cands = (dc, np.zeros(3, np.int64))               # DC 캐리 / 전체 0 리셋
    best = (-1, 0, dc, 0)                             # (bit, run, dc, stop사유)
    limit = min(nbits - base - 64, near)
    db = max(-32, floor_bit - base)                   # 역방향은 이전 MCU 시작까지만
    while db < limit:                                 # 1) 가까운 범위 byte정렬 스캔
        if abs(db) >= 24:                             # 제자리(masking) 제외
            for cd in cands:
                r, stop = _probe_stop(dec, buf, base + db, m_d, cd, maxW, rate)
                if r > best[1]:
                    best = (base + db, r, cd, stop)
        if best[1] >= maxW:
            break
        db += 8
    if full and best[1] < maxW * 0.8:                 # 2) 남은 전체 거친 스캔(철저 모드)
        db = limit
        while base + db < nbits - 64:
            for cd in cands:
                r, stop = _probe_stop(dec, buf, base + db, m_d, cd, maxW, rate)
                if r > best[1]:
                    best = (base + db, r, cd, stop)
            if best[1] >= maxW:
                break
            db += 64
    if best[0] >= 0:                                  # 3) 최적 부근 비트정밀 보정
        for rb in range(max(floor_bit, best[0] - 40), best[0] + 8):
            if abs(rb - base) < 24:
                continue
            for cd in cands:
                r, stop = _probe_stop(dec, buf, rb, m_d, cd, maxW, rate)
                if r > best[1]:
                    best = (rb, r, cd, stop)
    accept = max(30, int(maxW * 0.35))                # 창 비례 수락(임계 잠금 해제)
    if ((best[1] >= accept or (best[3] == 3 and best[1] >= 30))
            and abs(best[0] - base) >= 24):
        return best[0], best[2].copy(), best[1]
    return None


def _apply_edit(buf, kind, p, v):
    if kind == 'sub':
        buf = buf.copy(); buf[p] = v; return buf
    if kind == 'del':
        return np.delete(buf, p)
    if kind == 'ins':
        return np.insert(buf, p, v)
    return buf


def _resync_mcu_hint(segments, failed_mcu: int, failed_bit: int,
                     resume_bit: int):
    """Weakly estimate skipped MCUs from the preceding segment's bit rate."""
    starts = [segment for segment in segments if int(segment[0]) <= failed_mcu]
    if not starts:
        return None
    start_mcu, start_bit, _dc = max(starts, key=lambda segment: int(segment[0]))
    decoded = int(failed_mcu) - int(start_mcu)
    consumed = int(failed_bit) - int(start_bit)
    if decoded <= 0 or consumed <= 0:
        return None
    rate = consumed / decoded
    return float((int(resume_bit) - int(failed_bit)) / rate)


def recover(dec, maxW=900, max_ops=300, time_budget=90.0,
            resync_near=300000, resync_full=True, apply_shift=True):
    """디코더에 대해 반복 복구를 수행하고 (rgb, stats, segments)를 반환한다.

    철저함↔속도 조절:
    - resync_full=True + 큰 resync_near: 먼 구멍까지 건너뛰어 복구율↑(느림, 기본=철저).
    - resync_full=False + 작은 resync_near: 가까운 손상만(빠름).
    - time_budget(초): 이 복구 호출의 시간 상한. None/0이면 무제한. 심손상 파일의 비용 폭발 방지용
      안전장치(초과 시 남은 영역은 회색)."""
    total = dec.mcus_x * dec.mcus_y
    buf = dec.buf.copy()
    # MCU당 비트 상한(평균의 4배): 디싱크 후 비트 폭식을 탐지
    rate = max(350, int((buf.size * 8) / total * 4))
    segments = [(0, 0, np.zeros(3, np.int64))]
    phase_cuts = []
    n = dict(sub=0, dele=0, ins=0, resync=0, hole=0)
    last_front = -1
    stuck = 0
    deadline = (time.monotonic() + time_budget) if time_budget else None
    while sum(n.values()) < max_ops:
        if deadline is not None and time.monotonic() > deadline:
            break
        mb, dcr, frontier = _decode_traj(dec, buf, segments, rate)
        if frontier >= total - 1:
            break
        if frontier <= last_front:                    # 진전 없음 가드
            stuck += 1
            if stuck > 6:
                break
        else:
            stuck = 0
            last_front = frontier
        m_d = frontier
        rem = total - m_d
        W = int(min(maxW, rem))                       # probe 창을 잔여 MCU로 캡
        kind, p, v, run = _best_edit(dec, buf, m_d, mb, dcr, rate, maxW=W)
        m_s = max(0, m_d - 4)
        base = _probe(dec, buf, int(mb[m_s]), m_s, dcr[m_s], W, rate)
        # 편집 수락도 잔여 비례 — 절대 임계(120)는 소형·꼬리 구간에서 수락 불가를 만든다
        if (kind is not None and run > base + min(30, rem // 4)
                and run > min(120, max(20, int(rem * 0.4)))):
            phase_cuts.append((m_d, kind, 0.0))
            buf = _apply_edit(buf, kind, p, v)
            n['sub' if kind == 'sub' else 'dele' if kind == 'del' else 'ins'] += 1
            continue
        rk = _resync_skip(dec, buf, m_d, mb, dcr, rate,
                          near=resync_near, full=resync_full, maxW=W)
        if rk is not None:
            rb, dc, _run = rk
            hint = _resync_mcu_hint(
                segments, m_d, int(mb[m_d]), int(rb))
            segments.append((m_d, rb, dc))
            phase_cuts.append((m_d, 'resync', hint))
            n['resync'] += 1
            continue
        n['hole'] += 1
        break
    _mb, _dcr, frontier = _decode_traj(dec, buf, segments, rate)  # 최종 coef 채움
    rgb = dec.to_rgb()
    n['frontier'] = int(frontier)
    n['phase_cuts'] = phase_cuts
    shift_stats = dict(
        shifted=0, top_shifted=0, mcu_ins=0, mcu_drop=0,
        shift_margin=0.0, shift_reject=0,
        row_shift_plan=0, row_shift_rounds=0,
        row_shifted=0, row_shift_passes=0,
        row_shift_gain=0.0, row_shift_margin=0.0,
        row_residual_before=0.0, row_residual_after=0.0,
        row_residual_n_before=0, row_residual_n_after=0,
        row_shift_veto=0,
        row_global_passes=0, row_global_events=0,
        row_global_explored=0, row_global_unsafe_passes=0,
        row_global_changes=0, row_global_gain=0.0,
        row_global_margin=0.0,
        row_global_residual_before=0.0,
        row_global_residual_after=0.0,
        row_global_relaxed_after=0.0,
        row_global_veto=0, row_global_plan=(), row_global_trace=(),
        row_local_cuts=0, row_local_intervals=0,
        row_local_exact=0, row_local_soft=0,
        row_local_veto=0, row_local_plan=())
    if apply_shift:
        rgb, shift_stats = _correct_segment_shifts(
            dec, rgb, segments, frontier, phase_cuts)
    n.update(shift_stats)
    return rgb, n, segments


def recover_bytes(data: bytes):
    """JPEG 바이트를 복구해 (rgb_uint8, stats) 반환. 디코드 불가 시 (None, {})."""
    try:
        dec = jd.Decoder(data)
    except Exception:
        return None, {}
    rgb, stats, _segs = recover(dec)
    return rgb, stats


def _to_jpeg(rgb: np.ndarray, quality: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format='JPEG', quality=quality)
    return buf.getvalue()


def recover_file(src_path: Path, out_dir: Path, quality: int = 95,
                 time_budget=90.0, resync_near=300000, resync_full=True):
    """파일 1개를 복구해 out_dir에 저장. 반환 (out_path, action, stats).

    action: RECOVERED | HEADER_RECOVERED | CLEAN | FAILED | SKIP_UNDECODABLE.
    FAILED = 편집·재동기가 한 번도 수락되지 않고 hole로 종료(무행동) — 재인코딩
    회색본 대신 원본 바이트를 보존한다(입력보다 나쁜 복구본 저장 방지).
    헤더(DHT/DQT/SOF/SOS) 손상 파일은 `carver.headerfix`가 헤더를 재구성해 복구를
    시도한다 — 채택 시 `HEADER_RECOVERED`(원본 바이트가 디코드 불가하므로 렌더가
    유일 산출)로 `header_recovered/`에 저장하고 `header_fix`에 교체 세그먼트를 기록한다.
    어느 변형도 게이트를 통과 못하면 SKIP. 모든 경우 out_path는 실제 경로다(None 반환 없음).
    time_budget/resync_near/resync_full로 철저함↔속도 조절(→ recover 참조).
    """
    from carver import headerfix   # 지연 임포트(순환 회피)
    data = src_path.read_bytes()
    # Header candidates are selected with the historical unshifted render so
    # spatial post-processing cannot change the structural gate.  The chosen
    # candidate is aligned exactly once in _emit_headerfix below.
    hfix_fn = lambda d: recover(
        d, time_budget=time_budget, resync_near=resync_near,
        resync_full=resync_full, apply_shift=False)

    def _emit_headerfix(rec, elapsed):
        dec2, fix, rgb, stats, _segs, gray_p, undec_p = rec
        _shift_t0 = time.monotonic()
        rgb, shift_stats = _correct_segment_shifts(
            dec2, rgb, _segs, stats.get('frontier', dec2.mcus_x * dec2.mcus_y),
            stats.get('phase_cuts'))
        stats.update(shift_stats)
        elapsed += time.monotonic() - _shift_t0
        info = {
            'gray_before': gray_p, 'gray_after': gray_fraction(rgb),
            'undec_before': undec_p, 'undec_after': undecoded_fraction(rgb),
            'recover_sec': elapsed, 'ops': stats['sub'] + stats['dele'] + stats['ins'] + stats['resync'],
            'width': dec2.h.width, 'height': dec2.h.height,
            'mcus': dec2.mcus_x * dec2.mcus_y, 'header_fix': fix, **stats,
        }
        # 헤더 복구본은 원본 바이트가 디코드 불가하므로(그래서 SKIP이었다) 렌더가 유일 산출이다 —
        # CLEAN/FAILED(원본 보존)로 분기하지 않고 별도 action/폴더로 재인코딩 렌더를 저장한다.
        out_path = out_dir / 'header_recovered' / (src_path.stem + '.jpg')
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(_to_jpeg(rgb, quality))
        return out_path, 'HEADER_RECOVERED', info

    # 정상 디코더 구성을 시도한다. 실패하면 헤더 복구가 유일 경로.
    dec = None
    try:
        dec = jd.Decoder(data)
    except Exception:
        pass
    if dec is None:
        _t0 = time.monotonic()
        rec = headerfix.reconstruct(data, hfix_fn)
        if rec is not None:
            return _emit_headerfix(rec, time.monotonic() - _t0)
        # 디코드 불가 + 재구성 실패 → SKIP(가짜 채움 금지)
        skip_path = out_dir / 'skip_undecodable' / (src_path.stem + '.jpg')
        skip_path.parent.mkdir(parents=True, exist_ok=True)
        skip_path.write_bytes(data)
        return skip_path, 'SKIP_UNDECODABLE', {}

    # 개구부 probe가 바닥에 못 미치면 헤더 손상 후보다(첫 MCU부터 어긋남).
    triggered = headerfix.opening_probe(dec) < headerfix.floor_of(dec.mcus_x * dec.mcus_y)

    dec.decode_full()
    rgb0 = dec.to_rgb()
    before = gray_fraction(rgb0)
    before_undec = undecoded_fraction(rgb0)
    _t0 = time.monotonic()
    rgb, stats, _segs = recover(
        dec, time_budget=time_budget, resync_near=resync_near,
        resync_full=resync_full, apply_shift=False)
    unshifted_after_undec = undecoded_fraction(rgb)

    # 헤더 손상 의심 파일은 헤더 복구를 시도해 정상 경로보다 나을 때만(undec 감소) 채택한다.
    # Decoder가 구성됐다는 것은 자체 헤더로도 디코드가 되긴 한다는 뜻이므로, 정상 경로가
    # 강한 베이스라인이다 — 재구성이 이를 무조건 덮으면 잘못된 SOF 해석이 회귀를 낳는다.
    if triggered:
        _t1 = time.monotonic()
        rec = headerfix.reconstruct(data, hfix_fn)
        if (rec is not None
                and undecoded_fraction(rec[2]) < unshifted_after_undec - 0.01):
            return _emit_headerfix(rec, time.monotonic() - _t1)

    rgb, shift_stats = _correct_segment_shifts(
        dec, rgb, _segs, stats.get('frontier', dec.mcus_x * dec.mcus_y),
        stats.get('phase_cuts'))
    stats.update(shift_stats)
    recover_sec = time.monotonic() - _t0
    after = gray_fraction(rgb)
    after_undec = undecoded_fraction(rgb)

    ops = stats['sub'] + stats['dele'] + stats['ins'] + stats['resync']
    spatial_changed = bool(
        int(stats.get('shifted', 0))
        or int(stats.get('row_global_passes', 0))
        or int(stats.get('row_local_cuts', 0))
        or int(stats.get('row_shifted', 0)))
    info = {
        'gray_before': before, 'gray_after': after,
        'undec_before': before_undec, 'undec_after': after_undec,
        'recover_sec': recover_sec,
        'ops': ops, 'spatial_changed': int(spatial_changed),
        'width': dec.h.width, 'height': dec.h.height,
        'mcus': dec.mcus_x * dec.mcus_y, **stats,
    }
    if ops == 0 and not spatial_changed and before < 0.02:
        clean_path = out_dir / 'clean' / (src_path.stem + '.jpg')
        clean_path.parent.mkdir(parents=True, exist_ok=True)
        clean_path.write_bytes(data)
        return clean_path, 'CLEAN', info
    if ops == 0 and not spatial_changed and stats['hole'] >= 1:
        # 무행동: 회색 위주 재인코딩본은 입력 plain 디코드보다 항상 나쁘므로
        # 원본을 보존해 후속 pass(임계 비례화·헤더 복구)가 재시도할 수 있게 한다
        failed_path = out_dir / 'failed' / (src_path.stem + '.jpg')
        failed_path.parent.mkdir(parents=True, exist_ok=True)
        failed_path.write_bytes(data)
        return failed_path, 'FAILED', info
    out_path = out_dir / 'recovered' / (src_path.stem + '.jpg')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(_to_jpeg(rgb, quality))
    return out_path, 'RECOVERED', info
