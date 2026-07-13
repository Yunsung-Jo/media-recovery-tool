"""비트 단위 제어가 가능한 baseline(SOF0) JPEG 디코더.

resync 복구(`carver.resync`)를 위해 표준 디코더와 달리:
 - 임의의 시작 비트위치 + DC 예측값에서 디코딩을 재개할 수 있고,
 - per-MCU 비트위치/DC예측을 기록하며,
 - 무효 Huffman 코드·계수 오버플로·비정상 비트레이트(디싱크)에서 정확히 멈춘다.

Huffman 핫루프는 numba(`@njit`)로 가속하고, IDCT/색변환은 numpy로 벡터화한다.
대상은 소비자 카메라 JPEG(3-component YCbCr, baseline)이다.
"""
from __future__ import annotations
import struct
import numpy as np
from numba import njit

# zigzag k번째 계수가 들어갈 natural(행우선) 인덱스
ZIGZAG = np.array([
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63], dtype=np.int64)

DISABLE = 1 << 30   # dc/ac/rate 경계 비활성화 값


# ---------------- 헤더 파싱 ----------------
class Header:
    """SOS 이전 마커에서 추출한 프레임 파라미터."""
    qt: dict          # id -> (64,) zigzag-order 양자화표
    huff: dict        # (cls,id) -> (counts[16], symbols)
    comps: list       # frame components: (id, hsamp, vsamp, qid)
    scan: list        # scan components: (id, td, ta)
    width: int
    height: int
    dri: int
    scan_start: int   # 엔트로피 데이터 시작 절대 오프셋


def parse_header(data: bytes) -> Header:
    h = Header()
    h.qt = {}
    h.huff = {}
    h.comps = []
    h.scan = []
    h.width = h.height = 0
    h.dri = 0
    h.scan_start = -1
    n = len(data)
    i = 2
    while i < n - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        m = data[i]
        i += 1
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            continue
        if m == 0xD9:
            break
        if i + 1 >= n:
            break
        seg_len = (data[i] << 8) | data[i + 1]
        p = i + 2
        end = min(i + seg_len, n)  # 손상/절단 세그먼트 길이가 버퍼를 넘어 인덱스 초과하는 것 방지
        if m == 0xDB:  # DQT
            q = p
            while q < end:
                pq_tq = data[q]; q += 1
                prec = pq_tq >> 4; tq = pq_tq & 0xF
                if prec == 0:
                    tbl = np.frombuffer(data[q:q + 64], dtype=np.uint8).astype(np.int32)
                    q += 64
                else:
                    tbl = np.array(struct.unpack('>64H', data[q:q + 128]), dtype=np.int32)
                    q += 128
                h.qt[tq] = tbl
        elif m == 0xC4:  # DHT
            q = p
            while q < end:
                tc_th = data[q]; q += 1
                cls = tc_th >> 4; tid = tc_th & 0xF
                counts = np.frombuffer(data[q:q + 16], dtype=np.uint8).astype(np.int32)
                q += 16
                total = int(counts.sum())
                symbols = np.frombuffer(data[q:q + total], dtype=np.uint8).astype(np.int32)
                q += total
                h.huff[(cls, tid)] = (counts, symbols)
        elif 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):  # SOF
            h.sof_marker = m
            h.height = (data[p + 1] << 8) | data[p + 2]
            h.width = (data[p + 3] << 8) | data[p + 4]
            ncomp = data[p + 5]
            for c in range(ncomp):
                cid = data[p + 6 + c * 3]
                s = data[p + 7 + c * 3]
                qid = data[p + 8 + c * 3]
                h.comps.append((cid, s >> 4, s & 0xF, qid))
        elif m == 0xDD:  # DRI
            h.dri = (data[p] << 8) | data[p + 1]
        elif m == 0xDA:  # SOS
            ns = data[p]
            for c in range(ns):
                cs = data[p + 1 + c * 2]
                tdta = data[p + 2 + c * 2]
                h.scan.append((cs, tdta >> 4, tdta & 0xF))
            h.scan_start = end
            return h
        i = end
    return h


