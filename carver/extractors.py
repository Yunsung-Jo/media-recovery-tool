import struct
import mmap
from typing import Union

_Data = Union[bytes, bytearray, mmap.mmap]

JPEG_MAX_FALLBACK_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_AVI_SIZE_DEFAULT = 500 * 1024 * 1024   # 500 MB

# 길이 필드 없는 마커: TEM(01), RST0-RST7(D0-D7)
# SOI(D8), EOI(D9)는 루프에서 별도 처리
_MARKER_NO_LENGTH = frozenset([0x01] + list(range(0xD0, 0xD8)))

# EOI 검증: 손상 스트림에서 stuffing이 깨져 생긴 가짜 FF D9를 진짜 EOI로
# 오인하지 않기 위한 파라미터. EOI 직후 윈도우의 "FF 다음 00/RST" 비율이
# 임계 이상이면 엔트로피 연속(=가짜 EOI)으로 보고 다음 후보를 찾는다.
_EOI_PROBE_WINDOW = 4096
_EOI_STUFF_THRESHOLD = 0.3


def _stuffing_ratio(seg: bytes) -> float:
    """seg에서 FF 다음 바이트가 stuffing(00) 또는 RST(D0–D7)인 비율.
    JPEG 엔트로피 스트림이면 1에 가깝고, 헤더/패딩/타 파일이면 낮다."""
    ff = stuff = 0
    for i in range(len(seg) - 1):
        if seg[i] == 0xFF:
            ff += 1
            nb = seg[i + 1]
            if nb == 0x00 or 0xD0 <= nb <= 0xD7:
                stuff += 1
    return stuff / ff if ff else 0.0


def _is_genuine_eoi(data: _Data, eoi_end: int, upper: int) -> bool:
    """EOI 직후가 엔트로피 연속이 아니면 진짜 EOI로 판단한다.
    검사할 데이터가 부족하면(상한·파일끝 근처) 진짜로 간주한다."""
    seg = data[eoi_end:min(eoi_end + _EOI_PROBE_WINDOW, upper)]
    if len(seg) < 128:
        return True
    return _stuffing_ratio(seg) < _EOI_STUFF_THRESHOLD


def _next_header(data: _Data, start: int, size: int) -> int:
    """start 이후 첫 '진짜 JPEG 헤더'(FF D8 FF E0–EF = SOI+APPn) 오프셋. 없으면 size.
    엔트로피 시작 이후를 탐색하므로 헤더 내 EXIF 썸네일이 아닌 실제 다음 파일
    경계를 찾는다(엔트로피 중 우연한 FF D8 FF E0–EF 4바이트 매칭은 매우 드묾)."""
    p = start
    while True:
        i = data.find(b'\xff\xd8\xff', p)
        if i < 0 or i >= size:
            return size
        if i + 3 < size and 0xE0 <= data[i + 3] <= 0xEF:
            return i
        p = i + 1


def _next_avi(data: _Data, start: int, hi: int) -> int:
    """start~hi 사이 첫 AVI(RIFF … AVI ) 시그니처 오프셋. 없으면 hi.
    JPEG 경계 계산의 하드 정지점 — RIFF+AVI 12바이트 구조는 JPEG 헤더·엔트로피에
    정상적으로 나타나지 않으므로(스캐너 판별과 동일 기준) 뒤따르는 AVI를 실제 다음
    파일 경계로 본다. hi로 탐색을 제한해 전 이미지 스캔을 피한다(조사 2026-07-05
    AVI 손실: JPEG이 AVI 위로 확장해 삼키던 과다 카빙 방지)."""
    n = len(data)
    p = start
    while True:
        i = data.find(b'RIFF', p)
        if i < 0 or i >= hi:
            return hi
        if i + 12 <= n and data[i + 8:i + 12] == b'AVI ':
            return i
        p = i + 1


# 헤더 세그먼트 길이 sane 상한 — 초과 시 손상된 길이 필드로 본다. 마커 워크가 손상된 길이를
# 신뢰해 임베디드 이미지 위로 점프하는 과다 카빙 방지(조사 2026-07-05). DHT/DQT/DRI.
_SEG_SANE_MAX = {0xC4: 1200, 0xDB: 600, 0xDD: 10}


