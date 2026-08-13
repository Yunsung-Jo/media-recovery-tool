from __future__ import annotations

import bisect
import mmap
import re
import struct
from typing import Sequence, Union


_Data = Union[bytes, bytearray, mmap.mmap]

# JPEG 하나의 경계 탐색·추출 하드 상한이다. 더 큰 파일을 다루려면 CLI의
# --max-jpeg-size 또는 호출 인자의 max_size를 늘려야 한다.
JPEG_MAX_FALLBACK_SIZE = 10 * 1024 * 1024
MAX_AVI_SIZE_DEFAULT = 500 * 1024 * 1024

_JPEG_SIG = b'\xff\xd8\xff'
_RIFF_SIG = b'RIFF'
_AVI_TYPE = b'AVI '
_AVIX_TYPE = b'AVIX'

# 길이 필드 없는 마커: TEM(01), RST0-RST7(D0-D7)
# SOI(D8), EOI(D9)는 별도로 처리한다.
_MARKER_NO_LENGTH = frozenset([0x01] + list(range(0xD0, 0xD8)))

# EOI 직후 윈도우가 엔트로피 연속인지 판별하는 휴리스틱.
_EOI_PROBE_WINDOW = 4096
_EOI_STUFF_THRESHOLD = 0.3
_EOI_PADDING_PREFIX = 64
_EOI_STRONG_FF_SAMPLE = 16

# 손상 시작 후보는 스캐너가 이미 JFIF/Exif와 후속 구조를 검증한다. 여기서는
# 헤더 전체를 다시 해석하지 않고 이 범위 안의 첫 정상 SOS부터 경계를 계산한다.
_DAMAGED_HEADER_SEARCH = 256 * 1024

# Progressive/multi-scan JPEG에서 엔트로피 구간 사이에 올 수 있는 길이형 마커.
# 모든 FFxx를 세그먼트로 해석하면 손상 엔트로피의 우연한 길이에 속아 실제 EOI를
# 건너뛸 수 있으므로 표준상 필요한 종류로 제한한다.
_POST_SCAN_LENGTH_MARKERS = frozenset(
    (0xC4, 0xCC, 0xDA, 0xDB, 0xDC, 0xDD, 0xFE, *range(0xE0, 0xF0))
)

# FF00 stuffing, RST0~RST7, fill FF를 제외하고 처리할 수 있는 2바이트
# 엔트로피 marker 후보다. 가변 길이 정규식보다 단순한 이 패턴이 mmap의 큰
# 범위를 한 번에 건너뛰며, fill run은 match 위치에서 짧게 역추적한다.
_ENTROPY_CONTROL_RE = re.compile(rb'\xff[\x01-\xcf\xd8-\xfe]')

# 헤더 세그먼트 길이 sane 상한. APPn/COM은 16비트 길이 전체가 합법이다.
_SEG_SANE_MAX = {0xC4: 2200, 0xDB: 600, 0xDD: 10}


def _stuffing_stats(seg: bytes) -> tuple[int, int]:
    ff = stuff = 0
    for i in range(len(seg) - 1):
        if seg[i] == 0xFF:
            ff += 1
            nb = seg[i + 1]
            if nb == 0x00 or 0xD0 <= nb <= 0xD7:
                stuff += 1
    return ff, stuff


def _stuffing_ratio(seg: bytes) -> float:
    """FF 뒤가 stuffing(00) 또는 RST(D0-D7)인 비율을 반환한다."""
    ff, stuff = _stuffing_stats(seg)
    return stuff / ff if ff else 0.0


def _is_genuine_eoi(
    data: _Data,
    eoi_end: int,
    upper: int,
    trusted_upper: bool = False,
) -> bool:
    """EOI 직후가 엔트로피 연속이 아니면 실제 파일 끝으로 판단한다."""
    seg = data[eoi_end:min(eoi_end + _EOI_PROBE_WINDOW, upper)]
    # A long, immediate zero-filled run is a strong allocation-padding signal.
    # Check only the prefix: a later zero run must not make an EOI embedded in
    # entropy look genuine (the probe can contain the real EOI and its padding).
    if seg[:_EOI_PADDING_PREFIX] == b'\x00' * _EOI_PADDING_PREFIX:
        return True

    ff_count, stuffed_count = _stuffing_stats(seg)
    stuffing_ratio = stuffed_count / ff_count if ff_count else 0.0
    if stuffing_ratio < _EOI_STUFF_THRESHOLD:
        return True
    # 가까운 검증 경계는 보조 근거일 뿐, 충분한 byte-stuffing 증거를
    # 덮어써서 엔트로피 안의 가짜 EOI를 채택하게 해서는 안 된다.
    if ff_count >= _EOI_STRONG_FF_SAMPLE:
        return False
    if trusted_upper and 0 <= upper - eoi_end <= _EOI_PROBE_WINDOW:
        return True
    # 매우 짧은 probe만으로는 EOI를 기각할 만큼의 반증을 얻기 어렵다.
    return len(seg) < 128