def build_huff_lut(counts, symbols):
    """canonical Huffman을 16비트 룩업으로. huff_len[idx]=코드길이(0=무효), huff_sym[idx]=심볼."""
    huff_len = np.zeros(1 << 16, dtype=np.uint8)
    huff_sym = np.zeros(1 << 16, dtype=np.int32)
    code = 0
    k = 0
    for L in range(1, 17):
        for _ in range(int(counts[L - 1])):
            sym = int(symbols[k]); k += 1
            start = code << (16 - L)
            cnt = 1 << (16 - L)
            huff_len[start:start + cnt] = L
            huff_sym[start:start + cnt] = sym
            code += 1
        code <<= 1
    return huff_len, huff_sym


def destuff(data: bytes, start: int):
    """엔트로피를 클린 비트버퍼로 변환. FF00->FF(스터핑 제거), FF FF->FF(fill 제거),
    그 외 FF XX(가짜 RST/EOI/마커 = 손상)는 스터핑 플립으로 보고 FF만 채택하고 XX는 버린다.
    맨 끝의 진짜 EOI까지만 처리한다. 반환: (buf uint8, raw_of_clean: 클린바이트->raw오프셋)."""
    n = len(data)
    last_eoi = data.rfind(b"\xff\xd9")
    if last_eoi <= start:
        last_eoi = n
    out = bytearray()
    raw_of_clean = []
    i = start
    while i < last_eoi:
        b = data[i]
        if b == 0xFF:
            nb = data[i + 1] if i + 1 < n else 0
            if nb == 0x00:
                out.append(0xFF); raw_of_clean.append(i); i += 2; continue
            if nb == 0xFF:
                i += 1; continue
            out.append(0xFF); raw_of_clean.append(i); i += 2; continue
        out.append(b); raw_of_clean.append(i); i += 1
    return (np.frombuffer(bytes(out), dtype=np.uint8).copy(),
            np.array(raw_of_clean, dtype=np.int64))


# ---------------- numba 비트리더 ----------------
@njit(cache=True)
def _peek16(buf, nbits, bitpos):
    byte = bitpos >> 3
    off = bitpos & 7
    b0 = buf[byte] if byte < buf.size else 0
    b1 = buf[byte + 1] if byte + 1 < buf.size else 0
    b2 = buf[byte + 2] if byte + 2 < buf.size else 0
    v = (b0 << 16) | (b1 << 8) | b2
    return (v >> (8 - off)) & 0xFFFF


@njit(cache=True)
def _recv_extend(buf, nbits, bitpos, s):
    """s비트를 읽어 JPEG 부호확장한 값과 새 bitpos를 반환.
    s비트를 1개씩 돌지 않고 4바이트를 모아 한 번에 추출(off+s<=7+15<=22<32 보장)."""
    byte = bitpos >> 3
    off = bitpos & 7
    b0 = buf[byte] if byte < buf.size else 0
    b1 = buf[byte + 1] if byte + 1 < buf.size else 0
    b2 = buf[byte + 2] if byte + 2 < buf.size else 0
    b3 = buf[byte + 3] if byte + 3 < buf.size else 0
    acc = (b0 << 24) | (b1 << 16) | (b2 << 8) | b3
    val = (acc >> (32 - off - s)) & ((1 << s) - 1)
    bitpos += s
    if val < (1 << (s - 1)):
        val += (-(1 << s)) + 1
    return val, bitpos


