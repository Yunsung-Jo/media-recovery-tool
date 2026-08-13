import mmap
import struct
from dataclasses import FrozenInstanceError

import pytest

from media_recovery.discovery.scanner import find_all_hits
from media_recovery.domain.objects import FileHit


# ── 테스트 헬퍼 ──────────────────────────────────────────────

def make_mmap(data: bytes) -> mmap.mmap:
    """bytes를 익명 mmap으로 변환."""
    mm = mmap.mmap(-1, len(data))
    mm.write(data)
    mm.seek(0)
    return mm


def image_with(size: int, jpeg_offsets: list[int], avi_offsets: list[int]) -> bytes:
    """지정된 위치에 시그니처를 심은 더미 이미지 생성."""
    data = bytearray(b'\x00' * size)
    app0 = (
        b'\xff\xd8\xff\xe0\x00\x10JFIF\x00'
        b'\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    )
    for off in jpeg_offsets:
        data[off:off + len(app0)] = app0
    for off in avi_offsets:
        data[off:off + 4] = b'RIFF'
        if off + 12 <= size:
            data[off + 8:off + 12] = b'AVI '
    return bytes(data)


def jpeg_segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack('>H', len(payload) + 2) + payload


def damaged_jpeg(
    core: bytes,
    anchor: bytes = b'JFIF\x00',
    *,
    marker_order: tuple[int, ...] = (0xDB, 0xC0, 0xC4, 0xDA),
    include_eoi: bool = True,
) -> bytes:
    payloads = {
        0xDB: b'\x00' + b'\x01' * 64,
        0xC0: b'\x08\x00\x10\x00\x10\x01\x01\x11\x00',
        0xC4: b'\x00\x01' + b'\x00' * 15 + b'\x00',
        0xDA: b'\x01\x01\x00\x00\x3f\x00',
    }
    if anchor == b'JFIF\x00':
        app_payload = anchor + b'\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    else:
        app_payload = anchor
    body = core + struct.pack('>H', len(app_payload) + 2) + app_payload
    body += b''.join(jpeg_segment(marker, payloads[marker]) for marker in marker_order)
    body += b'\x11\x22\x33'
    if include_eoi:
        body += b'\xff\xd9'
    return body


def riff_chunk(chunk_id: bytes, payload: bytes) -> bytes:
    chunk = chunk_id + struct.pack('<I', len(payload)) + payload
    return chunk + (b'\x00' if len(payload) & 1 else b'')


def damaged_avi(
    riff_id: bytes,
    *,
    include_avih: bool = True,
    include_movi: bool = True,
) -> bytes:
    hdrl_payload = b'hdrl'
    if include_avih:
        hdrl_payload += riff_chunk(b'avih', b'\x00' * 56)
    hdrl = riff_chunk(b'LIST', hdrl_payload)
    movi = riff_chunk(b'LIST', b'movi') if include_movi else b''
    riff_payload = b'AVI ' + hdrl + movi
    return riff_id + struct.pack('<I', len(riff_payload)) + riff_payload


# ── 테스트 ──────────────────────────────────────────────────

def test_finds_jpeg():
    mm = make_mmap(image_with(1024, jpeg_offsets=[100], avi_offsets=[]))
    hits = find_all_hits(mm)
    mm.close()
    assert len(hits) == 1
    assert hits[0].file_type == 'jpeg'
    assert hits[0].offset == 100


def test_finds_avi():
    mm = make_mmap(image_with(1024, jpeg_offsets=[], avi_offsets=[200]))
    hits = find_all_hits(mm)
    mm.close()
    assert len(hits) == 1
    assert hits[0].file_type == 'avi'
    assert hits[0].offset == 200


def test_finds_both_sorted():
    mm = make_mmap(image_with(2048, jpeg_offsets=[500, 100], avi_offsets=[300]))
    hits = find_all_hits(mm)
    mm.close()
    offsets = [h.offset for h in hits]
    assert offsets == sorted(offsets)
    assert len(hits) == 3


def test_ignores_non_avi_riff():
    """WAV 등 AVI 가 아닌 RIFF 포맷은 무시한다."""
    data = bytearray(b'\x00' * 1024)
    data[100:104] = b'RIFF'
    data[108:112] = b'WAVE'  # AVI  가 아님
    mm = make_mmap(bytes(data))
    hits = find_all_hits(mm)
    mm.close()
    assert not any(h.file_type == 'avi' for h in hits)


def test_empty_image():
    mm = make_mmap(b'\x00' * 512)
    hits = find_all_hits(mm)
    mm.close()
    assert hits == []


def test_multiple_jpeg_signatures():
    """같은 이미지에 JPEG 시그니처가 여러 개 있으면 모두 반환한다."""
    mm = make_mmap(image_with(4096, jpeg_offsets=[100, 500, 1000], avi_offsets=[]))
    hits = find_all_hits(mm)
    mm.close()
    jpeg_hits = [h for h in hits if h.file_type == 'jpeg']
    assert len(jpeg_hits) == 3
    assert [h.offset for h in jpeg_hits] == [100, 500, 1000]


def test_filehit_is_frozen_slotted_and_backwards_compatible():
    hit = FileHit('jpeg', 100)
    assert hit.source == 'exact'
    assert hit.confidence == 1.0
    assert hit.scan_start is None
    assert not hasattr(hit, '__dict__')
    with pytest.raises(FrozenInstanceError):
        hit.offset = 200


@pytest.mark.parametrize(
    ('core', 'anchor'),
    [
        (b'\xfe\xd8\xff\xe0', b'JFIF\x00'),       # SOI first byte damaged
        (b'\xfb\xd9\xff\xe0', b'JFIF\x00'),       # SOI two bytes damaged
        (b'\xff\xd8\xf7\xe1', b'Exif\x00\x00'),  # APP marker prefix damaged
    ],
)
def test_finds_high_confidence_damaged_jpeg(core, anchor):
    offset = 128
    data = b'\x00' * offset + damaged_jpeg(core, anchor) + b'\x00' * 64
    mm = make_mmap(data)
    hits = find_all_hits(mm)
    mm.close()

    assert len(hits) == 1
    assert hits[0].file_type == 'jpeg'
    assert hits[0].offset == offset
    assert hits[0].source == 'damaged_jpeg_header'
    assert hits[0].confidence == 0.95
    assert hits[0].scan_start is not None
    assert data[hits[0].scan_start - 2:hits[0].scan_start] == b'\x3f\x00'


@pytest.mark.parametrize(
    'candidate',
    [
        damaged_jpeg(b'\xfa\xd9\xfe\xe0'),  # three of the four core bytes damaged
        damaged_jpeg(b'\xfb\xd9\xff\xe0', marker_order=(0xC4, 0xDB, 0xDA)),
        damaged_jpeg(b'\xfb\xd9\xff\xe0', include_eoi=False),
        b'\xfb\xd9\xff\xe0\x00\x10JFIF\x00' + b'random data only',
    ],
)
def test_rejects_weak_or_malformed_damaged_jpeg_candidates(candidate):
    mm = make_mmap(b'\x00' * 32 + candidate + b'\x00' * 32)
    hits = find_all_hits(mm)
    mm.close()
    assert not any(h.source == 'damaged_jpeg_header' for h in hits)


def test_damaged_jpeg_does_not_borrow_eoi_from_next_exact_file():
    damaged_without_eoi = damaged_jpeg(
        b'\xfb\xd9\xff\xe0',
        include_eoi=False,
    )
    following = damaged_jpeg(b'\xff\xd8\xff\xe0')
    data = damaged_without_eoi + b'\x00' * 16 + following
    following_offset = len(damaged_without_eoi) + 16

    mm = make_mmap(data)
    hits = find_all_hits(mm)
    mm.close()

    assert hits == [FileHit('jpeg', following_offset)]


def test_damaged_jpeg_does_not_borrow_eoi_from_next_damaged_file():
    first = damaged_jpeg(b'\xfb\xd9\xff\xe0', include_eoi=False)
    second = damaged_jpeg(b'\xfb\xd9\xff\xe0')
    second_offset = len(first) + 64
    data = first + b'\x00' * 64 + second

    mm = make_mmap(data)
    hits = find_all_hits(mm)
    mm.close()

    assert len(hits) == 1
    assert hits[0].offset == second_offset
    assert hits[0].source == 'damaged_jpeg_header'


def test_damaged_jpeg_does_not_borrow_eoi_from_damaged_avi_frame():
    first = damaged_jpeg(b'\xfb\xd9\xff\xe0', include_eoi=False)
    marker_stream = (
        jpeg_segment(0xDB, b'\x00' + b'\x01' * 64)
        + jpeg_segment(0xC0, b'\x08\x00\x10\x00\x10\x01\x01\x11\x00')
        + jpeg_segment(0xC4, b'\x00\x01' + b'\x00' * 15 + b'\x00')
        + jpeg_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
        + b'\x11\x22\xff\xd9'
    )
    hdrl = riff_chunk(b'LIST', b'hdrl' + riff_chunk(b'avih', b'\x00' * 56))
    movi = riff_chunk(b'LIST', b'movi' + riff_chunk(b'00dc', marker_stream))
    payload = b'AVI ' + hdrl + movi
    damaged_container = b'RIFX' + struct.pack('<I', len(payload)) + payload
    avi_offset = len(first) + 64
    data = first + b'\x00' * 64 + damaged_container

    mm = make_mmap(data)
    hits = find_all_hits(mm)
    mm.close()

    assert hits == [
        FileHit(
            'avi',
            avi_offset,
            source='damaged_avi_header',
            confidence=0.98,
        )
    ]


def test_exact_jpeg_wins_over_anchor_detection_without_duplicate():
    data = damaged_jpeg(b'\xff\xd8\xff\xe0')
    mm = make_mmap(data)
    hits = find_all_hits(mm)
    mm.close()

    assert hits == [FileHit('jpeg', 0)]


@pytest.mark.parametrize('riff_id', [b'RIFX', b'RXXF'])
def test_finds_structurally_valid_avi_with_damaged_riff(riff_id):
    offset = 96
    data = b'\x00' * offset + damaged_avi(riff_id) + b'\x00' * 32
    mm = make_mmap(data)
    hits = find_all_hits(mm)
    mm.close()

    assert hits == [
        FileHit('avi', offset, source='damaged_avi_header', confidence=0.98)
    ]


@pytest.mark.parametrize('form_type', [b'AVX ', b'XXI '])
def test_finds_structurally_valid_avi_with_damaged_form_type(form_type):
    offset = 96
    candidate = bytearray(damaged_avi(b'RIFF'))
    candidate[8:12] = form_type
    data = b'\x00' * offset + bytes(candidate) + b'\x00' * 32

    mm = make_mmap(data)
    hits = find_all_hits(mm)
    mm.close()

    assert hits == [
        FileHit('avi', offset, source='damaged_avi_header', confidence=0.98)
    ]


@pytest.mark.parametrize(
    'candidate',
    [
        damaged_avi(b'XXXF'),  # three RIFF bytes damaged
        damaged_avi(b'RIFX', include_avih=False),
        damaged_avi(b'RIFX', include_movi=False),
        b'RIFX\x20\x00\x00\x00AVI LISTxxxxhdrlavihLISTxxxxmovi',
        b'prefix AVI suffix',
    ],
)
def test_rejects_internal_or_weak_avi_anchors(candidate):
    mm = make_mmap(b'\x00' * 64 + candidate + b'\x00' * 64)
    hits = find_all_hits(mm)
    mm.close()
    assert not any(h.source == 'damaged_avi_header' for h in hits)


def test_exact_avi_wins_over_structural_detection_without_duplicate():
    data = damaged_avi(b'RIFF')
    mm = make_mmap(data)
    hits = find_all_hits(mm)
    mm.close()

    assert hits == [FileHit('avi', 0)]


def test_mixed_exact_and_damaged_hits_are_sorted():
    damaged_avi_data = damaged_avi(b'RIFX')
    inferred_jpeg = damaged_jpeg(b'\xfb\xd9\xff\xe0')
    data = bytearray(b'\x00' * 2048)
    data[700:700 + len(damaged_avi_data)] = damaged_avi_data
    data[300:300 + len(inferred_jpeg)] = inferred_jpeg
    exact = image_with(64, jpeg_offsets=[0], avi_offsets=[])
    data[100:100 + len(exact)] = exact

    mm = make_mmap(bytes(data))
    hits = find_all_hits(mm)
    mm.close()

    assert [(hit.file_type, hit.offset) for hit in hits] == [
        ('jpeg', 100),
        ('jpeg', 300),
        ('avi', 700),
    ]


def test_rejects_exact_soi_followed_by_invalid_entropy_byte():
    mm = make_mmap(b'prefix\xff\xd8\xff\x15entropy suffix')
    hits = find_all_hits(mm)
    mm.close()
    assert hits == []


@pytest.mark.parametrize(
    'candidate',
    [
        b'\xff\xd8\xff\xe0\x00\x01',
        b'\xff\xd8\xff\xfe\xff\xffshort',
    ],
)
def test_rejects_exact_app_or_com_with_invalid_length(candidate):
    mm = make_mmap(candidate)
    hits = find_all_hits(mm)
    mm.close()
    assert hits == []


def test_accepts_exact_app_less_coherent_header_and_records_scan_start():
    candidate = b'\xff\xd8'
    candidate += jpeg_segment(0xDB, b'\x00' + b'\x01' * 64)
    candidate += jpeg_segment(0xC0, b'\x08\x00\x10\x00\x10\x01\x01\x11\x00')
    candidate += jpeg_segment(0xC4, b'\x00\x01' + b'\x00' * 15 + b'\x00')
    candidate += jpeg_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    candidate += b'\x11\xff\xd9'

    mm = make_mmap(candidate)
    hits = find_all_hits(mm)
    mm.close()

    assert len(hits) == 1
    assert hits[0].offset == 0
    assert hits[0].source == 'exact'
    assert hits[0].scan_start == candidate.index(b'\x11\xff\xd9')


def test_accepts_exact_soi_with_marker_fill_ff():
    """SOI 다음 marker prefix의 합법적인 FF fill byte를 허용한다."""
    candidate = damaged_jpeg(b'\xff\xd8\xff\xe0')
    candidate = candidate[:3] + b'\xff' + candidate[3:]

    mm = make_mmap(candidate)
    hits = find_all_hits(mm)
    mm.close()

    assert len(hits) == 1
    assert hits[0].offset == 0
    assert hits[0].source == 'exact'


def test_exact_valid_app_length_survives_later_header_damage():
    candidate = (
        b'\xff\xd8\xff\xe0\x00\x10JFIF\x00'
        b'\x01\x01\x00\x00\x01\x00\x01\x00\x00'
        b'broken header after the valid APP segment'
    )
    mm = make_mmap(candidate)
    hits = find_all_hits(mm)
    mm.close()
    assert hits == [FileHit('jpeg', 0)]


def test_exact_valid_dht_length_survives_later_header_damage():
    """정확 SOI와 합법 DHT 길이는 후속 내용이 손상돼도 복구 후보로 남긴다."""
    candidate = (
        b'\xff\xd8\xff\xc4\x00\x13'
        + b'\x00' * 17
        + b'broken header after the first segment'
    )
    mm = make_mmap(candidate)
    hits = find_all_hits(mm)
    mm.close()

    assert hits == [FileHit('jpeg', 0)]


def test_rejects_unaligned_non_contiguous_damaged_header():
    candidate = damaged_jpeg(b'\xfb\xd9\xff\xe0')
    app_end = 20
    candidate = candidate[:app_end] + b'junk' + candidate[app_end:]
    offset = 128
    mm = make_mmap(b'\x00' * offset + candidate)
    hits = find_all_hits(mm)
    mm.close()
    assert not any(hit.source == 'damaged_jpeg_header' for hit in hits)


def test_rejects_aligned_resync_candidate_with_excessive_marker_gap():
    candidate = damaged_jpeg(b'\xfb\xd9\xff\xe0')
    app_end = 20
    candidate = candidate[:app_end] + b'\x00' * 4097 + candidate[app_end:]
    offset = 4096
    mm = make_mmap(b'\x00' * offset + candidate)
    hits = find_all_hits(mm)
    mm.close()
    assert not any(hit.source == 'damaged_jpeg_header' for hit in hits)


def test_aligned_tolerant_resync_records_scan_start_at_lower_confidence():
    candidate = bytearray(damaged_jpeg(b'\xfb\xd9\xff\xe0'))
    candidate[4:6] = b'\x01\x10'  # damaged APP length defeats strict walking
    sof = candidate.index(b'\xff\xc0')
    candidate[sof + 2] = 0x80  # one repairable SOF length byte
    offset = 4096

    mm = make_mmap(b'\x00' * offset + bytes(candidate))
    hits = find_all_hits(mm)
    mm.close()

    assert len(hits) == 1
    assert hits[0].source == 'damaged_jpeg_header'
    assert hits[0].confidence == 0.85
    assert hits[0].scan_start is not None


@pytest.mark.parametrize('offset', [128, 4096])
def test_exact_core_with_damaged_app_length_uses_tolerant_resync(offset):
    """SOI/APP core가 정상이어도 APP length 손상 시 구조 resync로 보존한다."""
    candidate = bytearray(damaged_jpeg(b'\xff\xd8\xff\xe0'))
    candidate[4:6] = b'\xff\xff'
    mm = make_mmap(b'\x00' * offset + bytes(candidate))
    hits = find_all_hits(mm)
    mm.close()

    assert hits == [
        FileHit(
            'jpeg',
            offset,
            source='damaged_jpeg_header',
            confidence=0.85,
            scan_start=offset + candidate.index(b'\x11\x22\x33'),
        )
    ]


def test_tolerant_resync_accepts_dht_larger_than_1200_bytes():
    """여러 Huffman 표를 담을 수 있는 1200바이트 초과 DHT를 누락하지 않는다."""
    candidate = bytearray(damaged_jpeg(b'\xff\xd8\xff\xe0'))
    candidate[4:6] = b'\xff\xff'
    dht_start = candidate.index(b'\xff\xc4')
    dht_length = struct.unpack('>H', candidate[dht_start + 2:dht_start + 4])[0]
    dht_end = dht_start + 2 + dht_length
    candidate[dht_start:dht_end] = jpeg_segment(0xC4, b'\x00' * 1300)
    offset = 4096

    mm = make_mmap(b'\x00' * offset + bytes(candidate))
    hits = find_all_hits(mm)
    mm.close()

    assert len(hits) == 1
    assert hits[0].source == 'damaged_jpeg_header'
    assert hits[0].confidence == 0.85


def test_eoi_inside_com_payload_is_not_entropy_termination():
    candidate = damaged_jpeg(b'\xfb\xd9\xff\xe0', include_eoi=False)
    fake_eoi_in_com = jpeg_segment(0xFE, b'prefix\xff\xd9suffix')
    candidate += fake_eoi_in_com
    offset = 128

    mm = make_mmap(b'\x00' * offset + candidate)
    hits = find_all_hits(mm)
    mm.close()
    assert not any(hit.source == 'damaged_jpeg_header' for hit in hits)

    candidate += b'\x11\x22\xff\xd9'
    mm = make_mmap(b'\x00' * offset + candidate)
    hits = find_all_hits(mm)
    mm.close()
    assert len(hits) == 1
    assert hits[0].source == 'damaged_jpeg_header'


@pytest.mark.parametrize('include_outer_eoi', [False, True])
def test_damaged_progressive_parent_skips_exact_jpeg_inside_app1(include_outer_eoi):
    """손상 부모 gate는 inter-scan APP1 child의 EOI를 빌리지 않는다."""
    app_payload = b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    outer = b'\xfb\xd9\xff\xe0' + struct.pack('>H', len(app_payload) + 2) + app_payload
    outer += jpeg_segment(0xDB, b'\x00' + b'\x01' * 64)
    outer += jpeg_segment(0xC2, b'\x08\x00\x10\x00\x10\x01\x01\x11\x00')
    outer += jpeg_segment(0xC4, b'\x00\x01' + b'\x00' * 15 + b'\x00')
    outer += jpeg_segment(0xDA, b'\x01\x01\x00\x00\x00\x00') + b'\x11\x22'
    child = damaged_jpeg(b'\xff\xd8\xff\xe0')
    outer += jpeg_segment(0xE1, b'Exif\x00\x00' + child)
    outer += jpeg_segment(0xDA, b'\x01\x01\x00\x01\x3f\x00') + b'\x33\x44'
    child_offset = outer.index(child)
    if include_outer_eoi:
        outer += b'\xff\xd9'

    mm = make_mmap(outer)
    hits = find_all_hits(mm)
    mm.close()

    assert any(hit.offset == child_offset and hit.source == 'exact' for hit in hits)
    assert any(hit.offset == 0 and hit.source == 'damaged_jpeg_header' for hit in hits) is include_outer_eoi
