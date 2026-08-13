"""reconstruction.engine 복구 엔진 검증."""
import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from media_recovery.formats.jpeg import baseline_decoder as jd
from media_recovery.reconstruction import engine as resync


def encode(img: np.ndarray, subsampling: int = 1, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format='JPEG', quality=quality, subsampling=subsampling)
    return buf.getvalue()


def textured_image(h=256, w=384, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    xx, yy = np.meshgrid(np.linspace(0, 255, w), np.linspace(0, 255, h))
    base = np.stack([xx, yy, (xx + yy) / 2], -1) + rng.normal(0, 25, (h, w, 3))
    return np.clip(base, 0, 255).astype(np.uint8)


def corrupt_entropy(data: bytes, n_bytes: int, seed: int = 1) -> bytes:
    """SOS 이후 엔트로피의 한 덩어리를 손상시켜 디싱크를 유발(디스크 손상 모사)."""
    h = jd.parse_header(data)
    start = h.scan_start
    last_eoi = data.rfind(b'\xff\xd9')
    arr = bytearray(data)
    rng = np.random.default_rng(seed)
    pos = start + (last_eoi - start) * 2 // 5        # 엔트로피 ~40% 지점
    for i in range(n_bytes):
        arr[pos + i] = int(rng.integers(0, 256))
    return bytes(arr)


def _tile_view(rgb: np.ndarray, mcus_y: int, mcus_x: int,
               mcu_h: int, mcu_w: int) -> np.ndarray:
    return rgb.reshape(mcus_y, mcu_h, mcus_x, mcu_w, 3).transpose(0, 2, 1, 3, 4)


# ── gray_fraction ──────────────────────────────────────────

def test_gray_fraction_detects_gray():
    gray = np.full((64, 64, 3), 128, np.uint8)
    assert resync.gray_fraction(gray) > 0.95


def test_gray_fraction_low_on_texture():
    assert resync.gray_fraction(textured_image()) < 0.2


# ── 복구 ────────────────────────────────────────────────────

def test_recover_clean_image_is_noop():
    """손상 없는 이미지는 회색이 낮고 편집(ops)이 거의 없다."""
    dec = jd.Decoder(encode(textured_image()))
    rgb, stats, _segs = resync.recover(dec)
    assert resync.gray_fraction(rgb) < 0.1
    assert stats['resync'] == 0 and stats['hole'] == 0
    assert stats['row_global_relaxed_after'] == 0.0


def test_recover_output_shape():
    dec = jd.Decoder(encode(textured_image(200, 320)))
    rgb, _stats, _segs = resync.recover(dec)
    assert rgb.shape == (200, 320, 3)


def test_explicit_empty_phase_cuts_bypass_all_spatial_correction(monkeypatch):
    """No accepted repair evidence must preserve the decoded RGB exactly."""
    mcus_x, mcus_y = 4, 4
    rgb = np.arange(mcus_x * mcus_y, dtype=np.uint8).reshape(
        mcus_y, mcus_x, 1).repeat(8, axis=0).repeat(8, axis=1)
    rgb = rgb.repeat(3, axis=2)
    dec = SimpleNamespace(mcus_x=mcus_x, mcus_y=mcus_y)
    dc = np.zeros(3, np.int64)

    def unexpected(*_args, **_kwargs):
        pytest.fail('explicit empty phase_cuts must bypass spatial stages')

    monkeypatch.setattr(resync, '_adaptive_phase_estimate', unexpected)
    monkeypatch.setattr(resync, '_correct_global_row_shifts', unexpected)
    monkeypatch.setattr(resync, '_correct_structural_row_shifts', unexpected)
    monkeypatch.setattr(resync, '_stitch_mcu_row_bands', unexpected)

    corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc)], mcus_x * mcus_y, phase_cuts=[])

    assert corrected is rgb
    assert stats['shifted'] == stats['top_shifted'] == 0
    assert stats['mcu_ins'] == stats['mcu_drop'] == 0
    assert stats['row_global_passes'] == stats['row_global_explored'] == 0
    assert stats['row_local_cuts'] == stats['row_shifted'] == 0


def test_none_phase_cuts_keeps_legacy_segment_inference(monkeypatch):
    """None still enables the historical segment-derived spatial pipeline."""
    mcus_x, mcus_y = 4, 4
    rgb = np.arange(mcus_x * mcus_y, dtype=np.uint8).reshape(
        mcus_y, mcus_x, 1).repeat(8, axis=0).repeat(8, axis=1)
    rgb = rgb.repeat(3, axis=2)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=mcus_x * 8, height=mcus_y * 8),
        to_rgb=lambda crop=False: rgb,
    )
    called = {'global': 0}

    monkeypatch.setattr(
        resync, '_adaptive_phase_estimate', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        resync, '_boundary_signature', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        resync, '_layout_quality_safe', lambda *_args, **_kwargs: True)

    def global_stage(image, _mcus_x, _mcu_w, _mcu_h, *, base_owner,
                     **_kwargs):
        called['global'] += 1
        return image, base_owner, {
            'row_global_passes': 1,
            '_final_mcu_ins': 0,
            '_final_mcu_drop': 0,
        }

    monkeypatch.setattr(resync, '_correct_global_row_shifts', global_stage)
    dc = np.zeros(3, np.int64)
    _corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc)], mcus_x * mcus_y, phase_cuts=None)

    assert called['global'] == 1
    assert stats['row_global_passes'] == 1


def test_mcu_phase_estimator_finds_scan_row_wrap():
    """Packed MCU tiles expose the omitted source row-wrap residue.

    Within each synthetic source row, adjacent tile edges share the same
    vertical profile.  Profiles differ between rows, so after packing with a
    known three-MCU phase the repeated non-neighbor edge must identify +3.
    """
    mcus_x, mcus_y, mcu_w, mcu_h = 8, 12, 4, 8
    rng = np.random.default_rng(20260721)
    base = np.empty((mcus_y * mcu_h, mcus_x * mcu_w, 3), np.uint8)
    for my in range(mcus_y):
        profile = rng.integers(35, 180, size=mcu_h, dtype=np.uint8)
        for mx in range(mcus_x):
            tile = np.clip(profile[:, None].astype(np.int16) + mx * 5, 0, 255)
            base[my * mcu_h:(my + 1) * mcu_h,
                 mx * mcu_w:(mx + 1) * mcu_w] = tile[:, :, None]

    source = _tile_view(base, mcus_y, mcus_x, mcu_h, mcu_w)
    packed = np.empty_like(base)
    packed_tiles = _tile_view(packed, mcus_y, mcus_x, mcu_h, mcu_w)
    phase = 3
    total = mcus_x * mcus_y
    for target in range(total):
        source_index = (target + phase) % total
        ty, tx = divmod(target, mcus_x)
        sy, sx = divmod(source_index, mcus_x)
        packed_tiles[ty, tx] = source[sy, sx]

    left, right = resync._mcu_edge_arrays(
        packed, mcus_x, mcus_y, mcu_w, mcu_h)
    estimate = resync._estimate_mcu_phase(
        left, right, 0, mcus_x * mcus_y, mcus_x)
    assert estimate is not None and estimate['confident']
    assert estimate['phase'] == phase

    left0, right0 = resync._mcu_edge_arrays(
        base, mcus_x, mcus_y, mcu_w, mcu_h)
    estimate0 = resync._estimate_mcu_phase(
        left0, right0, 0, mcus_x * mcus_y, mcus_x)
    assert estimate0 is not None and estimate0['confident']
    assert estimate0['phase'] == 0


def test_mcu_phase_estimator_rejects_natural_edge_peak_in_aligned_span():
    """A textured but already aligned suffix must not be shifted.

    Centered residual/decorrelation alone can rank a persistent natural edge
    above the true row boundary.  Its raw step is not distinct from the other
    columns, so the absolute-edge gate rejects that false phase.
    """
    dec = jd.Decoder(encode(textured_image(), subsampling=1))
    dec.decode_full()
    rgb = dec.to_rgb()
    left, right = resync._mcu_edge_arrays(
        rgb, dec.mcus_x, dec.mcus_y, 8 * dec.hmax, 8 * dec.vmax)
    estimate = resync._estimate_mcu_phase(
        left, right, 72, dec.mcus_x * dec.mcus_y, dec.mcus_x)
    assert estimate is not None
    assert not estimate['confident'] or estimate['phase'] == 0


def test_shift_rejects_persistent_aligned_vertical_seam():
    """A strong aligned seam is vetoed even if global cost would improve."""
    mcus_x, mcus_y, mcu_w, mcu_h = 24, 32, 16, 8
    profile_a = np.array([0, 1, 3, 6, 6, 3, 1, 0.], np.float64)
    profile_b = np.array([0, 6, -3, 5, 5, -3, 6, 0.], np.float64)
    wave_x = np.sin(np.linspace(0, 2 * np.pi, mcu_w))
    fade = np.r_[np.zeros(9), np.linspace(1, 0, 15)]
    weight_y = np.array([1, 2, 3, 4, 4, 3, 2, 1.])
    green = np.empty((mcus_y, mcu_h, mcus_x, mcu_w), np.uint8)
    for row in range(mcus_y):
        for column in range(mcus_x):
            profile_y = ((1 - fade[column]) * profile_a
                         + fade[column] * profile_b)
            tile = (128 + 8 * profile_y[:, None]
                    + 8 * (1 - 2 * fade[column])
                    * weight_y[:, None] * wave_x[None, :])
            green[row, :, column] = np.clip(tile, 0, 255)
    rgb = green.reshape(
        mcus_y * mcu_h, mcus_x * mcu_w)[:, :, None].repeat(3, 2)

    start = 3 * mcus_x
    left, right = resync._mcu_edge_arrays(
        rgb, mcus_x, mcus_y, mcu_w, mcu_h)
    estimate = resync._estimate_mcu_phase(
        left, right, start, mcus_x * mcus_y, mcus_x)
    assert estimate is not None and estimate['confident']
    assert estimate['phase'] != 0  # Deliberately ambiguous internal evidence.

    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=2, vmax=1,
        h=SimpleNamespace(
            width=mcus_x * mcu_w, height=mcus_y * mcu_h),
        to_rgb=lambda crop=False: rgb,
    )
    dc = np.zeros(3, np.int64)
    corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc), (start, 123, dc)], mcus_x * mcus_y)
    assert stats['shifted'] == 0
    assert stats['shift_reject'] == 1
    assert np.array_equal(corrected, rgb)


