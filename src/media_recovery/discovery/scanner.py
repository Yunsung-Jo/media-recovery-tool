import mmap
import struct

from media_recovery.domain.objects import FileHit
from media_recovery.formats.boundaries import jpeg_end

_JPEG_SIG = b'\xff\xd8\xff'
_RIFF_SIG = b'RIFF'
_AVI_TYPE = b'AVI '
_JPEG_DQT = b'\xff\xdb'
_JPEG_DHT = b'\xff\xc4'
_JPEG_SOS = b'\xff\xda'
_JPEG_EOI = b'\xff\xd9'

_JPEG_SOF_MARKERS = frozenset(
    (*range(0xC0, 0xC4), *range(0xC5, 0xC8), *range(0xC9, 0xCC), *range(0xCD, 0xD0))
)
_JPEG_ANCHORS = (
    (b'JFIF\x00', b'\xff\xd8\xff\xe0'),
    (b'Exif\x00\x00', b'\xff\xd8\xff\xe1'),
)
_JPEG_HEADER_SEARCH = 256 * 1024
_JPEG_EOI_SEARCH = 10 * 1024 * 1024
_JPEG_RESYNC_GAP = 4096
_DAMAGED_JPEG_ALIGNMENT = 4096

_DAMAGED_JPEG_SOURCE = 'damaged_jpeg_header'
_DAMAGED_AVI_SOURCE = 'damaged_avi_header'
_DAMAGED_JPEG_CONFIDENCE = 0.95
_RESYNCED_JPEG_CONFIDENCE = 0.85
_DAMAGED_AVI_CONFIDENCE = 0.98


def _different_bytes(actual: bytes, expected: bytes) -> int:
    return sum(a != b for a, b in zip(actual, expected))


def _find_length_segment(
    data: mmap.mmap,
    marker: bytes,
    start: int,
    end: int,
    min_length: int,
    max_length: int,
) -> tuple[int, int] | None:
    """Find a marker whose declared segment stays inside ``end``."""
    pos = start
    while pos < end:
        found = data.find(marker, pos, end)
        if found < 0:
            return None
        if found + 4 <= end:
            seg_len = struct.unpack('>H', data[found + 2:found + 4])[0]
            if (
                min_length <= seg_len <= max_length
                and found + 2 + seg_len <= end
            ):
                return found, found + 2 + seg_len
        pos = found + 1
    return None


def _read_length_marker(
    data: mmap.mmap,
    pos: int,
    end: int,
) -> tuple[int, int, int, int] | None:
    """Read the length marker exactly at ``pos``, allowing FF fill bytes."""
    if pos >= end or data[pos] != 0xFF:
        return None
    code_pos = pos + 1
    while code_pos < end and data[code_pos] == 0xFF:
        code_pos += 1
    if code_pos + 3 > end:
        return None
    code = data[code_pos]
    if code in (0x00, 0x01, 0xD8, 0xD9) or 0xD0 <= code <= 0xD7:
        return None
    seg_len = struct.unpack('>H', data[code_pos + 1:code_pos + 3])[0]
    if seg_len < 2:
        return None
    segment_end = code_pos + 1 + seg_len
    if segment_end > end:
        return None
    return code, seg_len, code_pos + 3, segment_end


def _valid_dqt(payload: bytes) -> bool:
    pos = 0
    tables = 0
    while pos < len(payload):
        table_info = payload[pos]
        pos += 1
        precision = table_info >> 4
        table_id = table_info & 0x0F
        if precision > 1 or table_id > 3:
            return False
        table_size = 64 * (precision + 1)
        if pos + table_size > len(payload):
            return False
        table = payload[pos:pos + table_size]
        if precision == 0:
            if 0 in table:
                return False
        elif any(table[i:i + 2] == b'\x00\x00' for i in range(0, table_size, 2)):
            return False
        pos += table_size
        tables += 1
    return tables > 0 and pos == len(payload)