def _first_marker(data: _Data, offset: int, stop: int) -> int | None:
    pos = offset + 2
    while pos < stop and data[pos] == 0xFF:  # marker prefix와 fill FF
        pos += 1
    if pos >= stop:
        return None
    return int(data[pos])


def _has_coherent_jpeg_header(data: _Data, offset: int, stop: int) -> bool:
    """APPn 없이 시작하는 후보는 SOF와 SOS까지 marker walk가 이어져야 인정한다."""
    pos = offset + 2
    limit = min(offset + _DAMAGED_HEADER_SEARCH, stop)
    saw_dqt = saw_dht = saw_sof = False

    while pos < limit:
        if data[pos] != 0xFF:
            return False
        parsed = _segment_end(data, pos, limit)
        if parsed is None:
            return False
        marker, end = parsed
        if marker in (0xD8, 0xD9, 0x00) or marker in _MARKER_NO_LENGTH:
            return False
        sane = _seg_sane_max(marker)
        marker_byte_pos = pos
        while marker_byte_pos < limit and data[marker_byte_pos] == 0xFF:
            marker_byte_pos += 1
        seg_len = struct.unpack(
            '>H', data[marker_byte_pos + 1:marker_byte_pos + 3]
        )[0]
        if sane is not None and seg_len > sane:
            return False
        if not _segment_semantics_valid(
            data,
            marker_byte_pos,
            marker,
            seg_len,
            end,
        ):
            return False
        if marker == 0xDB:
            saw_dqt = True
        if marker == 0xC4:
            saw_dht = True
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            saw_sof = True
        if marker == 0xDA:
            return saw_dqt and saw_dht and saw_sof
        pos = end
    return False


def _is_plausible_first_marker(data: _Data, offset: int, stop: int) -> bool:
    """SOI 뒤 APPn/COM 또는 검증된 table-first 헤더인지 확인한다."""
    marker = _first_marker(data, offset, stop)
    if marker is None or marker < 0xC0 or marker == 0xFF:
        return False
    if marker in _MARKER_NO_LENGTH or marker in (0xD8, 0xD9):
        return False
    if 0xE0 <= marker <= 0xEF or marker == 0xFE:
        return _segment_end(
            data,
            offset + 2,
            min(stop, offset + _DAMAGED_HEADER_SEARCH),
        ) is not None
    return _has_coherent_jpeg_header(data, offset, stop)


def _next_header(data: _Data, start: int, size: int) -> int:
    """[start, size)에서 다음 구조적으로 가능한 JPEG SOI를 찾는다.

    APPn뿐 아니라 DQT/DHT/SOF로 곧바로 시작하는 합법 JPEG도 경계로 인정한다.
    ``find`` 자체에 종료 위치를 전달해 디스크 끝까지 불필요하게 검색하지 않는다.
    """
    pos = start
    while pos < size:
        found = data.find(_JPEG_SIG, pos, size)
        if found < 0:
            return size
        if _is_plausible_first_marker(data, found, size):
            return found
        pos = found + 1
    return size


def _next_avi(data: _Data, start: int, hi: int) -> int:
    """[start, hi)에서 다음 ``RIFF....AVI `` 시작을 찾는다."""
    pos = start
    size = len(data)
    while pos < hi:
        found = data.find(_RIFF_SIG, pos, hi)
        if found < 0:
            return hi
        if found + 12 <= hi and found + 12 <= size and data[found + 8:found + 12] == _AVI_TYPE:
            return found
        pos = found + 1
    return hi


def _next_indexed(offsets: Sequence[int] | None, start: int, stop: int) -> int:
    if not offsets:
        return stop
    index = bisect.bisect_left(offsets, start)
    if index < len(offsets) and offsets[index] < stop:
        return int(offsets[index])
    return stop


def _next_avi_boundary(
    data: _Data,
    start: int,
    stop: int,
    avi_offsets: Sequence[int] | None,
) -> int:
    # 호출자가 스캐너의 전체 AVI 인덱스를 넘기면 exact뿐 아니라 손상 시작도
    # 포함되어 있으므로 raw RIFF 재검색은 중복이다. None은 인덱스가 없다는 뜻이다.
    if avi_offsets is not None:
        return _next_indexed(avi_offsets, start, stop)
    return _next_avi(data, start, stop)