def _seg_sane_max(mb: int) -> int | None:
    """마커별 세그먼트 길이 상한. APPn(E0-EF)·COM(FE)은 ≤65535라 제한 없음(None)."""
    if mb in _SEG_SANE_MAX:
        return _SEG_SANE_MAX[mb]
    if 0xC0 <= mb <= 0xCF and mb not in (0xC4, 0xC8, 0xCC):  # SOF (DHT/JPG/DAC 제외)
        return 100
    return None


def _corrupt_boundary(data: _Data, corrupt_pos: int, offset: int, size: int,
                      saw_sof: bool, next_sig_offset: int | None) -> int:
    """헤더 손상 검출 시 경계 — 다음 진짜 이미지 헤더나 AVI 시그니처로 축소해 뒤따르는 임베디드
    이미지·AVI를 별도 히트로 만든다(과다 카빙 복구). 단 SOF 도달 전 손상(=유효 이미지 아님,
    위양성)이면 다음 시그니처로 타이트하게 잡아 디스크 낭비·연쇄 삼킴을 막는다."""
    cap = min(_next_header(data, corrupt_pos, size), offset + JPEG_MAX_FALLBACK_SIZE, size)
    cap = _next_avi(data, corrupt_pos, cap)  # 다음 AVI(RIFF) 시그니처도 하드 경계 — AVI 삼킴 방지
    if not saw_sof and next_sig_offset is not None and next_sig_offset < cap:
        return next_sig_offset
    return cap