def test_shift_rejects_globally_worse_two_field_seam(monkeypatch):
    """Global edge guards reject a realistic confident aligned suffix."""
    mcus_x, mcus_y, mcu_w, mcu_h = 24, 20, 16, 16
    rng = np.random.default_rng(157)

    def field():
        low = rng.integers(0, 256, (10, 12, 3), dtype=np.uint8)
        return np.asarray(Image.fromarray(low).resize(
            (mcus_x * mcu_w, mcus_y * mcu_h),
            Image.Resampling.BILINEAR)).copy()

    first, second = field(), field()
    rgb = first.copy()
    rgb[:, 10 * mcu_w:] = second[:, 10 * mcu_w:]
    start = 3 * mcus_x
    left, right = resync._mcu_edge_arrays(
        rgb, mcus_x, mcus_y, mcu_w, mcu_h)
    estimate = resync._estimate_mcu_phase(
        left, right, start, mcus_x * mcus_y, mcus_x)
    assert estimate is not None and estimate['confident']
    assert estimate['phase'] != 0

    # Isolate the whole-layout guard from the independent cut-local veto.
    monkeypatch.setattr(resync, '_transition_quality_safe',
                        lambda *args, **kwargs: True)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=2, vmax=2,
        h=SimpleNamespace(
            width=mcus_x * mcu_w, height=mcus_y * mcu_h),
        to_rgb=lambda crop=False: rgb,
    )
    dc = np.zeros(3, np.int64)
    corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc), (start, 123, dc)], mcus_x * mcus_y)
    assert stats['shifted'] == 0 and stats['shift_reject'] == 1
    assert np.array_equal(corrected, rgb)


def test_shift_accepts_true_suffix_phase_with_cut_continuity():
    """A packed suffix is moved when both raster phase and resync cut agree."""
    mcus_x, mcus_y, mcu_w, mcu_h = 24, 20, 8, 8
    height, width = mcus_y * mcu_h, mcus_x * mcu_w
    yy, xx = np.mgrid[:height, :width]
    plane = (110 + 38 * np.sin(xx / 7.0 + yy / 31.0)
             + 29 * np.sin(xx / 13.0 - yy / 11.0))
    base = np.clip(plane, 0, 255).astype(np.uint8)[:, :, None].repeat(3, 2)
    base_tiles = _tile_view(base, mcus_y, mcus_x, mcu_h, mcu_w)
    packed = np.full_like(base, 128)
    packed_tiles = _tile_view(packed, mcus_y, mcus_x, mcu_h, mcu_w)
    start = 3 * mcus_x
    phase = 7
    frontier = mcus_x * mcus_y - phase
    for target in range(frontier):
        source = target if target < start else target + phase
        target_y, target_x = divmod(target, mcus_x)
        source_y, source_x = divmod(source, mcus_x)
        packed_tiles[target_y, target_x] = base_tiles[source_y, source_x]

    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=width, height=height),
        to_rgb=lambda crop=False: packed,
    )
    dc = np.zeros(3, np.int64)
    corrected, stats = resync._correct_segment_shifts(
        dec, packed, [(0, 0, dc), (start, 123, dc)], frontier)
    assert stats['shifted'] == 1
    assert stats['mcu_ins'] == phase and stats['mcu_drop'] == 0
    corrected_tiles = _tile_view(corrected, mcus_y, mcus_x, mcu_h, mcu_w)
    assert np.array_equal(
        corrected_tiles.reshape(-1, mcu_h, mcu_w, 3)[start + phase:],
        base_tiles.reshape(-1, mcu_h, mcu_w, 3)[start + phase:])


def test_mcu_phase_estimator_does_not_trust_flat_zero_phase():
    """No spatial evidence means inherit the previous phase, not reset it."""
    mcus_x, mcus_y, mcu_w, mcu_h = 8, 8, 4, 8
    flat = np.full((mcus_y * mcu_h, mcus_x * mcu_w, 3), 128, np.uint8)
    left, right = resync._mcu_edge_arrays(
        flat, mcus_x, mcus_y, mcu_w, mcu_h)
    estimate = resync._estimate_mcu_phase(
        left, right, 0, mcus_x * mcus_y, mcus_x)
    assert estimate is not None
    assert estimate['phase'] == 0 and not estimate['confident']


@pytest.mark.parametrize('source_phase', [-7, 9])
def test_mcu_row_band_stitcher_repairs_cyclic_suffix_both_directions(
        source_phase):
    """A residual suffix is aligned without wrapping its edge in-place."""
    mcus_x, mcu_w, mcu_h = 24, 8, 8
    height, width = 12 * mcu_h, mcus_x * mcu_w
    yy, xx = np.mgrid[:height, :width]
    green = (118 + 43 * np.sin(xx / 6.5 + yy / 73.0)
             + 31 * np.sin(xx / 13.0 - yy / 47.0)
             + 17 * np.cos(xx / 3.7 + yy / 101.0))
    base = np.stack([
        np.clip(green + 12 * np.sin(yy / 19.0), 0, 255),
        np.clip(green, 0, 255),
        np.clip(green - 9 * np.cos(yy / 23.0), 0, 255),
    ], axis=2).astype(np.uint8)
    damaged = base.copy()
    cut_y = 4 * mcu_h
    damaged[cut_y:] = np.roll(
        damaged[cut_y:], source_phase * mcu_w, axis=1)

    corrected, stats = resync._stitch_mcu_row_bands(
        damaged, mcus_x, mcu_w, mcu_h)

    assert stats['row_shifted'] == 1
    assert stats['row_shift_plan'] == 1
    assert stats['row_shift_gain'] >= 0.15
    assert stats['row_shift_margin'] >= 0.10
    correction = -source_phase * mcu_w
    if correction > 0:
        assert np.array_equal(
            corrected[cut_y:, correction:], base[cut_y:, correction:])
    else:
        assert np.array_equal(
            corrected[cut_y:, :correction], base[cut_y:, :correction])
    # The displaced edge follows flat JPEG scan order instead of appearing at
    # the opposite side of the same row.
    assert not np.array_equal(corrected, base)
    assert not np.array_equal(damaged, base)


@pytest.mark.parametrize(
    ('phases', 'expected'), [
        ([0, 1, 1], [0, 1, 2, 3, 128, 4, 5, 6, 7, 8, 9, 10]),
        ([0, -1, -1], [0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 128]),
    ])
def test_mcu_row_plan_uses_flat_scan_order_without_side_wrap(
        phases, expected):
    """A row-end MCU advances to the adjacent scan row, never its own edge."""
    rgb = np.arange(12, dtype=np.uint8).reshape(3, 4, 1).repeat(3, axis=2)
    corrected, owner = resync._apply_mcu_row_plan(
        rgb, 4, 1, 1, np.asarray(phases), np.arange(12))

    assert corrected[:, :, 0].ravel().tolist() == expected
    assert (owner >= 0).sum() == 11


def test_owner_placement_loss_separates_valid_slots_from_retained_sources():
    final_owner = np.array([0, -1, 2, -1, 4, -1])
    valid_target_slots = np.array([1, 1, 1, 1, 1, 0], dtype=bool)

    assert resync._owner_placement_loss(
        final_owner, valid_target_slots, source_count=5) == (2, 2)


def test_multistrip_row_detector_requires_narrow_phase_consensus(monkeypatch):
    """All narrow views must support one nontrivial MCU displacement."""
    phases = {1: 10, 2: 11, 3: 10, 4: 9}

    def detect(_rgb, _mx, _mw, mcu_h, *, strip_h=None, **_kwargs):
        phase = phases[int(strip_h)]
        return {
            'events': [{
                'y': mcu_h, 'phase': phase,
                'gain': 0.11 + strip_h / 100,
                'score': 0.20 + strip_h / 100,
                'margin': 0.08 + strip_h / 100,
                'support': 200,
            }],
            'valid': {mcu_h}, 'step_h': mcu_h,
        }

    monkeypatch.setattr(resync, '_detect_residual_row_seams', detect)
    audit = resync._detect_multistrip_row_seams(
        np.zeros((24, 64, 3), np.uint8), 8, 8, 8)

    assert len(audit['events']) == 1
    assert audit['events'][0]['phase'] == 10
    assert audit['events'][0]['strip_heights'] == (1, 2, 3, 4)


def test_multistrip_row_detector_rejects_tiny_or_missing_view(monkeypatch):
    """Small peaks and a phase absent from one narrow view cannot seed loss."""

    def detect(_rgb, _mx, _mw, mcu_h, *, strip_h=None, **_kwargs):
        events = [] if strip_h == 3 else [{
            'y': mcu_h, 'phase': 2, 'gain': 0.2, 'score': 0.3,
            'margin': 0.15, 'support': 200,
        }]
        return {'events': events, 'valid': {mcu_h}, 'step_h': mcu_h}

    monkeypatch.setattr(resync, '_detect_residual_row_seams', detect)
    audit = resync._detect_multistrip_row_seams(
        np.zeros((24, 64, 3), np.uint8), 8, 8, 8)

    assert audit['events'] == []


@pytest.mark.parametrize(('weak_views', 'expected'), [(1, 1), (2, 0)])
def test_relaxed_consensus_allows_only_one_weak_support_view(
        monkeypatch, weak_views, expected):
    calls = {'n': 0}

    def scores(_first, _second, phases, _stride):
        view = calls['n']
        calls['n'] += 1
        values = np.zeros(phases.size, dtype=np.float32)
        support = np.full(phases.size, 100, dtype=np.int32)
        selected = int(np.flatnonzero(phases == 2)[0])
        values[selected] = 0.30
        if view < weak_views:
            support[selected] = 0
        return values, support

    monkeypatch.setattr(resync, '_gradient_phase_scores', scores)
    audit = resync._detect_relaxed_consensus_row_seams(
        np.zeros((8, 32, 3), np.uint8), 8, 4, 4)

    assert len(audit['events']) == expected
    if expected:
        assert audit['events'][0]['phase'] == 2
        assert audit['events'][0]['support'] == 100


def test_global_row_fit_accumulates_absolute_phases_from_original(monkeypatch):
    mcus_x, rows, mcu_h = 8, 4, 4
    rgb = np.arange(rows * mcus_x, dtype=np.uint8).reshape(
        rows, mcus_x, 1).repeat(mcu_h, axis=0).repeat(3, axis=2)
    owner = np.arange(rows * mcus_x, dtype=np.int64)
    valid = np.ones(owner.size, dtype=bool)
    all_valid = set(range(mcu_h, rows * mcu_h, mcu_h))

    def audit(*events):
        return {
            'events': [
                {'y': y, 'phase': phase, 'gain': gain,
                 'score': 0.5, 'margin': 0.2, 'support': 100}
                for y, phase, gain in events
            ],
            'valid': all_valid, 'step_h': mcu_h,
        }

    initial = audit((mcu_h, 2, 1.0))
    empty = audit()
    audit_calls = {'n': 0}

    def audits(*_args):
        audit_calls['n'] += 1
        if audit_calls['n'] == 1:
            return {'exact': initial, 'soft': initial,
                    'multistrip': initial}
        return {'exact': empty, 'soft': empty, 'multistrip': empty}

    relaxed_calls = {'n': 0}

    def relaxed(*_args):
        relaxed_calls['n'] += 1
        return audit((2 * mcu_h, -1, 0.5)) if relaxed_calls['n'] == 1 else empty

    captured = []

    def apply(image, _mx, _mw, _mh, phases, base_owner):
        captured.append(np.asarray(phases).copy())
        return image.copy(), base_owner.copy()

    monkeypatch.setattr(resync, '_global_row_audits', audits)
    monkeypatch.setattr(
        resync, '_detect_relaxed_consensus_row_seams', relaxed)
    monkeypatch.setattr(resync, '_apply_mcu_row_plan', apply)

    corrected, final_owner, stats = resync._correct_global_row_shifts(
        rgb, mcus_x, 1, mcu_h, base_owner=owner,
        valid_sources=valid, source_count=owner.size,
        loss_budget=8, max_passes=5)

    assert np.array_equal(corrected, rgb)
    assert np.array_equal(final_owner, owner)
    assert [phase.tolist() for phase in captured] == [
        [0, 2, 2, 2], [0, 2, 1, 1]]
    assert stats['row_global_passes'] == 2
    assert stats['row_global_events'] == 2
    assert stats['row_global_plan'] == ((1, 2), (2, 1))
    assert (stats['_final_mcu_ins'], stats['_final_mcu_drop']) == (0, 0)


