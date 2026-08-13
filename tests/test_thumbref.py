# tests/test_thumbref.py
"""thumbnail_guided.py — 썸네일 오라클 보정의 합성 이미지 회귀 테스트.

실데이터 게이트 검증은 shift_experiments/refsdco/(PoC·전수 스캔)에서 수행했고,
여기서는 합성 입력으로 계약을 고정한다: 무손상 identity, 순환 밀림 복원,
색 밴드 중화, 소형 이미지 스킵, JPEG 파서.
"""
import io
import struct

import numpy as np
import pytest
from PIL import Image

from media_recovery.enhancement.thumbnail_guided import (
    _rgb2ycc,
    _ycc2rgb,
    extract_thumbnail,
    parse_mcu_size,
    process_arrays,
)

MCU_W, MCU_H = 16, 8


def synth(w=640, h=480, seed=0):
    """비주기 저주파 텍스처 — 행별 상관이 성립하되 가짜 주기 피크가 없다."""
    rng = np.random.default_rng(seed)
    small = rng.uniform(0, 255, (h // 16, w // 16, 3))
    return np.asarray(
        Image.fromarray(small.astype(np.uint8)).resize((w, h), Image.BILINEAR),
        np.float64)


def thumb_of(img, factor=2):
    w = img.shape[1] // factor
    h = img.shape[0] // factor
    return np.asarray(
        Image.fromarray(img.astype(np.uint8)).resize((w, h), Image.BILINEAR),
        np.float64)


def luma_mae(a, b):
    la = a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114
    lb = b[..., 0] * 0.299 + b[..., 1] * 0.587 + b[..., 2] * 0.114
    return float(np.abs(la - lb).mean())


def test_identity_on_clean_image():
    """무손상 입력은 보정 0(identity)이어야 한다 — 정상 가드 계약."""
    truth = synth(seed=1)
    res = process_arrays(thumb_of(truth), truth, MCU_W, MCU_H)
    assert res["status"] == "identity"
    assert res["shift_rows"] == 0
    assert res["color_rows"] == 0


def test_cyclic_shift_recovered():
    """행 밴드 순환 밀림을 검출·복원한다."""
    truth = synth(seed=2)
    damaged = truth.copy()
    lo, hi = 25 * MCU_H, 40 * MCU_H          # MCU 행 25..39
    damaged[lo:hi] = np.roll(truth[lo:hi], -3 * MCU_W, axis=1)
    res = process_arrays(thumb_of(truth), damaged, MCU_W, MCU_H)
    assert res["status"] == "corrected"
    assert res["shift_rows"] > 0
    mae_before = luma_mae(damaged, truth)
    mae_after = luma_mae(res["corrected"], truth)
    assert mae_after < 0.2 * mae_before


def test_multiband_iterative_shift():
    """여러 밴드의 큰 순환 밀림을 반복 보정으로 수렴 복원한다.

    1회 apply_shift가 놓치는 광범위 재동기를 반복이 잡는지 회귀 고정.
    """
    truth = synth(w=1024, h=768, seed=7)
    damaged = truth.copy()
    for a, b, s in [(12, 26, -9), (40, 54, 13), (68, 82, -16)]:
        lo, hi = a * MCU_H, b * MCU_H
        damaged[lo:hi] = np.roll(truth[lo:hi], s * MCU_W, axis=1)
    res = process_arrays(thumb_of(truth), damaged, MCU_W, MCU_H)
    assert res["status"] == "corrected"
    assert res["shift_iters"] >= 1
    assert luma_mae(res["corrected"], truth) < 0.3 * luma_mae(damaged, truth)


def test_color_band_neutralized():
    """크로마 캐스트 밴드를 검출·중화한다."""
    truth = synth(seed=3)
    damaged = truth.copy()
    lo, hi = 12 * MCU_H, 32 * MCU_H
    ycc = _rgb2ycc(damaged[lo:hi])
    ycc[..., 1] += 18.0                      # Cb 캐스트
    damaged[lo:hi] = np.clip(_ycc2rgb(ycc), 0, 255)
    res = process_arrays(thumb_of(truth), damaged, MCU_W, MCU_H)
    assert res["status"] == "corrected"
    assert res["color_rows"] > 0
    diff_before = np.abs(_rgb2ycc(damaged[lo:hi])[..., 1]
                         - _rgb2ycc(truth[lo:hi])[..., 1]).mean()
    diff_after = np.abs(_rgb2ycc(res["corrected"][lo:hi])[..., 1]
                        - _rgb2ycc(truth[lo:hi])[..., 1]).mean()
    assert diff_after < 0.4 * diff_before


def test_small_main_skipped():
    """메인이 썸네일급 크기면 오라클 이득이 없어 스킵한다."""
    truth = synth(w=320, h=240)
    res = process_arrays(thumb_of(truth, factor=1), truth, MCU_W, MCU_H)
    assert res["status"] == "skip_small"


def _jpeg_bytes(img_arr, **save_kwargs):
    buf = io.BytesIO()
    Image.fromarray(img_arr.astype(np.uint8)).save(buf, "JPEG", **save_kwargs)
    return buf.getvalue()


@pytest.mark.parametrize("subsampling,expected", [(0, (8, 8)), (1, (16, 8)), (2, (16, 16))])
def test_parse_mcu_size(subsampling, expected):
    data = _jpeg_bytes(synth(w=160, h=128), subsampling=subsampling, quality=90)
    assert parse_mcu_size(data) == expected


def test_extract_thumbnail_roundtrip():
    """APP1(Exif) 페이로드에 내장한 JPEG 썸네일을 추출한다."""
    thumb = _jpeg_bytes(synth(w=64, h=48, seed=4), quality=85)
    payload = b"Exif\x00\x00" + b"\x00" * 16 + thumb
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    main = _jpeg_bytes(synth(w=160, h=128, seed=5), quality=90)
    data = main[:2] + app1 + main[2:]
    im = extract_thumbnail(data)
    assert im is not None
    assert im.size == (64, 48)


def test_extract_thumbnail_absent():
    data = _jpeg_bytes(synth(w=160, h=128), quality=90)
    assert extract_thumbnail(data) is None