def _next_entropy_marker(
    data: _Data,
    start: int,
    stop: int,
) -> tuple[int, int]:
    """다음 처리 대상 marker의 첫 FF와 marker byte 위치를 반환한다."""
    if isinstance(data, (bytes, bytearray, mmap.mmap)):
        match = _ENTROPY_CONTROL_RE.search(data, start, stop)
        if match is None:
            return -1, -1
        marker_pos = match.start()
        marker_byte_pos = marker_pos + 1
        while marker_pos > start and data[marker_pos - 1] == 0xFF:
            marker_pos -= 1
        return marker_pos, marker_byte_pos

    # 테스트 대역이나 다른 bytes-like 구현은 같은 후보를 find로 순회한다.
    pos = start
    while pos < stop:
        marker_pos = data.find(b'\xff', pos, stop)
        if marker_pos < 0:
            return -1, -1
        marker_byte_pos = marker_pos + 1
        while marker_byte_pos < stop and data[marker_byte_pos] == 0xFF:
            marker_byte_pos += 1
        if marker_byte_pos >= stop:
            return -1, -1
        marker = int(data[marker_byte_pos])
        if marker != 0x00 and not 0xD0 <= marker <= 0xD7:
            return marker_pos, marker_byte_pos
        pos = marker_byte_pos + 1
    return -1, -1


def _seg_sane_max(marker: int) -> int | None:
    if marker in _SEG_SANE_MAX:
        return _SEG_SANE_MAX[marker]
    if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
        return 100
    return None


def _segment_end(data: _Data, marker_pos: int, stop: int) -> tuple[int, int] | None:
    """marker_pos의 fill FF를 소비하고 (marker, segment_end)를 반환한다."""
    pos = marker_pos
    while pos < stop and data[pos] == 0xFF:
        pos += 1
    if pos >= stop:
        return None
    marker = data[pos]
    if marker == 0x00 or marker in _MARKER_NO_LENGTH or marker in (0xD8, 0xD9):
        return marker, pos + 1
    if pos + 3 > stop:
        return None
    seg_len = struct.unpack('>H', data[pos + 1:pos + 3])[0]
    if seg_len < 2:
        return None
    end = pos + 1 + seg_len
    if end > stop:
        return None
    return marker, end


def _segment_semantics_valid(
    data: _Data,
    marker_byte_pos: int,
    marker: int,
    seg_len: int,
    end: int,
) -> bool:
    """경계 후보에 쓰는 핵심 JPEG 세그먼트의 내부 길이를 검증한다."""
    payload = marker_byte_pos + 3
    if seg_len < 2 or end != marker_byte_pos + 1 + seg_len:
        return False

    if marker == 0xDB:  # DQT: Pq/Tq + 64(또는 128)바이트 테이블
        pos = payload
        if seg_len < 67:
            return False
        while pos < end:
            info = int(data[pos])
            precision, table_id = info >> 4, info & 0x0F
            if precision not in (0, 1) or table_id > 3:
                return False
            table_start = pos + 1
            table_end = table_start + (128 if precision else 64)
            if table_end > end:
                return False
            if precision == 0:
                if any(value == 0 for value in data[table_start:table_end]):
                    return False
            else:
                for value_pos in range(table_start, table_end, 2):
                    if data[value_pos:value_pos + 2] == b'\x00\x00':
                        return False
            pos = table_end
        return pos == end

    if marker == 0xC4:  # DHT: Tc/Th + 16 code counts + symbols
        pos = payload
        if seg_len < 19:
            return False
        while pos < end:
            if pos + 17 > end:
                return False
            info = int(data[pos])
            table_class, table_id = info >> 4, info & 0x0F
            if table_class > 1 or table_id > 3:
                return False
            symbol_count = sum(data[pos + 1:pos + 17])
            if symbol_count > 256:
                return False
            pos += 17 + symbol_count
        return pos == end

    if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
        if seg_len < 11 or payload + 6 > end:
            return False
        precision = int(data[payload])
        height = struct.unpack('>H', data[payload + 1:payload + 3])[0]
        width = struct.unpack('>H', data[payload + 3:payload + 5])[0]
        components = int(data[payload + 5])
        if not (2 <= precision <= 16 and width > 0 and height > 0):
            return False
        if not (1 <= components <= 4) or seg_len != 8 + 3 * components:
            return False
        for component in range(components):
            sampling = int(data[payload + 7 + 3 * component])
            if sampling >> 4 == 0 or sampling & 0x0F == 0:
                return False
        return True

    if marker == 0xDA:  # SOS: Ns + (Cs,TdTa)*Ns + Ss/Se/AhAl
        if seg_len < 8 or payload >= end:
            return False
        components = int(data[payload])
        return 1 <= components <= 4 and seg_len == 6 + 2 * components

    if marker in (0xDC, 0xDD):  # DNL / DRI
        return seg_len == 4
    if marker == 0xCC:  # DAC: conditioning table pairs
        return seg_len >= 4 and (seg_len - 2) % 2 == 0
    return True


def _corrupt_boundary(
    data: _Data,
    corrupt_pos: int,
    offset: int,
    size: int,
    saw_sof: bool,
    protected_until: int,
    next_sig_offset: int | None,
    boundary_offsets: Sequence[int] | None,
    avi_offsets: Sequence[int] | None,
    max_size: int,
) -> int:
    """헤더 손상 시 뒤 파일을 삼키지 않는 보수적 경계를 선택한다."""
    limit = min(offset + max_size, size)
    cap = _next_header(data, corrupt_pos, limit)
    cap = _next_avi_boundary(data, corrupt_pos, cap, avi_offsets)

    indexed = _next_indexed(boundary_offsets, corrupt_pos, cap)
    if saw_sof and indexed < cap:
        cap = indexed
    if (
        not saw_sof
        and next_sig_offset is not None
        and not (offset < next_sig_offset < protected_until)
        and next_sig_offset < cap
    ):
        cap = next_sig_offset
    return cap