def test_global_row_residual_stats_separate_strict_and_relaxed(monkeypatch):
    mcus_x, rows, mcu_h = 8, 2, 4
    rgb = np.zeros((rows * mcu_h, mcus_x, 3), np.uint8)
    owner = np.arange(rows * mcus_x, dtype=np.int64)
    valid = np.ones(owner.size, dtype=bool)

    def audit(gain):
        events = [] if gain == 0.0 else [{
            'y': mcu_h, 'phase': 1, 'gain': gain,
            'score': gain + 0.1, 'margin': gain, 'support': 100,
        }]
        return {'events': events, 'valid': {mcu_h}, 'step_h': mcu_h}

    baseline = {name: audit(1.0)
                for name in ('exact', 'soft', 'multistrip')}
    candidate = {name: audit(0.25)
                 for name in ('exact', 'soft', 'multistrip')}
    audits = iter((baseline, candidate))
    relaxed_calls = {'n': 0}

    def relaxed(*_args):
        relaxed_calls['n'] += 1
        return audit(0.1)

    monkeypatch.setattr(
        resync, '_global_row_audits', lambda *_args: next(audits))
    monkeypatch.setattr(
        resync, '_detect_relaxed_consensus_row_seams', relaxed)

    _corrected, _final_owner, stats = resync._correct_global_row_shifts(
        rgb, mcus_x, 1, mcu_h, base_owner=owner,
        valid_sources=valid, source_count=owner.size,
        loss_budget=2, max_passes=1)

    assert stats['row_global_residual_before'] == pytest.approx(1.0)
    assert stats['row_global_residual_after'] == pytest.approx(0.25)
    assert stats['row_global_relaxed_after'] == pytest.approx(0.1)
    assert relaxed_calls['n'] == 1


def test_global_row_passes_reuse_original_rgb_and_base_owner(monkeypatch):
    mcus_x, rows, mcu_h = 4, 3, 4
    rgb = np.arange(rows * mcus_x, dtype=np.uint8).reshape(
        rows, mcus_x, 1).repeat(mcu_h, axis=0).repeat(3, axis=2)
    base_owner = np.arange(rows * mcus_x, dtype=np.int64) - 1
    valid = np.ones(base_owner.size, dtype=bool)
    valid_y = {mcu_h, 2 * mcu_h}

    def audit(*events):
        return {
            'events': [{
                'y': y, 'phase': phase, 'gain': gain,
                'score': 0.5, 'margin': 0.2, 'support': 100,
            } for y, phase, gain in events],
            'valid': valid_y, 'step_h': mcu_h,
        }

    seed = audit((mcu_h, 1, 1.0))
    empty = audit()
    audits = iter((
        {name: seed for name in ('exact', 'soft', 'multistrip')},
        {name: empty for name in ('exact', 'soft', 'multistrip')},
        {name: empty for name in ('exact', 'soft', 'multistrip')},
    ))
    relaxed = iter((audit((2 * mcu_h, -1, 0.5)), empty))
    original_apply = resync._apply_mcu_row_plan
    calls = []

    def apply(image, width, mcu_w, height, phases, owner):
        calls.append((image is rgb, np.array_equal(owner, base_owner)))
        return original_apply(
            image, width, mcu_w, height, phases, owner)

    monkeypatch.setattr(
        resync, '_global_row_audits', lambda *_args: next(audits))
    monkeypatch.setattr(
        resync, '_detect_relaxed_consensus_row_seams',
        lambda *_args: next(relaxed))
    monkeypatch.setattr(resync, '_apply_mcu_row_plan', apply)

    corrected, final_owner, stats = resync._correct_global_row_shifts(
        rgb, mcus_x, 1, mcu_h, base_owner=base_owner,
        valid_sources=valid, source_count=11,
        loss_budget=4, max_passes=2)
    expected_rgb, expected_owner = original_apply(
        rgb, mcus_x, 1, mcu_h, np.array([0, 1, 0]), base_owner)

    assert calls == [(True, True), (True, True)]
    assert np.array_equal(corrected, expected_rgb)
    assert np.array_equal(final_owner, expected_owner)
    assert stats['row_global_plan'] == ((1, 1), (2, 0))


def test_global_row_all_unsafe_passes_return_identity(monkeypatch):
    mcus_x, rows, mcu_h = 8, 3, 4
    rgb = np.arange(rows * mcus_x, dtype=np.uint8).reshape(
        rows, mcus_x, 1).repeat(mcu_h, axis=0).repeat(3, axis=2)
    owner = np.arange(rows * mcus_x, dtype=np.int64)
    valid = np.ones(owner.size, dtype=bool)
    valid_y = {mcu_h, 2 * mcu_h}

    def audit(y=None, gain=0.0):
        events = [] if y is None else [{
            'y': y, 'phase': 1, 'gain': gain,
            'score': gain + 0.1, 'margin': gain, 'support': 100,
        }]
        return {'events': events, 'valid': valid_y, 'step_h': mcu_h}

    seed = audit(mcu_h, 1.0)
    first = audit(mcu_h, 0.4)
    empty = audit()
    audits = iter((
        {name: seed for name in ('exact', 'soft', 'multistrip')},
        {name: first for name in ('exact', 'soft', 'multistrip')},
        {name: empty for name in ('exact', 'soft', 'multistrip')},
    ))
    relaxed = iter((audit(2 * mcu_h, 0.5), empty))
    monkeypatch.setattr(
        resync, '_global_row_audits', lambda *_args: next(audits))
    monkeypatch.setattr(
        resync, '_detect_relaxed_consensus_row_seams',
        lambda *_args: next(relaxed))
    monkeypatch.setattr(
        resync, '_global_row_audits_safe', lambda *_args: False)

    corrected, final_owner, stats = resync._correct_global_row_shifts(
        rgb, mcus_x, 1, mcu_h, base_owner=owner,
        valid_sources=valid, source_count=owner.size,
        loss_budget=4, max_passes=2)

    assert np.array_equal(corrected, rgb)
    assert np.array_equal(final_owner, owner)
    assert stats['row_global_passes'] == 0
    assert stats['row_global_explored'] == 2
    assert stats['row_global_unsafe_passes'] == 2
    assert stats['row_global_residual_after'] == pytest.approx(1.0)
    assert stats['row_global_relaxed_after'] == 0.0
    assert '_final_mcu_ins' not in stats


@pytest.mark.parametrize(
    ('loss', 'duplicate_owner', 'expected_passes'),
    [((5, 5), False, 1), ((6, 6), False, 0), ((0, 0), True, 0)],
)
def test_global_row_enforces_five_percent_and_owner_order(
        monkeypatch, loss, duplicate_owner, expected_passes):
    mcus_x, rows, mcu_h = 10, 10, 4
    total = rows * mcus_x
    rgb = np.zeros((rows * mcu_h, mcus_x, 3), np.uint8)
    owner = np.arange(total, dtype=np.int64)
    candidate_owner = owner.copy()
    if duplicate_owner:
        candidate_owner[1] = candidate_owner[0]
    valid = np.ones(total, dtype=bool)
    valid_y = set(range(mcu_h, rows * mcu_h, mcu_h))

    def audit(gain=0.0):
        events = [] if gain == 0.0 else [{
            'y': mcu_h, 'phase': 1, 'gain': gain,
            'score': gain + 0.1, 'margin': gain, 'support': 100,
        }]
        return {'events': events, 'valid': valid_y, 'step_h': mcu_h}

    seed = audit(1.0)
    empty = audit()
    audits = iter((
        {name: seed for name in ('exact', 'soft', 'multistrip')},
        {name: empty for name in ('exact', 'soft', 'multistrip')},
    ))
    monkeypatch.setattr(
        resync, '_global_row_audits', lambda *_args: next(audits))
    monkeypatch.setattr(
        resync, '_detect_relaxed_consensus_row_seams',
        lambda *_args: empty)
    monkeypatch.setattr(
        resync, '_apply_mcu_row_plan',
        lambda image, *_args: (image.copy(), candidate_owner.copy()))
    monkeypatch.setattr(
        resync, '_owner_placement_loss', lambda *_args: loss)

    corrected, final_owner, stats = resync._correct_global_row_shifts(
        rgb, mcus_x, 1, mcu_h, base_owner=owner,
        valid_sources=valid, source_count=total,
        loss_budget=5, max_passes=1)

    assert stats['row_global_passes'] == expected_passes
    if expected_passes:
        assert np.array_equal(final_owner, candidate_owner)
    else:
        assert np.array_equal(corrected, rgb)
        assert np.array_equal(final_owner, owner)
        assert stats['row_global_veto'] == 1


def test_global_row_fit_vetoes_cumulative_owner_loss(monkeypatch):
    mcus_x, rows, mcu_h = 8, 4, 4
    rgb = np.zeros((rows * mcu_h, mcus_x, 3), np.uint8)
    owner = np.arange(rows * mcus_x, dtype=np.int64)
    valid = np.ones(owner.size, dtype=bool)
    event = {
        'events': [{
            'y': mcu_h, 'phase': 2, 'gain': 1.0,
            'score': 0.5, 'margin': 0.2, 'support': 100,
        }],
        'valid': set(range(mcu_h, rows * mcu_h, mcu_h)),
        'step_h': mcu_h,
    }
    monkeypatch.setattr(
        resync, '_global_row_audits',
        lambda *_args: {
            'exact': event, 'soft': event, 'multistrip': event})
    monkeypatch.setattr(
        resync, '_owner_placement_loss', lambda *_args: (9, 9))

    corrected, final_owner, stats = resync._correct_global_row_shifts(
        rgb, mcus_x, 1, mcu_h, base_owner=owner,
        valid_sources=valid, source_count=owner.size,
        loss_budget=8)

    assert np.array_equal(corrected, rgb)
    assert np.array_equal(final_owner, owner)
    assert stats['row_global_passes'] == 0
    assert stats['row_global_veto'] == 1