@njit(cache=True)
def decode_range(buf, nbits, start_bit, dc0, dc1, dc2,
                 hl, hs, dc_idx, ac_idx, qmat,
                 hsamp, vsamp, hmax, vmax, mcus_x, mcus_y,
                 start_mcu, max_mcu,
                 cy, cb, cr, mcu_bit, zz, dc_rec,
                 dc_bound, ac_bound, rate_bound):
    """start_mcu/start_bit/DC예측에서 최대 max_mcu개 MCU 디코드.
    cy/cb/cr(블록그리드,8,8 dequant)·mcu_bit[m]·dc_rec[m]을 채운다.
    dc/ac_bound 초과 계수 또는 rate_bound 초과 비트레이트(디싱크)에서 정지.
    반환: (mcus_done, end_bit, err)  err: 0=완료 1=무효코드 2=버퍼끝 3=계수오버플로 4=비트레이트."""
    dc = np.empty(3, dtype=np.int64)
    dc[0] = dc0; dc[1] = dc1; dc[2] = dc2
    bitpos = start_bit
    coef = np.empty(64, dtype=np.int64)
    done = 0
    m = start_mcu
    total = mcus_x * mcus_y
    while done < max_mcu and m < total:
        mx = m % mcus_x
        my = m // mcus_x
        if m < mcu_bit.size:
            mcu_bit[m] = bitpos
            dc_rec[m, 0] = dc[0]
            dc_rec[m, 1] = dc[1]
            dc_rec[m, 2] = dc[2]
        if done >= 40 and (bitpos - start_bit) > done * rate_bound:
            return done, bitpos, 4   # frontier 비트 기록 후 정지 → mcu_bit[m] 유효
        for comp in range(3):
            hh = hsamp[comp]; vv = vsamp[comp]
            di = dc_idx[comp]; ai = ac_idx[comp]
            for by in range(vv):
                for bx in range(hh):
                    idx = _peek16(buf, nbits, bitpos)
                    L = hl[di, idx]
                    if L == 0:
                        return done, bitpos, 1
                    s = hs[di, idx]
                    bitpos += L
                    if bitpos > nbits:
                        return done, bitpos, 2
                    diff = 0
                    if s > 0:
                        diff, bitpos = _recv_extend(buf, nbits, bitpos, s)
                    dc[comp] += diff
                    if dc[comp] * qmat[comp, 0] > dc_bound or dc[comp] * qmat[comp, 0] < -dc_bound:
                        return done, bitpos, 3
                    for t in range(64):
                        coef[t] = 0
                    coef[0] = dc[comp]
                    k = 1
                    while k < 64:
                        idx = _peek16(buf, nbits, bitpos)
                        L = hl[ai, idx]
                        if L == 0:
                            return done, bitpos, 1
                        rs = hs[ai, idx]
                        bitpos += L
                        r = rs >> 4
                        ss = rs & 15
                        if ss == 0:
                            if r == 15:
                                k += 16
                                continue
                            else:
                                break  # EOB
                        k += r
                        if k > 63:
                            break
                        val, bitpos = _recv_extend(buf, nbits, bitpos, ss)
                        if val * qmat[comp, zz[k]] > ac_bound or val * qmat[comp, zz[k]] < -ac_bound:
                            return done, bitpos, 3
                        coef[zz[k]] = val
                        k += 1
                    cbx = mx * hh + bx
                    cby = my * vv + by
                    if comp == 0:
                        for yy in range(8):
                            for xx in range(8):
                                cy[cby, cbx, yy, xx] = coef[yy * 8 + xx] * qmat[0, yy * 8 + xx]
                    elif comp == 1:
                        for yy in range(8):
                            for xx in range(8):
                                cb[cby, cbx, yy, xx] = coef[yy * 8 + xx] * qmat[1, yy * 8 + xx]
                    else:
                        for yy in range(8):
                            for xx in range(8):
                                cr[cby, cbx, yy, xx] = coef[yy * 8 + xx] * qmat[2, yy * 8 + xx]
        done += 1
        m += 1
    return done, bitpos, 0