def _entropy_upper(
    offset: int,
    size: int,
    max_size: int,
) -> int:
    """JPEG 하나에 허용된 엔트로피 탐색의 하드 상한을 반환한다."""
    return min(offset + max_size, size)


def _scan_entropy(
    data: _Data,
    pos: int,
    upper: int,
    next_sig_offset: int | None,
    boundary_offsets: Sequence[int] | None,
    avi_offsets: Sequence[int] | None,
    allow_multiscan: bool = False,
) -> tuple[int, bool]:
    """SOS 이후 엔트로피와 마커 세그먼트를 오가며 실제 EOI를 찾는다.

    Progressive/multi-scan JPEG는 scan 사이에 DHT·COM·새 SOS가 올 수 있다.
    길이형 세그먼트 payload를 건너뛰므로 그 안의 ``FF D9``를 EOI로 오인하지 않는다.
    """
    last_eoi: int | None = None
    indexed_boundary = _next_indexed(boundary_offsets, pos, upper)
    avi_boundary = _next_avi_boundary(data, pos, upper, avi_offsets)
    while pos < upper:
        if indexed_boundary <= pos:
            indexed_boundary = _next_indexed(boundary_offsets, pos, upper)
        if avi_boundary <= pos:
            # APP/COM payload 안의 AVI 후보를 건너뛴 경우에만 다음 후보를 찾는다.
            # 매 FF마다 남은 전체 범위를 다시 검색하지 않아 scan 전체가 선형이다.
            avi_boundary = _next_avi_boundary(data, pos, upper, avi_offsets)
        next_sig = (
            next_sig_offset
            if next_sig_offset is not None and pos < next_sig_offset < upper
            else upper
        )
        boundary = min(indexed_boundary, next_sig, avi_boundary)
        marker_pos, marker_byte_pos = _next_entropy_marker(
            data,
            pos,
            min(boundary, upper),
        )
        if boundary < upper and marker_pos < 0:
            return boundary, False
        if marker_pos < 0 or marker_pos >= upper - 1:
            return upper, False

        marker = data[marker_byte_pos]
        end = marker_byte_pos + 1

        if marker == 0x00 or 0xD0 <= marker <= 0xD7:
            pos = end
            continue
        if marker == 0xD9:
            probe_upper = min(
                indexed_boundary if end < indexed_boundary else upper,
                avi_boundary if end < avi_boundary else upper,
            )
            if (
                next_sig_offset is not None
                and end < next_sig_offset < probe_upper
            ):
                probe_upper = next_sig_offset
            if _is_genuine_eoi(
                data,
                end,
                probe_upper,
                trusted_upper=(probe_upper < upper),
            ):
                return end, True
            pos = end
            continue
        if marker == 0xD8:
            soi_pos = marker_byte_pos - 1
            if _is_plausible_first_marker(data, soi_pos, upper):
                return soi_pos, False
            pos = marker_byte_pos + 1
            continue
        if marker == 0xFF:
            pos = end
            continue

        if marker not in _POST_SCAN_LENGTH_MARKERS:
            pos = marker_byte_pos + 1
            continue

        if not allow_multiscan:
            pos = marker_byte_pos + 1
            continue

        # 실제 inter-scan marker라면 길이형 세그먼트 연쇄가 새 SOS까지 이어진다.
        # 새 SOS 없는 우연한 FFxx는 엔트로피 손상으로 보고 한 바이트만 전진한다.
        chain_end = _post_scan_chain_end(data, marker_pos, upper)
        if chain_end is not None and chain_end < upper:
            # inter-scan 연쇄가 실제로 보일 때만 뒤 EOI를 한 번 역검색한다.
            # 대부분의 단일 scan JPEG에서 최대 10 MiB 범위를 선검색하지 않는다.
            if last_eoi is None:
                last_eoi = data.rfind(b'\xff\xd9', pos, upper)
            if last_eoi < chain_end:
                # 새 scan 뒤 EOI조차 없으면 padding/다음 파일을 우연히 SOS로 해석한 것이다.
                chain_end = None
        pos = chain_end if chain_end is not None else marker_byte_pos + 1
    return upper, False