def jpeg_end(
    data: _Data,
    offset: int,
    next_sig_offset: int | None = None,
) -> tuple[int, bool]:
    """
    JPEG 세그먼트 파싱으로 파일 끝 오프셋 반환.

    스캔 데이터의 FF D9 후보는 즉시 채택하지 않고, 직후가 엔트로피 연속인지
    검사해 손상으로 생긴 가짜 EOI를 건너뛴다(_is_genuine_eoi). 탐색 상한은
    엔트로피 시작 이후의 다음 JPEG 헤더(_next_header)와 다음 AVI 시그니처
    (_next_avi)로, 다음 파일을 침범하지 않는다. next_sig_offset은 SOS를 찾지
    못한 경우의 fallback에만 쓴다.

    헤더 마커 워크는 세그먼트 길이를 신뢰해 전진하므로, 손상된 길이 필드나
    엔트로피성 바이트(FF00 등, 0xC0 미만)를 만나면 뒤따르는 임베디드 이미지 위로
    점프해 과다 카빙된다. 이를 막기 위해 유효마커(mb≥0xC0)·마커별 길이 상한
    (_seg_sane_max)을 검증하고, 위반 시 _corrupt_boundary로 경계를 축소한다
    (조사 2026-07-05).

    Returns:
        (end_offset, is_complete)
        end_offset: 파일 마지막 바이트 다음 위치 (exclusive)
        is_complete: True면 진짜 EOI 발견, False면 fallback 사용
    """
    pos = offset
    size = len(data)

    if data[pos:pos + 2] != b'\xff\xd8':
        raise ValueError(f'SOI 없음: {offset:#x}')
    pos += 2
    saw_sof = False  # SOF 도달 여부 — 손상 시 경계 선택(진짜 헤더 vs 다음 시그니처)에 쓴다

    while pos < size - 1:
        if data[pos] != 0xFF:
            pos += 1
            continue

        mb = data[pos + 1]

        if mb == 0xD9:  # EOI
            return pos + 2, True

        if mb == 0xDA:  # SOS — 이후는 스캔 데이터
            pos += 2
            if pos + 2 > size:
                break
            sos_len = struct.unpack('>H', data[pos:pos + 2])[0]
            if sos_len < 2 or pos + sos_len > size:
                break
            pos += sos_len  # SOS 헤더 건너뜀

            # 상한: 엔트로피 시작 이후 다음 JPEG 헤더(=다음 파일 경계)와 MAX_FALLBACK
            # 중 작은 쪽. 정확한 경계로 제한해야 _is_genuine_eoi 검사가 다음 파일을
            # 섞어 보지 않고, carve가 다음 파일을 삼키지도 않는다. 다음 AVI(RIFF)
            # 시그니처도 하드 경계로 포함해 뒤따르는 AVI를 삼키지 않는다.
            upper = min(_next_header(data, pos, size), offset + JPEG_MAX_FALLBACK_SIZE, size)
            upper = _next_avi(data, pos, upper)

            # 스캔 데이터: FF D9 후보를 검증하며 진짜 EOI를 탐색한다.
            while pos < size - 1 and pos < upper:
                ff = data.find(b'\xff', pos)
                if ff == -1 or ff >= size - 1 or ff >= upper:
                    break
                nb = data[ff + 1]
                if nb == 0xD9:  # EOI 후보
                    if _is_genuine_eoi(data, ff + 2, upper):
                        return ff + 2, True
                    pos = ff + 2   # 가짜 EOI(엔트로피 연속) → 다음 후보로
                elif nb == 0x00 or 0xD0 <= nb <= 0xD7:  # stuffed byte or RST
                    pos = ff + 2
                else:
                    pos = ff + 1
            # 상한까지 진짜 EOI를 못 찾음 → 상한에서 fallback
            return upper, False

        if mb == 0xD8:  # SOI mid-stream — 손상된 파일
            break

        if mb == 0xFF:  # 필 바이트 (JPEG 스펙 §B.1.1.2 허용)
            pos += 1
            continue

        if mb in _MARKER_NO_LENGTH:
            pos += 2
            continue

        if 0xC0 <= mb <= 0xCF and mb not in (0xC4, 0xC8, 0xCC):  # SOF 도달
            saw_sof = True

        # 유효마커 검증: 0xC0 미만은 헤더 마커가 아니다(엔트로피 FF00·쓰레기 바이트).
        # 마커 워크가 엔트로피에 진입한 것이므로 경계를 다음 진짜 헤더로 축소한다.
        if mb < 0xC0:
            return _corrupt_boundary(data, pos, offset, size, saw_sof, next_sig_offset), False

        # 길이 있는 세그먼트
        if pos + 4 > size:
            break
        seg_len = struct.unpack('>H', data[pos + 2:pos + 4])[0]
        if seg_len < 2 or pos + 2 + seg_len > size:
            break  # 비정상 길이 → fallback
        # 길이 검증: 마커별 상한 초과 = 손상된 길이 필드(임베디드 이미지 위로 점프 방지)
        sane = _seg_sane_max(mb)
        if sane is not None and seg_len > sane:
            return _corrupt_boundary(data, pos, offset, size, saw_sof, next_sig_offset), False
        pos = pos + 2 + seg_len

    # Fallback
    if next_sig_offset is not None:
        return next_sig_offset, False
    return min(offset + JPEG_MAX_FALLBACK_SIZE, size), False


def avi_end(
    data: _Data,
    offset: int,
    max_size: int = MAX_AVI_SIZE_DEFAULT,
    next_sig_offset: int | None = None,
) -> tuple[int, bool]:
    """
    AVI (RIFF) 파일 끝 오프셋 반환.

    Returns:
        (end_offset, used_header)
        used_header: True면 RIFF chunk_size 기반, False면 fallback 사용
    """
    size = len(data)

    if data[offset:offset + 4] != b'RIFF':
        raise ValueError(f'RIFF 없음: {offset:#x}')

    if offset + 8 > size:
        fallback = next_sig_offset if next_sig_offset is not None else offset + max_size
        return min(fallback, size), False

    chunk_size = struct.unpack('<I', data[offset + 4:offset + 8])[0]
    end_from_header = offset + 8 + chunk_size

    if 0 < chunk_size <= max_size and end_from_header <= size:
        return end_from_header, True

    # Fallback: next signature 또는 max_size 상한
    if next_sig_offset is not None:
        return min(next_sig_offset, offset + max_size, size), False
    return min(offset + max_size, size), False
