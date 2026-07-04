"""carver.resync 복구 엔진 검증."""
import io

import numpy as np
import pytest
from PIL import Image

from carver import jpegdecode as jd
from carver import resync


def encode(img: np.ndarray, subsampling: int = 1, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format='JPEG', quality=quality, subsampling=subsampling)
    return buf.getvalue()


def textured_image(h=256, w=384, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    xx, yy = np.meshgrid(np.linspace(0, 255, w), np.linspace(0, 255, h))
    base = np.stack([xx, yy, (xx + yy) / 2], -1) + rng.normal(0, 25, (h, w, 3))
    return np.clip(base, 0, 255).astype(np.uint8)


def corrupt_entropy(data: bytes, n_bytes: int, seed: int = 1) -> bytes:
    """SOS 이후 엔트로피의 한 덩어리를 손상시켜 디싱크를 유발(디스크 손상 모사)."""
    h = jd.parse_header(data)
    start = h.scan_start
    last_eoi = data.rfind(b'\xff\xd9')
    arr = bytearray(data)
    rng = np.random.default_rng(seed)
    pos = start + (last_eoi - start) * 2 // 5        # 엔트로피 ~40% 지점
    for i in range(n_bytes):
        arr[pos + i] = int(rng.integers(0, 256))
    return bytes(arr)


# ── gray_fraction ──────────────────────────────────────────

def test_gray_fraction_detects_gray():
    gray = np.full((64, 64, 3), 128, np.uint8)
    assert resync.gray_fraction(gray) > 0.95


def test_gray_fraction_low_on_texture():
    assert resync.gray_fraction(textured_image()) < 0.2


# ── 복구 ────────────────────────────────────────────────────

def test_recover_clean_image_is_noop():
    """손상 없는 이미지는 회색이 낮고 편집(ops)이 거의 없다."""
    dec = jd.Decoder(encode(textured_image()))
    rgb, stats, _segs = resync.recover(dec)
    assert resync.gray_fraction(rgb) < 0.1
    assert stats['resync'] == 0 and stats['hole'] == 0


def test_recover_output_shape():
    dec = jd.Decoder(encode(textured_image(200, 320)))
    rgb, _stats, _segs = resync.recover(dec)
    assert rgb.shape == (200, 320, 3)


def test_recover_segments_strictly_increasing_bits():
    """회귀 방지: 세그먼트 시작 비트는 단조 증가해야 한다.

    과거 버그는 디싱크 후 mcu_bit가 미기록(0)으로 남아 resync가 비트 0으로 역행,
    스트림 앞부분을 반복 디코딩(주기적 중복)했다. 손상본을 복구해 비트위치가
    뒤로 가지 않음을 확인한다."""
    data = corrupt_entropy(encode(textured_image(), subsampling=1), n_bytes=40)
    dec = jd.Decoder(data)
    _rgb, _stats, segments = resync.recover(dec)
    start_bits = [sbit for (_sm, sbit, _dc) in sorted(segments)]
    assert start_bits == sorted(start_bits)          # 단조 비감소
    assert len(set(start_bits)) == len(start_bits)   # 중복(재디코딩) 없음


def test_recover_fast_and_thorough_both_run():
    """철저(기본)·빠른 모드 모두 유효한 이미지를 낸다(파라미터 스레딩 스모크)."""
    data = corrupt_entropy(encode(textured_image(200, 320)), n_bytes=24)
    for kw in ({}, dict(resync_near=160000, resync_full=False, time_budget=5.0)):
        dec = jd.Decoder(data)
        rgb, _stats, _segs = resync.recover(dec, **kw)
        assert rgb.shape == (200, 320, 3)


def test_recover_bytes_handles_garbage():
    """디코드 불가 입력은 (None, {})로 안전 처리."""
    rgb, stats = resync.recover_bytes(b'\xff\xd8not a real jpeg\xff\xd9')
    assert rgb is None and stats == {}


def test_recover_file_roundtrip(tmp_path):
    """recover_file이 유효한 JPEG를 저장한다."""
    src = tmp_path / '0xDEADBEEF.jpg'
    src.write_bytes(corrupt_entropy(encode(textured_image()), n_bytes=24))
    out, action, info = resync.recover_file(src, tmp_path)
    assert action in ('RECOVERED', 'CLEAN')
    if action == 'RECOVERED':
        assert out.exists()
        Image.open(out).load()                       # 유효 JPEG
        assert 'gray_before' in info and 'gray_after' in info


def test_recover_file_routes_recovered_subdir(tmp_path):
    """RECOVERED 결과는 out_dir/recovered/ 아래에 저장된다."""
    src = tmp_path / '0xDEADBEEF.jpg'
    src.write_bytes(corrupt_entropy(encode(textured_image()), n_bytes=32, seed=99))
    out, action, info = resync.recover_file(src, tmp_path)
    assert action == 'RECOVERED'
    assert out.parent == tmp_path / 'recovered'
    assert out.exists()
    Image.open(out).load()                       # 유효 JPEG


def test_recover_file_clean_copies_original(tmp_path):
    """손상 없는 JPEG는 clean/ 폴더에 원본 바이트 그대로 복사된다."""
    src = tmp_path / '0xCAFEBABE.jpg'
    raw = encode(textured_image())
    src.write_bytes(raw)
    out, action, info = resync.recover_file(src, tmp_path)
    assert action == 'CLEAN'
    assert out.parent == tmp_path / 'clean'
    assert out.read_bytes() == raw               # 원본 바이트 동일


def test_recover_file_skip_copies_original(tmp_path):
    """디코드 불가 입력은 skip_undecodable/ 폴더에 원본 바이트 그대로 복사된다."""
    src = tmp_path / '0xFEEDFACE.jpg'
    raw = b'\xff\xd8 not a decodable jpeg \xff\xd9'
    src.write_bytes(raw)
    out, action, info = resync.recover_file(src, tmp_path)
    assert action == 'SKIP_UNDECODABLE'
    assert out.parent == tmp_path / 'skip_undecodable'
    assert out.read_bytes() == raw


def test_recover_file_failed_preserves_original(tmp_path):
    """무행동(ops 0·hole≥1) 파일은 failed/에 원본 바이트를 보존한다.

    절단(EOI 없이 엔트로피 후반 소실)으로 디싱크 후 잔여 MCU가 수락 바닥(run 30)
    미만이면 어떤 편집·재동기도 수락될 수 없어 무행동으로 끝난다 — 회색
    재인코딩본 대신 원본을 남겨야 한다. (임계 비례화 이후에도 30 MCU 바닥은
    남는다 — 그보다 짧은 꼬리는 진위를 검증할 수 없다.)"""
    data = encode(textured_image(64, 64, seed=7))    # 4:2:2 → 32 MCU 소형
    h = jd.parse_header(data)
    last_eoi = data.rfind(b'\xff\xd9')
    trunc = data[:h.scan_start + (last_eoi - h.scan_start) * 7 // 10]  # 후반 30% 절단
    src = tmp_path / '0xBADD1E00.jpg'
    src.write_bytes(trunc)
    out, action, info = resync.recover_file(src, tmp_path, time_budget=15)
    assert action == 'FAILED'
    assert out.parent == tmp_path / 'failed'
    assert out.read_bytes() == trunc                 # 원본 보존
    assert info['ops'] == 0 and info['hole'] >= 1
    assert info['mcus'] == 32


# ── 수락 임계 잔여 비례화 (W2) ──────────────────────────────

def fe_hole(data: bytes, frac: float, n_bytes: int) -> bytes:
    """엔트로피의 frac 지점에 0xFE 채움 hole을 만든다. 0xFE는 Annex-K DC cat-10
    코드(11111110)라 큰 DC 차분이 연쇄돼 계수 경계를 결정적으로 반복 발동시킨다
    (무작위 손상은 조밀 코드 공간 탓에 '그럴듯한' 스트림으로 조용히 디코드될 수
    있어 테스트 재료로 비결정적이다)."""
    h = jd.parse_header(data)
    last_eoi = data.rfind(b'\xff\xd9')
    span = last_eoi - h.scan_start
    arr = bytearray(data)
    pos = h.scan_start + int(span * frac)
    for i in range(n_bytes):
        arr[pos + i] = 0xFE
    return bytes(arr)


def test_recover_small_image_resync_unlocked():
    """총 MCU<450 소형 이미지도 재동기가 수락된다(임계 잠금 해제).

    과거 절대 임계(max(250, maxW//2)=450)에서는 총 MCU 256인 이미지의 재동기
    수락이 산술적으로 불가능했다 — 손상 클러스터 이후 전량 회색(임계 잠금).
    창 비례 임계(max(30, 0.35·W))는 소형에서도 수락을 허용한다. hole을 2개 둬
    바이트 오라클 단독으로는 복구가 끝나지 않게 한다."""
    data = fe_hole(fe_hole(encode(textured_image(128, 256, seed=3)), 0.40, 150), 0.75, 150)
    dec = jd.Decoder(data)                           # 4:2:2 → 16×16 = 256 MCU
    assert dec.mcus_x * dec.mcus_y == 256
    rgb, stats, _segs = resync.recover(dec, time_budget=0)
    assert stats['resync'] >= 1                      # 절대 임계에서는 불가능했던 수락
    assert resync.undecoded_fraction(rgb) < 0.3      # hole 이후가 회색으로 남지 않음


def test_recover_truncated_tail_resync_via_buffer_end():
    """버퍼 끝까지 완주하는 후보(stop=3)는 0.35·W 미만이어도 run≥30이면 수락된다.

    절단 파일은 남은 데이터 전체가 이어져도 그 길이(여기선 ~115 MCU)가 창 비례
    임계(0.35·460=161)에 못 미친다 — 데이터가 소진돼 뒤에 가릴 내용이 없으므로
    완주 run은 신뢰할 수 있고, 이 규칙이 없으면 hole로 끝나 절단 앞 콘텐츠까지
    버려진다."""
    data = encode(textured_image(256, 384, seed=11))  # 4:2:2 → 24×32 = 768 MCU
    h = jd.parse_header(data)
    last_eoi = data.rfind(b'\xff\xd9')
    span = last_eoi - h.scan_start
    data = fe_hole(data, 0.40, 100)                  # hole 먼저(EOI 존재 시점에 위치 계산)
    data = data[:h.scan_start + span * 55 // 100]    # 후반 45% 절단
    dec = jd.Decoder(data)
    rgb, stats, _segs = resync.recover(dec, time_budget=0)
    assert stats['resync'] >= 1                      # 완주 규칙에 의한 수락
    assert stats['hole'] >= 1                        # 데이터 소진 꼬리는 hole로 남음
    assert rgb.shape == (256, 384, 3)


# ── 헤더 복구 (W3) ──────────────────────────────────────────

def strip_dht(data: bytes) -> bytes:
    """SOS 이전의 DHT 마커(FFC4)를 FF00으로 무력화해 huff를 비운다(DHT 소실 모사).
    마커 워크는 길이 기반이라 세그먼트는 그대로 건너뛰어져 스트림이 일관된다."""
    h = jd.parse_header(data)
    arr = bytearray(data)
    i = 2
    while i < h.scan_start - 1:
        if arr[i] == 0xFF and arr[i + 1] == 0xC4:
            arr[i + 1] = 0x00
        i += 1
    return bytes(arr)


def test_recover_file_header_dht_transplant(tmp_path):
    """DHT 소실 파일은 도너(Annex-K) 테이블 이식으로 복구된다(HEADER_RECOVERED, header_fix=dht).

    엔트로피가 온전하고 헤더만 손상된 경우 — 현행 엔진(엔트로피만 편집)은 Decoder 구성
    실패로 SKIP했으나, 헤더 복구 pass가 도너 DHT를 이식해 디코드를 복원한다. PIL 인코더는
    Annex-K 표준 테이블(도너와 동일 지문 050df0dc)을 쓰므로 이식이 정확히 맞는다.
    헤더 복구본은 원본이 디코드 불가하므로 recovered/가 아닌 header_recovered/에 저장된다."""
    raw = encode(textured_image(96, 96, seed=5))
    src = tmp_path / '0xDEAD00C4.jpg'
    src.write_bytes(strip_dht(raw))
    with pytest.raises(ValueError):                  # 소실 확인 — 정상 경로는 거부
        jd.Decoder(strip_dht(raw))
    out, action, info = resync.recover_file(src, tmp_path, time_budget=0)
    assert action == 'HEADER_RECOVERED'
    assert out.parent == tmp_path / 'header_recovered'
    assert 'dht' in info['header_fix']
    assert info['undec_after'] < 0.05                # 엔트로피 온전 → 사실상 완전 복구
    Image.open(out).load()                           # 유효 JPEG


def test_recover_file_header_unrecoverable_stays_skip(tmp_path):
    """헤더 후보를 못 찾는 손상은 재구성이 None을 반환해 SKIP으로 남는다(가짜 채움 금지)."""
    src = tmp_path / '0xN0FIX000.jpg'
    raw = b'\xff\xd8' + bytes(range(200)) * 4 + b'\xff\xd9'   # SOS/SOF/DQT 없음
    src.write_bytes(raw)
    out, action, info = resync.recover_file(src, tmp_path, time_budget=0)
    assert action == 'SKIP_UNDECODABLE'
    assert out.read_bytes() == raw                   # 원본 보존


def test_recover_file_header_truncated_dqt_pattern_no_crash(tmp_path):
    """DQT len 패턴('00 84')이 파일 끝 근처에 매치돼 테이블 슬라이스가 64B 미만이어도
    크래시하지 않고 SKIP으로 처리한다(carve 위양성 쓰레기 파일 방어)."""
    src = tmp_path / '0xTRUNCD00.jpg'
    raw = b'\xff\xd8' + b'\x00' * 40 + b'\x00\x84\x00\x11\x22'  # 00 84 직후 EOF
    src.write_bytes(raw)
    out, action, info = resync.recover_file(src, tmp_path, time_budget=0)
    assert action == 'SKIP_UNDECODABLE'              # 예외 없이 분류
