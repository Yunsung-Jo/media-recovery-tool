"""손상 JPEG 헤더 복구 pass — DHT 이식·DQT 교정·SOF/SOS 재구성 (백로그 W3).

`Decoder` 구성이 실패하거나 개구부 probe가 바닥에 못 미치는 파일에 대해,
관용 스캔(마커·길이 오염 허용)으로 헤더 후보를 모으고 템플릿·도너 테이블로 교정한
변형들을 probe 대결시켜, 구조 게이트(소비율·엔진 결과)를 통과한 것만 채택한다.
어느 변형도 통과하지 못하면 None — 가짜 채움 금지(SKIP 유지).

판별의 핵심은 렌더 통계가 아니라 구조 신호다(경계+rate probe, 스터핑 규칙이 보장하는
FFD9 앵커 기반 소비율, own-엄격-우월). 근거·기각 실험은
docs/investigations/2026-07-04-w3-header-recovery.md 참조.
"""
from __future__ import annotations

import numpy as np

from . import jpegdecode as jd

HDR_LIMIT = 1 << 20          # 헤더 후보 스캔 범위 상한
FLOOR_CAP = 30               # probe 수락 바닥 상한 (min(30, (총MCU+1)//2))
FIT_CONSUME_LO = 0.25        # fit==1 소비율 하한 (치수 과소 쓰레기 완주 차단)
FIT_CONSUME_HI = 1.10        # fit==1 소비율 상한 (첫 FFD9 관통 완주 = 위해석 차단)
ENGINE_UNDEC_MAX = 0.95      # 엔진 결과가 사실상 전량 회색이면 기각(다음 그룹 강하)
COMPLETE_UNDEC = 0.05        # "완결 주장" 판정선 — 이 미만이면 소비율 재확인

# 코퍼스 상위 해상도(W,H) — 치수 후보의 순위 힌트(기각 근거로는 쓰지 않는다)
CORPUS_RES = [(2816, 2112), (324, 243), (2592, 1944), (96, 72), (320, 240),
              (50, 50), (240, 320), (318, 425), (337, 337), (320, 180),
              (2448, 1836), (306, 230), (345, 459), (320, 239), (1377, 1836),
              (96, 96), (72, 96), (480, 320), (2048, 1536), (160, 120)]

# 도너 허프만 테이블 — ITU T.81 Annex K 전형(코퍼스 다수파 685/697과 일치, 지문 050df0dc).
# (cls, id) -> (counts[16], symbols)
_AC_LUM_SYMS = [
    1, 2, 3, 0, 4, 17, 5, 18, 33, 49, 65, 6, 19, 81, 97, 7, 34, 113, 20, 50,
    129, 145, 161, 8, 35, 66, 177, 193, 21, 82, 209, 240, 36, 51, 98, 114,
    130, 9, 10, 22, 23, 24, 25, 26, 37, 38, 39, 40, 41, 42, 52, 53, 54, 55,
    56, 57, 58, 67, 68, 69, 70, 71, 72, 73, 74, 83, 84, 85, 86, 87, 88, 89,
    90, 99, 100, 101, 102, 103, 104, 105, 106, 115, 116, 117, 118, 119, 120,
    121, 122, 131, 132, 133, 134, 135, 136, 137, 138, 146, 147, 148, 149,
    150, 151, 152, 153, 154, 162, 163, 164, 165, 166, 167, 168, 169, 170,
    178, 179, 180, 181, 182, 183, 184, 185, 186, 194, 195, 196, 197, 198,
    199, 200, 201, 202, 210, 211, 212, 213, 214, 215, 216, 217, 218, 225,
    226, 227, 228, 229, 230, 231, 232, 233, 234, 241, 242, 243, 244, 245,
    246, 247, 248, 249, 250]
_AC_CHR_SYMS = [
    0, 1, 2, 3, 17, 4, 5, 33, 49, 6, 18, 65, 81, 7, 97, 113, 19, 34, 50, 129,
    8, 20, 66, 145, 161, 177, 193, 9, 35, 51, 82, 240, 21, 98, 114, 209, 10,
    22, 36, 52, 225, 37, 241, 23, 24, 25, 26, 38, 39, 40, 41, 42, 53, 54, 55,
    56, 57, 58, 67, 68, 69, 70, 71, 72, 73, 74, 83, 84, 85, 86, 87, 88, 89,
    90, 99, 100, 101, 102, 103, 104, 105, 106, 115, 116, 117, 118, 119, 120,
    121, 122, 130, 131, 132, 133, 134, 135, 136, 137, 138, 146, 147, 148,
    149, 150, 151, 152, 153, 154, 162, 163, 164, 165, 166, 167, 168, 169,
    170, 178, 179, 180, 181, 182, 183, 184, 185, 186, 194, 195, 196, 197,
    198, 199, 200, 201, 202, 210, 211, 212, 213, 214, 215, 216, 217, 218,
    226, 227, 228, 229, 230, 231, 232, 233, 234, 242, 243, 244, 245, 246,
    247, 248, 249, 250]