def _valid_dht(payload: bytes) -> bool:
    pos = 0
    tables = 0
    while pos < len(payload):
        if pos + 17 > len(payload):
            return False
        table_info = payload[pos]
        if table_info >> 4 > 1 or table_info & 0x0F > 3:
            return False
        symbol_count = sum(payload[pos + 1:pos + 17])
        if symbol_count == 0 or symbol_count > 256:
            return False
        pos += 17
        if pos + symbol_count > len(payload):
            return False
        pos += symbol_count
        tables += 1
    return tables > 0 and pos == len(payload)


def _sof_components(payload: bytes) -> set[int] | None:
    if len(payload) < 9:
        return None
    component_count = payload[5]
    if component_count == 0 or len(payload) != 6 + 3 * component_count:
        return None
    if payload[0] == 0 or not any(payload[1:3]) or not any(payload[3:5]):
        return None

    components: set[int] = set()
    for pos in range(6, len(payload), 3):
        component_id, sampling, table_id = payload[pos:pos + 3]
        if (
            component_id in components
            or sampling >> 4 == 0
            or sampling & 0x0F == 0
            or table_id > 3
        ):
            return None
        components.add(component_id)
    return components


def _valid_sos(payload: bytes, sof_components: set[int] | None = None) -> bool:
    if len(payload) < 6:
        return False
    component_count = payload[0]
    if component_count == 0 or len(payload) != 4 + 2 * component_count:
        return False
    components = {payload[pos] for pos in range(1, 1 + 2 * component_count, 2)}
    if len(components) != component_count:
        return False
    return sof_components is None or components <= sof_components


def _tolerant_sof_is_semantic(payload: bytes) -> bool:
    """Check the stable SOF fields without trusting damaged sampling metadata."""
    if len(payload) < 9:
        return False
    component_count = payload[5]
    if (
        component_count == 0
        or len(payload) != 6 + 3 * component_count
        or payload[0] == 0
        or not any(payload[1:3])
        or not any(payload[3:5])
    ):
        return False
    component_ids = payload[6::3]
    return len(set(component_ids)) == component_count


def _walk_jpeg_header(data: mmap.mmap, start: int, end: int) -> int | None:
    """Continuously validate markers through SOS and return entropy start."""
    pos = start
    saw_dqt = False
    saw_dht = False
    components: set[int] | None = None

    while pos + 4 <= end:
        marker = _read_length_marker(data, pos, end)
        if marker is None:
            return None
        code, seg_len, payload_start, segment_end = marker
        payload = data[payload_start:segment_end]

        if code == 0xDB:
            if seg_len < 67 or not _valid_dqt(payload):
                return None
            saw_dqt = True
        elif code == 0xC4:
            if seg_len < 19 or not _valid_dht(payload):
                return None
            saw_dht = True
        elif code in _JPEG_SOF_MARKERS:
            components = _sof_components(payload)
            if components is None:
                return None
        elif code == 0xDA:
            if (
                not saw_dqt
                or not saw_dht
                or components is None
                or not _valid_sos(payload, components)
            ):
                return None
            return segment_end
        elif 0xE0 <= code <= 0xEF or code == 0xFE:
            pass
        elif code == 0xDD:
            if seg_len != 4:
                return None
        elif code in (0xCC, 0xDC, 0xDE, 0xDF):
            pass
        else:
            return None
        pos = segment_end
    return None


def _strict_damaged_jpeg_scan_start(
    data: mmap.mmap,
    offset: int,
    anchor: bytes,
    size: int,
) -> int | None:
    if offset + 6 + len(anchor) > size:
        return None
    seg_len = struct.unpack('>H', data[offset + 4:offset + 6])[0]
    min_app_length = 16 if anchor == b'JFIF\x00' else 8
    app_end = offset + 4 + seg_len
    if seg_len < min_app_length or app_end > min(offset + _JPEG_HEADER_SEARCH, size):
        return None
    return _walk_jpeg_header(
        data,
        app_end,
        min(offset + _JPEG_HEADER_SEARCH, size),
    )


