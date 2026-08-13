import mmap
import struct
import pytest
from media_recovery.formats.boundaries import (
    JPEG_MAX_FALLBACK_SIZE,
    MAX_AVI_SIZE_DEFAULT,
    avi_end,
    jpeg_end,
)


# ── 테스트 헬퍼 ──────────────────────────────────────────────

def make_app_segment(marker_byte: int, payload: bytes) -> bytes:
    """마커 + 길이 + 페이로드 형식의 JPEG 세그먼트 생성."""
    length = len(payload) + 2  # 길이 필드 자신(2바이트) 포함
    return bytes([0xFF, marker_byte]) + struct.pack('>H', length) + payload


def make_jpeg(*segments: bytes, include_eoi: bool = True) -> bytes:
    """SOI + 세그먼트들 + EOI 형식의 최소 JPEG 생성."""
    data = b'\xff\xd8'
    for seg in segments:
        data += seg
    if include_eoi:
        data += b'\xff\xd9'
    return data


# APP0(JFIF) 세그먼트 — 실제 카메라 JPEG에 흔히 등장
APP0 = make_app_segment(0xE0, b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00')


# ── jpeg_end 테스트 ──────────────────────────────────────────

def test_jpeg_end_simple():
    """단순 SOI + APP0 + EOI 구조를 정확히 파싱한다."""
    data = make_jpeg(APP0)
    end, complete = jpeg_end(data, 0)
    assert end == len(data)
    assert complete is True


def test_jpeg_end_offset_not_zero():
    """오프셋 0이 아닌 위치에서도 올바르게 동작한다."""
    prefix = b'\x00' * 100
    data = prefix + make_jpeg(APP0)
    end, complete = jpeg_end(data, 100)
    assert end == len(data)
    assert complete is True


def test_jpeg_end_skips_embedded_thumbnail():
    """APP1 내부의 임베디드 썸네일 FF D9를 EOI로 오판하지 않는다."""
    thumbnail = b'\xff\xd8\xff\xd9'  # 내장 썸네일 (FF D9 포함)
    app1_payload = b'Exif\x00\x00' + thumbnail + b'\x00' * 8
    app1 = make_app_segment(0xE1, app1_payload)
    data = make_jpeg(app1)  # 진짜 EOI는 맨 끝
    end, complete = jpeg_end(data, 0)
    assert end == len(data)
    assert complete is True


def test_jpeg_end_no_eoi_uses_next_sig():
    """EOI가 없으면 next_sig_offset을 fallback으로 사용한다."""
    data = make_jpeg(APP0, include_eoi=False) + b'\x00' * 200
    next_sig = len(data) - 50
    end, complete = jpeg_end(data, 0, next_sig)
    assert end == next_sig
    assert complete is False


def test_jpeg_end_no_eoi_no_fallback_uses_size_limit():
    """EOI도 없고 next_sig도 없으면 10 MB 상한을 적용한다."""
    data = make_jpeg(APP0, include_eoi=False) + b'\x00' * 10
    end, complete = jpeg_end(data, 0)
    assert end == min(JPEG_MAX_FALLBACK_SIZE, len(data))
    assert complete is False


def test_jpeg_end_corrupt_segment_triggers_fallback():
    """세그먼트 길이 필드가 1(비정상)이면 fallback으로 전환한다."""
    corrupt = b'\xff\xe0\x00\x01'  # 길이=1, JPEG 스펙상 최솟값은 2
    data = b'\xff\xd8' + corrupt + b'\xff\xd9'
    end, complete = jpeg_end(data, 0, next_sig_offset=50)
    assert complete is False


def test_jpeg_end_raises_on_missing_soi():
    """SOI(FF D8)가 없으면 ValueError를 발생시킨다."""
    data = b'\x00' * 20
    with pytest.raises(ValueError, match='SOI'):
        jpeg_end(data, 0)


def test_jpeg_end_with_scan_data():
    """SOS 이후 스캔 데이터에서 stuffed byte(FF 00)를 올바르게 처리한다."""
    # SOS 세그먼트 헤더: marker + length + 최소 헤더 내용
    # SOS header: FF DA, length=12, 1 component, component spec, Ss, Se, Ah/Al
    sos_header_payload = b'\x01\x01\x00\x00\x3f\x00'  # 6바이트 payload
    sos_seg = make_app_segment(0xDA, sos_header_payload)
    # 스캔 데이터: FF 00 (stuffed byte), 일반 데이터, FF D9 (EOI)
    scan_data = b'\x10\x20\xff\x00\x30\x40\xff\xd9'
    data = b'\xff\xd8' + sos_seg + scan_data
    end, complete = jpeg_end(data, 0)
    assert complete is True
    assert end == len(data)


def test_jpeg_end_scan_data_with_rst():
    """스캔 데이터 내 RST 마커(FF D0~D7)를 건너뛰고 EOI를 찾는다."""
    sos_header_payload = b'\x01\x01\x00\x00\x3f\x00'
    sos_seg = make_app_segment(0xDA, sos_header_payload)
    # 스캔 데이터: RST0(FF D0), 데이터, RST1(FF D1), 데이터, EOI
    scan_data = b'\x10\xff\xd0\x20\xff\xd1\x30\xff\xd9'
    data = b'\xff\xd8' + sos_seg + scan_data
    end, complete = jpeg_end(data, 0)
    assert complete is True
    assert end == len(data)


def test_jpeg_end_skips_fake_eoi_in_entropy():
    """엔트로피 중 stuffing이 깨져 생긴 가짜 FF D9를 건너뛰고 진짜 EOI를 찾는다."""
    sos_seg = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    fake_eoi = b'\xff\xd9'
    entropy = b'\xff\x00' * 100            # 직후 stuffing 100% = 엔트로피 연속(가짜)
    real_eoi = b'\xff\xd9'
    padding = b'\x00' * 200                # 직후 stuffing 0% = 진짜 EOI
    scan = b'\x10\x20' + fake_eoi + entropy + real_eoi + padding
    data = b'\xff\xd8' + sos_seg + scan
    end, complete = jpeg_end(data, 0)
    assert complete is True
    expected = len(b'\xff\xd8' + sos_seg + b'\x10\x20' + fake_eoi + entropy + real_eoi)
    assert end == expected


def test_jpeg_end_genuine_eoi_followed_by_padding():
    """EOI 직후가 패딩(낮은 stuffing)이면 첫 EOI를 그대로 진짜로 채택한다."""
    sos_seg = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    scan = b'\x10\x20\x30' + b'\xff\xd9' + b'\x00' * 300
    data = b'\xff\xd8' + sos_seg + scan
    end, complete = jpeg_end(data, 0)
    assert complete is True
    assert end == len(b'\xff\xd8' + sos_seg + b'\x10\x20\x30' + b'\xff\xd9')


def test_jpeg_end_sparse_ff_padding_does_not_reject_real_eoi():
    """패딩에 소수의 FF00만 있어도 작은 표본 비율로 EOI를 기각하지 않는다."""
    sos_seg = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    padding = (b'\x00' * 200 + b'\xff\x00') * 6
    data = b'\xff\xd8' + sos_seg + b'\x10\x20\xff\xd9' + padding

    end, complete = jpeg_end(data, 0)

    assert end == len(b'\xff\xd8' + sos_seg + b'\x10\x20\xff\xd9')
    assert complete is True


def test_jpeg_end_fake_eoi_until_next_sig_falls_back():
    """진짜 EOI 없이 가짜만 이어지면 next_sig 상한에서 fallback한다."""
    sos_seg = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    fake_eoi = b'\xff\xd9'
    entropy = b'\xff\x00' * 100
    scan = b'\x10\x20' + fake_eoi + entropy * 3   # 진짜 EOI 없음(전부 엔트로피 연속)
    data = b'\xff\xd8' + sos_seg + scan
    next_sig = len(data)
    end, complete = jpeg_end(data, 0, next_sig_offset=next_sig)
    assert complete is False
    assert end == next_sig


def test_jpeg_end_trusted_next_sig_does_not_override_strong_fake_eoi():
    """가까운 다음 JPEG가 있어도 강한 stuffing 뒤의 EOI는 가짜로 본다."""
    sos_seg = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    fake_eoi = b'\xff\xd9'
    entropy = b'\xff\x00' * 100
    truncated = b'\xff\xd8' + sos_seg + b'\x10\x20' + fake_eoi + entropy
    data = truncated + make_baseline_jpeg_without_app()

    end, complete = jpeg_end(data, 0, next_sig_offset=len(truncated))

    assert end == len(truncated)
    assert complete is False


def test_stuffing_ratio_distinguishes_entropy_from_padding():
    """_stuffing_ratio: 엔트로피(높음) vs 패딩/헤더(낮음)을 구분한다."""
    from media_recovery.formats.boundaries import _stuffing_ratio
    assert _stuffing_ratio(b'\xff\x00' * 50) == 1.0           # 전부 stuffing
    assert _stuffing_ratio(b'\xff\xd0\xff\xd7' * 20) == 1.0   # 전부 RST
    assert _stuffing_ratio(b'\x00' * 100) == 0.0              # FF 없음(패딩)
    assert _stuffing_ratio(b'\xff\xe0\xff\xe1' * 20) == 0.0   # FF 다음 APPn(헤더)


# ── jpeg_end 과다 카빙 방지(손상 헤더 경계) 테스트 ───────────────

# 최소 SOF0: precision 8, 16x16, 1 컴포넌트 — mb=0xC0이라 saw_sof를 세운다
SOF0 = make_app_segment(0xC0, b'\x08\x00\x10\x00\x10\x01\x01\x11\x00')
# 임베디드 진짜 이미지 헤더(FF D8 FF E0 + JFIF): _next_header가 찾는 다음 파일 경계
EMBEDDED = b'\xff\xd8\xff\xe0' + struct.pack('>H', 16) + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00' + b'\xff\xd9'


def make_baseline_jpeg_without_app() -> bytes:
    """APPn 없이 DQT로 시작하는, 8x8 grayscale baseline JPEG.

    최소 DC/AC Huffman 표에서 category 0과 EOB에 각각 1비트 코드 0을 배정한다.
    엔트로피 바이트 0x3F는 DC=0, EOB 뒤를 1로 패딩한 한 블록이다.
    """
    dqt = make_app_segment(0xDB, b'\x00' + b'\x01' * 64)
    sof = make_app_segment(0xC0, b'\x08\x00\x08\x00\x08\x01\x01\x11\x00')
    counts = bytes([1] + [0] * 15)
    dht = make_app_segment(
        0xC4,
        b'\x00' + counts + b'\x00' + b'\x10' + counts + b'\x00',
    )
    sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    return b'\xff\xd8' + dqt + sof + dht + sos + b'\x3f\xff\xd9'


def test_jpeg_end_truncated_stops_at_next_jpeg_without_app_marker():
    """APPn은 JPEG 필수 세그먼트가 아니다. 절단 JPEG 뒤의 정상 JPEG가 DQT로
    시작해도 그 시작점에서 경계를 끊어 다음 파일을 삼키지 않는다."""
    sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    truncated = b'\xff\xd8' + APP0 + SOF0 + sos + b'\x12\x34' * 40
    next_jpeg = make_baseline_jpeg_without_app()
    assert next_jpeg.startswith(b'\xff\xd8\xff\xdb')

    data = truncated + next_jpeg + b'\x00' * 200
    end, complete = jpeg_end(data, 0, next_sig_offset=len(truncated))

    assert end == len(truncated)
    assert complete is False


def test_jpeg_end_damaged_boundary_index_does_not_hide_earlier_exact_jpeg():
    """후방 손상 후보 인덱스가 있어도 그보다 앞 정상 SOI가 우선 경계다."""
    sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    truncated = b'\xff\xd8' + APP0 + SOF0 + sos + b'\x12\x34' * 40
    next_jpeg = make_baseline_jpeg_without_app()
    damaged_offset = len(truncated) + len(next_jpeg) + 128
    data = truncated + next_jpeg + b'\x00' * 256

    end, complete = jpeg_end(
        data,
        0,
        next_sig_offset=len(truncated),
        boundary_offsets=(damaged_offset,),
    )

    assert end == len(truncated)
    assert complete is False


def test_jpeg_end_multiscan_skips_eoi_bytes_inside_com_payload():
    """첫 SOS 뒤의 COM payload에 든 FF D9는 데이터이지 이미지 EOI가 아니다.
    뒤따르는 두 번째 SOS를 파싱해 마지막 실제 EOI까지 진행한다."""
    sof2 = make_app_segment(0xC2, b'\x08\x00\x08\x00\x08\x01\x01\x11\x00')
    dc_sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x00\x00')
    ac_sos = make_app_segment(0xDA, b'\x01\x01\x00\x01\x3f\x00')
    false_eoi = b'\xff\xd9'
    comment = make_app_segment(0xFE, b'metadata' + false_eoi + b'\x00' * 200)
    data = (
        b'\xff\xd8' + APP0 + sof2
        + dc_sos + b'\x12\x34' * 10
        + comment
        + ac_sos + b'\x56\x78' * 10
        + b'\xff\xd9'
        + b'\x00' * 200
    )
    false_end = data.index(false_eoi) + 2
    real_end = data.rindex(b'\xff\xd9') + 2

    end, complete = jpeg_end(data, 0)

    assert end != false_end
    assert end == real_end
    assert complete is True


def test_jpeg_end_accepts_fill_bytes_before_entropy_eoi():
    """EOI marker 앞의 합법적인 FF fill run도 원래 시작부터 처리한다."""
    sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    data = b'\xff\xd8' + APP0 + SOF0 + sos + b'entropy\xff\xff\xd9' + b'\x00' * 64

    end, complete = jpeg_end(data, 0)

    assert end == data.index(b'\xff\xff\xd9') + 3
    assert complete is True


def test_jpeg_end_multiscan_skips_eoi_bytes_inside_app1_payload():
    """scan 사이 APP1 payload의 FF D9도 실제 EOI로 오인하지 않는다."""
    sof2 = make_app_segment(0xC2, b'\x08\x00\x08\x00\x08\x01\x01\x11\x00')
    first_sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x00\x00')
    second_sos = make_app_segment(0xDA, b'\x01\x01\x00\x01\x3f\x00')
    false_eoi = b'\xff\xd9'
    app1 = make_app_segment(0xE1, b'Exif\x00\x00' + false_eoi + b'\x00' * 200)
    data = (
        b'\xff\xd8' + APP0 + sof2
        + first_sos + b'\x12\x34' * 10
        + app1 + second_sos + b'\x56\x78' * 10
        + b'\xff\xd9' + b'\x00' * 200
    )

    end, complete = jpeg_end(data, 0)

    assert end == data.rindex(b'\xff\xd9') + 2
    assert complete is True


def test_jpeg_end_multiscan_does_not_stop_at_jpeg_inside_app1_payload():
    """scan 사이 APP1의 내장 JPEG SOI도 바깥 JPEG 경계가 아니다."""
    sof2 = make_app_segment(0xC2, b'\x08\x00\x08\x00\x08\x01\x01\x11\x00')
    first_sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x00\x00')
    second_sos = make_app_segment(0xDA, b'\x01\x01\x00\x01\x3f\x00')
    child = make_baseline_jpeg_without_app()
    app1 = make_app_segment(0xE1, b'Exif\x00\x00' + child + b'\x00' * 16)
    data = (
        b'\xff\xd8' + APP0 + sof2
        + first_sos + b'\x12\x34' * 10
        + app1 + second_sos + b'\x56\x78' * 10
        + b'\xff\xd9' + b'\x00' * 200
    )

    end, complete = jpeg_end(data, 0)

    assert end == data.rindex(b'\xff\xd9') + 2
    assert complete is True


def test_jpeg_end_final_scan_skips_jpeg_inside_trailing_app1():
    """마지막 scan 뒤 APP1 payload도 건너뛰고 바깥 EOI를 채택한다."""
    child = make_baseline_jpeg_without_app()
    trailing_app1 = make_app_segment(0xE1, b'Exif\x00\x00' + child + b'\x00' * 16)
    data = (
        b'\xff\xd8' + APP0 + SOF0
        + make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
        + b'\x12\x34' * 10
        + trailing_app1
        + b'\xff\xd9' + b'\x00' * 200
    )

    end, complete = jpeg_end(data, 0)

    assert end == data.rindex(b'\xff\xd9') + 2
    assert complete is True


def test_jpeg_end_multiscan_skips_indexed_damaged_jpeg_inside_app1():
    """구조 인덱스의 손상 JPEG도 inter-scan APP1 안이면 경계가 아니다."""
    sof2 = make_app_segment(0xC2, b'\x08\x00\x08\x00\x08\x01\x01\x11\x00')
    first_sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x00\x00')
    second_sos = make_app_segment(0xDA, b'\x01\x01\x00\x01\x3f\x00')
    damaged_child = b'\xfb\xd9\xff\xe0\x00\x10JFIF\x00' + b'\x00' * 32
    app1 = make_app_segment(0xE1, b'Exif\x00\x00' + damaged_child)
    data = (
        b'\xff\xd8' + APP0 + sof2
        + first_sos + b'\x12\x34' * 10
        + app1 + second_sos + b'\x56\x78' * 10
        + b'\xff\xd9' + b'\x00' * 200
    )
    child_offset = data.index(damaged_child)

    end, complete = jpeg_end(data, 0, boundary_offsets=(child_offset,))

    assert end == data.rindex(b'\xff\xd9') + 2
    assert complete is True


def test_jpeg_end_multiscan_skips_avi_inside_app1():
    """inter-scan APP1 payload의 RIFF/AVI 구조도 외부 파일 경계가 아니다."""
    sof2 = make_app_segment(0xC2, b'\x08\x00\x08\x00\x08\x01\x01\x11\x00')
    first_sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x00\x00')
    second_sos = make_app_segment(0xDA, b'\x01\x01\x00\x01\x3f\x00')
    nested_avi = make_avi(chunk_size=16, extra=b'\x00' * 12)
    app1 = make_app_segment(0xE1, b'Exif\x00\x00' + nested_avi)
    data = (
        b'\xff\xd8' + APP0 + sof2
        + first_sos + b'\x12\x34' * 10
        + app1 + second_sos + b'\x56\x78' * 10
        + b'\xff\xd9' + b'\x00' * 200
    )
    avi_offset = data.index(nested_avi)

    end, complete = jpeg_end(data, 0, avi_offsets=(avi_offset,))

    assert end == data.rindex(b'\xff\xd9') + 2
    assert complete is True


def test_jpeg_end_multiscan_supports_more_than_sixteen_misc_markers():
    """긴 합법 inter-scan marker 연쇄도 고정 개수 제한 없이 건너뛴다."""
    sof2 = make_app_segment(0xC2, b'\x08\x00\x08\x00\x08\x01\x01\x11\x00')
    first_sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x00\x00')
    second_sos = make_app_segment(0xDA, b'\x01\x01\x00\x01\x3f\x00')
    child = make_baseline_jpeg_without_app()
    app1 = make_app_segment(0xE1, b'Exif\x00\x00' + child)
    comments = b''.join(make_app_segment(0xFE, bytes([index])) for index in range(16))
    data = (
        b'\xff\xd8' + APP0 + sof2
        + first_sos + b'\x12\x34'
        + app1 + comments + second_sos + b'\x56\x78'
        + b'\xff\xd9' + b'\x00' * 200
    )

    end, complete = jpeg_end(data, 0)

    assert end == data.rindex(b'\xff\xd9') + 2
    assert complete is True


def test_jpeg_end_truncated_scan_stops_at_indexed_dht_start_jpeg():
    """후속 헤더가 일부 손상돼도 스캐너 hit이면 앞 JPEG의 경계로 보존한다."""
    sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    first = b'\xff\xd8' + APP0 + SOF0 + sos + b'\x12\x34' * 20
    dht = make_app_segment(0xC4, b'\x00' + b'\x00' * 16)
    second = b'\xff\xd8' + dht + b'broken\xff\xd9' + b'\x00' * 32
    data = first + second

    end, complete = jpeg_end(
        data,
        0,
        next_sig_offset=len(first),
        boundary_offsets=(0, len(first)),
    )

    assert end == len(first)
    assert complete is False


def test_jpeg_end_exif_header_payload_may_contain_avi_signature():
    """유효 길이 Exif APP1 안의 RIFF/AVI 바이트는 metadata로 보호한다."""
    nested_avi = make_avi(chunk_size=16, extra=b'\x00' * 12)
    app1 = make_app_segment(0xE1, b'Exif\x00\x00' + nested_avi)
    data = b'\xff\xd8' + app1 + b'\xff\xd9' + b'\x00' * 200
    avi_offset = data.index(nested_avi)

    end, complete = jpeg_end(data, 0, avi_offsets=(avi_offset,))

    assert end == data.index(b'\xff\xd9', avi_offset + len(nested_avi)) + 2
    assert complete is True


def test_jpeg_end_uses_validated_scan_start_for_damaged_header():
    """스캐너가 검증한 SOS를 사용해 앞쪽 가짜 SOS/EOI를 건너뛴다."""
    fake_sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    real_sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    prefix = b'\xfb\xd9\xff\xe0\x00\x10JFIF\x00' + fake_sos + b'\x11\xff\xd9'
    real_sos_offset = len(prefix) + 128
    data = prefix + b'\x00' * 128 + real_sos + b'\x22\x33\xff\xd9' + b'\x00' * 200
    scan_start = real_sos_offset + len(real_sos)

    end, complete = jpeg_end(
        data,
        0,
        allow_corrupt_header=True,
        validated_scan_start=scan_start,
    )

    assert end == data.rindex(b'\xff\xd9') + 2
    assert complete is True


def test_next_header_rejects_semantically_empty_direct_segments():
    """길이 2인 DQT/SOF/DHT/SOS 연쇄는 JPEG 경계 근거가 아니다."""
    from media_recovery.formats.boundaries import _next_header

    invalid = (
        b'\xff\xd8'
        + b'\xff\xdb\x00\x02'
        + b'\xff\xc0\x00\x02'
        + b'\xff\xc4\x00\x02'
        + b'\xff\xda\x00\x02'
    )
    data = invalid + b'\x00' * 32

    assert _next_header(data, 0, len(data)) == len(data)


def test_jpeg_end_corrupt_dht_length_bounds_at_next_header():
    """SOF 뒤 DHT 길이가 합법 상한을 넘으면 다음 진짜 헤더로 경계를 축소해
    그 안의 임베디드 이미지를 삼키지 않는다(과다 카빙 방지)."""
    dht_payload = b'\x00' * 80 + EMBEDDED + b'\x00' * 2200
    corrupt_dht = b'\xff\xc4' + struct.pack('>H', len(dht_payload) + 2) + dht_payload
    data = b'\xff\xd8' + APP0 + SOF0 + corrupt_dht + b'\x00' * 8
    emb_off = data.index(EMBEDDED)
    end, complete = jpeg_end(data, 0, next_sig_offset=len(data))
    assert end == emb_off          # 손상 DHT를 따라 점프하지 않고 임베디드 헤더에서 경계
    assert complete is False


def test_jpeg_end_bad_marker_before_sof_uses_next_sig():
    """SOF 도달 전(=유효 이미지 아님, 위양성) 0xC0 미만 바이트를 만나면 다음 시그니처로
    타이트하게 잡는다(garbage가 10MB로 커지는 것 방지 — SOF-게이트)."""
    data = b'\xff\xd8' + APP0 + b'\xff\x41\x00\x10' + b'\x00' * 200  # FF41: 0x41<0xC0, SOF 전
    end, complete = jpeg_end(data, 0, next_sig_offset=30)
    assert end == 30               # SOF 미도달 → next_sig
    assert complete is False


def test_corrupt_boundary_does_not_move_back_into_consumed_app1():
    """next_sig가 이미 소비한 APP1 썸네일이면 경계를 뒤로 되감지 않는다."""
    thumbnail = make_baseline_jpeg_without_app()
    app1 = make_app_segment(0xE1, b'Exif\x00\x00' + thumbnail + b'\x00' * 8)
    corrupt = b'\xff\x41\x00\x10' + b'\x00' * 32
    following = make_jpeg(APP0)
    data = b'\xff\xd8' + app1 + corrupt + following
    thumb_offset = data.index(thumbnail)
    following_offset = data.index(following, thumb_offset + len(thumbnail))

    end, complete = jpeg_end(data, 0, next_sig_offset=thumb_offset)

    assert end == following_offset
    assert complete is False


def test_jpeg_end_entropy_ff00_in_header_bounds_at_next_header():
    """SOF 뒤 헤더 영역에 FF00(엔트로피 스터핑)이 나오면 다음 진짜 헤더로 경계를 축소한다."""
    data = b'\xff\xd8' + APP0 + SOF0 + b'\xff\x00' + b'\x00' * 40 + EMBEDDED + b'\x00' * 8
    emb_off = data.index(EMBEDDED)
    end, complete = jpeg_end(data, 0, next_sig_offset=len(data))
    assert end == emb_off
    assert complete is False


def test_parse_header_truncated_dht_no_crash():
    """DHT 세그먼트 길이가 실제 데이터보다 길어도(절단) 인덱스 초과로 죽지 않는다
    (축소된 carve 조각이 노출한 크래시 회귀)."""
    from media_recovery.formats.jpeg import baseline_decoder as jd
    truncated_dht = b'\xff\xc4' + struct.pack('>H', 300) + b'\x00\x01\x01' + b'\x05' * 6
    data = b'\xff\xd8' + APP0 + truncated_dht  # EOI 없이 절단
    h = jd.parse_header(data)  # 크래시 없이 반환
    assert h is not None


# ── jpeg_end AVI 경계(과다 카빙이 AVI 삼킴 방지) 테스트 ──────────

def test_next_avi_finds_signature_within_bound():
    """_next_avi는 [start, hi) 안의 첫 RIFF…AVI 오프셋을 반환하고, 없으면 hi를 반환한다.
    RIFF지만 AVI 아님(WAV 등)은 무시하고, hi 밖 시그니처도 무시한다."""
    from media_recovery.formats.boundaries import _next_avi
    data = b'\x00' * 50 + make_avi(chunk_size=100) + b'\x00' * 50
    assert _next_avi(data, 0, len(data)) == 50        # 첫 AVI 시그니처
    assert _next_avi(data, 51, len(data)) == len(data)  # 시그니처 지난 뒤 → 없음(hi)
    assert _next_avi(data, 0, 40) == 40               # AVI가 hi(40) 밖 → hi
    wav = b'\x00' * 10 + b'RIFF' + struct.pack('<I', 100) + b'WAVE' + b'\x00' * 10
    assert _next_avi(wav, 0, len(wav)) == len(wav)     # WAVE는 AVI 아님 → 무시


class TrackingFindData:
    """find가 받은 end 인자를 기록하는 bytes 대역."""

    def __init__(self, data: bytes):
        self.data = data
        self.calls: list[tuple[bytes, int, int | None]] = []
        self.rfind_calls: list[tuple[bytes, int, int | None]] = []

    def __len__(self):
        return len(self.data)

    def __getitem__(self, key):
        return self.data[key]

    def find(self, sub: bytes, start: int = 0, end: int | None = None):
        self.calls.append((sub, start, end))
        if end is None:
            return self.data.find(sub, start)
        return self.data.find(sub, start, end)

    def rfind(self, sub: bytes, start: int = 0, end: int | None = None):
        self.rfind_calls.append((sub, start, end))
        if end is None:
            return self.data.rfind(sub, start)
        return self.data.rfind(sub, start, end)


def test_next_entropy_marker_skips_stuffing_restart_and_preserves_fill_start():
    """최적화 경로와 find fallback이 같은 marker/fill 위치를 반환한다."""
    from media_recovery.formats.boundaries import _next_entropy_marker

    raw = b'prefix\xff\x00data\xff\xd3more\xff\xff\xd9tail'
    expected_start = raw.index(b'\xff\xff\xd9')
    expected = (expected_start, expected_start + 2)
    anonymous = mmap.mmap(-1, len(raw))
    anonymous.write(raw)
    anonymous.seek(0)
    try:
        for data in (raw, bytearray(raw), anonymous, TrackingFindData(raw)):
            assert _next_entropy_marker(data, 0, len(raw)) == expected
    finally:
        anonymous.close()


def test_next_header_limits_each_find_to_requested_bound():
    """경계 밖 시그니처를 찾느라 큰 디스크 이미지의 나머지를 스캔하지 않는다."""
    from media_recovery.formats.boundaries import _next_header

    bound = 64
    data = TrackingFindData(b'\x00' * 100 + b'\xff\xd8\xff\xe0')

    assert _next_header(data, 0, bound) == bound
    assert data.calls == [(b'\xff\xd8\xff', 0, bound)]


def test_next_avi_limits_each_find_to_requested_bound():
    """AVI 경계 탐색도 hi를 bytes.find의 end로 전달한다."""
    from media_recovery.formats.boundaries import _next_avi

    bound = 64
    data = TrackingFindData(b'\x00' * 100 + make_avi())

    assert _next_avi(data, 0, bound) == bound
    assert data.calls == [(b'RIFF', 0, bound)]


def test_indexed_avi_boundary_does_not_repeat_raw_riff_search():
    """전체 AVI 인덱스가 있으면 같은 범위를 raw RIFF로 다시 검색하지 않는다."""
    from media_recovery.formats.boundaries import _next_avi_boundary

    data = TrackingFindData(b'\x00' * 100 + make_avi())

    assert _next_avi_boundary(data, 0, 64, ()) == 64
    assert data.calls == []


def test_avi_boundary_without_index_keeps_raw_riff_fallback():
    """AVI 인덱스가 None이면 독립 API 호출을 위해 raw 검색을 유지한다."""
    from media_recovery.formats.boundaries import _next_avi_boundary

    avi_offset = 64
    data = TrackingFindData(b'\x00' * avi_offset + make_avi())

    assert _next_avi_boundary(data, 0, len(data), None) == avi_offset
    assert data.calls == [(b'RIFF', 0, len(data))]


def test_entropy_scan_does_not_rescan_for_avi_at_every_ff_byte():
    """FF00가 조밀한 entropy에서도 raw AVI 검색은 cursor당 한 번만 수행한다."""
    sos = make_app_segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    raw = b'\xff\xd8' + APP0 + SOF0 + sos + b'\xff\x00' * 8192
    data = TrackingFindData(raw)

    end, complete = jpeg_end(data, 0, max_size=len(raw))

    entropy_start = len(b'\xff\xd8' + APP0 + SOF0 + sos)
    riff_calls = [
        call
        for call in data.calls
        if call[0] == b'RIFF' and call[1] >= entropy_start
    ]
    assert end == len(raw)
    assert complete is False
    assert len(riff_calls) == 1
    assert data.rfind_calls == []


def test_entropy_scan_reverse_searches_eoi_once_for_multiscan_chain():
    """inter-scan 연쇄가 반복돼도 EOI 역검색은 scan 전체에서 한 번뿐이다."""
    sof2 = make_app_segment(0xC2, b'\x08\x00\x08\x00\x08\x01\x01\x11\x00')
    sos1 = make_app_segment(0xDA, b'\x01\x01\x00\x00\x00\x00')
    sos2 = make_app_segment(0xDA, b'\x01\x01\x00\x01\x3f\x00')
    sos3 = make_app_segment(0xDA, b'\x01\x01\x00\x01\x3f\x00')
    raw = (
        b'\xff\xd8' + APP0 + sof2 + sos1 + b'first'
        + sos2 + b'second' + sos3 + b'third\xff\xd9' + b'\x00' * 64
    )
    data = TrackingFindData(raw)

    end, complete = jpeg_end(data, 0, max_size=len(raw))

    entropy_start = len(b'\xff\xd8' + APP0 + sof2 + sos1)
    assert end == raw.rindex(b'\xff\xd9') + 2
    assert complete is True
    assert data.rfind_calls == [(b'\xff\xd9', entropy_start, len(raw))]


def test_jpeg_end_sos_overshoot_stops_at_avi():
    """SOS 이후 진짜 EOI를 못 찾고 상한까지 가는 절단 JPEG이 뒤따르는 AVI(RIFF)를 삼키지
    않고 AVI 시작점에서 끊는다(과다 카빙이 AVI를 삼키는 회귀 방지)."""
    sos = b'\xff\xda' + struct.pack('>H', 12) + b'\x01\x01\x00\x00\x3f\x00\x00\x00\x00\x00'
    entropy = b'\x12\x34' * 100  # FF 없음 → 진짜/가짜 EOI 후보 없음, 상한까지 진행
    avi = make_avi(chunk_size=2000)
    data = b'\xff\xd8' + APP0 + SOF0 + sos + entropy + avi + b'\x00' * 2000
    avi_off = data.index(b'RIFF')
    end, complete = jpeg_end(data, 0, next_sig_offset=avi_off)
    assert end == avi_off          # AVI 시그니처에서 경계(삼키지 않음)
    assert complete is False


def test_jpeg_end_corrupt_dht_bounds_at_avi():
    """SOF 뒤 DHT 길이 손상 시, 다음 JPEG 헤더가 없어도 뒤따르는 AVI(RIFF)에서 경계를
    축소해 AVI를 삼키지 않는다."""
    avi = make_avi(chunk_size=3000)
    dht_payload = b'\x00' * 80 + avi + b'\x00' * 1200  # ≥1201(손상), AVI 포함, JPEG 헤더 없음
    corrupt_dht = b'\xff\xc4' + struct.pack('>H', len(dht_payload) + 2) + dht_payload
    data = b'\xff\xd8' + APP0 + SOF0 + corrupt_dht + b'\x00' * 8
    avi_off = data.index(b'RIFF')
    end, complete = jpeg_end(data, 0, next_sig_offset=len(data))
    assert end == avi_off
    assert complete is False


def test_jpeg_end_app_length_overshoot_stops_at_avi():
    """손상 APP 길이가 다음 AVI 위로 점프하더라도 AVI 시작을 넘지 않는다."""
    avi = make_avi(chunk_size=64, extra=b'\x00' * 60)
    corrupt_app = make_app_segment(
        0xE1,
        b'\x00' * 40 + avi + b'\x00' * 100,
    )
    data = b'\xff\xd8' + APP0 + SOF0 + corrupt_app + b'\xff\xd9'
    avi_off = data.index(b'RIFF')

    end, complete = jpeg_end(data, 0, next_sig_offset=avi_off)

    assert end == avi_off
    assert complete is False


def test_jpeg_end_valid_jpeg_then_avi_ends_at_eoi():
    """진짜 EOI로 끝나는 정상 JPEG은 뒤에 AVI가 있어도 EOI에서 끝난다(_next_avi가 조기
    절단하지 않음 — 회귀 방지)."""
    jpg = make_jpeg(APP0, SOF0)  # EOI 포함
    data = jpg + make_avi(chunk_size=1000) + b'\x00' * 1000
    end, complete = jpeg_end(data, 0, next_sig_offset=len(jpg))
    assert end == len(jpg)         # EOI 직후
    assert complete is True


# ── avi_end 테스트 ───────────────────────────────────────────

def make_avi(chunk_size: int | None = None, extra: bytes = b'') -> bytes:
    """RIFF + chunk_size + AVI  형식의 최소 AVI 생성."""
    payload = b'AVI ' + extra
    if chunk_size is None:
        chunk_size = len(payload)
    return b'RIFF' + struct.pack('<I', chunk_size) + payload


def make_riff_chunk(chunk_id: bytes, payload: bytes) -> bytes:
    """WORD padding을 포함한 RIFF chunk를 만든다."""
    assert len(chunk_id) == 4
    padding = b'\x00' if len(payload) % 2 else b''
    return chunk_id + struct.pack('<I', len(payload)) + payload + padding


def make_riff_list(list_type: bytes, payload: bytes) -> bytes:
    assert len(list_type) == 4
    return make_riff_chunk(b'LIST', list_type + payload)


def make_riff_form(form_type: bytes, payload: bytes, chunk_size: int | None = None) -> bytes:
    assert len(form_type) == 4
    form_payload = form_type + payload
    if chunk_size is None:
        chunk_size = len(form_payload)
    return b'RIFF' + struct.pack('<I', chunk_size) + form_payload


def make_structured_avi(frame: bytes, chunk_size: int | None = None) -> bytes:
    avih = make_riff_chunk(b'avih', b'\x00' * 56)
    hdrl = make_riff_list(b'hdrl', avih)
    movi = make_riff_list(b'movi', make_riff_chunk(b'00dc', frame))
    return make_riff_form(b'AVI ', hdrl + movi, chunk_size=chunk_size)


def test_avi_end_valid_header():
    """RIFF chunk_size가 정상이면 헤더 기반으로 끝을 계산한다."""
    data = make_structured_avi(b'\x00\x01')
    end, used_header = avi_end(data, 0)
    assert end == len(data)
    assert used_header is True


def test_avi_end_accepts_declared_size_with_damaged_hdrl_list_id():
    """hdrl LIST id만 경미하게 손상돼도 avih/movi가 있으면 RIFF size를 보존한다."""
    data = bytearray(make_structured_avi(b'\x00\x01'))
    first_list = data.index(b'LIST', 12)
    data[first_list:first_list + 4] = b'LICT'

    end, used_header = avi_end(bytes(data), 0)

    assert end == len(data)
    assert used_header is True


def test_avi_end_allows_structurally_valid_damaged_form_type_when_flagged():
    data = bytearray(make_structured_avi(b'\x00\x01'))
    data[8:12] = b'AVX '

    with pytest.raises(ValueError, match='form type'):
        avi_end(bytes(data), 0)

    end, used_header = avi_end(
        bytes(data),
        0,
        allow_corrupt_header=True,
    )

    assert end == len(data)
    assert used_header is True


def test_avi_end_chunk_size_zero_uses_fallback():
    """chunk_size가 0이면 next_sig_offset을 fallback으로 사용한다."""
    data = make_avi(chunk_size=0) + b'\x00' * 100
    next_sig = 50
    end, used_header = avi_end(data, 0, next_sig_offset=next_sig)
    assert end == next_sig
    assert used_header is False


@pytest.mark.parametrize('chunk_size', [1, 2, 3, 4, 15])
def test_avi_end_chunk_size_smaller_than_form_type_uses_fallback(chunk_size):
    """실제 AVI LIST 하나도 담을 수 없는 RIFF size는 신뢰하지 않는다."""
    data = make_avi(chunk_size=chunk_size) + b'\x00' * 100
    next_sig = 50

    end, used_header = avi_end(data, 0, next_sig_offset=next_sig)

    assert end == next_sig
    assert used_header is False


def test_avi_end_corrupt_size_ignores_internal_mjpeg_signature():
    """손상 RIFF size fallback은 movi/00dc 안의 JPEG hit에서 AVI를 자르지 않는다."""
    frame = make_baseline_jpeg_without_app()
    first_avi = make_structured_avi(frame, chunk_size=0)
    next_avi = make_avi()
    data = first_avi + next_avi
    frame_offset = data.index(b'\xff\xd8')

    end, used_header = avi_end(
        data,
        0,
        max_size=len(data),
        next_sig_offset=frame_offset,
    )

    assert end == len(first_avi)
    assert used_header is False


def test_avi_end_plausible_but_wrong_size_stops_before_following_jpeg():
    """범위 내 정수인 손상 RIFF size도 다음 외부 JPEG를 가로지르면 신뢰하지 않는다."""
    base = make_structured_avi(b'\x00\x01')
    following = make_baseline_jpeg_without_app()
    declared_end = len(base) + len(following) + 16
    corrupt_base = (
        b'RIFF'
        + struct.pack('<I', declared_end - 8)
        + base[8:]
    )
    data = corrupt_base + following + b'\x00' * 16
    following_offset = len(base)

    end, used_header = avi_end(
        data,
        0,
        max_size=len(data),
        next_sig_offset=following_offset,
    )

    assert end == following_offset
    assert used_header is False


def test_avi_end_too_small_declared_size_recovers_through_movi():
    """hdrl까지만 포함한 RIFF size는 무효이며 movi 끝까지 chunk-walk한다."""
    base = make_structured_avi(b'\x00\x01')
    movi_offset = base.find(b'LIST', 16)
    corrupt = b'RIFF' + struct.pack('<I', movi_offset - 8) + base[8:]

    end, used_header = avi_end(corrupt, 0, max_size=len(corrupt))

    assert end == len(corrupt)
    assert used_header is False


def test_avi_end_includes_consecutive_avix_riff_extensions():
    """OpenDML AVI 뒤의 연속 RIFF AVIX 세그먼트도 같은 AVI 범위에 포함한다."""
    frame = make_baseline_jpeg_without_app()
    base = make_structured_avi(frame)
    avix_payload = make_riff_list(b'movi', make_riff_chunk(b'00dc', frame))
    avix1 = make_riff_form(b'AVIX', avix_payload)
    avix2 = make_riff_form(b'AVIX', avix_payload)
    data = base + avix1 + avix2 + b'\x00' * 32

    end, used_header = avi_end(data, 0)

    assert end == len(base) + len(avix1) + len(avix2)
    assert used_header is True


def test_avi_end_includes_open_dml_standard_index_in_avix():
    """AVIX의 OpenDML ix## standard-index chunk를 합법 구조로 허용한다."""
    base = make_structured_avi(b'\x00\x01')
    avix_payload = (
        make_riff_list(b'movi', make_riff_chunk(b'00dc', b'\x01\x02'))
        + make_riff_chunk(b'ix00', b'\x00' * 24)
    )
    avix = make_riff_form(b'AVIX', avix_payload)

    end, used_header = avi_end(base + avix, 0)

    assert end == len(base) + len(avix)
    assert used_header is True


def test_avi_end_rejects_avix_without_movi_structure():
    """RIFF/AVIX와 size만 맞는 임의 payload는 base AVI에 붙이지 않는다."""
    base = make_structured_avi(b'\x00\x01')
    fake_avix = make_riff_form(b'AVIX', b'\x00' * 20)

    end, used_header = avi_end(base + fake_avix, 0)

    assert end == len(base)
    assert used_header is True


def test_avi_end_rejects_avix_size_crossing_following_jpeg():
    """손상 AVIX size가 뒤 독립 JPEG를 가로지르면 extension을 버린다."""
    base = make_structured_avi(b'\x00\x01')
    avix_payload = make_riff_list(b'movi', make_riff_chunk(b'00dc', b'\x01\x02'))
    avix = make_riff_form(b'AVIX', avix_payload)
    following = make_baseline_jpeg_without_app()
    corrupt_size = struct.unpack('<I', avix[4:8])[0] + len(following)
    corrupt_avix = b'RIFF' + struct.pack('<I', corrupt_size) + avix[8:]

    end, used_header = avi_end(base + corrupt_avix + following, 0)

    assert end == len(base)
    assert used_header is True


def test_avi_end_corrupt_base_size_still_includes_avix_extension():
    """base RIFF size가 손상돼 chunk walk를 써도 연속 AVIX를 포함한다."""
    frame = make_baseline_jpeg_without_app()
    base = make_structured_avi(frame, chunk_size=0)
    avix_payload = make_riff_list(b'movi', make_riff_chunk(b'00dc', frame))
    avix = make_riff_form(b'AVIX', avix_payload)
    data = base + avix + b'\x00' * 32

    end, used_header = avi_end(data, 0, max_size=len(data))

    assert end == len(base) + len(avix)
    assert used_header is False


def test_avi_end_corrupt_movi_size_ignores_jpeg_inside_stream_chunk():
    """movi 길이가 손상돼도 00dc payload 내부 SOI를 외부 경계로 쓰지 않는다."""
    inner = make_baseline_jpeg_without_app()
    frame = b'\x11' * 16 + inner + b'\x22' * 16
    avih = make_riff_chunk(b'avih', b'\x00' * 56)
    hdrl = make_riff_list(b'hdrl', avih)
    corrupt_movi = b'LIST' + struct.pack('<I', 2) + b'movi' + make_riff_chunk(b'00dc', frame)
    first_avi = make_riff_form(b'AVI ', hdrl + corrupt_movi, chunk_size=0)
    next_avi = make_structured_avi(b'\x00\x01')
    data = first_avi + next_avi
    nested_offset = data.index(inner)

    end, used_header = avi_end(
        data,
        0,
        max_size=len(data),
        next_sig_offset=nested_offset,
    )

    assert end == len(first_avi)
    assert used_header is False


def test_avi_end_chunk_size_exceeds_max_size():
    """chunk_size가 max_size를 초과하면 fallback으로 전환한다."""
    huge = 600 * 1024 * 1024  # 600 MB
    data = b'RIFF' + struct.pack('<I', huge) + b'AVI ' + b'\x00' * 20
    end, used_header = avi_end(data, 0, max_size=500 * 1024 * 1024)
    assert used_header is False
    assert end <= len(data)


def test_avi_end_no_fallback_uses_max_size():
    """next_sig도 없으면 max_size를 상한으로 잘라 저장한다."""
    data = make_avi(chunk_size=0) + b'\x00' * 200
    max_size = 50
    end, used_header = avi_end(data, 0, max_size=max_size)
    assert end <= max_size + 8  # offset(0) + 8(RIFF header) + max_size
    assert used_header is False


def test_avi_end_raises_on_missing_riff():
    """RIFF 시그니처가 없으면 ValueError를 발생시킨다."""
    data = b'\x00' * 20
    with pytest.raises(ValueError, match='RIFF'):
        avi_end(data, 0)