def _post_scan_chain_end(data: _Data, start: int, upper: int) -> int | None:
    """길이형 marker 연쇄 뒤 새 SOS payload나 EOI marker 위치를 반환한다."""
    pos = start
    while pos < upper:
        marker_byte_pos = pos + 1
        while marker_byte_pos < upper and data[marker_byte_pos] == 0xFF:
            marker_byte_pos += 1
        if marker_byte_pos >= upper:
            return None
        marker = data[marker_byte_pos]
        if marker == 0xD9 and pos != start:
            return pos
        if marker not in _POST_SCAN_LENGTH_MARKERS:
            return None
        parsed = _segment_end(data, pos, upper)
        if parsed is None:
            return None
        marker, end = parsed
        seg_len = struct.unpack(
            '>H', data[marker_byte_pos + 1:marker_byte_pos + 3]
        )[0]
        sane = _seg_sane_max(marker)
        if sane is not None and seg_len > sane:
            return None
        if not _segment_semantics_valid(
            data,
            marker_byte_pos,
            marker,
            seg_len,
            end,
        ):
            return None
        if marker == 0xDA:
            return end
        if end == upper:
            return upper
        if end > upper or data[end] != 0xFF:
            return None
        pos = end
    return None


def _damaged_jpeg_end(
    data: _Data,
    offset: int,
    next_sig_offset: int | None,
    boundary_offsets: Sequence[int] | None,
    avi_offsets: Sequence[int] | None,
    max_size: int,
    validated_scan_start: int | None = None,
) -> tuple[int, bool]:
    size = len(data)
    search_end = min(offset + _DAMAGED_HEADER_SEARCH, offset + max_size, size)

    if (
        validated_scan_start is not None
        and offset + 2 <= validated_scan_start <= search_end
    ):
        upper = _entropy_upper(offset, size, max_size)
        return _scan_entropy(
            data,
            validated_scan_start,
            upper,
            next_sig_offset,
            boundary_offsets,
            avi_offsets,
            allow_multiscan=True,
        )

    pos = offset + 2
    while pos < search_end:
        sos = data.find(b'\xff\xda', pos, search_end)
        if sos < 0:
            break
        parsed = _segment_end(data, sos, search_end)
        if parsed is not None:
            marker, scan_start = parsed
            if marker == 0xDA:
                upper = _entropy_upper(offset, size, max_size)
                return _scan_entropy(
                    data,
                    scan_start,
                    upper,
                    next_sig_offset,
                    boundary_offsets,
                    avi_offsets,
                )
        pos = sos + 1

    fallback = min(offset + max_size, size)
    if next_sig_offset is not None and offset < next_sig_offset < fallback:
        fallback = next_sig_offset
    fallback = _next_avi_boundary(data, offset + 2, fallback, avi_offsets)
    return fallback, False


def jpeg_end(
    data: _Data,
    offset: int,
    next_sig_offset: int | None = None,
    *,
    allow_corrupt_header: bool = False,
    boundary_offsets: Sequence[int] | None = None,
    avi_offsets: Sequence[int] | None = None,
    max_size: int = JPEG_MAX_FALLBACK_SIZE,
    validated_scan_start: int | None = None,
) -> tuple[int, bool]:
    """JPEG 파일의 exclusive 끝과 EOI 완결 여부를 반환한다.

    정확 헤더는 JPEG 마커 상태 머신으로 파싱한다. ``allow_corrupt_header``는
    스캐너가 별도 구조 검증한 JFIF/Exif 손상 시작 후보에만 사용한다.
    """
    size = len(data)
    if offset < 0 or offset + 2 > size:
        raise ValueError(f'잘못된 JPEG 오프셋: {offset:#x}')
    if max_size <= 0:
        raise ValueError('max_size는 0보다 커야 합니다')

    if data[offset:offset + 2] != b'\xff\xd8':
        if allow_corrupt_header:
            return _damaged_jpeg_end(
                data,
                offset,
                next_sig_offset,
                boundary_offsets,
                avi_offsets,
                max_size,
                validated_scan_start,
            )
        raise ValueError(f'SOI 없음: {offset:#x}')

    # SOI는 남았지만 바로 뒤 marker/길이가 손상된 후보도 tolerant 경로로 보낸다.
    if allow_corrupt_header and not _is_plausible_first_marker(
        data,
        offset,
        min(offset + _DAMAGED_HEADER_SEARCH, size),
    ):
        return _damaged_jpeg_end(
            data,
            offset,
            next_sig_offset,
            boundary_offsets,
            avi_offsets,
            max_size,
            validated_scan_start,
        )

    pos = offset + 2
    protected_until = pos
    saw_sof = False
    allow_multiscan = False
    limit = min(offset + max_size, size)

    # 손상 JPEG에는 헤더 payload 길이 자체가 깨진 경우가 많다. 기존 복구율을
    # 보존하기 위해 marker prefix가 아닌 바이트는 한 바이트씩 재동기화하되,
    # 실제 marker를 만났을 때는 길이와 sane 상한을 검증한다.
    while pos < limit - 1:
        if data[pos] != 0xFF:
            pos += 1
            continue

        marker = data[pos + 1]
        if marker == 0xD9:
            return pos + 2, True
        if marker == 0xD8:
            break
        if marker == 0xFF:
            pos += 1
            continue
        if marker in _MARKER_NO_LENGTH:
            pos += 2
            continue
        if marker < 0xC0:
            return (
                _corrupt_boundary(
                    data,
                    pos,
                    offset,
                    size,
                    saw_sof,
                    protected_until,
                    next_sig_offset,
                    boundary_offsets,
                    avi_offsets,
                    max_size,
                ),
                False,
            )
        if pos + 4 > limit:
            break

        seg_len = struct.unpack('>H', data[pos + 2:pos + 4])[0]
        if seg_len < 2:
            break
        end = pos + 2 + seg_len
        if end > limit:
            break

        is_exif_segment = (
            marker == 0xE1
            and data[pos + 4:pos + 10] == b'Exif\x00\x00'
        )
        # Exif payload의 raw AVI 바이트는 metadata다. 그 밖의 세그먼트에서
        # 선언 길이가 외부 AVI를 가로지르면 손상 길이로 보고 AVI를 보존한다.
        if not is_exif_segment:
            avi = _next_avi_boundary(data, pos, end, avi_offsets)
            if avi < end:
                return avi, False

        sane = _seg_sane_max(marker)
        if sane is not None and seg_len > sane:
            return (
                _corrupt_boundary(
                    data,
                    pos,
                    offset,
                    size,
                    saw_sof,
                    protected_until,
                    next_sig_offset,
                    boundary_offsets,
                    avi_offsets,
                    max_size,
                ),
                False,
            )

        if not _segment_semantics_valid(
            data,
            pos + 1,
            marker,
            seg_len,
            end,
        ):
            return (
                _corrupt_boundary(
                    data,
                    pos,
                    offset,
                    size,
                    saw_sof,
                    protected_until,
                    next_sig_offset,
                    boundary_offsets,
                    avi_offsets,
                    max_size,
                ),
                False,
            )

        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            saw_sof = True
            # Sequential JPEG도 컴포넌트를 여러 scan으로 나눌 수 있다.
            allow_multiscan = True
        if marker == 0xDA:
            upper = _entropy_upper(offset, size, max_size)
            return _scan_entropy(
                data,
                end,
                upper,
                next_sig_offset,
                boundary_offsets,
                avi_offsets,
                allow_multiscan=allow_multiscan,
            )
        if is_exif_segment:
            protected_until = max(protected_until, end)
        pos = end

    fallback = min(offset + max_size, size)
    if (
        next_sig_offset is not None
        and not (offset < next_sig_offset < protected_until)
        and offset < next_sig_offset < fallback
    ):
        fallback = next_sig_offset
    return fallback, False