def test_global_row_fit_explores_unsafe_intermediate_to_later_safe(monkeypatch):
    """A B8-like unsafe second pass may expose a later all-zero safe fit."""
    mcus_x, rows, mcu_h = 16, 2, 4
    rgb = np.zeros((rows * mcu_h, mcus_x, 3), np.uint8)
    owner = np.arange(rows * mcus_x, dtype=np.int64)
    valid_sources = np.ones(owner.size, dtype=bool)
    valid_y = set(range(mcu_h, rows * mcu_h, mcu_h))

    def audit(gain=0.0):
        events = [] if gain == 0.0 else [{
            'y': mcu_h, 'phase': 1, 'gain': gain,
            'score': gain + 0.1, 'margin': gain, 'support': 100,
        }]
        return {'events': events, 'valid': valid_y, 'step_h': mcu_h}

    baseline = {
        'exact': audit(5.920946335660119),
        'soft': audit(6.466222873896186),
        'multistrip': audit(5.33806414809078),
    }
    candidates = iter((
        {
            'exact': audit(0.36045353300869465),
            'soft': audit(0.6567117206286639),
            'multistrip': audit(0.48627156112343073),
        },
        {
            'exact': audit(),
            'soft': audit(0.1638354379683733),
            'multistrip': audit(),
        },
        {name: audit() for name in ('exact', 'soft', 'multistrip')},
        {name: audit() for name in ('exact', 'soft', 'multistrip')},
        {name: audit() for name in ('exact', 'soft', 'multistrip')},
    ))
    empty = audit()
    audit_calls = {'n': 0}

    def audits(*_args):
        audit_calls['n'] += 1
        return baseline if audit_calls['n'] == 1 else next(candidates)

    relaxed = iter((
        audit(1.8191982261050725),
        audit(1.2941048463690095),
        audit(0.2619290929287672),
        audit(0.1535933017730713),
        empty,
    ))
    safe = iter((True, False, True, True, True, True, True, True))
    plans = []

    def apply(image, _mx, _mw, _mh, phases, base_owner):
        plans.append(np.asarray(phases).copy())
        return image.copy(), base_owner.copy()

    monkeypatch.setattr(resync, '_global_row_audits', audits)
    monkeypatch.setattr(
        resync, '_detect_relaxed_consensus_row_seams',
        lambda *_args: next(relaxed))
    monkeypatch.setattr(
        resync, '_global_row_audits_safe',
        lambda *_args: next(safe))
    monkeypatch.setattr(resync, '_apply_mcu_row_plan', apply)

    corrected, final_owner, stats = resync._correct_global_row_shifts(
        rgb, mcus_x, 1, mcu_h, base_owner=owner,
        valid_sources=valid_sources, source_count=owner.size,
        loss_budget=8, max_passes=5)

    assert np.array_equal(corrected, rgb)
    assert np.array_equal(final_owner, owner)
    assert [phase.tolist() for phase in plans] == [
        [0, phase] for phase in range(1, 6)]
    assert stats['row_global_passes'] == 5
    assert stats['row_global_explored'] == 5
    assert stats['row_global_unsafe_passes'] == 1
    assert stats['row_global_events'] == 5
    assert stats['row_global_plan'] == ((1, 5),)
    trace = stats['row_global_trace']
    assert [item['safe'] for item in trace] == [
        True, False, True, True, True]
    assert [item['selected'] for item in trace] == [
        True, False, True, True, True]
    assert [item['before'] for item in trace] == pytest.approx([
        5.33806414809078, 1.8191982261050725,
        1.2941048463690095, 0.2619290929287672,
        0.1535933017730713])
    assert [item['after'] for item in trace] == pytest.approx([
        0.48627156112343073, 1.2941048463690095,
        0.2619290929287672, 0.1535933017730713, 0.0])
    assert [(item['exact'], item['multistrip'], item['soft'])
            for item in trace] == pytest.approx([
                (0.36045353300869465, 0.48627156112343073,
                 0.6567117206286639),
                (0.0, 0.0, 0.1638354379683733),
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
            ])


def test_global_row_safety_rejects_opposite_phase_at_same_boundary():
    valid = {4}

    def audit(phase, gain):
        return {
            'events': [{
                'y': 4, 'phase': phase, 'gain': gain,
                'score': gain + 0.1, 'margin': gain, 'support': 100,
            }],
            'valid': valid, 'step_h': 4,
        }

    before = {name: audit(2, 0.2)
              for name in ('exact', 'soft', 'multistrip')}
    opposite = {name: audit(-2, 0.1)
                for name in ('exact', 'soft', 'multistrip')}

    assert not resync._global_row_audits_safe(opposite, before, 8)


@pytest.mark.parametrize('phases', [
    [0, 1, 1, -2, -2],
    [0, -1, -1, 3, 3],
])
def test_compressed_phase_runs_equal_production_row_spans(phases):
    rows, mcus_x = len(phases), 4
    rgb = np.arange(rows * mcus_x, dtype=np.uint8).reshape(
        rows, mcus_x, 1).repeat(3, axis=2)
    phases = np.asarray(phases, dtype=np.int32)
    per_row_rgb, per_row_owner = resync._apply_mcu_row_plan(
        rgb, mcus_x, 1, 1, phases,
        np.arange(rows * mcus_x, dtype=np.int64))
    spans, offsets = resync._row_phase_run_spans(phases, mcus_x)
    run_rgb, inserted, dropped = resync._scatter_mcu_segments(
        rgb, mcus_x, rows, 1, 1, spans, offsets)
    run_owner, _labels = resync._mcu_owner_map(
        rows * mcus_x, spans, offsets)

    assert np.array_equal(run_rgb, per_row_rgb)
    assert np.array_equal(run_owner, per_row_owner)
    assert (inserted, dropped) == resync._mcu_placement_stats(
        rows * mcus_x, spans, offsets)


def test_residual_chain_composes_adjacent_multistrip_seeds(monkeypatch):
    """Two ends of a narrow displaced band are applied in one phase plan."""
    monkeypatch.setattr(resync, '_residual_chain_events',
                        lambda *_args, **_kwargs: [])
    rgb = np.arange(4 * 8, dtype=np.uint8).reshape(4, 8, 1).repeat(3, 2)
    seeds = [
        {'y': 1, 'phase': 2, 'gain': .2, 'margin': .1, 'score': .3},
        {'y': 2, 'phase': -1, 'gain': .2, 'margin': .1, 'score': .3},
    ]

    phases, generated = resync._build_residual_chain_plan(
        rgb, 8, 1, 1, seeds)

    assert phases.tolist() == [0, 2, 1, 1]
    assert generated['passes'] == 1
    assert len(generated['events']) == 2


def _structural_cut(row=2, mode='anchor', side=None):
    metric = {
        'phase': 1, 'score': 0.5, 'gain': 0.2,
        'margin': 0.1, 'support': 100,
    }
    return resync._StructuralRowCut(
        1, row, mode, side, metric, None, 3, 3)


def _structural_candidate(cut, side, lo, hi, phase, key=(0,)):
    target = {
        'phase': 0, 'score': 0.5, 'gain': 0.0,
        'margin': 0.1, 'support': 100,
    }
    return resync._StructuralRowCandidate(
        cut, side, lo, hi, phase, target, None, 0, 0, key)


def test_structural_rows_use_final_placed_offsets_and_skip_discarded():
    placed, rows = resync._placed_structural_rows(
        [(0, 8), (8, 16), (16, 24), (24, 32)],
        [0, 3, None, -5], mcus_x=4, total_rows=8)

    assert placed == [(1, 3), (3, 5)]
    assert rows == [0, 3, 5, 8]


def test_structural_candidates_merge_equal_overlap_and_reject_conflict():
    cut = _structural_cut()
    first = _structural_candidate(cut, 'after', 2, 6, 3)
    same = _structural_candidate(cut, 'before', 4, 8, 3)
    conflict = _structural_candidate(cut, 'before', 4, 8, -2)

    merged = resync._merge_structural_row_candidates(
        (first, same), total_rows=10)
    assert merged.tolist() == [0, 0, 3, 3, 3, 3, 3, 3, 0, 0]
    assert resync._merge_structural_row_candidates(
        (first, conflict), total_rows=10) is None


def test_structural_half_width_exact_tie_rejects_both_directions():
    cut = _structural_cut()
    negative = _structural_candidate(
        cut, 'after', 2, 6, -4, key=(0, 0, 0))
    positive = _structural_candidate(
        cut, 'after', 2, 6, 4, key=(0, 0, 0))

    assert resync._structural_candidate_phases(8)[-1] == 4
    assert resync._drop_ambiguous_half_candidates(
        [negative, positive], 8) == []
    assert len(resync._drop_ambiguous_half_candidates(
        [negative, _structural_candidate(
            cut, 'after', 2, 6, 4, key=(0, 0, 1))], 8)) == 2


def test_structural_soft_audit_allows_small_max_jitter_not_regression():
    before = _row_audit((8, 0.15), (24, 0.10), valid=(8, 24))
    small_jitter = _row_audit(
        (8, 0.159), (24, 0.08), valid=(8, 24))
    larger_max = _row_audit(
        (8, 0.161), (24, 0.08), valid=(8, 24))
    larger_sum = _row_audit(
        (8, 0.15), (24, 0.101), valid=(8, 24))

    assert resync._structural_audit_safe(before, small_jitter, 16)
    assert not resync._structural_audit_safe(before, larger_max, 16)
    assert not resync._structural_audit_safe(before, larger_sum, 16)


def _row_audit(*events, valid=None, step=8):
    return {
        'events': [
            {'y': y, 'gain': gain, 'phase': 1, 'score': 0.5,
             'margin': 0.2, 'support': 100}
            for y, gain in events
        ],
        'valid': set(valid if valid is not None else (y for y, _ in events)),
        'step_h': step,
    }