def _find_tolerant_sof(
    data: mmap.mmap,
    start: int,
    end: int,
) -> tuple[int, int] | None:
    """Find a semantic SOF, allowing one damaged length byte."""
    pos = start
    while pos + 10 <= end:
        marker_pos = data.find(b'\xff', pos, end)
        if marker_pos < 0 or marker_pos + 10 > end:
            return None
        code = data[marker_pos + 1]
        if code in _JPEG_SOF_MARKERS:
            component_count = data[marker_pos + 9]
            expected_len = 8 + 3 * component_count
            expected_end = marker_pos + 2 + expected_len
            if component_count and expected_end <= end:
                declared = data[marker_pos + 2:marker_pos + 4]
                declared_len = struct.unpack('>H', declared)[0]
                length_valid = declared_len == expected_len
                length_repairable = _different_bytes(
                    declared,
                    struct.pack('>H', expected_len),
                ) == 1
                payload = data[marker_pos + 4:expected_end]
                if (
                    (length_valid or length_repairable)
                    and _tolerant_sof_is_semantic(payload)
                ):
                    return marker_pos, expected_end
        pos = marker_pos + 1
    return None


def _tolerant_damaged_jpeg_scan_start(
    data: mmap.mmap,
    offset: int,
    anchor: bytes,
    size: int,
    *,
    require_alignment: bool = True,
) -> int | None:
    """Resync a candidate with tightly bounded ordered markers."""
    if require_alignment and offset % _DAMAGED_JPEG_ALIGNMENT:
        return None
    anchor_end = offset + 6 + len(anchor)
    header_end = min(offset + _JPEG_HEADER_SEARCH, size)

    dqt = _find_length_segment(
        data, _JPEG_DQT, anchor_end, min(anchor_end + _JPEG_RESYNC_GAP, header_end), 67, 600
    )
    if dqt is None:
        return None
    sof = _find_tolerant_sof(
        data, dqt[1], min(dqt[1] + _JPEG_RESYNC_GAP, header_end)
    )
    if sof is None:
        return None
    dht = _find_length_segment(
        data, _JPEG_DHT, sof[1], min(sof[1] + _JPEG_RESYNC_GAP, header_end), 19, 2200
    )
    if dht is None:
        return None
    sos = _find_length_segment(
        data, _JPEG_SOS, dht[1], min(dht[1] + _JPEG_RESYNC_GAP, header_end), 8, 64
    )
    if sos is None or not _valid_sos(data[sos[0] + 4:sos[1]]):
        return None
    return sos[1]


def _exact_jpeg_scan_start(data: mmap.mmap, offset: int, size: int) -> tuple[bool, int | None]:
    """Validate exact SOI without discarding a later-damaged recoverable header."""
    header_end = min(offset + _JPEG_HEADER_SEARCH, size)
    if offset + 4 > size:
        return False, None
    first = _read_length_marker(data, offset + 2, header_end)
    if first is None:
        return False, None
    code, seg_len, _, segment_end = first
    if 0xE0 <= code <= 0xEF or code == 0xFE:
        min_length, max_length = 2, 0xFFFF
    elif code == 0xDB:
        min_length, max_length = 67, 600
    elif code == 0xC4:
        min_length, max_length = 19, 2200
    elif code in _JPEG_SOF_MARKERS:
        min_length, max_length = 11, 100
    elif code == 0xDA:
        min_length, max_length = 8, 64
    elif code in (0xDC, 0xDD):
        min_length = max_length = 4
    elif code == 0xCC:
        min_length, max_length = 4, 34
    else:
        return False, None

    if (
        not min_length <= seg_len <= max_length
        or segment_end > header_end
    ):
        return False, None
    if 0xE0 <= code <= 0xEF or code == 0xFE:
        return True, None

    # 연속 walk가 성공하면 SOS 근거도 보존한다. 실패하더라도 정확 SOI와
    # 의미 있는 첫 세그먼트 길이는 후속 headerfix가 살릴 수 있는 강한 시작 근거다.
    scan_start = _walk_jpeg_header(
        data,
        offset + 2,
        header_end,
    )
    return True, scan_start