def _is_stream_chunk_payload(data: _Data, payload_offset: int, limit: int) -> bool:
    """payload_offset이 AVI ``NNdc/NNdb``류 chunk payload 시작인지 확인한다."""
    if payload_offset < 8 or payload_offset + 2 > limit:
        return False
    chunk_id = bytes(data[payload_offset - 8:payload_offset - 4])
    if len(chunk_id) != 4:
        return False
    stream = chunk_id[:2]
    kind = chunk_id[2:]
    if not all(chr(value) in '0123456789ABCDEFabcdef' for value in stream):
        return False
    if kind not in (b'dc', b'db', b'wb', b'tx', b'pc'):
        return False
    chunk_size = struct.unpack('<I', data[payload_offset - 4:payload_offset])[0]
    return chunk_size >= 2 and payload_offset + chunk_size <= limit


def _is_stream_chunk_id(chunk_id: bytes) -> bool:
    if len(chunk_id) != 4:
        return False
    stream = chunk_id[:2]
    kind = chunk_id[2:]
    return (
        all(chr(value) in '0123456789ABCDEFabcdef' for value in stream)
        and kind in (b'dc', b'db', b'wb', b'tx', b'pc')
    )


def _is_avi_top_level_chunk_id(chunk_id: bytes) -> bool:
    """보수적으로 허용할 AVI/OpenDML top-level chunk id인지 확인한다."""
    return (
        chunk_id in (b'LIST', b'JUNK', b'idx1', b'INFO', b'PAD ', b'IDIT')
        or (
            len(chunk_id) == 4
            and chunk_id[:2] == b'ix'
            and all(chr(value) in '0123456789ABCDEFabcdef' for value in chunk_id[2:])
        )
    )


def _walk_stream_chunks_for_offset(
    data: _Data,
    pos: int,
    stop: int,
    candidate: int,
    limit: int,
    depth: int = 0,
) -> int | None:
    """movi/rec 영역을 따라 candidate를 포함한 stream payload 끝을 찾는다."""
    if depth > 8:
        return None
    while pos + 8 <= stop and pos <= candidate:
        chunk_id = bytes(data[pos:pos + 4])
        chunk_size = struct.unpack('<I', data[pos + 4:pos + 8])[0]
        payload = pos + 8
        chunk_end = payload + chunk_size
        next_pos = chunk_end + (chunk_size & 1)
        if chunk_end > limit or next_pos > limit:
            return None

        if _is_stream_chunk_id(chunk_id):
            if payload <= candidate < chunk_end:
                return chunk_end
        elif chunk_id == b'LIST' and chunk_size >= 4:
            list_type = bytes(data[payload:payload + 4])
            if list_type in (b'rec ', b'movi'):
                contained = _walk_stream_chunks_for_offset(
                    data,
                    payload + 4,
                    chunk_end,
                    candidate,
                    limit,
                    depth + 1,
                )
                if contained is not None:
                    return contained
        pos = next_pos
    return None


