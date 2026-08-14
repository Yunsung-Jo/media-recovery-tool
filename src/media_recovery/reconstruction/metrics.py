"""Single-best reconstruction에서 공유하는 순수 RGB 품질 지표."""
from __future__ import annotations

import numpy as np


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