@njit(cache=True)
def decode_probe(buf, nbits, start_bit, dc0, dc1, dc2,
                 hl, hs, dc_idx, ac_idx, qmat,
                 hsamp, vsamp, mcus_x, mcus_y, start_mcu, maxW, zz,
                 dc_bound, ac_bound, rate_bound):
    """저장 없이 plausibility만 추적하는 경량 디코드(오라클 채점용).
    반환: (clean_mcus, end_bit, reason)  reason: 0=maxW 1=무효코드 2=계수오버플로 3=버퍼끝 4=비트레이트."""
    dc = np.empty(3, dtype=np.int64)
    dc[0] = dc0; dc[1] = dc1; dc[2] = dc2
    bitpos = start_bit
    done = 0
    m = start_mcu
    total = mcus_x * mcus_y
    while done < maxW and m < total:
        for comp in range(3):
            hh = hsamp[comp]; vv = vsamp[comp]
            di = dc_idx[comp]; ai = ac_idx[comp]
            qd = qmat[comp, 0]
            for _by in range(vv):
                for _bx in range(hh):
                    idx = _peek16(buf, nbits, bitpos)
                    L = hl[di, idx]
                    if L == 0:
                        return done, bitpos, 1
                    s = hs[di, idx]
                    bitpos += L
                    if bitpos > nbits:
                        return done, bitpos, 3
                    if s > 0:
                        diff, bitpos = _recv_extend(buf, nbits, bitpos, s)
                        dc[comp] += diff
                    if dc[comp] * qd > dc_bound or dc[comp] * qd < -dc_bound:
                        return done, bitpos, 2
                    k = 1
                    while k < 64:
                        idx = _peek16(buf, nbits, bitpos)
                        L = hl[ai, idx]
                        if L == 0:
                            return done, bitpos, 1
                        rs = hs[ai, idx]
                        bitpos += L
                        r = rs >> 4
                        ss = rs & 15
                        if ss == 0:
                            if r == 15:
                                k += 16
                                continue
                            else:
                                break
                        k += r
                        if k > 63:
                            break
                        val, bitpos = _recv_extend(buf, nbits, bitpos, ss)
                        dq = val * qmat[comp, zz[k]]
                        if dq > ac_bound or dq < -ac_bound:
                            return done, bitpos, 2
                        k += 1
        done += 1
        m += 1
        if done >= 40 and (bitpos - start_bit) > done * rate_bound:
            return done, bitpos, 4
    return done, bitpos, 0


# ---------------- IDCT + 색변환 ----------------
def _idct_mat():
    M = np.zeros((8, 8))
    for u in range(8):
        cu = (1 / np.sqrt(2)) if u == 0 else 1.0
        for x in range(8):
            M[u, x] = 0.5 * cu * np.cos((2 * x + 1) * u * np.pi / 16)
    return M


_M = _idct_mat()


def idct_blocks(coef):
    """coef (nbh,nbw,8,8) dequant -> spatial plane (nbh*8, nbw*8) (레벨시프트 +128 포함)."""
    tmp = np.einsum('ux,abuv->abxv', _M, coef, optimize=True)
    sp = np.einsum('abxv,vy->abxy', tmp, _M, optimize=True)
    sp = sp + 128.0
    nbh, nbw = coef.shape[:2]
    return sp.transpose(0, 2, 1, 3).reshape(nbh * 8, nbw * 8)