def _stream_chunk_end_containing(
    data: _Data,
    avi_offset: int,
    candidate: int,
    limit: int,
) -> int | None:
    """candidate가 AVI movi stream chunk 내부면 그 payload 끝을 반환한다.

    movi LIST의 크기만 손상된 경우에도 LIST/movi 헤더와 내부 chunk 길이가
    정상이라면 프레임 안의 SOI를 외부 JPEG 경계로 쓰지 않는다.
    """
    if candidate <= avi_offset + 12 or candidate >= limit:
        return None
    if _is_stream_chunk_payload(data, candidate, limit):
        chunk_size = struct.unpack('<I', data[candidate - 4:candidate])[0]
        return candidate + chunk_size

    search = avi_offset + 12
    search_end = min(candidate + 1, limit)
    while search < search_end:
        movi = data.find(b'movi', search, search_end)
        if movi < 0:
            return None
        list_pos = movi - 8
        if list_pos >= avi_offset + 12 and data[list_pos:list_pos + 4] == b'LIST':
            declared_size = struct.unpack('<I', data[list_pos + 4:list_pos + 8])[0]
            declared_end = list_pos + 8 + declared_size
            if movi + 4 <= candidate < declared_end <= limit:
                # 정상 선언 범위 안의 모든 바이트는 movi payload다. 손상 프레임의
                # 개별 chunk walk가 중간에 끊겨도 내부 SOI를 외부 경계로 보지 않는다.
                return declared_end
            # candidate가 선언 범위 밖이면 movi 길이 손상으로 보고 max 상한까지
            # 내부 chunk walk를 계속한다.
            region_end = (
                declared_end
                if candidate < declared_end <= limit
                else limit
            )
            contained = _walk_stream_chunks_for_offset(
                data,
                movi + 4,
                region_end,
                candidate,
                limit,
            )
            if contained is not None:
                return contained
        search = movi + 1
    return None


def _avi_hdrl_has_avih(data: _Data, list_pos: int, list_end: int) -> bool:
    pos = list_pos + 12
    while pos + 8 <= list_end:
        chunk_id = bytes(data[pos:pos + 4])
        chunk_size = struct.unpack('<I', data[pos + 4:pos + 8])[0]
        chunk_end = pos + 8 + chunk_size
        next_pos = chunk_end + (chunk_size & 1)
        if chunk_end > list_end or next_pos > list_end:
            return False
        if chunk_id == b'avih' and chunk_size >= 56:
            return True
        pos = next_pos
    return False


def _walk_avi_chunks(data: _Data, offset: int, limit: int) -> int | None:
    """손상된 RIFF 크기 대신 정상 top-level LIST/chunk 길이로 AVI 끝을 복원한다."""
    pos = offset + 12
    saw_hdrl = saw_avih = saw_movi = False
    last_end = pos

    while pos + 8 <= limit:
        chunk_id = bytes(data[pos:pos + 4])
        # 다음 RIFF는 현재 컨테이너 경계다.
        if chunk_id == _RIFF_SIG:
            break
        chunk_size = struct.unpack('<I', data[pos + 4:pos + 8])[0]
        chunk_end = pos + 8 + chunk_size
        next_pos = chunk_end + (chunk_size & 1)
        if chunk_end > limit or next_pos > limit:
            break
        list_type = bytes(data[pos + 8:pos + 12]) if chunk_size >= 4 else b''
        damaged_hdrl_list = (
            list_type == b'hdrl'
            and sum(a != b for a, b in zip(chunk_id, b'LIST')) <= 2
        )
        if not _is_avi_top_level_chunk_id(chunk_id) and not damaged_hdrl_list:
            break
        if (chunk_id == b'LIST' or damaged_hdrl_list) and chunk_size >= 4:
            if list_type == b'hdrl':
                saw_hdrl = True
                saw_avih = _avi_hdrl_has_avih(data, pos, chunk_end)
            elif list_type == b'movi' and saw_hdrl and saw_avih:
                saw_movi = True
        last_end = next_pos
        pos = next_pos

    return last_end if saw_hdrl and saw_avih and saw_movi else None