def test_structural_selector_composes_owner_and_cumulative_loss(monkeypatch):
    mcus_x, rows = 4, 4
    rgb = np.arange(16, dtype=np.uint8).reshape(
        rows, mcus_x, 1).repeat(3, axis=2)
    base_owner = np.arange(16, dtype=np.int64)
    base_owner[1] = -1
    valid = np.ones(16, dtype=bool)
    cut = _structural_cut(row=2)
    candidate = _structural_candidate(
        cut, 'after', 2, 4, 1, key=(0,) * 9)
    empty = {'events': [], 'valid': set(range(1, rows)), 'step_h': 1}
    target = {
        'phase': 0, 'score': 0.5, 'gain': 0.0,
        'margin': 0.1, 'support': 100,
    }

    monkeypatch.setattr(
        resync, '_discover_structural_row_cuts',
        lambda *args, **kwargs: ([cut], [0, 2, 4]))
    monkeypatch.setattr(
        resync, '_enumerate_structural_row_candidates',
        lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(
        resync, '_row_boundary_metric',
        lambda *args, **kwargs: target)
    monkeypatch.setattr(
        resync, '_structural_boundary_from_owner',
        lambda *args, **kwargs: target)
    monkeypatch.setattr(
        resync, '_structural_audit',
        lambda *args, **kwargs: empty)

    corrected, owner, stats = resync._correct_structural_row_shifts(
        rgb, mcus_x, 1, 1, [(0, 8), (8, 16)], [0, 0],
        base_owner, valid, source_count=16, loss_budget=4)
    expected_rgb, expected_owner = resync._apply_mcu_row_plan(
        rgb, mcus_x, 1, 1, np.array([0, 0, 1, 1]), base_owner)
    expected_loss = resync._owner_placement_loss(
        expected_owner, valid, source_count=16)

    assert np.array_equal(corrected, expected_rgb)
    assert np.array_equal(owner, expected_owner)
    assert (stats['_final_mcu_ins'], stats['_final_mcu_drop']) == expected_loss
    assert stats['row_local_plan'] == ((2, 2, 4, 1),)


def test_segment_shift_feeds_structural_owner_into_row_stitch(monkeypatch):
    mcus_x, mcus_y = 4, 8
    rgb = np.arange(mcus_x * mcus_y, dtype=np.uint8).reshape(
        mcus_y, mcus_x, 1, 1, 1).repeat(8, 2).repeat(8, 3).repeat(3, 4)
    rgb = rgb.transpose(0, 2, 1, 3, 4).reshape(
        mcus_y * 8, mcus_x * 8, 3)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=mcus_x * 8, height=mcus_y * 8),
        to_rgb=lambda crop=False: rgb,
    )
    band = 4 * mcus_x

    def estimate(_left, _right, start, _end, _width):
        return {
            'phase': 0 if start == 0 else 1,
            'margin': 3.0, 'score': 9.0, 'raw_ratio': 3.0,
            'pairs': 8, 'confident': True,
        }

    captured = {}
    order = []

    def global_fit(image, *_args, base_owner, **_kwargs):
        order.append('global')
        return image, base_owner, {
            'row_global_passes': 0,
            'row_global_relaxed_after': 0.0,
        }

    def local(image, width, mcu_w, mcu_h, spans, offsets,
              base_owner, valid_sources, source_count, loss_budget):
        order.append('local')
        captured['spans'] = spans
        captured['offsets'] = tuple(offsets)
        captured['segment_owner'] = base_owner.copy()
        owner = base_owner.copy()
        owner[0] = -1
        inserted, dropped = resync._owner_placement_loss(
            owner, valid_sources, source_count)
        return image, owner, {
            'row_local_cuts': 1, 'row_local_intervals': 1,
            'row_local_exact': 1, 'row_local_soft': 0,
            'row_local_veto': 0,
            'row_local_plan': ((4, 4, 5, 1),),
            '_final_mcu_ins': inserted, '_final_mcu_drop': dropped,
        }

    def stitch(image, width, mcu_w, mcu_h, **kwargs):
        order.append('stitch')
        captured['stitch_owner'] = kwargs['base_owner'].copy()
        return image, {
            'row_shift_plan': 0, 'row_shift_rounds': 0,
            'row_shifted': 0, 'row_shift_passes': 0,
            'row_shift_gain': 0.0, 'row_shift_margin': 0.0,
            'row_residual_before': 0.0, 'row_residual_after': 0.0,
            'row_residual_n_before': 0, 'row_residual_n_after': 0,
            'row_shift_veto': 0,
        }

    monkeypatch.setattr(resync, '_adaptive_phase_estimate', estimate)
    monkeypatch.setattr(resync, '_boundary_signature',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(resync, '_transition_quality_safe',
                        lambda *args, **kwargs: True)
    monkeypatch.setattr(resync, '_layout_quality_safe',
                        lambda *args, **kwargs: True)
    monkeypatch.setattr(resync, '_correct_global_row_shifts', global_fit)
    monkeypatch.setattr(resync, '_correct_structural_row_shifts', local)
    monkeypatch.setattr(resync, '_stitch_mcu_row_bands', stitch)
    dc = np.zeros(3, np.int64)

    _corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc), (band, 123, dc)], mcus_x * mcus_y)

    expected_owner, _labels = resync._mcu_owner_map(
        mcus_x * mcus_y, captured['spans'], list(captured['offsets']))
    assert np.array_equal(captured['segment_owner'], expected_owner)
    assert captured['stitch_owner'][0] == -1
    assert np.array_equal(
        captured['stitch_owner'][1:], captured['segment_owner'][1:])
    assert stats['row_local_cuts'] == 1
    assert order == ['global', 'local', 'stitch']


def test_global_success_skips_local_and_receives_16x16_grid(monkeypatch):
    mcus_x = mcus_y = 4
    rgb = np.zeros((mcus_y * 16, mcus_x * 16, 3), np.uint8)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=2, vmax=2,
        h=SimpleNamespace(width=mcus_x * 16, height=mcus_y * 16),
        to_rgb=lambda crop=False: rgb,
    )
    captured = {}

    def global_fit(image, width, mcu_w, mcu_h, *, base_owner,
                   valid_sources, source_count, loss_budget, max_passes):
        captured.update(
            grid=(width, mcu_w, mcu_h),
            owner=base_owner.copy(), valid=valid_sources.copy(),
            source_count=source_count, loss_budget=loss_budget,
            max_passes=max_passes)
        return image, base_owner, {
            'row_global_passes': 1,
            'row_global_relaxed_after': 0.0,
            '_final_mcu_ins': 0, '_final_mcu_drop': 0,
        }

    monkeypatch.setattr(
        resync, '_adaptive_phase_estimate',
        lambda *_args, **_kwargs: {
            'phase': 0, 'margin': 1.0, 'score': 1.0,
            'raw_ratio': 1.0, 'pairs': 5, 'confident': True,
        })
    monkeypatch.setattr(
        resync, '_boundary_signature', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        resync, '_layout_quality_safe', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(resync, '_correct_global_row_shifts', global_fit)
    monkeypatch.setattr(
        resync, '_correct_structural_row_shifts',
        lambda *_args, **_kwargs: pytest.fail('local must be skipped'))
    monkeypatch.setattr(
        resync, '_stitch_mcu_row_bands',
        lambda *_args, **_kwargs: pytest.fail('stitch must be skipped'))
    dc = np.zeros(3, np.int64)

    corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc)], mcus_x * mcus_y,
        [(2 * mcus_x, 'sub', 0.0)])

    assert np.array_equal(corrected, rgb)
    assert captured['grid'] == (4, 16, 16)
    assert captured['source_count'] == 16
    assert captured['loss_budget'] == 1
    assert captured['max_passes'] == 5
    assert np.array_equal(captured['owner'], np.arange(16))
    assert np.all(captured['valid'])
    assert stats['row_global_passes'] == 1
    assert stats['row_local_cuts'] == 0
    assert stats['row_shifted'] == 0


def test_row_candidate_requires_full_twenty_percent_residual_reduction():
    before = _row_audit((8, 0.5), (24, 0.5), valid=(8, 24))
    exact = _row_audit((8, 0.4), (24, 0.4), valid=(8, 24))
    short = _row_audit((8, 0.401), (24, 0.4), valid=(8, 24))

    assert resync._row_candidate_safe(before, exact)
    assert not resync._row_candidate_safe(before, short)


def test_row_candidate_vetoes_new_strong_unmatched_seam():
    before = _row_audit((8, 0.5), (48, 0.5), valid=(8, 48))
    after = _row_audit(
        (8, 0.1), (48, 0.1), (80, 0.3), valid=(8, 48, 80))

    assert not resync._row_candidate_safe(before, after)


def test_row_candidate_matches_nearby_seam_once_and_preserves_support():
    before = _row_audit((8, 0.5), valid=(8,))
    nearby = _row_audit((16, 0.2), valid=(8, 16))
    missing_support = _row_audit((16, 0.2), valid=(16,))

    assert resync._row_candidate_safe(before, nearby)
    assert not resync._row_candidate_safe(before, missing_support)


def test_row_candidate_vetoes_matched_seam_that_becomes_much_stronger():
    before = _row_audit((8, 0.5), (24, 0.5), valid=(8, 24))
    stronger = _row_audit((8, 0.7), (24, 0.1), valid=(8, 24))

    assert not resync._row_candidate_safe(before, stronger)


def test_coarse_row_guard_allows_stability_but_not_large_worsening():
    before = _row_audit((16, 0.3), (48, 0.3), valid=(16, 48), step=16)
    stable = _row_audit((16, 0.31), (48, 0.3), valid=(16, 48), step=16)
    worse = _row_audit((16, 0.8), (48, 0.8), valid=(16, 48), step=16)

    assert resync._row_candidate_safe(
        before, stable, require_reduction=False)
    assert not resync._row_candidate_safe(
        before, worse, require_reduction=False)


def test_mcu_row_band_stitcher_preserves_aligned_vertical_seam():
    """A persistent vertical edge is not mistaken for a cyclic row wrap."""
    mcus_x, mcu_w, mcu_h = 24, 8, 8
    height, width = 12 * mcu_h, mcus_x * mcu_w
    yy, xx = np.mgrid[:height, :width]
    green = (95 + 37 * np.sin(xx / 8.0 + yy / 59.0)
             + 19 * np.cos(xx / 3.1 - yy / 71.0)
             + 55 * (xx >= 11 * mcu_w))
    aligned = np.clip(green, 0, 255).astype(np.uint8)
    aligned = aligned[:, :, None].repeat(3, axis=2)

    corrected, stats = resync._stitch_mcu_row_bands(
        aligned, mcus_x, mcu_w, mcu_h)

    assert stats['row_shifted'] == 0
    assert corrected is aligned
    assert np.array_equal(corrected, aligned)


def test_mcu_row_band_stitcher_rejects_flat_evidence():
    """Flat bands provide no registration evidence and remain untouched."""
    flat = np.full((96, 192, 3), 128, np.uint8)
    damaged = flat.copy()
    damaged[32:] = np.roll(damaged[32:], 7 * 8, axis=1)

    corrected, stats = resync._stitch_mcu_row_bands(
        damaged, 24, 8, 8)

    assert stats['row_shifted'] == 0
    assert corrected is damaged
    assert np.array_equal(corrected, flat)


def test_scatter_mcu_segments_inserts_and_deletes_flat_slots():
    """Positive gaps insert gray; negative gaps drop the prior boundary tail."""
    rgb = np.arange(8, dtype=np.uint8).reshape(2, 4, 1).repeat(3, axis=2)
    spans = [(0, 3), (3, 8)]

    inserted, gaps, dropped = resync._scatter_mcu_segments(
        rgb, 4, 2, 1, 1, spans, [0, 1])
    assert inserted[:, :, 0].ravel().tolist() == [0, 1, 2, 128, 3, 4, 5, 6]
    assert gaps == 1 and dropped == 1

    deleted, gaps, dropped = resync._scatter_mcu_segments(
        rgb, 4, 2, 1, 1, spans, [0, -1])
    assert deleted[:, :, 0].ravel().tolist() == [0, 1, 3, 4, 5, 6, 7, 128]
    assert gaps == 1 and dropped == 1


def test_explicit_mcu_zero_resync_unlocks_the_top_anchor(monkeypatch):
    """A recorded nonzero-bit MCU-zero resync may move the first band."""
    mcus_x, mcus_y = 4, 8
    full = np.arange(mcus_x * mcus_y, dtype=np.uint8).reshape(
        mcus_y, mcus_x, 1, 1, 1).repeat(8, 2).repeat(8, 3).repeat(3, 4)
    full = full.transpose(0, 2, 1, 3, 4).reshape(mcus_y * 8, mcus_x * 8, 3)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=mcus_x * 8, height=mcus_y * 8),
        to_rgb=lambda crop=False: full,
    )
    monkeypatch.setattr(resync, '_estimate_mcu_phase', lambda *args, **kwargs: {
        'phase': 1, 'margin': 4.0, 'confident': True,
    })
    dc = np.zeros(3, np.int64)
    corrected, stats = resync._correct_segment_shifts(
        dec, full, [(0, 0, dc), (0, 123, dc)], mcus_x * mcus_y,
        [(0, 'resync', 1.25)])
    tiles = _tile_view(corrected, mcus_y, mcus_x, 8, 8)
    assert stats['shifted'] == 1
    assert stats['top_shifted'] == 1
    assert stats['mcu_ins'] == 1 and stats['mcu_drop'] == 1
    assert np.all(tiles[0, 0] == 128)
    assert np.all(tiles[0, 1] == 0)


