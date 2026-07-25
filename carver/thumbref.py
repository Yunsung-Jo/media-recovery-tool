# carver/thumbref.py
"""EXIF 썸네일을 참조 오라클로 쓰는 잔여 밀림·색 캐스트 보정.

recover 산출물에 남은 순환 MCU 밀림과 DC 색 밴드를, 같은 카빙 원본의 EXIF
썸네일(무손상 저해상 정답 근사)과 정합·대조해 추정하고 픽셀 도메인에서
보정한다. 모든 추정은 보수적 게이트를 통과해야 하며, 근거 없는 파일은
identity로 남긴다. 게이트 검증 과정은 ADR 0012와
`shift_experiments/refsdco/PLAN.md`에 있다.

용어와 부호 규약:
- 워프: 썸네일 캔버스 위에 X 축소본을 (scale, dy)로 붙인 것. dx는 행별
  추정에 흡수하고 프레이밍 성분만 뒤에서 뺀다.
- 행 시프트 k(캔버스 px): ``np.roll(x_row, k)``가 X를 썸네일에 정렬하는 양.
  메인 px = ``k * scale_x / s``, MCU 단위 = 그 값 / mcu_w.
"""
from __future__ import annotations

import io
import struct

import numpy as np
from PIL import Image

# ---- 게이트 상수 (검증: refsdco PoC — 정상 가드 identity·양성 복원 동시 통과) ----
VAR_MIN = 2.0        # 행 표준편차 최소값. 미만이면 그 행은 증거로 쓰지 않는다
MARGIN_ROW = 0.10    # corr[peak]-corr[0] 최소 마진. 상관 절대값은 판별력이 없다
NCC_MIN = 0.25       # 블록 재검증에서 roll 후 NCC 최소값
BAND_MARGIN = 0.08   # 블록 재검증에서 roll-zero NCC 개선 최소값
REG_MIN = 0.30       # 전역 정합(자유-시프트 행 상관 중앙값) 최소값
MEDFILT = 5          # 행 추정 중앙값 필터 창
# 렌더 차이(비네팅·노출)는 Y에 몰리고 손상 캐스트는 크로마가 지배한다
COLOR_GATE = np.array([6.0, 3.5, 3.5])   # |밴드 오프셋| 최소값 (Y, Cb, Cr)
SMALL_MAIN_RATIO = 2.0   # 메인 폭 < 썸네일 폭*비율이면 오라클 이득 없음 → 스킵
DMATCH_MIN = 0.01        # 시프트 적용 시 요구하는 self-check 개선 최소값(회차별)
MAX_SHIFT_ITERS = 5      # 밀림 반복 보정 최대 회차(수렴·개선 없음이면 조기 정지)


# ---------- JPEG 세그먼트 파서 ----------

def _walk_segments(data: bytes):
    """(marker, payload) 순회. SOS에서 멈춘다."""
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return
        marker = data[i + 1]
        if marker == 0xDA:
            return
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        yield marker, data[i + 4:i + 2 + seg_len]
        i += 2 + seg_len


def extract_thumbnail(data: bytes):
    """APP1(Exif) 내장 JPEG 썸네일을 디코드해 RGB로 반환. 없거나 깨지면 None."""
    for marker, seg in _walk_segments(data):
        if marker == 0xE1 and seg[:6] == b"Exif\x00\x00":
            s = seg.find(b"\xff\xd8\xff")
            if s == -1:
                continue
            e = seg.find(b"\xff\xd9", s)
            if e == -1:
                continue
            try:
                im = Image.open(io.BytesIO(seg[s:e + 2])).convert("RGB")
                im.load()
                return im
            except Exception:
                return None
    return None


def parse_mcu_size(data: bytes):
    """SOF 성분 샘플링 팩터로 (mcu_w, mcu_h)를 계산. 읽지 못하면 None."""
    for marker, seg in _walk_segments(data):
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            if len(seg) < 6:
                return None
            hmax = vmax = 1
            for c in range(seg[5]):
                off = 6 + c * 3
                if off + 1 < len(seg):
                    samp = seg[off + 1]
                    hmax = max(hmax, samp >> 4)
                    vmax = max(vmax, samp & 0x0F)
            return 8 * hmax, 8 * vmax
    return None