def _iter_damaged_jpeg_candidates(
    data: mmap.mmap,
    size: int,
    exact_jpeg_offsets: list[int],
):
    exact_offset_set = set(exact_jpeg_offsets)
    for anchor, expected_core in _JPEG_ANCHORS:
        pos = 0
        while pos < size:
            found = data.find(anchor, pos, size)
            if found < 0:
                break
            offset = found - 6
            if offset >= 0:
                core = data[offset:offset + 4]
                damage = _different_bytes(core, expected_core)
                near_exact = any(
                    probe in exact_offset_set
                    for probe in range(max(0, offset - 16), offset + 17)
                )
                if not near_exact and (1 <= damage <= 2 or (
                    damage == 0 and offset not in exact_offset_set
                )):
                    scan_start = _strict_damaged_jpeg_scan_start(
                        data, offset, anchor, size
                    )
                    confidence = _DAMAGED_JPEG_CONFIDENCE
                    if scan_start is None:
                        scan_start = _tolerant_damaged_jpeg_scan_start(
                            data,
                            offset,
                            anchor,
                            size,
                            require_alignment=(damage != 0),
                        )
                        confidence = _RESYNCED_JPEG_CONFIDENCE
                    if scan_start is None:
                        pos = found + 1
                        continue
                    yield FileHit(
                        file_type='jpeg',
                        offset=offset,
                        source=_DAMAGED_JPEG_SOURCE,
                        confidence=confidence,
                        scan_start=scan_start,
                    )
            pos = found + 1


def _hdrl_has_avih(data: mmap.mmap, list_pos: int, list_end: int) -> bool:
    pos = list_pos + 12  # LIST header + "hdrl" type
    while pos + 8 <= list_end:
        chunk_id = data[pos:pos + 4]
        chunk_size = struct.unpack('<I', data[pos + 4:pos + 8])[0]
        chunk_data_end = pos + 8 + chunk_size
        next_pos = chunk_data_end + (chunk_size & 1)
        if chunk_data_end > list_end or next_pos > list_end:
            return False
        if chunk_id == b'avih' and chunk_size >= 56:
            return True
        pos = next_pos
    return False


def _has_avi_structure(
    data: mmap.mmap,
    offset: int,
    size: int,
    *,
    require_avi_type: bool = True,
) -> bool:
    if offset < 0 or offset + 12 > size:
        return False
    if require_avi_type and data[offset + 8:offset + 12] != _AVI_TYPE:
        return False

    riff_size = struct.unpack('<I', data[offset + 4:offset + 8])[0]
    if riff_size < 4:
        return False
    riff_end = offset + 8 + riff_size
    if riff_end > size:
        return False

    has_hdrl = False
    has_avih = False
    has_movi = False
    pos = offset + 12
    while pos + 8 <= riff_end:
        chunk_id = data[pos:pos + 4]
        chunk_size = struct.unpack('<I', data[pos + 4:pos + 8])[0]
        chunk_data_end = pos + 8 + chunk_size
        next_pos = chunk_data_end + (chunk_size & 1)
        if chunk_data_end > riff_end or next_pos > riff_end:
            return False

        if chunk_id == b'LIST' and chunk_size >= 4:
            list_type = data[pos + 8:pos + 12]
            if list_type == b'hdrl':
                has_hdrl = True
                has_avih = _hdrl_has_avih(data, pos, chunk_data_end)
            elif list_type == b'movi' and has_hdrl and has_avih:
                has_movi = True
        pos = next_pos

    return has_hdrl and has_avih and has_movi


def _iter_damaged_avi_hits(data: mmap.mmap, size: int):
    pos = 0
    while pos < size:
        avi_type = data.find(_AVI_TYPE, pos, size)
        if avi_type < 0:
            break
        offset = avi_type - 8
        if offset >= 0:
            damage = _different_bytes(data[offset:offset + 4], _RIFF_SIG)
            if 1 <= damage <= 2 and _has_avi_structure(data, offset, size):
                yield FileHit(
                    file_type='avi',
                    offset=offset,
                    source=_DAMAGED_AVI_SOURCE,
                    confidence=_DAMAGED_AVI_CONFIDENCE,
                )
        pos = avi_type + 1