def _tbl(counts, syms):
    return (np.array(counts, np.int32), np.array(syms, np.int32))


DONOR_HUFF = {
    (0, 0): _tbl([0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0], list(range(12))),
    (0, 1): _tbl([0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0], list(range(12))),
    (1, 0): _tbl([0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 125], _AC_LUM_SYMS),
    (1, 1): _tbl([0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 119], _AC_CHR_SYMS),
}


def floor_of(total: int) -> int:
    """probe 수락 바닥 — 소형은 총 MCU의 절반, 상한 30."""
    return min(FLOOR_CAP, (total + 1) // 2)


# ---------------- 관용 후보 스캔 ----------------

_SOS_BODY = bytes([0x00, 0x0C, 0x03, 0x01, 0x00, 0x02, 0x11, 0x03, 0x11, 0x00, 0x3F, 0x00])


def sos_candidates(data: bytes):
    """SOS 후보 — 12바이트 정형 body를 채점(마커 일치 +3, ≥9/15 채택).
    scan_start는 len 필드 오염과 무관하게 템플릿 고정(pos+14)."""
    lim = min(len(data), HDR_LIMIT)
    out = []
    for p in range(2, lim - 14):
        if data[p] != 0xFF:
            continue
        body = data[p + 2:p + 14]
        score = sum(1 for a, b in zip(body, _SOS_BODY) if a == b)
        if data[p + 1] == 0xDA:
            score += 3
        if score >= 9:
            out.append((p, p + 14, score))
    out.sort(key=lambda t: -t[2])
    return out[:4]


def sof_candidates(data: bytes):
    """SOF 후보 — len+precision 패턴 '00 11 08'(마커 바이트 오염 허용)."""
    lim = min(len(data), HDR_LIMIT)
    out, p = [], 0
    while len(out) < 6:
        p = data.find(b'\x00\x11\x08', p + 1, lim)
        if p < 0 or p + 7 > len(data):
            break
        H = (data[p + 3] << 8) | data[p + 4]
        W = (data[p + 5] << 8) | data[p + 6]
        out.append((p - 2, H, W))
        p += 3
    return out


def smooth_repair(t) -> np.ndarray:
    """양자화표 이상치(0, 이웃 중앙값 3배 초과, (중앙값≥3에서) 1/3 미만)를 이웃 중앙값으로."""
    t = np.asarray(t, np.int32).copy()
    for i in range(64):
        lo, hi = max(0, i - 3), min(64, i + 4)
        neigh = [int(t[j]) for j in range(lo, hi) if j != i and t[j] > 0]
        if not neigh:
            continue
        med = int(np.median(neigh))
        if t[i] == 0 or t[i] > 3 * med or (med >= 3 and t[i] * 3 < med):
            t[i] = med
    return t


def dqt_candidates(data: bytes, h) -> list:
    """DQT 후보 세트({0: Y, 1: C}) — 자체 파싱 + 관용 세그 재파싱(pq 니블 오염 무시)
    + len 패턴 재접속 + 각 세트의 스무딩판. 최대 4종."""
    sets = []
    if 0 in h.qt and 1 in h.qt and len(h.qt[0]) == 64 and len(h.qt[1]) == 64:
        sets.append({0: np.asarray(h.qt[0], np.int32), 1: np.asarray(h.qt[1], np.int32)})
    lim = min(len(data), HDR_LIMIT)
    # FFDB 세그를 pq 니블 무시하고 8bit 2테이블 순차 재파싱 (Pq 비트 오염 대응)
    p = 0
    while True:
        p = data.find(b'\xff\xdb', p + 1, lim)
        if p < 0:
            break
        seg_len = (data[p + 2] << 8) | data[p + 3]
        if seg_len < 67:
            continue
        q = p + 4
        tabs = []
        while q + 65 <= p + 2 + seg_len and len(tabs) < 2:
            t = np.frombuffer(data[q + 1:q + 65], np.uint8).astype(np.int32)
            if t.size == 64 and t[0] > 0:
                tabs.append(t)
            q += 65
        if len(tabs) == 2:
            sets.append({0: tabs[0], 1: tabs[1]})
    # 마커 소실 대응: len 패턴('00 84' 2테이블 / '00 43' 단일 인접쌍) 재접속.
    # 패턴이 파일 끝 근처에 매치되면 슬라이스가 64B 미만일 수 있어 크기를 확인한다.
    p = 0
    while True:
        p = data.find(b'\x00\x84', p + 1, lim)
        if p < 0 or p + 132 > len(data):
            break
        if data[p + 2] >> 4 == 0 and data[p + 67] >> 4 == 0:
            t0 = np.frombuffer(data[p + 3:p + 67], np.uint8).astype(np.int32)
            t1 = np.frombuffer(data[p + 68:p + 132], np.uint8).astype(np.int32)
            if t0.size == 64 and t1.size == 64 and t0[0] > 0 and t1[0] > 0:
                sets.append({0: t0, 1: t1})
    singles, p = [], 0
    while len(singles) < 8:
        p = data.find(b'\x00\x43', p + 1, lim)
        if p < 0 or p + 67 > len(data):
            break
        pq = data[p + 2]
        if pq >> 4 == 0 and (pq & 0xF) <= 1:
            t = np.frombuffer(data[p + 3:p + 67], np.uint8).astype(np.int32)
            if t.size == 64 and t[0] > 0:
                singles.append((pq & 0xF, t))
    for i in range(len(singles) - 1):
        if singles[i][0] == 0 and singles[i + 1][0] == 1:
            sets.append({0: singles[i][1], 1: singles[i + 1][1]})
    for s in list(sets):
        sm = {0: smooth_repair(s[0]), 1: smooth_repair(s[1])}
        if not (np.array_equal(sm[0], s[0]) and np.array_equal(sm[1], s[1])):
            sets.append(sm)
    uniq, seen = [], set()
    for s in sets:
        k = (tuple(s[0].tolist()), tuple(s[1].tolist()))
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq[:4]


def dht_candidates(h) -> list:
    """DHT 후보 — 자체 4클래스 완비 → 자체+도너 채움 → 도너. 중복 제거는 counts+symbols
    (counts만 비교하면 심볼 바이트만 손상된 DHT가 도너를 삼킨다). 최대 2종."""
    valid = {k: v for k, v in h.huff.items() if k[0] <= 1 and k[1] <= 1}
    out = []
    if len(valid) == 4:
        out.append(valid)
    if 0 < len(valid) < 4:
        filled = dict(DONOR_HUFF)
        filled.update(valid)
        out.append(filled)
    out.append(DONOR_HUFF)
    uniq, seen = [], set()
    for hu in out:
        k = tuple(sorted((kk, tuple(int(x) for x in vv[0]), tuple(int(x) for x in vv[1]))
                         for kk, vv in hu.items()))
        if k not in seen:
            seen.add(k)
            uniq.append(hu)
    return uniq[:2]


def dims_candidates(H: int, W: int) -> list:
    """치수 후보 — 파싱값(그럴듯하면) + 코퍼스 same-W 해상도. 코퍼스 prior는 순위 힌트일 뿐
    기각 근거가 아니다(2816×2240 실측 — EOI 앵커로 비코퍼스 해상도가 진짜였던 사례)."""
    out = []
    if 8 <= H <= 4224 and 8 <= W <= 4224:
        out.append((H, W))
        if (W, H) not in CORPUS_RES:
            for cw, ch in CORPUS_RES:
                if cw == W and (ch, cw) not in out:
                    out.append((ch, cw))
    else:
        out.extend([(ch, cw) for cw, ch in CORPUS_RES[:3]])
    return out[:3]


# ---------------- 디코더 수동 구성·채점 ----------------

def build_decoder(data: bytes, huff, qt, comps, scan, width, height, scan_start) -> jd.Decoder:
    """오버라이드된 헤더 필드로 Decoder를 수동 구성(파싱 우회). 실패 시 ValueError."""
    dec = jd.Decoder.__new__(jd.Decoder)
    h = jd.Header()
    h.qt = qt
    h.huff = huff
    h.comps = comps
    h.scan = scan
    h.width, h.height = width, height
    h.dri = 0
    h.scan_start = scan_start
    dec.data = data
    dec.h = h
    if len(comps) != 3 or width <= 0 or height <= 0 or scan_start <= 0:
        raise ValueError('구조 불완전')
    if scan_start >= len(data):
        raise ValueError('scan_start 범위 밖')
    dec.hl = np.zeros((4, 1 << 16), np.uint8)
    dec.hs = np.zeros((4, 1 << 16), np.int32)
    for (cls, tid), (counts, syms) in huff.items():
        if cls > 1 or tid > 1:
            continue
        l, s = jd.build_huff_lut(counts, syms)
        dec.hl[cls * 2 + tid] = l
        dec.hs[cls * 2 + tid] = s
    dec.hmax = max(c[1] for c in comps)
    dec.vmax = max(c[2] for c in comps)
    dec.hsamp = np.array([c[1] for c in comps], np.int64)
    dec.vsamp = np.array([c[2] for c in comps], np.int64)
    if dec.hsamp.min() < 1 or dec.vsamp.min() < 1 or dec.hmax > 4 or dec.vmax > 4:
        raise ValueError('비정상 샘플링')
    scan_map = {cs: (td, ta) for cs, td, ta in scan}
    dec.dc_idx = np.zeros(3, np.int64)
    dec.ac_idx = np.zeros(3, np.int64)
    dec.qmat = np.zeros((3, 64), np.int64)
    for ci, (cid, _hs, _vs, qid) in enumerate(comps):
        td, ta = scan_map[cid]
        if (0, td) not in huff or (1, ta) not in huff or qid not in qt:
            raise ValueError('테이블 참조 불가')
        dec.dc_idx[ci] = td
        dec.ac_idx[ci] = 2 + ta
        qn = np.zeros(64, np.int64)
        for k in range(64):
            qn[jd.ZIGZAG[k]] = qt[qid][k]
        dec.qmat[ci] = qn
    dec.mcus_x = (width + 8 * dec.hmax - 1) // (8 * dec.hmax)
    dec.mcus_y = (height + 8 * dec.vmax - 1) // (8 * dec.vmax)
    dec.cy = np.zeros((dec.mcus_y * dec.vsamp[0], dec.mcus_x * dec.hsamp[0], 8, 8))
    dec.cb = np.zeros((dec.mcus_y * dec.vsamp[1], dec.mcus_x * dec.hsamp[1], 8, 8))
    dec.cr = np.zeros((dec.mcus_y * dec.vsamp[2], dec.mcus_x * dec.hsamp[2], 8, 8))
    buf, roc = jd.destuff(data, scan_start)
    if buf.size == 0:
        raise ValueError('빈 엔트로피')
    dec.buf = buf
    dec.nbits = buf.size * 8
    dec.raw_of_clean = roc
    return dec


def opening_probe(dec: jd.Decoder) -> int:
    """비트 0·MCU 0·DC 0에서 경계+rate 켠 clean run — 엔진과 동일 잣대."""
    from . import resync as rs   # 지연 임포트(순환 회피)
    total = dec.mcus_x * dec.mcus_y
    rate = max(350, int(dec.nbits / total * 4))
    W = int(min(900, total))
    return rs._probe(dec, dec.buf, 0, 0, np.zeros(3, np.int64), W, rate)


def _consumed_fraction(data: bytes, dec: jd.Decoder, end_bit: int) -> float:
    """소비율 — 분모는 scan_start→그 뒤 첫 FFD9(없으면 EOF). 스터핑 규칙상 진짜 스트림
    내부에 FFD9가 없으므로 완주 주장의 소비율은 [FIT_CONSUME_LO, FIT_CONSUME_HI]여야 한다."""
    ss = dec.h.scan_start
    eoi = data.find(b'\xff\xd9', ss)
    denom = (eoi if eoi > 0 else len(data)) - ss
    if denom <= 0:
        return 0.0
    cb = min(end_bit // 8, dec.raw_of_clean.size - 1)
    return (int(dec.raw_of_clean[cb]) - ss) / denom


def _fix_tags(h, v) -> str:
    """자체 파스 대비 어떤 세그먼트가 교체됐는지 — report의 header_fix 값."""
    tags = []
    own_huff = {k: (tuple(int(x) for x in c), tuple(int(x) for x in s))
                for k, (c, s) in h.huff.items()}
    var_huff = {k: (tuple(int(x) for x in c), tuple(int(x) for x in s))
                for k, (c, s) in v['huff'].items()}
    if own_huff != var_huff:
        tags.append('dht')
    own_qt = {k: tuple(int(x) for x in t) for k, t in h.qt.items()} if h.qt else {}
    var_qt = {k: tuple(int(x) for x in t) for k, t in v['qt'].items()}
    if own_qt != var_qt:
        tags.append('dqt')
    if (h.width, h.height, h.comps) != (v['width'], v['height'], v['comps']):
        tags.append('sof')
    if h.scan != v['scan'] or h.scan_start != v['scan_start']:
        tags.append('sos')
    return '+'.join(tags)


def reconstruct(data: bytes, recover_fn):
    """헤더 복구 대결 — 채택 시 (dec, fix_tags, rgb, stats, segments, gray_plain, undec_plain)
    반환, 전 후보 기각 시 None. recover_fn(dec) -> (rgb, stats, segments) — 엔진은 게이트 판정을
    겸해 여기서 1회만 실행되고 결과가 그대로 산출물이 된다(중복 실행 방지)."""
    from . import resync as rs   # 지연 임포트(순환 회피)
    h = jd.parse_header(data)
    soss = sos_candidates(data)
    sofs = sof_candidates(data)
    dqts = dqt_candidates(data, h)
    dhts = dht_candidates(h)
    variants = []
    own_ok = (len(h.comps) == 3 and h.width > 0 and h.height > 0 and h.scan_start > 0)
    if own_ok:
        # own-first: 자체 구조(치수·샘플링·SOS 위치)를 두고 테이블 교체만 우선 시도 —
        # 교차 변형의 캔버스 오채택 방지
        for qt in dqts:
            for hu in dhts:
                variants.append(dict(px=(1 << 60) + h.width * h.height, huff=hu, qt=qt,
                                     comps=h.comps, scan=h.scan, width=h.width,
                                     height=h.height, scan_start=h.scan_start))
    for spos, sstart, _sc in soss:
        for fpos, H, W in sofs:
            if fpos >= spos:
                continue
            for (HH, WW) in dims_candidates(H, W):
                for samp in ((2, 2), (2, 1)):
                    for qt in dqts:
                        for hu in dhts:
                            variants.append(dict(
                                px=WW * HH, huff=hu, qt=qt,
                                comps=[(1, samp[0], samp[1], 0), (2, 1, 1, 1), (3, 1, 1, 1)],
                                scan=[(1, 0, 0), (2, 1, 1), (3, 1, 1)],
                                width=WW, height=HH, scan_start=sstart))
    groups: dict = {}
    for v in variants:
        groups.setdefault(v['px'], []).append(v)
    for px in sorted(groups, reverse=True):
        scored = []
        for v in groups[px][:48]:
            try:
                dec = build_decoder(data, v['huff'], v['qt'], v['comps'], v['scan'],
                                    v['width'], v['height'], v['scan_start'])
            except Exception:
                continue
            run = opening_probe(dec)
            if run >= floor_of(dec.mcus_x * dec.mcus_y):
                scored.append((run, v, dec))
        scored.sort(key=lambda t: -t[0])
        # run tier별 — 동률군은 plain (fit, undec)로 세부 순위(잘못된 DQT의 색 왜곡 방지),
        # 상위 tier가 전부 기각되면 하위 tier로 진행(순위 컷은 순서일 뿐 자격이 아니다)
        ranked = []
        i = 0
        while i < len(scored):
            tier_run = scored[i][0]
            tier = []
            while i < len(scored) and scored[i][0] == tier_run:
                run, v, dec = scored[i]
                done, eb, _err = dec.decode_full()
                u_plain = rs.undecoded_fraction(dec.to_rgb())
                tier.append((run, int(done == dec.mcus_x * dec.mcus_y), -u_plain,
                             v, dec, done, eb))
                i += 1
            tier.sort(key=lambda t: (t[1], t[2]), reverse=True)
            ranked.extend(tier)
        for run, fit, nup, v, dec, done, eb in ranked:
            total = dec.mcus_x * dec.mcus_y
            if fit:
                cf = _consumed_fraction(data, dec, eb)
                if cf < FIT_CONSUME_LO or cf > FIT_CONSUME_HI:
                    continue
            dec2 = build_decoder(data, v['huff'], v['qt'], v['comps'], v['scan'],
                                 v['width'], v['height'], v['scan_start'])
            rgb, stats, segs = recover_fn(dec2)
            u = rs.undecoded_fraction(rgb)
            if u >= ENGINE_UNDEC_MAX:
                continue
            if u < COMPLETE_UNDEC:
                cons_bit = max(int(eb), max((int(s[1]) for s in segs), default=0))
                if _consumed_fraction(data, dec, cons_bit) < FIT_CONSUME_LO:
                    continue
            gray_plain = rs.gray_fraction(dec.to_rgb())
            return dec2, _fix_tags(h, v), rgb, stats, segs, gray_plain, -nup
    return None