def _walk_avix_chunks(data: _Data, offset: int, limit: int) -> int | None:
    """AVIX 선언 범위를 끝까지 걷고 LIST/movi가 있을 때만 끝을 반환한다."""
    pos = offset + 12
    saw_movi = False
    while pos + 8 <= limit:
        chunk_id = bytes(data[pos:pos + 4])
        if not _is_avi_top_level_chunk_id(chunk_id):
            return None
        chunk_size = struct.unpack('<I', data[pos + 4:pos + 8])[0]
        chunk_end = pos + 8 + chunk_size
        next_pos = chunk_end + (chunk_size & 1)
        if chunk_end > limit or next_pos > limit:
            return None
        if (
            chunk_id == b'LIST'
            and chunk_size >= 4
            and data[pos + 8:pos + 12] == b'movi'
        ):
            saw_movi = True
        pos = next_pos
    return pos if pos == limit and saw_movi else None


def _next_external_avi_boundary(data: _Data, offset: int, limit: int) -> int:
    """손상 AVI 내부 MJPEG chunk를 건너 다음 독립 JPEG/AVI 시작을 찾는다."""
    pos = offset + 12
    while pos < limit:
        jpeg = _next_header(data, pos, limit)
        avi = _next_avi(data, pos, limit)
        candidate = min(jpeg, avi)
        if candidate >= limit:
            return limit
        if candidate == jpeg:
            stream_end = _stream_chunk_end_containing(data, offset, candidate, limit)
            if stream_end is not None:
                pos = max(candidate + 1, stream_end)
                continue
        return candidate
    return limit


def _extend_avix(data: _Data, end: int, origin: int, max_size: int) -> int:
    size = len(data)
    while True:
        candidates = (end, end + 1)
        avix_start = next(
            (
                pos
                for pos in candidates
                if pos + 12 <= size
                and data[pos:pos + 4] == _RIFF_SIG
                and data[pos + 8:pos + 12] == _AVIX_TYPE
            ),
            None,
        )
        if avix_start is None:
            return end
        chunk_size = struct.unpack('<I', data[avix_start + 4:avix_start + 8])[0]
        avix_end = avix_start + 8 + chunk_size
        if (
            chunk_size < 16
            or avix_end > size
            or avix_end - origin > max_size + 8
            or _walk_avix_chunks(data, avix_start, avix_end) != avix_end
        ):
            return end
        end = avix_end


def avi_end(
    data: _Data,
    offset: int,
    max_size: int = MAX_AVI_SIZE_DEFAULT,
    next_sig_offset: int | None = None,
    *,
    allow_corrupt_header: bool = False,
) -> tuple[int, bool]:
    """AVI의 exclusive 끝과 RIFF 크기 필드 사용 여부를 반환한다."""
    size = len(data)
    if max_size <= 0:
        raise ValueError('max_size는 0보다 커야 합니다')
    if offset < 0 or offset + 12 > size:
        raise ValueError(f'잘못된 AVI 오프셋: {offset:#x}')
    riff_id = bytes(data[offset:offset + 4])
    riff_damage = sum(a != b for a, b in zip(riff_id, _RIFF_SIG))
    if riff_id != _RIFF_SIG and (
        not allow_corrupt_header or not 1 <= riff_damage <= 2
    ):
        raise ValueError(f'RIFF 없음: {offset:#x}')
    form_type = bytes(data[offset + 8:offset + 12])
    form_damage = sum(a != b for a, b in zip(form_type, _AVI_TYPE))
    if form_type != _AVI_TYPE and (
        not allow_corrupt_header or not 1 <= form_damage <= 2
    ):
        raise ValueError(f'AVI form type 없음: {offset:#x}')

    chunk_size = struct.unpack('<I', data[offset + 4:offset + 8])[0]
    end_from_header = offset + 8 + chunk_size
    # 선언 범위 안의 top-level walk에서 hdrl/avih와 movi를 확인해야 size를
    # 신뢰한다. movi 뒤의 opaque padding/벤더 청크는 RIFF 선언 범위에 보존한다.
    valid_size = 16 <= chunk_size <= max_size and end_from_header <= size
    if valid_size and _walk_avi_chunks(data, offset, end_from_header) is None:
        valid_size = False
    if (
        valid_size
        and next_sig_offset is not None
        and offset < next_sig_offset < end_from_header
        and _stream_chunk_end_containing(
            data,
            offset,
            next_sig_offset,
            end_from_header,
        ) is None
    ):
        # 선언 크기가 다음 독립 시작 후보를 가로지르면 size 필드 손상으로 본다.
        # movi/stream 내부 후보는 위 helper가 배제한다.
        valid_size = False

    if valid_size:
        # 유효한 RIFF 크기는 컨테이너 내부 JPEG 시그니처보다 강한 경계 근거다.
        return _extend_avix(data, end_from_header, offset, max_size), True

    limit = min(offset + max_size, size)
    walked = _walk_avi_chunks(data, offset, limit)
    if walked is not None:
        return _extend_avix(data, walked, offset, max_size), False

    boundary = _next_external_avi_boundary(data, offset, limit)
    if (
        next_sig_offset is not None
        and offset < next_sig_offset < boundary
        and _stream_chunk_end_containing(data, offset, next_sig_offset, limit) is None
    ):
        boundary = next_sig_offset
    return boundary, False