def find_all_hits(mm: mmap.mmap) -> list[FileHit]:
    """
    디스크 이미지에서 JPEG/AVI 시작 후보를 찾아 오프셋 순으로 반환한다.

    정확한 JPEG·RIFF 시그니처를 먼저 찾고, JFIF/Exif 및 AVI type 앵커에서
    시작점을 역산한 뒤 후속 JPEG 마커·RIFF 청크 구조를 통과한 손상 헤더만
    추가한다. 고정 시그니처·앵커 순회는 O(N), 결과 정렬은 O(U log U)다.
    JPEG 후보 검증은 후보당 256 KiB 헤더와 설정된 JPEG 상한으로 제한된다.
    AVI 구조 검증은 후보의 RIFF 선언 범위를 걷기 때문에 합계 비용은 각 후보
    검사 범위의 합에 비례하며, 적대적 입력에서는 후보 수와 이미지 크기의 곱까지
    커질 수 있다.
    """
    size = len(mm)
    hits: dict[tuple[str, int], FileHit] = {}

    # JPEG 시그니처 탐색
    pos = 0
    while True:
        p = mm.find(_JPEG_SIG, pos, size)
        if p == -1:
            break
        valid, scan_start = _exact_jpeg_scan_start(mm, p, size)
        if valid:
            hit = FileHit(file_type='jpeg', offset=p, scan_start=scan_start)
            hits[(hit.file_type, hit.offset)] = hit
        pos = p + 1

    # AVI 시그니처 탐색 (RIFF + AVI  검증으로 WAV 등 제외)
    pos = 0
    while True:
        p = mm.find(_RIFF_SIG, pos, size)
        if p == -1:
            break
        if p + 12 <= size:
            form_type = mm[p + 8:p + 12]
            if form_type == _AVI_TYPE:
                hit = FileHit(file_type='avi', offset=p)
                hits[(hit.file_type, hit.offset)] = hit
            elif (
                1 <= _different_bytes(form_type, _AVI_TYPE) <= 2
                and _has_avi_structure(
                    mm,
                    p,
                    size,
                    require_avi_type=False,
                )
            ):
                hit = FileHit(
                    file_type='avi',
                    offset=p,
                    source=_DAMAGED_AVI_SOURCE,
                    confidence=_DAMAGED_AVI_CONFIDENCE,
                )
                hits[(hit.file_type, hit.offset)] = hit
        pos = p + 1

    # Exact signatures win when a structural anchor resolves to the same offset.
    exact_jpeg_offsets = sorted(
        hit.offset for hit in hits.values() if hit.file_type == 'jpeg'
    )
    exact_avi_offsets = sorted(
        hit.offset for hit in hits.values() if hit.file_type == 'avi'
    )
    damaged_avi_candidates = list(_iter_damaged_avi_hits(mm, size))
    avi_boundaries = sorted(
        set(exact_avi_offsets)
        | {hit.offset for hit in damaged_avi_candidates}
    )
    damaged_candidates: dict[int, FileHit] = {}
    for hit in _iter_damaged_jpeg_candidates(mm, size, exact_jpeg_offsets):
        previous = damaged_candidates.get(hit.offset)
        if previous is None or hit.confidence > previous.confidence:
            damaged_candidates[hit.offset] = hit

    # 후보를 모두 모은 뒤 EOI를 검증해야 앞 손상 파일이 뒤 손상 파일의 EOI를
    # 빌려 완결된 것으로 오인하지 않는다. extractor의 segment-aware 경계는
    # APP/COM payload 안의 내장 후보는 건너뛴다.
    jpeg_boundaries = sorted(
        set(exact_jpeg_offsets) | set(damaged_candidates)
    )
    for hit in sorted(damaged_candidates.values(), key=lambda item: item.offset):
        try:
            _, complete = jpeg_end(
                mm,
                hit.offset,
                allow_corrupt_header=True,
                boundary_offsets=jpeg_boundaries,
                avi_offsets=avi_boundaries,
                max_size=_JPEG_EOI_SEARCH,
                validated_scan_start=hit.scan_start,
            )
        except (ValueError, struct.error):
            complete = False
        if complete:
            hits.setdefault((hit.file_type, hit.offset), hit)
    for hit in damaged_avi_candidates:
        hits.setdefault((hit.file_type, hit.offset), hit)

    return sorted(hits.values(), key=lambda h: (h.offset, h.file_type))