@pytest.mark.parametrize(
    ('extra_segments', 'phase_cuts'),
    [
        ([(0, 123, np.zeros(3, np.int64))], []),
        ([], [(0, 'resync', 1.25)]),
    ],
)
def test_mcu_zero_top_unlock_requires_both_resync_records(
        monkeypatch, extra_segments, phase_cuts):
    """Neither a cut nor a duplicate segment alone proves a zero resync."""
    mcus_x, mcus_y = 4, 8
    full = np.arange(mcus_x * mcus_y, dtype=np.uint8).reshape(
        mcus_y, mcus_x, 1, 1, 1).repeat(8, 2).repeat(8, 3).repeat(3, 4)
    full = full.transpose(0, 2, 1, 3, 4).reshape(
        mcus_y * 8, mcus_x * 8, 3)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=mcus_x * 8, height=mcus_y * 8),
        to_rgb=lambda crop=False: full,
    )
    monkeypatch.setattr(resync, '_adaptive_phase_estimate',
                        lambda *args, **kwargs: {
                            'phase': 1, 'margin': 4.0, 'score': 8.0,
                            'raw_ratio': 3.0, 'pairs': 5,
                            'confident': True,
                        })
    dc = np.zeros(3, np.int64)
    segments = [(0, 0, dc), *extra_segments]
    corrected, stats = resync._correct_segment_shifts(
        dec, full, segments, mcus_x * mcus_y, phase_cuts)

    assert stats['shifted'] == 0
    assert stats['top_shifted'] == 0
    assert np.array_equal(corrected, full)


def test_shift_uses_hidden_pixels_from_partial_edge_mcus(monkeypatch):
    """A moved partial MCU uses its full decoded coefficients, not gray crop fill."""
    mcus_x, mcus_y = 4, 8
    full = np.full((mcus_y * 8, mcus_x * 8, 3), 10, np.uint8)
    full[:, 29:] = 77
    cropped = full[:61, :29].copy()
    calls = []

    def render(crop=True):
        calls.append(crop)
        return cropped if crop else full

    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=29, height=61), to_rgb=render,
    )
    monkeypatch.setattr(resync, '_estimate_mcu_phase', lambda *args, **kwargs: {
        'phase': 1, 'margin': 4.0, 'confident': True,
    })
    monkeypatch.setattr(resync, '_transition_quality_safe',
                        lambda *args, **kwargs: True)
    monkeypatch.setattr(resync, '_layout_quality_safe',
                        lambda *args, **kwargs: True)
    dc = np.zeros(3, np.int64)
    corrected, stats = resync._correct_segment_shifts(
        dec, cropped, [(0, 0, dc), (16, 123, dc)], mcus_x * mcus_y)
    assert calls == [False]
    assert corrected.shape == cropped.shape
    assert stats['shifted'] == 1
    # Source MCU 19's hidden right edge moves into target MCU 20's interior.
    assert np.all(corrected[43, 7] == 77)


def test_mcu_placement_stats_count_intentionally_discarded_band():
    """A whole-band discard is reported as real MCU loss and blank space."""
    spans = [(0, 4), (4, 6), (6, 10)]
    inserted, dropped = resync._mcu_placement_stats(
        10, spans, [0, None, 0])
    assert inserted == 2
    assert dropped == 2


def test_boundary_signature_distinguishes_reliable_and_auxiliary_phase():
    """Complete left/right profiles recover a cyclic phase relation."""
    rng = np.random.default_rng(20260722)
    width = 32
    profile = rng.normal(size=(width, 3))
    profile = (profile - profile.mean(axis=0)) / profile.std(axis=0)
    shifted = np.roll(profile, -7, axis=0)

    reliable = resync._signature_correlation(
        {'profile': profile, 'support': 25},
        {'profile': shifted, 'support': 24}, width)
    assert reliable['phase_delta'] == 7
    assert reliable['strong'] and not reliable['auxiliary']

    auxiliary = resync._signature_correlation(
        {'profile': profile, 'support': 12},
        {'profile': shifted, 'support': 12}, width)
    assert auxiliary['phase_delta'] == 7
    assert auxiliary['auxiliary'] and not auxiliary['strong']


def test_auxiliary_signature_does_not_propagate_as_a_later_anchor(monkeypatch):
    """A medium-support repair may move one band but must not form a chain."""
    mcus_x, mcus_y = 32, 40
    band = 10 * mcus_x
    rgb = np.full((mcus_y * 8, mcus_x * 8, 3), 90, np.uint8)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=mcus_x * 8, height=mcus_y * 8),
        to_rgb=lambda crop=False: rgb,
    )
    phases = {0: 0, band: 9, 2 * band: 0, 3 * band: 9}

    def estimate(_left, _right, start, _end, _width):
        return {
            'phase': phases[start], 'margin': 1.0, 'score': 4.0,
            'raw_ratio': 2.0, 'pairs': 10, 'confident': False,
        }

    monkeypatch.setattr(resync, '_adaptive_phase_estimate', estimate)
    monkeypatch.setattr(resync, '_boundary_signature',
                        lambda *args, **kwargs: {'support': 12})
    monkeypatch.setattr(resync, '_signature_correlation',
                        lambda *args, **kwargs: {
                            'delta': 2, 'phase_delta': 2, 'score': 1.0,
                            'peak': 1.0, 'margin': 0.2, 'support': 12,
                            'strong': False, 'auxiliary': True,
                            'hint_used': False,
                        })
    dc = np.zeros(3, np.int64)
    _corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc)], mcus_x * mcus_y,
        [(band, 'sub', 0.0), (2 * band, 'sub', 0.0),
         (3 * band, 'sub', 0.0)])

    # Independent A→B and C→D corrections yield [0, 2, 0, 2].  Propagating
    # the first auxiliary correction would instead create [0, 2, 4, 6].
    assert stats['shifted'] == 2
    assert stats['mcu_ins'] == 4
    assert stats['mcu_drop'] == 4


def test_relaxed_phase_unwrap_does_not_force_whole_row_branch_drift():
    """Last-strict representatives remain available after relaxed drift."""
    spans = [(index * 10, (index + 1) * 10) for index in range(7)]
    estimates = [
        {'phase': 0, 'confident': True},
        {'phase': 6, 'confident': False},
        {'phase': 2, 'confident': False},
        {'phase': 8, 'confident': False},
        {'phase': 4, 'confident': False},
        {'phase': 0, 'confident': False},
        {'phase': 6, 'confident': False},
    ]
    strict = resync._materialize_phase_offsets(
        spans, estimates, set(), 10)
    previous = resync._materialize_phase_offsets(
        spans, estimates, set(), 10, relaxed_updates=True)
    assert strict == [0, -4, 2, -2, 4, 0, -4]
    assert previous == [0, -4, -8, -12, -16, -20, -24]


def test_three_tuple_phase_cuts_keep_the_clean_top_prefix_anchored(monkeypatch):
    """Later repair cuts cannot retroactively move the MCU-zero prefix."""
    mcus_x, mcus_y = 4, 8
    rgb = np.arange(mcus_x * mcus_y, dtype=np.uint8).reshape(
        mcus_y, mcus_x, 1, 1, 1).repeat(8, 2).repeat(8, 3).repeat(3, 4)
    rgb = rgb.transpose(0, 2, 1, 3, 4).reshape(
        mcus_y * 8, mcus_x * 8, 3)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=mcus_x * 8, height=mcus_y * 8),
        to_rgb=lambda crop=False: rgb,
    )
    monkeypatch.setattr(resync, '_adaptive_phase_estimate',
                        lambda *args, **kwargs: {
                            'phase': 1, 'margin': 2.0, 'score': 8.0,
                            'raw_ratio': 3.0, 'pairs': 2,
                            'confident': True,
                        })
    monkeypatch.setattr(resync, '_boundary_signature',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(resync, '_layout_quality_safe',
                        lambda *args, **kwargs: True)
    dc = np.zeros(3, np.int64)
    corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc)], mcus_x * mcus_y,
        [(8, 'sub', 0.0), (16, 'resync', 1.25)])
    tiles = _tile_view(corrected, mcus_y, mcus_x, 8, 8)
    assert stats['shifted'] == 2
    assert stats['top_shifted'] == 0
    assert stats['mcu_ins'] == 1 and stats['mcu_drop'] == 1
    assert np.all(tiles[0, 0] == 0)
    assert np.all(tiles[0, 1] == 1)
    assert np.all(tiles[2, 0] == 128)
    assert np.all(tiles[2, 1] == 8)


def test_two_band_common_rotation_cannot_move_a_locked_top(monkeypatch):
    """A visually cheaper common rotation cannot override scan origin."""
    mcus_x, mcus_y = 4, 8
    rgb = np.arange(mcus_x * mcus_y, dtype=np.uint8).reshape(
        mcus_y, mcus_x, 1, 1, 1).repeat(8, 2).repeat(8, 3).repeat(3, 4)
    rgb = rgb.transpose(0, 2, 1, 3, 4).reshape(
        mcus_y * 8, mcus_x * 8, 3)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=mcus_x * 8, height=mcus_y * 8),
        to_rgb=lambda crop=False: rgb,
    )
    monkeypatch.setattr(resync, '_adaptive_phase_estimate',
                        lambda *args, **kwargs: {
                            'phase': 1, 'margin': 3.0, 'score': 8.0,
                            'raw_ratio': 3.0, 'pairs': 5,
                            'confident': True,
                        })
    monkeypatch.setattr(resync, '_boundary_signature',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(resync, '_transition_quality_safe',
                        lambda *args, **kwargs: True)
    monkeypatch.setattr(resync, '_layout_quality_safe',
                        lambda *args, **kwargs: True)

    def layout(total, spans, offsets, _width, *_edges):
        inserted, dropped = resync._mcu_placement_stats(
            total, spans, offsets)
        top = float(offsets[0])
        return inserted, dropped, np.array([top]), float(top == 0)

    monkeypatch.setattr(resync, '_layout_for_offsets', layout)
    monkeypatch.setattr(
        resync, '_layout_after_metrics',
        lambda _before, after: (
            (0.0, 0.0, 0.0)
            if after.size == 1 and float(after[0]) == 1.0
            else (1.0, 1.0, 1.0)))
    row_zero = {
        'row_shift_plan': 0, 'row_shift_rounds': 0,
        'row_shifted': 0, 'row_shift_passes': 0,
        'row_shift_gain': 0.0, 'row_shift_margin': 0.0,
        'row_residual_before': 0.0, 'row_residual_after': 0.0,
        'row_residual_n_before': 0, 'row_residual_n_after': 0,
        'row_shift_veto': 0,
    }
    monkeypatch.setattr(
        resync, '_stitch_mcu_row_bands',
        lambda image, *args, **kwargs: (image, dict(row_zero)))

    dc = np.zeros(3, np.int64)
    corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc)], mcus_x * mcus_y,
        [(16, 'resync', 1.25)])
    tiles = _tile_view(corrected, mcus_y, mcus_x, 8, 8)

    assert stats['shifted'] == 1
    assert stats['top_shifted'] == 0
    assert np.all(tiles[0, 0] == 0)
    assert np.all(tiles[0, 1] == 1)


