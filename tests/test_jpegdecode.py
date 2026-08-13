"""baseline_decoder 디코더 검증 — PIL/libjpeg와 대조."""
import io

import numpy as np
import pytest
from PIL import Image

from media_recovery.formats.jpeg import baseline_decoder as jd


def encode(img: np.ndarray, subsampling: int, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format='JPEG', quality=quality, subsampling=subsampling)
    return buf.getvalue()


def smooth_image(h=256, w=384) -> np.ndarray:
    xx, yy = np.meshgrid(np.linspace(20, 235, w), np.linspace(20, 235, h))
    return np.clip(np.stack([xx, yy, (xx + yy) / 2], -1), 0, 255).astype(np.uint8)


@pytest.mark.parametrize('subsampling', [1, 2])  # 1=4:2:2, 2=4:2:0
def test_decoder_matches_pil_on_clean(subsampling):
    """깨끗한 JPEG을 PIL과 거의 동일하게(반올림 오차) 디코딩한다."""
    data = encode(smooth_image(), subsampling)
    dec = jd.Decoder(data)
    done, _end, err = dec.decode_full()
    assert err == 0
    assert done == dec.mcus_x * dec.mcus_y          # 디싱크 없이 전체 디코드
    mine = dec.to_rgb().astype(np.int16)
    pil = np.asarray(Image.open(io.BytesIO(data)).convert('RGB'), np.int16)
    H = min(mine.shape[0], pil.shape[0])
    W = min(mine.shape[1], pil.shape[1])
    diff = np.abs(mine[:H, :W] - pil[:H, :W])
    assert diff.mean() < 3.0                         # 매끄러운 영상: 반올림 수준


def test_decoder_dimensions():
    data = encode(smooth_image(200, 320), 1)
    dec = jd.Decoder(data)
    assert dec.h.width == 320 and dec.h.height == 200
    dec.decode_full()
    rgb = dec.to_rgb()
    assert rgb.shape == (200, 320, 3)


def test_decoder_can_render_partial_mcu_padding():
    """crop=False preserves coefficient pixels hidden by partial edge MCUs."""
    data = encode(smooth_image(61, 93), 2)
    dec = jd.Decoder(data)
    dec.decode_full()
    cropped = dec.to_rgb()
    padded = dec.to_rgb(crop=False)
    assert cropped.shape == (61, 93, 3)
    assert padded.shape == (
        dec.mcus_y * 8 * dec.vmax, dec.mcus_x * 8 * dec.hmax, 3)
    assert np.array_equal(padded[:61, :93], cropped)


def test_rejects_corrupt_sampling_factor():
    """SOF 샘플링 계수가 0으로 손상된 파일은 거부한다(빈 이미지 크래시 방지)."""
    data = bytearray(encode(smooth_image(), 1))
    sof = data.find(b'\xff\xc0')
    assert sof != -1
    hv = sof + 11                       # 첫 컴포넌트 샘플링(h<<4|v) 바이트
    data[hv] = data[hv] & 0xF0          # v 니블을 0으로 → 비정상
    with pytest.raises(ValueError):
        jd.Decoder(bytes(data))


def test_rejects_non_three_component():
    """그레이스케일(1-component)은 지원 대상이 아니므로 거부한다."""
    gray = np.repeat(smooth_image()[:, :, :1], 1, axis=2)[:, :, 0]
    buf = io.BytesIO()
    Image.fromarray(gray, mode='L').save(buf, format='JPEG')
    with pytest.raises(ValueError):
        jd.Decoder(buf.getvalue())


def test_rejects_missing_dht():
    """DHT 소실 파일은 거부한다.

    미검증 시 all-zero LUT로 진행돼 MCU 0에서 무효 코드 → 전량 회색이
    RECOVERED로 오분류될 수 있다."""
    data = encode(smooth_image(), 1)
    out = bytearray(data[:2])                       # SOI
    i = 2
    while i < len(data) - 4:
        assert data[i] == 0xFF
        m = data[i + 1]
        if m == 0xDA:                               # SOS부터는 그대로 복사
            out += data[i:]
            break
        seglen = int.from_bytes(data[i + 2:i + 4], 'big')
        if m != 0xC4:                               # DHT(FFC4)만 제거
            out += data[i:i + 2 + seglen]
        i += 2 + seglen
    with pytest.raises(ValueError):
        jd.Decoder(bytes(out))