class Decoder:
    """baseline 3-component JPEG 디코더. 헤더 파싱 + 룩업/그리드 준비 후
    decode_full()(전체) 또는 carver.resync(세그먼트 단위)에서 사용한다."""

    def __init__(self, data: bytes):
        self.data = data
        self.h = parse_header(data)
        H = self.h
        if len(H.comps) != 3:
            raise ValueError(f"3-component baseline JPEG만 지원 (comps={len(H.comps)})")
        if H.width == 0 or H.height == 0 or H.scan_start < 0:
            raise ValueError(f"비정상 JPEG (w={H.width} h={H.height} sos={H.scan_start})")
        self.hl = np.zeros((4, 1 << 16), dtype=np.uint8)
        self.hs = np.zeros((4, 1 << 16), dtype=np.int32)
        for (cls, tid), (counts, syms) in H.huff.items():
            l, s = build_huff_lut(counts, syms)
            self.hl[cls * 2 + tid] = l
            self.hs[cls * 2 + tid] = s
        self.hmax = max(c[1] for c in H.comps)
        self.vmax = max(c[2] for c in H.comps)
        self.hsamp = np.array([c[1] for c in H.comps], dtype=np.int64)
        self.vsamp = np.array([c[2] for c in H.comps], dtype=np.int64)
        if (self.hsamp.min() < 1 or self.vsamp.min() < 1
                or self.hsamp.max() > 4 or self.vsamp.max() > 4):
            raise ValueError(f"비정상 샘플링 계수 (h={self.hsamp.tolist()} v={self.vsamp.tolist()})")
        if len({c[0] for c in H.comps}) != 3:
            raise ValueError("컴포넌트 ID 중복(손상된 SOF)")
        scan_map = {cs: (td, ta) for cs, td, ta in H.scan}
        self.dc_idx = np.zeros(3, dtype=np.int64)
        self.ac_idx = np.zeros(3, dtype=np.int64)
        self.qmat = np.zeros((3, 64), dtype=np.int64)
        for ci, (cid, _hs, _vs, qid) in enumerate(H.comps):
            td, ta = scan_map[cid]
            if (0, td) not in H.huff or (1, ta) not in H.huff:
                # DHT 소실/손상: 미검증 시 all-zero LUT로 진행돼 MCU 0에서 무효 코드
                # → 전량 회색이 RECOVERED로 오분류될 수 있다.
                raise ValueError(f"허프만 테이블 누락 (DHT 소실/손상: comp {cid} DC{td} AC{ta})")
            self.dc_idx[ci] = td          # class 0 (DC)
            self.ac_idx[ci] = 2 + ta      # class 1 (AC) => index 2+ta
            qz = H.qt[qid]
            qn = np.zeros(64, dtype=np.int64)
            for k in range(64):
                qn[ZIGZAG[k]] = qz[k]
            self.qmat[ci] = qn
        self.mcus_x = (H.width + 8 * self.hmax - 1) // (8 * self.hmax)
        self.mcus_y = (H.height + 8 * self.vmax - 1) // (8 * self.vmax)
        self.cy = np.zeros((self.mcus_y * self.vsamp[0], self.mcus_x * self.hsamp[0], 8, 8))
        self.cb = np.zeros((self.mcus_y * self.vsamp[1], self.mcus_x * self.hsamp[1], 8, 8))
        self.cr = np.zeros((self.mcus_y * self.vsamp[2], self.mcus_x * self.hsamp[2], 8, 8))
        buf, raw_of_clean = destuff(data, H.scan_start)
        self.buf = buf
        self.nbits = buf.size * 8
        self.raw_of_clean = raw_of_clean

    def decode_full(self):
        """엔트로피 전체를 순차 디코드(경계 비활성). mcu_bit/dc_rec 기록."""
        total = self.mcus_x * self.mcus_y
        mcu_bit = np.zeros(total + 1, dtype=np.int64)
        dc_rec = np.zeros((total + 1, 3), dtype=np.int64)
        done, end_bit, err = decode_range(
            self.buf, self.nbits, 0, 0, 0, 0,
            self.hl, self.hs, self.dc_idx, self.ac_idx, self.qmat,
            self.hsamp, self.vsamp, self.hmax, self.vmax,
            self.mcus_x, self.mcus_y, 0, total,
            self.cy, self.cb, self.cr, mcu_bit, ZIGZAG, dc_rec,
            DISABLE, DISABLE, DISABLE)
        self.mcu_bit = mcu_bit
        self.dc_rec = dc_rec
        return done, end_bit, err

    def to_rgb(self):
        """현재 계수 그리드(cy/cb/cr)를 RGB uint8 이미지로."""
        Y = idct_blocks(self.cy)
        Cb = idct_blocks(self.cb)
        Cr = idct_blocks(self.cr)
        Cb = np.repeat(np.repeat(Cb, self.vmax // self.vsamp[1], 0), self.hmax // self.hsamp[1], 1)
        Cr = np.repeat(np.repeat(Cr, self.vmax // self.vsamp[2], 0), self.hmax // self.hsamp[2], 1)
        H = min(Y.shape[0], Cb.shape[0], self.h.height)
        W = min(Y.shape[1], Cb.shape[1], self.h.width)
        Y = Y[:H, :W]; Cb = Cb[:H, :W]; Cr = Cr[:H, :W]
        R = Y + 1.402 * (Cr - 128)
        G = Y - 0.344136 * (Cb - 128) - 0.714136 * (Cr - 128)
        B = Y + 1.772 * (Cb - 128)
        return np.clip(np.stack([R, G, B], -1), 0, 255).astype(np.uint8)
