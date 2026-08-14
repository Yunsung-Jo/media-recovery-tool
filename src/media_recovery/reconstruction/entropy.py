"""Baseline JPEG entropy trajectory, byte edit와 bit resync 복구 loop."""
from __future__ import annotations

import time

import numpy as np

from media_recovery.formats.jpeg import baseline_decoder as jd
from media_recovery.reconstruction import placement

DC_BOUND, AC_BOUND = 1400, 6000
_ZZ = jd.ZIGZAG


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
        rgb, shift_stats = placement._correct_segment_shifts(
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