def test_signature_chain_cannot_rotate_a_locked_top(monkeypatch):
    """A strong relative-phase chain preserves an absolute MCU-zero node."""
    mcus_x, mcus_y = 8, 12
    total = mcus_x * mcus_y
    rgb = np.arange(total, dtype=np.uint8).reshape(
        mcus_y, mcus_x, 1, 1, 1).repeat(8, 2).repeat(8, 3).repeat(3, 4)
    rgb = rgb.transpose(0, 2, 1, 3, 4).reshape(
        mcus_y * 8, mcus_x * 8, 3)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=mcus_x * 8, height=mcus_y * 8),
        to_rgb=lambda crop=False: rgb,
    )
    phases = {0: 0, 32: 2, 64: 4}

    def estimate(_left, _right, start, _end, _width):
        return {
            'phase': phases[start], 'margin': 3.0, 'score': 8.0,
            'raw_ratio': 3.0, 'pairs': 5, 'confident': True,
        }

    monkeypatch.setattr(resync, '_adaptive_phase_estimate', estimate)
    monkeypatch.setattr(resync, '_boundary_signature',
                        lambda *args, **kwargs: {'support': 25})
    monkeypatch.setattr(resync, '_signature_correlation',
                        lambda *args, **kwargs: {
                            'delta': 2, 'phase_delta': 2,
                            'score': 1.0, 'peak': 1.0, 'margin': 0.2,
                            'support': 25, 'strong': True,
                            'auxiliary': False, 'hint_used': False,
                        })
    monkeypatch.setattr(resync, '_layout_quality_safe',
                        lambda *args, **kwargs: True)

    def layout(total_mcus, spans, offsets, _width, *_edges):
        inserted, dropped = resync._mcu_placement_stats(
            total_mcus, spans, offsets)
        top = int(offsets[0])
        mean = 0.0 if top == -2 else 10.0
        return inserted, dropped, np.array([float(top)]), mean

    monkeypatch.setattr(resync, '_layout_for_offsets', layout)
    row_zero = {
        'row_shift_plan': 0, 'row_shift_rounds': 0,
        'row_shifted': 0, 'row_shift_passes': 0,
        'row_shift_gain': 0.0, 'row_shift_margin': 0.0,
        'row_residual_before': 0.0, 'row_residual_after': 0.0,
        'row_residual_n_before': 0, 'row_residual_n_after': 0,
        'row_shift_veto': 0,
    }
    monkeypatch.setattr(
        resync, '_stitch_mcu_row_bands',
        lambda image, *args, **kwargs: (image, dict(row_zero)))

    dc = np.zeros(3, np.int64)
    corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc)], total,
        [(32, 'sub', 0.0), (64, 'sub', 0.0)])
    tiles = _tile_view(corrected, mcus_y, mcus_x, 8, 8)

    assert stats['shifted'] == 2
    assert stats['top_shifted'] == 0
    assert np.all(tiles[0, 0] == 0)
    assert np.all(tiles[0, 1] == 1)


def test_fine_phase_noop_does_not_fall_back_to_coarser_resync(monkeypatch):
    """Resolved edit bands at phase zero outrank a mixed coarse span."""
    mcus_x, mcus_y = 4, 8
    rgb = np.arange(mcus_x * mcus_y, dtype=np.uint8).reshape(
        mcus_y, mcus_x, 1, 1, 1).repeat(8, 2).repeat(8, 3).repeat(3, 4)
    rgb = rgb.transpose(0, 2, 1, 3, 4).reshape(
        mcus_y * 8, mcus_x * 8, 3)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=mcus_x * 8, height=mcus_y * 8),
        to_rgb=lambda crop=False: rgb,
    )

    def estimate(_left, _right, start, end, _width):
        return {
            'phase': 1 if end - start > 16 else 0,
            'margin': 2.0, 'score': 8.0, 'raw_ratio': 3.0,
            'pairs': 4, 'confident': True,
        }

    monkeypatch.setattr(resync, '_adaptive_phase_estimate', estimate)
    monkeypatch.setattr(resync, '_boundary_signature',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(resync, '_transition_quality_safe',
                        lambda *args, **kwargs: True)
    monkeypatch.setattr(resync, '_layout_quality_safe',
                        lambda *args, **kwargs: True)
    dc = np.zeros(3, np.int64)
    corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc), (8, 123, dc)], mcus_x * mcus_y,
        [(8, 'resync', None), (16, 'sub', 0.0)])
    assert stats['shifted'] == 0
    assert stats['shift_reject'] == 0
    assert np.array_equal(corrected, rgb)


def test_fine_reject_with_coarse_noop_preserves_reject_flag(monkeypatch):
    """An unsafe edit-band shift must not disappear as a clean coarse no-op."""
    mcus_x, mcus_y = 4, 8
    rgb = np.arange(mcus_x * mcus_y, dtype=np.uint8).reshape(
        mcus_y, mcus_x, 1, 1, 1).repeat(8, 2).repeat(8, 3).repeat(3, 4)
    rgb = rgb.transpose(0, 2, 1, 3, 4).reshape(
        mcus_y * 8, mcus_x * 8, 3)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=mcus_x * 8, height=mcus_y * 8),
        to_rgb=lambda crop=False: rgb,
    )

    def estimate(_left, _right, start, _end, _width):
        return {
            'phase': 0 if start == 0 else 1,
            'margin': 2.0, 'score': 8.0, 'raw_ratio': 3.0,
            'pairs': 4, 'confident': True,
        }

    monkeypatch.setattr(resync, '_adaptive_phase_estimate', estimate)
    monkeypatch.setattr(resync, '_boundary_signature',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(resync, '_transition_quality_safe',
                        lambda *args, **kwargs: True)
    monkeypatch.setattr(resync, '_layout_quality_safe',
                        lambda *args, **kwargs: False)
    dc = np.zeros(3, np.int64)
    corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc)], mcus_x * mcus_y,
        [(16, 'sub', 0.0)])

    assert stats['shifted'] == 0
    assert stats['mcu_ins'] == 0 and stats['mcu_drop'] == 0
    assert stats['shift_reject'] == 1
    assert np.array_equal(corrected, rgb)


@pytest.mark.parametrize(
    ('capped_after', 'high_after', 'trusted',
     'expected_shifted', 'expected_reject'),
    [
        (1.004, 0.001, True, 2, 0),
        (1.006, 0.001, True, 0, 1),
        (1.004, 0.006, True, 0, 1),
        (1.004, 0.001, False, 0, 1),
    ],
)
def test_trusted_multiband_shift_uses_capped_global_mean(
        monkeypatch, capped_after, high_after, trusted,
        expected_shifted, expected_reject):
    """Damaged cut outliers cannot veto consensus, but added high edges can."""
    mcus_x, mcus_y = 4, 12
    band = 4 * mcus_x
    rgb = np.arange(mcus_x * mcus_y, dtype=np.uint8).reshape(
        mcus_y, mcus_x, 1, 1, 1).repeat(8, 2).repeat(8, 3).repeat(3, 4)
    rgb = rgb.transpose(0, 2, 1, 3, 4).reshape(
        mcus_y * 8, mcus_x * 8, 3)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=mcus_x * 8, height=mcus_y * 8),
        to_rgb=lambda crop=False: rgb,
    )

    def estimate(_left, _right, start, _end, _width):
        phase = {0: 0, band: 1, 2 * band: 2}[start]
        return {
            'phase': phase, 'margin': 2.0, 'score': 8.0,
            'raw_ratio': 3.0, 'pairs': 4,
            'confident': trusted and start == band,
        }

    monkeypatch.setattr(resync, '_adaptive_phase_estimate', estimate)
    monkeypatch.setattr(resync, '_boundary_signature',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(resync, '_layout_quality_safe',
                        lambda *args, **kwargs: False)
    monkeypatch.setattr(resync, '_layout_quality_metrics',
                        lambda *args, **kwargs: (
                            1.0, 2.0, 1.0, capped_after, 0.0, high_after))
    dc = np.zeros(3, np.int64)
    corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc)], mcus_x * mcus_y,
        [(band, 'sub', 0.0), (2 * band, 'sub', 0.0)])

    assert stats['shifted'] == expected_shifted
    assert stats['shift_reject'] == expected_reject
    if expected_shifted:
        assert stats['mcu_ins'] == 2 and stats['mcu_drop'] == 2
    else:
        assert np.array_equal(corrected, rgb)


def test_aligned_trace_does_not_apply_short_band_deletion_only(monkeypatch):
    """Short-band loss is allowed only as part of an actual phase correction."""
    mcus_x, mcus_y = 10, 100
    rgb = np.full((mcus_y * 8, mcus_x * 8, 3), 90, np.uint8)
    dec = SimpleNamespace(
        mcus_x=mcus_x, mcus_y=mcus_y, hmax=1, vmax=1,
        h=SimpleNamespace(width=mcus_x * 8, height=mcus_y * 8),
        to_rgb=lambda crop=False: rgb,
    )
    monkeypatch.setattr(resync, '_adaptive_phase_estimate',
                        lambda *args, **kwargs: {
                            'phase': 0, 'margin': 3.0, 'score': 8.0,
                            'raw_ratio': 3.0, 'pairs': 5,
                            'confident': True,
                        })
    monkeypatch.setattr(resync, '_boundary_signature',
                        lambda *args, **kwargs: None)
    dc = np.zeros(3, np.int64)
    corrected, stats = resync._correct_segment_shifts(
        dec, rgb, [(0, 0, dc)], mcus_x * mcus_y,
        [(400, 'sub', 0.0), (420, 'sub', 0.0)])
    assert stats['shifted'] == 0
    assert stats['mcu_drop'] == 0
    assert np.array_equal(corrected, rgb)

def test_recover_segments_strictly_increasing_bits():
    """회귀 방지: 세그먼트 시작 비트는 단조 증가해야 한다.

    과거 버그는 디싱크 후 mcu_bit가 미기록(0)으로 남아 resync가 비트 0으로 역행,
    스트림 앞부분을 반복 디코딩(주기적 중복)했다. 손상본을 복구해 비트위치가
    뒤로 가지 않음을 확인한다."""
    data = corrupt_entropy(encode(textured_image(), subsampling=1), n_bytes=40)
    dec = jd.Decoder(data)
    _rgb, _stats, segments = resync.recover(dec)
    start_bits = [sbit for (_sm, sbit, _dc) in sorted(segments)]
    assert start_bits == sorted(start_bits)          # 단조 비감소
    assert len(set(start_bits)) == len(start_bits)   # 중복(재디코딩) 없음


def test_recover_fast_and_thorough_both_run():
    """철저(기본)·빠른 모드 모두 유효한 이미지를 낸다(파라미터 스레딩 스모크)."""
    data = corrupt_entropy(encode(textured_image(200, 320)), n_bytes=24)
    for kw in ({}, dict(resync_near=160000, resync_full=False, time_budget=5.0)):
        dec = jd.Decoder(data)
        rgb, _stats, _segs = resync.recover(dec, **kw)
        assert rgb.shape == (200, 320, 3)


def test_recover_bytes_handles_garbage():
    """디코드 불가 입력은 (None, {})로 안전 처리."""
    rgb, stats = resync.recover_bytes(b'\xff\xd8not a real jpeg\xff\xd9')
    assert rgb is None and stats == {}