# ---------- 색 변환 ----------

def _luma(a):
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def _rgb2ycc(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return np.stack([
        0.299 * r + 0.587 * g + 0.114 * b,
        -0.168736 * r - 0.331264 * g + 0.5 * b + 128.0,
        0.5 * r - 0.418688 * g - 0.081312 * b + 128.0,
    ], axis=-1)


def _ycc2rgb(a):
    y, cb, cr = a[..., 0], a[..., 1] - 128.0, a[..., 2] - 128.0
    return np.stack([
        y + 1.402 * cr,
        y - 0.344136 * cb - 0.714136 * cr,
        y + 1.772 * cb,
    ], axis=-1)


# ---------- 전역 정합 (scale + dy) ----------

def _warp_canvas(arr, s, dy, out_h, out_w, fill):
    """arr를 중심 기준 s배로 리샘플해 캔버스에 (dy) 오프셋으로 붙인다."""
    h, w = arr.shape[:2]
    nh, nw = max(1, int(round(h * s))), max(1, int(round(w * s)))
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    rs = np.asarray(im.resize((nw, nh), Image.BILINEAR), np.float64)
    shape = (out_h, out_w) + arr.shape[2:]
    canvas = np.full(shape, fill, np.float64)
    valid = np.zeros((out_h, out_w), bool)
    y0 = (out_h - nh) // 2 + dy
    x0 = (out_w - nw) // 2
    ys0, xs0 = max(0, -y0), max(0, -x0)
    yd0, xd0 = max(0, y0), max(0, x0)
    hh = min(nh - ys0, out_h - yd0)
    ww = min(nw - xs0, out_w - xd0)
    if hh > 0 and ww > 0:
        canvas[yd0:yd0 + hh, xd0:xd0 + ww] = rs[ys0:ys0 + hh, xs0:xs0 + ww]
        valid[yd0:yd0 + hh, xd0:xd0 + ww] = True
    return canvas, valid


def _row_fft(l):
    """행별 (FFT, std). 평탄 행은 None — 증거로 쓰지 않는다."""
    out = []
    for y in range(l.shape[0]):
        r = l[y] - l[y].mean()
        sd = r.std()
        out.append((np.fft.fft(r), sd) if sd >= VAR_MIN else None)
    return out


def _reg_score(tf, xf, dys, sub_rows, w):
    """dy별로 행 자유-시프트 최대 순환 상관의 중앙값을 계산한다."""
    res = {}
    for dy in dys:
        vals = []
        for y in sub_rows:
            xy = y - dy
            if not (0 <= xy < len(xf)):
                continue
            a, b = tf[y], xf[xy]
            if a is None or b is None:
                continue
            corr = np.fft.ifft(a[0] * np.conj(b[0])).real / (a[1] * b[1] * w)
            vals.append(float(corr.max()))
        res[dy] = float(np.median(vals)) if len(vals) >= 15 else -1.0
    return res


def _global_register(tl, xl):
    """(score, scale, dy)를 찾는다.

    스코어가 행별 시프트에 불변이라, 순환 밀림이 있는 손상 파일에서도 정합이
    성립한다. dx는 여기서 정하지 않고 행별 추정의 프레이밍 성분으로 뺀다.
    """
    h, w = tl.shape
    tf = _row_fft(tl)
    sub_rows = list(range(4, h - 4, 4))
    best = None
    for s in np.exp(np.linspace(np.log(0.80), np.log(1.25), 41)):
        xw, _ = _warp_canvas(xl, s, 0, h, w, fill=float(xl.mean()))
        xf = _row_fft(xw)
        sc = _reg_score(tf, xf, range(-14, 15, 2), sub_rows, w)
        dy = max(sc, key=sc.get)
        if best is None or sc[dy] > best[0]:
            best = (sc[dy], float(s), dy)
    score, s0, dy0 = best
    for s in np.linspace(s0 * 0.985, s0 * 1.015, 7):
        xw, _ = _warp_canvas(xl, s, 0, h, w, fill=float(xl.mean()))
        xf = _row_fft(xw)
        sc = _reg_score(tf, xf, range(dy0 - 2, dy0 + 3), sub_rows, w)
        dy = max(sc, key=sc.get)
        if sc[dy] > best[0]:
            best = (sc[dy], float(s), dy)
    return best


# ---------- 행별 시프트 추정 ----------

def _row_estimates(tl, xw):
    """행별 (시프트 k, 피크 상관, 0-시프트 대비 마진)."""
    h, w = tl.shape
    k_arr = np.zeros(h)
    pk_arr = np.zeros(h)
    mg_arr = np.zeros(h)
    for y in range(h):
        t = tl[y] - tl[y].mean()
        x = xw[y] - xw[y].mean()
        st, sx = t.std(), x.std()
        if st < VAR_MIN or sx < VAR_MIN:
            continue
        corr = np.fft.ifft(np.fft.fft(t) * np.conj(np.fft.fft(x))).real
        corr /= st * sx * w
        k = int(np.argmax(corr))
        c0, c1, c2 = corr[(k - 1) % w], corr[k], corr[(k + 1) % w]
        den = c0 - 2 * c1 + c2
        frac = float(np.clip(0.5 * (c0 - c2) / den, -0.5, 0.5)) if abs(den) > 1e-9 else 0.0
        kk = k + frac
        if kk > w / 2:
            kk -= w
        k_arr[y] = kk
        pk_arr[y] = c1
        mg_arr[y] = c1 - corr[0]
    return k_arr, pk_arr, mg_arr


def _masked_medfilt(vals, mask, win=MEDFILT):
    out = np.zeros_like(vals, dtype=float)
    n = len(vals)
    for i in range(n):
        lo, hi = max(0, i - win // 2), min(n, i + win // 2 + 1)
        v = vals[lo:hi][mask[lo:hi]]
        out[i] = np.median(v) if len(v) >= 3 else 0.0
    return out


def _ncc(a, b):
    a = a - a.mean()
    b = b - b.mean()
    d = a.std() * b.std()
    return float((a * b).mean() / d) if d > 1e-9 else 0.0


def _bands_of(units):
    out = []
    i, n = 0, len(units)
    while i < n:
        j = i
        while j < n and units[j] == units[i]:
            j += 1
        out.append((i, j, units[i]))
        i = j
    return out


def estimate(thumb_rgb, x_rgb, mcu_w, mcu_h):
    """썸네일 대비 X의 전역 정합과 행별 MCU 시프트를 추정한다.

    반환: 성공 시 est dict, 실패 시 {"skip": 사유}.
    """
    h_t, w_t = thumb_rgb.shape[0], thumb_rgb.shape[1]
    h_m, w_m = x_rgb.shape[0], x_rgb.shape[1]
    scale_x = w_m / w_t

    x_small = np.asarray(
        Image.fromarray(x_rgb.astype(np.uint8)).resize((w_t, h_t), Image.BILINEAR),
        np.float64)
    tl = _luma(thumb_rgb)
    xl = _luma(x_small)

    reg = _global_register(tl, xl)
    if reg is None or reg[0] < REG_MIN:
        return {"skip": f"reg_failed({reg[0]:.3f})" if reg else "reg_failed"}
    score, s, gdy = reg
    xw_rgb, _ = _warp_canvas(x_small, s, gdy, h_t, w_t, fill=float(x_small.mean()))
    xw = _luma(xw_rgb)

    k, pk, mg = _row_estimates(tl, xw)
    px_per_canvas = scale_x / s
    # 프레이밍 dx: 근사-0 신뢰 행들의 중앙값. 서브-MCU 성분만 보정한다
    near = (np.abs(k * px_per_canvas) < 0.7 * mcu_w) & (pk >= 0.4)
    framing = float(np.median(k[near])) if near.sum() >= 10 else 0.0
    k = k - framing
    units_f = k * px_per_canvas / mcu_w
    mask = (mg >= MARGIN_ROW) & (np.abs(units_f) >= 0.5) & (pk >= NCC_MIN)
    units = np.round(_masked_medfilt(units_f, mask)).astype(int)

    # 밴드 재검증: 블록 NCC가 0-시프트보다 실제로 나아야 유지한다
    min_band = 3 if scale_x < 12 else 2
    for i, j, u in _bands_of(units):
        if u == 0:
            continue
        if (j - i) < min_band:
            units[i:j] = 0
            continue
        k_px = int(round(u * mcu_w / px_per_canvas))
        rolled = np.roll(xw[i:j], k_px, axis=1)
        if (_ncc(tl[i:j], rolled) < NCC_MIN
                or (_ncc(tl[i:j], rolled) - _ncc(tl[i:j], xw[i:j])) < BAND_MARGIN):
            units[i:j] = 0

    return dict(skip=None, reg_score=score, s=s, gdy=gdy, framing=framing,
                k=k, pk=pk, mg=mg, units=units,
                px_per_canvas=px_per_canvas, x_small=x_small,
                h_t=h_t, w_t=w_t)


def _canvas_row_of_mcu(m, mcu_h, h_m, h_t, s, gdy):
    """메인 MCU 행 m을 썸네일 캔버스 행으로 순방향 매핑한다."""
    ys = (m * mcu_h + mcu_h / 2) * h_t / h_m
    nh = int(round(h_t * s))
    y0 = (h_t - nh) // 2 + gdy
    return int(round(y0 + ys * s))


def apply_shift(x_rgb, est, mcu_w, mcu_h):
    """추정 시프트를 MCU 행 단위 순환 roll(16px 배수)로 적용한다."""
    h_m = x_rgb.shape[0]
    n_rows = (h_m + mcu_h - 1) // mcu_h
    row_units = np.zeros(n_rows, int)
    for m in range(n_rows):
        yc = _canvas_row_of_mcu(m, mcu_h, h_m, est["h_t"], est["s"], est["gdy"])
        if 0 <= yc < est["h_t"]:
            row_units[m] = est["units"][yc]
    y = x_rgb.copy()
    for m, u in enumerate(row_units):
        if u == 0:
            continue
        lo, hi = m * mcu_h, min((m + 1) * mcu_h, h_m)
        y[lo:hi] = np.roll(x_rgb[lo:hi], int(u) * mcu_w, axis=1)
    return y, row_units


# ---------- 색 밴드 보정 ----------

def _robust_linear(x, y):
    """채널별 강건 gain+offset(y ~ a*x + b). 썸네일 톤·채도 처리 차이를 흡수해
    국소 캐스트 밴드만 잔차로 남긴다(단순 median 차는 과보정을 만든다)."""
    a, b = 1.0, float(np.median(y - x))
    for _ in range(2):
        r = np.abs(y - (a * x + b))
        keep = r <= np.percentile(r, 60)
        if keep.sum() < 10:
            break
        xs, ys = x[keep], y[keep]
        vx = xs.std()
        if vx < 2.0:
            a = 1.0
        else:
            a = float(np.clip(np.cov(xs, ys)[0, 1] / (vx * vx), 0.7, 1.4))
        b = float(np.median(ys - a * xs))
    return a, b


def _measure_band(thumb_rgb, y_rgb, est, pk_min=0.2):
    """전역 선형 톤 모델 대비 캔버스 행별 YCbCr 밴드 오프셋."""
    h_t, w_t = est["h_t"], est["w_t"]
    y_small = np.asarray(
        Image.fromarray(np.clip(y_rgb, 0, 255).astype(np.uint8))
        .resize((w_t, h_t), Image.BILINEAR), np.float64)
    yw_rgb, valid = _warp_canvas(y_small, est["s"], est["gdy"], h_t, w_t,
                                 fill=float(y_small.mean()))
    tc, yc = _rgb2ycc(thumb_rgb), _rgb2ycc(yw_rgb)
    mt = np.full((h_t, 3), np.nan)
    my = np.full((h_t, 3), np.nan)
    for yy in range(h_t):
        v = valid[yy]
        if v.sum() < w_t * 0.5:
            continue
        if est["pk"][yy] < pk_min:      # 콘텐츠가 어디에서도 안 맞은 행: 증거 없음
            continue
        if yc[yy, v, 0].std() < 0.5:    # 평탄 회색 hole 행
            continue
        mt[yy] = np.median(tc[yy, v], axis=0)
        my[yy] = np.median(yc[yy, v], axis=0)
    ok = ~np.isnan(mt[:, 0])
    if ok.sum() < 10:
        return None
    band = np.zeros((h_t, 3))
    stds = np.zeros(3)
    for c in range(3):
        a, b = _robust_linear(my[ok, c], mt[ok, c])
        off = np.zeros(h_t)
        off[ok] = mt[ok, c] - (a * my[ok, c] + b)
        stds[c] = off[ok].std()
        band[:, c] = _masked_medfilt(off, ok)
    band[np.abs(band) < COLOR_GATE[None, :]] = 0.0
    nz = band.any(axis=1).astype(int)
    for i, j, u in _bands_of(nz):
        if u == 1 and (j - i) < 3:
            band[i:j] = 0.0
    return dict(band=band, stds=stds)


def _apply_band(y_rgb, band, est, mcu_h):
    yc_main = _rgb2ycc(y_rgb)
    h_m = y_rgb.shape[0]
    h_t = est["h_t"]
    n_rows = (h_m + mcu_h - 1) // mcu_h
    applied = 0
    for m in range(n_rows):
        yc = _canvas_row_of_mcu(m, mcu_h, h_m, h_t, est["s"], est["gdy"])
        if not (0 <= yc < h_t):
            continue
        b = band[yc]
        if not b.any():
            continue
        lo, hi = m * mcu_h, min((m + 1) * mcu_h, h_m)
        yc_main[lo:hi] += b
        applied += 1
    return np.clip(_ycc2rgb(yc_main), 0, 255), applied


def apply_color(thumb_rgb, y_rgb, est, mcu_h, iters=2):
    """밴드 오프셋을 추정·적용하고 잔차를 1회 재적용해 수렴시킨다."""
    z = y_rgb
    std0 = None
    total = 0
    for _ in range(iters):
        m = _measure_band(thumb_rgb, z, est)
        if m is None:
            break
        if std0 is None:
            std0 = m["stds"]
        if not m["band"].any():
            break
        z2, applied = _apply_band(z, m["band"], est, mcu_h)
        if applied == 0:
            break
        z = z2
        total += applied
    m = _measure_band(thumb_rgb, z, est)
    return dict(std0=std0, res_std=(m["stds"] if m else None)), z, total


# ---------- self-check ----------

def zero_match(tl, wl, rows=None, min_rows=15):
    """질감 있는 행들의 0-시프트 정규화 상관 중앙값. 보정 수락 판정에 쓴다.

    rows가 주어지면 그 행들만 본다 — 국소 밴드 보정은 전체 중앙값을 거의
    움직이지 않으므로, 판정은 보정이 실제로 닿은 행들에서 해야 한다.
    """
    it = rows if rows is not None else range(tl.shape[0])
    vals = []
    for y in it:
        t = tl[y] - tl[y].mean()
        x = wl[y] - wl[y].mean()
        st, sx = t.std(), x.std()
        if st < VAR_MIN or sx < VAR_MIN:
            continue
        vals.append(float((t * x).mean() / (st * sx)))
    return float(np.median(vals)) if len(vals) >= min_rows else None


def _warp_small(rgb, est):
    small = np.asarray(
        Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
        .resize((est["w_t"], est["h_t"]), Image.BILINEAR), np.float64)
    warped, _ = _warp_canvas(small, est["s"], est["gdy"], est["h_t"], est["w_t"],
                             fill=float(small.mean()))
    return warped


# ---------- 전체 흐름 ----------

def process_arrays(thumb_rgb, x_rgb, mcu_w, mcu_h):
    """추정→(밀림 반복)→색→self-check 판정까지의 전체 흐름 (배열 입력).

    반환 dict:
      status: corrected | identity | rollback | skip_small | skip_reg
      corrected: 보정된 RGB(float) — corrected일 때만
      shift_rows(누적)/shift_iters/max_units/color_rows/dmatch/reg 지표들
    """
    h_t, w_t = thumb_rgb.shape[0], thumb_rgb.shape[1]
    if x_rgb.shape[1] < w_t * SMALL_MAIN_RATIO:
        return dict(status="skip_small")

    tl = _luma(thumb_rgb)
    # 밀림을 반복 보정한다. 1회 apply_shift는 광범위 재동기(저해상 썸네일·다밴드)에서
    # 큰 밴드를 놓치므로, 재추정→적용을 수렴까지 반복하되 각 회차를 적용 행 한정
    # dmatch로 검증해 개선될 때만 채택한다(순환 roll 부작용·발산 방지).
    y = x_rgb
    first_est = None
    color_est = None
    first_touched = 0
    shift_rows = 0
    max_units = 0
    iters = 0
    for it in range(MAX_SHIFT_ITERS):
        est = estimate(thumb_rgb, y, mcu_w, mcu_h)
        if est.get("skip"):
            if it == 0:
                return dict(status="skip_reg", reason=est["skip"])
            break
        color_est = est                      # 밀림 수렴 후 색 정합에 쓸 최신 정합
        if it == 0:
            first_est = est
            first_touched = int(np.count_nonzero(est["units"]))
        touched = np.nonzero(est["units"])[0]
        if touched.size == 0:                # 남은 밀림 없음: 수렴
            break
        y2, row_units = apply_shift(y, est, mcu_w, mcu_h)
        m0 = zero_match(tl, _luma(_warp_small(y, est)), rows=touched, min_rows=5)
        m1 = zero_match(tl, _luma(_warp_small(y2, est)), rows=touched, min_rows=5)
        gain = (m1 - m0) if (m0 is not None and m1 is not None) else None
        if gain is None or gain < DMATCH_MIN:  # 개선 없음: 이 회차 버리고 정지
            break
        y = y2
        iters += 1
        shift_rows += int(np.count_nonzero(row_units))
        max_units = max(max_units, int(np.abs(est["units"]).max()))

    col, z, color_rows = apply_color(thumb_rgb, y, color_est, mcu_h)

    res = dict(reg_score=round(first_est["reg_score"], 3),
               s=round(first_est["s"], 3), dy=first_est["gdy"],
               framing=round(first_est["framing"], 2),
               shift_rows=shift_rows, shift_iters=iters, max_units=max_units,
               color_rows=color_rows)
    if col["std0"] is not None:
        res["band_std0"] = [round(float(v), 2) for v in col["std0"]]
    if col["res_std"] is not None:
        res["band_res"] = [round(float(v), 2) for v in col["res_std"]]

    # 리포트용 전체 dmatch(참고 지표)
    m0 = zero_match(tl, _luma(_warp_small(x_rgb, color_est)))
    m1 = zero_match(tl, _luma(_warp_small(z, color_est)))
    if m0 is not None and m1 is not None:
        res["dmatch"] = round(m1 - m0, 3)

    if shift_rows == 0 and color_rows == 0:
        # 첫 회차에 밀림이 감지됐으나 개선이 없어 전량 버린 경우는 rollback,
        # 애초에 밀림·색 근거가 없으면 identity.
        res["status"] = "rollback" if first_touched > 0 else "identity"
        return res
    res["status"] = "corrected"
    res["corrected"] = z
    return res


def process_file(orig_path, recovered_path):
    """파일 입력 편의 래퍼. (status, corrected|None, info)를 반환한다.

    MCU 격자와 썸네일은 카빙 원본에서, 픽셀은 복구본에서 읽는다 — 밀림의 물리
    단위는 원본 스트림의 MCU다.
    """
    orig = open(orig_path, "rb").read()
    thumb = extract_thumbnail(orig)
    if thumb is None:
        return "skip_no_thumb", None, {}
    ms = parse_mcu_size(orig)
    if ms is None:
        return "skip_no_sof", None, {}
    try:
        x_rgb = np.asarray(Image.open(recovered_path).convert("RGB"), np.float64)
    except Exception:
        return "skip_unreadable", None, {}
    res = process_arrays(np.asarray(thumb, np.float64), x_rgb, ms[0], ms[1])
    status = res.pop("status")
    corrected = res.pop("corrected", None)
    return status, corrected, res
