import struct
import pytest
from carver.extractors import jpeg_end, JPEG_MAX_FALLBACK_SIZE, avi_end, MAX_AVI_SIZE_DEFAULT


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


def test_stuffing_ratio_distinguishes_entropy_from_padding():
    """_stuffing_ratio: 엔트로피(높음) vs 패딩/헤더(낮음)을 구분한다."""
    from carver.extractors import _stuffing_ratio
    assert _stuffing_ratio(b'\xff\x00' * 50) == 1.0           # 전부 stuffing
    assert _stuffing_ratio(b'\xff\xd0\xff\xd7' * 20) == 1.0   # 전부 RST
    assert _stuffing_ratio(b'\x00' * 100) == 0.0              # FF 없음(패딩)
    assert _stuffing_ratio(b'\xff\xe0\xff\xe1' * 20) == 0.0   # FF 다음 APPn(헤더)


# ── jpeg_end 과다 카빙 방지(손상 헤더 경계) 테스트 ───────────────

# 최소 SOF0: precision 8, 16x16, 1 컴포넌트 — mb=0xC0이라 saw_sof를 세운다
SOF0 = make_app_segment(0xC0, b'\x08\x00\x10\x00\x10\x01\x01\x11\x00')
# 임베디드 진짜 이미지 헤더(FF D8 FF E0 + JFIF): _next_header가 찾는 다음 파일 경계
EMBEDDED = b'\xff\xd8\xff\xe0' + struct.pack('>H', 16) + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00' + b'\xff\xd9'


def test_jpeg_end_corrupt_dht_length_bounds_at_next_header():
    """SOF 뒤 DHT 길이가 sane 상한(1200)을 넘으면(손상) 다음 진짜 헤더로 경계를 축소해
    그 안의 임베디드 이미지를 삼키지 않는다(과다 카빙 방지)."""
    dht_payload = b'\x00' * 80 + EMBEDDED + b'\x00' * 1100  # ≥1201, 임베디드 헤더 포함
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
    from carver import jpegdecode as jd
    truncated_dht = b'\xff\xc4' + struct.pack('>H', 300) + b'\x00\x01\x01' + b'\x05' * 6
    data = b'\xff\xd8' + APP0 + truncated_dht  # EOI 없이 절단
    h = jd.parse_header(data)  # 크래시 없이 반환
    assert h is not None


# ── jpeg_end AVI 경계(과다 카빙이 AVI 삼킴 방지) 테스트 ──────────

def test_next_avi_finds_signature_within_bound():
    """_next_avi는 [start, hi) 안의 첫 RIFF…AVI 오프셋을 반환하고, 없으면 hi를 반환한다.
    RIFF지만 AVI 아님(WAV 등)은 무시하고, hi 밖 시그니처도 무시한다."""
    from carver.extractors import _next_avi
    data = b'\x00' * 50 + make_avi(chunk_size=100) + b'\x00' * 50
    assert _next_avi(data, 0, len(data)) == 50        # 첫 AVI 시그니처
    assert _next_avi(data, 51, len(data)) == len(data)  # 시그니처 지난 뒤 → 없음(hi)
    assert _next_avi(data, 0, 40) == 40               # AVI가 hi(40) 밖 → hi
    wav = b'\x00' * 10 + b'RIFF' + struct.pack('<I', 100) + b'WAVE' + b'\x00' * 10
    assert _next_avi(wav, 0, len(wav)) == len(wav)     # WAVE는 AVI 아님 → 무시


def test_jpeg_end_sos_overshoot_stops_at_avi():
    """SOS 이후 진짜 EOI를 못 찾고 상한까지 가는 절단 JPEG이 뒤따르는 AVI(RIFF)를 삼키지
    않고 AVI 시작점에서 끊는다(과다 카빙이 AVI 삼킴 방지, 조사 2026-07-05)."""
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


def test_avi_end_valid_header():
    """RIFF chunk_size가 정상이면 헤더 기반으로 끝을 계산한다."""
    data = make_avi()
    end, used_header = avi_end(data, 0)
    assert end == len(data)
    assert used_header is True


def test_avi_end_chunk_size_zero_uses_fallback():
    """chunk_size가 0이면 next_sig_offset을 fallback으로 사용한다."""
    data = make_avi(chunk_size=0) + b'\x00' * 100
    next_sig = 50
    end, used_header = avi_end(data, 0, next_sig_offset=next_sig)
    assert end == next_sig
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