def test_recover_file_roundtrip(tmp_path):
    """recover_file이 유효한 JPEG를 저장한다."""
    src = tmp_path / '0xDEADBEEF.jpg'
    src.write_bytes(corrupt_entropy(encode(textured_image()), n_bytes=24))
    out, action, info = resync.recover_file(src, tmp_path)
    assert action in ('RECOVERED', 'CLEAN')
    if action == 'RECOVERED':
        assert out.exists()
        Image.open(out).load()                       # 유효 JPEG
        assert 'gray_before' in info and 'gray_after' in info


def test_recover_file_routes_recovered_subdir(tmp_path):
    """RECOVERED 결과는 out_dir/recovered/ 아래에 저장된다."""
    src = tmp_path / '0xDEADBEEF.jpg'
    src.write_bytes(corrupt_entropy(encode(textured_image()), n_bytes=32, seed=99))
    out, action, info = resync.recover_file(src, tmp_path)
    assert action == 'RECOVERED'
    assert out.parent == tmp_path / 'recovered'
    assert out.exists()
    Image.open(out).load()                       # 유효 JPEG


def test_recover_file_clean_copies_original(tmp_path):
    """손상 없는 JPEG는 clean/ 폴더에 원본 바이트 그대로 복사된다."""
    src = tmp_path / '0xCAFEBABE.jpg'
    raw = encode(textured_image())
    src.write_bytes(raw)
    out, action, info = resync.recover_file(src, tmp_path)
    assert action == 'CLEAN'
    assert out.parent == tmp_path / 'clean'
    assert out.read_bytes() == raw               # 원본 바이트 동일


def test_recover_file_global_only_shift_writes_recovered(
        tmp_path, monkeypatch):
    """An ops-zero global row fit must not be discarded as CLEAN."""
    src = tmp_path / '0xA11A1A11.jpg'
    raw = encode(textured_image())
    src.write_bytes(raw)

    def global_only(_dec, rgb, *_args, **_kwargs):
        corrected = np.flip(rgb, axis=1).copy()
        return corrected, {
            'shifted': 0, 'mcu_ins': 1, 'mcu_drop': 1,
            'row_global_passes': 1, 'row_global_events': 1,
            'row_global_changes': 1, 'row_global_plan': ((1, 1),),
        }

    monkeypatch.setattr(resync, '_correct_segment_shifts', global_only)
    out, action, info = resync.recover_file(src, tmp_path)

    assert action == 'RECOVERED'
    assert out.parent == tmp_path / 'recovered'
    assert out.read_bytes() != raw
    assert info['ops'] == 0
    assert info['spatial_changed'] == 1
    assert info['row_global_passes'] == 1


def test_recover_file_skip_copies_original(tmp_path):
    """디코드 불가 입력은 skip_undecodable/ 폴더에 원본 바이트 그대로 복사된다."""
    src = tmp_path / '0xFEEDFACE.jpg'
    raw = b'\xff\xd8 not a decodable jpeg \xff\xd9'
    src.write_bytes(raw)
    out, action, info = resync.recover_file(src, tmp_path)
    assert action == 'SKIP_UNDECODABLE'
    assert out.parent == tmp_path / 'skip_undecodable'
    assert out.read_bytes() == raw


def test_recover_file_failed_preserves_original(tmp_path):
    """무행동(ops 0·hole≥1) 파일은 failed/에 원본 바이트를 보존한다.

    절단(EOI 없이 엔트로피 후반 소실)으로 디싱크 후 잔여 MCU가 수락 바닥(run 30)
    미만이면 어떤 편집·재동기도 수락될 수 없어 무행동으로 끝난다 — 회색
    재인코딩본 대신 원본을 남겨야 한다. (임계 비례화 이후에도 30 MCU 바닥은
    남는다 — 그보다 짧은 꼬리는 진위를 검증할 수 없다.)"""
    data = encode(textured_image(64, 64, seed=7))    # 4:2:2 → 32 MCU 소형
    h = jd.parse_header(data)
    last_eoi = data.rfind(b'\xff\xd9')
    trunc = data[:h.scan_start + (last_eoi - h.scan_start) * 7 // 10]  # 후반 30% 절단
    src = tmp_path / '0xBADD1E00.jpg'
    src.write_bytes(trunc)
    out, action, info = resync.recover_file(src, tmp_path, time_budget=15)
    assert action == 'FAILED'
    assert out.parent == tmp_path / 'failed'
    assert out.read_bytes() == trunc                 # 원본 보존
    assert info['ops'] == 0 and info['hole'] >= 1
    assert info['mcus'] == 32


# ── 수락 임계 잔여 비례화 ───────────────────────────────────

def fe_hole(data: bytes, frac: float, n_bytes: int) -> bytes:
    """엔트로피의 frac 지점에 0xFE 채움 hole을 만든다. 0xFE는 Annex-K DC cat-10
    코드(11111110)라 큰 DC 차분이 연쇄돼 계수 경계를 결정적으로 반복 발동시킨다
    (무작위 손상은 조밀 코드 공간 탓에 '그럴듯한' 스트림으로 조용히 디코드될 수
    있어 테스트 재료로 비결정적이다)."""
    h = jd.parse_header(data)
    last_eoi = data.rfind(b'\xff\xd9')
    span = last_eoi - h.scan_start
    arr = bytearray(data)
    pos = h.scan_start + int(span * frac)
    for i in range(n_bytes):
        arr[pos + i] = 0xFE
    return bytes(arr)


def test_recover_small_image_resync_unlocked():
    """총 MCU<450 소형 이미지도 재동기가 수락된다(임계 잠금 해제).

    과거 절대 임계(max(250, maxW//2)=450)에서는 총 MCU 256인 이미지의 재동기
    수락이 산술적으로 불가능했다 — 손상 클러스터 이후 전량 회색(임계 잠금).
    창 비례 임계(max(30, 0.35·W))는 소형에서도 수락을 허용한다. hole을 2개 둬
    바이트 오라클 단독으로는 복구가 끝나지 않게 한다."""
    data = fe_hole(fe_hole(encode(textured_image(128, 256, seed=3)), 0.40, 150), 0.75, 150)
    dec = jd.Decoder(data)                           # 4:2:2 → 16×16 = 256 MCU
    assert dec.mcus_x * dec.mcus_y == 256
    rgb, stats, _segs = resync.recover(dec, time_budget=0)
    assert stats['resync'] >= 1                      # 절대 임계에서는 불가능했던 수락
    assert resync.undecoded_fraction(rgb) < 0.3      # hole 이후가 회색으로 남지 않음


def test_recover_truncated_tail_resync_via_buffer_end():
    """버퍼 끝까지 완주하는 후보(stop=3)는 0.35·W 미만이어도 run≥30이면 수락된다.

    절단 파일은 남은 데이터 전체가 이어져도 그 길이(여기선 ~115 MCU)가 창 비례
    임계(0.35·460=161)에 못 미친다 — 데이터가 소진돼 뒤에 가릴 내용이 없으므로
    완주 run은 신뢰할 수 있고, 이 규칙이 없으면 hole로 끝나 절단 앞 콘텐츠까지
    버려진다."""
    data = encode(textured_image(256, 384, seed=11))  # 4:2:2 → 24×32 = 768 MCU
    h = jd.parse_header(data)
    last_eoi = data.rfind(b'\xff\xd9')
    span = last_eoi - h.scan_start
    data = fe_hole(data, 0.40, 100)                  # hole 먼저(EOI 존재 시점에 위치 계산)
    data = data[:h.scan_start + span * 55 // 100]    # 후반 45% 절단
    dec = jd.Decoder(data)
    rgb, stats, _segs = resync.recover(dec, time_budget=0)
    assert stats['resync'] >= 1                      # 완주 규칙에 의한 수락
    assert stats['hole'] >= 1                        # 데이터 소진 꼬리는 hole로 남음
    assert rgb.shape == (256, 384, 3)


# ── 헤더 복구 ───────────────────────────────────────────────

def strip_dht(data: bytes) -> bytes:
    """SOS 이전의 DHT 마커(FFC4)를 FF00으로 무력화해 huff를 비운다(DHT 소실 모사).
    마커 워크는 길이 기반이라 세그먼트는 그대로 건너뛰어져 스트림이 일관된다."""
    h = jd.parse_header(data)
    arr = bytearray(data)
    i = 2
    while i < h.scan_start - 1:
        if arr[i] == 0xFF and arr[i + 1] == 0xC4:
            arr[i + 1] = 0x00
        i += 1
    return bytes(arr)


def test_recover_file_header_dht_transplant(tmp_path):
    """DHT 소실 파일은 도너(Annex-K) 테이블 이식으로 복구된다(HEADER_RECOVERED, header_fix=dht).

    엔트로피가 온전하고 헤더만 손상된 경우 — 현행 엔진(엔트로피만 편집)은 Decoder 구성
    실패로 SKIP했으나, 헤더 복구 pass가 도너 DHT를 이식해 디코드를 복원한다. PIL 인코더는
    Annex-K 표준 테이블(도너와 동일 지문 050df0dc)을 쓰므로 이식이 정확히 맞는다.
    헤더 복구본은 원본이 디코드 불가하므로 recovered/가 아닌 header_recovered/에 저장된다."""
    raw = encode(textured_image(96, 96, seed=5))
    src = tmp_path / '0xDEAD00C4.jpg'
    src.write_bytes(strip_dht(raw))
    with pytest.raises(ValueError):                  # 소실 확인 — 정상 경로는 거부
        jd.Decoder(strip_dht(raw))
    out, action, info = resync.recover_file(src, tmp_path, time_budget=0)
    assert action == 'HEADER_RECOVERED'
    assert out.parent == tmp_path / 'header_recovered'
    assert 'dht' in info['header_fix']
    assert info['undec_after'] < 0.05                # 엔트로피 온전 → 사실상 완전 복구
    Image.open(out).load()                           # 유효 JPEG


def test_recover_file_header_unrecoverable_stays_skip(tmp_path):
    """헤더 후보를 못 찾는 손상은 재구성이 None을 반환해 SKIP으로 남는다(가짜 채움 금지)."""
    src = tmp_path / '0xN0FIX000.jpg'
    raw = b'\xff\xd8' + bytes(range(200)) * 4 + b'\xff\xd9'   # SOS/SOF/DQT 없음
    src.write_bytes(raw)
    out, action, info = resync.recover_file(src, tmp_path, time_budget=0)
    assert action == 'SKIP_UNDECODABLE'
    assert out.read_bytes() == raw                   # 원본 보존


def test_recover_file_header_truncated_dqt_pattern_no_crash(tmp_path):
    """DQT len 패턴('00 84')이 파일 끝 근처에 매치돼 테이블 슬라이스가 64B 미만이어도
    크래시하지 않고 SKIP으로 처리한다(carve 위양성 쓰레기 파일 방어)."""
    src = tmp_path / '0xTRUNCD00.jpg'
    raw = b'\xff\xd8' + b'\x00' * 40 + b'\x00\x84\x00\x11\x22'  # 00 84 직후 EOF
    src.write_bytes(raw)
    out, action, info = resync.recover_file(src, tmp_path, time_budget=0)
    assert action == 'SKIP_UNDECODABLE'              # 예외 없이 분류
