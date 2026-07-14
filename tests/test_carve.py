import mmap
import struct
from io import StringIO

import pytest

import carve as carve_cli
from carver import carving
from carver.carving import write_range
from carver.models import FileHit
from carve import process, is_in_range, make_output_dirs


# ── 테스트 헬퍼 ──────────────────────────────────────────────

# 실제 파서를 통과하는 최소 JPEG: SOI + APP0(JFIF) + EOI
_APP0 = b'\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
MINIMAL_JPEG = b'\xff\xd8' + _APP0 + b'\xff\xd9'


def _riff_chunk(chunk_id: bytes, payload: bytes) -> bytes:
    chunk = chunk_id + struct.pack('<I', len(payload)) + payload
    return chunk + (b'\x00' if len(payload) & 1 else b'')


# AVI form + hdrl/avih + movi 구조를 갖춘 최소 컨테이너
_HDRL = _riff_chunk(b'LIST', b'hdrl' + _riff_chunk(b'avih', b'\x00' * 56))
_MOVI = _riff_chunk(b'LIST', b'movi')
_AVI_PAYLOAD = b'AVI ' + _HDRL + _MOVI
MINIMAL_AVI = b'RIFF' + struct.pack('<I', len(_AVI_PAYLOAD)) + _AVI_PAYLOAD


def jpeg_with_embedded_thumbnail() -> tuple[bytes, int]:
    """실제 JPEG 구조를 가진 EXIF 썸네일과 부모 JPEG를 만든다."""
    app1_payload = b'Exif\x00\x00' + MINIMAL_JPEG + b'\x00' * 8
    app1 = b'\xff\xe1' + struct.pack('>H', len(app1_payload) + 2) + app1_payload
    parent = b'\xff\xd8' + app1 + b'\xff\xd9'
    return parent, parent.index(MINIMAL_JPEG)


def make_mmap(data: bytes) -> mmap.mmap:
    mm = mmap.mmap(-1, len(data))
    mm.write(data)
    mm.seek(0)
    return mm


# ── is_in_range 테스트 ──────────────────────────────────────

def test_is_in_range_inside():
    assert is_in_range(150, [(100, 200)]) is True


def test_is_in_range_outside():
    assert is_in_range(50, [(100, 200)]) is False


def test_is_in_range_boundary():
    assert is_in_range(100, [(100, 200)]) is False  # 시작점은 범위 밖
    assert is_in_range(200, [(100, 200)]) is False  # 끝점도 범위 밖


# ── make_output_dirs 테스트 ─────────────────────────────────

def test_make_output_dirs_creates_jpeg_and_avi(tmp_path):
    make_output_dirs(tmp_path, save_thumbnails=False)
    assert (tmp_path / 'jpeg').exists()
    assert (tmp_path / 'avi').exists()
    assert not (tmp_path / 'jpeg_thumbnails').exists()


def test_make_output_dirs_creates_thumbnails_when_flagged(tmp_path):
    make_output_dirs(tmp_path, save_thumbnails=True)
    assert (tmp_path / 'jpeg_thumbnails').exists()


# ── process() 통합 테스트 ────────────────────────────────────

def test_process_extracts_jpeg(tmp_path):
    """process()가 JPEG를 jpeg/ 폴더에 추출한다."""
    padding = b'\x00' * 100
    img = padding + MINIMAL_JPEG + b'\x00' * 100
    mm = make_mmap(img)
    make_output_dirs(tmp_path, save_thumbnails=False)
    hits = [FileHit('jpeg', 100)]

    result = process(mm, hits, tmp_path, 500 * 1024 * 1024, False, StringIO())
    mm.close()

    assert result['jpeg'] == 1
    out = tmp_path / 'jpeg' / '0x00000064.jpg'
    assert out.exists()
    assert out.read_bytes()[:2] == b'\xff\xd8'


def test_process_extracts_avi(tmp_path):
    """process()가 AVI를 avi/ 폴더에 추출한다."""
    padding = b'\x00' * 100
    img = padding + MINIMAL_AVI + b'\x00' * 100
    mm = make_mmap(img)
    make_output_dirs(tmp_path, save_thumbnails=False)
    hits = [FileHit('avi', 100)]

    result = process(mm, hits, tmp_path, 500 * 1024 * 1024, False, StringIO())
    mm.close()

    assert result['avi'] == 1
    out = tmp_path / 'avi' / '0x00000064.avi'
    assert out.exists()


def test_process_skips_embedded_thumbnail(tmp_path):
    """부모 JPEG 범위 내 오프셋의 JPEG hit은 썸네일로 처리한다."""
    padding = b'\x00' * 100
    parent, relative_thumb_offset = jpeg_with_embedded_thumbnail()
    img = padding + parent + b'\x00' * 100
    mm = make_mmap(img)
    make_output_dirs(tmp_path, save_thumbnails=False)

    thumb_offset = 100 + relative_thumb_offset
    hits = [FileHit('jpeg', 100), FileHit('jpeg', thumb_offset)]

    result = process(mm, hits, tmp_path, 500 * 1024 * 1024, False, StringIO())
    mm.close()

    assert result['jpeg'] == 1
    assert result['thumbnails'] == 1
    assert not (tmp_path / 'jpeg' / f'0x{thumb_offset:08X}.jpg').exists()


def test_process_exif_hit_does_not_hide_following_top_level_jpeg(tmp_path):
    """Exif 내부 hit은 부모 경계에서 제외하되 뒤 독립 JPEG는 보존한다."""
    offset = 100
    parent, relative_thumb_offset = jpeg_with_embedded_thumbnail()
    truncated_parent = parent[:-2] + b'\x12\x34' * 20
    next_offset = offset + len(truncated_parent)
    mm = make_mmap(
        b'\x00' * offset + truncated_parent + MINIMAL_JPEG + b'\x00' * 32
    )
    make_output_dirs(tmp_path, save_thumbnails=False)

    result = process(
        mm,
        [
            FileHit('jpeg', offset),
            FileHit('jpeg', offset + relative_thumb_offset),
            FileHit('jpeg', next_offset),
        ],
        tmp_path,
        500 * 1024 * 1024,
        False,
        StringIO(),
    )
    mm.close()

    assert result == {'jpeg': 2, 'avi': 0, 'thumbnails': 1, 'errors': 0}
    assert (tmp_path / 'jpeg' / f'0x{offset:08X}.jpg').read_bytes() == truncated_parent
    assert (tmp_path / 'jpeg' / f'0x{next_offset:08X}.jpg').read_bytes() == MINIMAL_JPEG


@pytest.mark.parametrize('avi_source', ['exact', 'damaged_avi_header'])
def test_process_exif_avi_hit_does_not_hide_following_jpeg(tmp_path, avi_source):
    """Exif APP1 안의 AVI-like hit도 건너뛰고 뒤 독립 JPEG를 경계로 쓴다."""
    offset = 100
    app1_payload = b'Exif\x00\x00' + MINIMAL_AVI
    app1 = b'\xff\xe1' + struct.pack('>H', len(app1_payload) + 2) + app1_payload
    truncated_parent = b'\xff\xd8' + app1 + b'broken-parent'
    dht = b'\xff\xc4\x00\x13' + b'\x00' * 17
    following = b'\xff\xd8' + dht + b'broken\xff\xd9'
    avi_offset = offset + truncated_parent.index(MINIMAL_AVI)
    next_offset = offset + len(truncated_parent)
    mm = make_mmap(
        b'\x00' * offset + truncated_parent + following + b'\x00' * 32
    )
    make_output_dirs(tmp_path, save_thumbnails=False)

    result = process(
        mm,
        [
            FileHit('jpeg', offset),
            FileHit('avi', avi_offset, source=avi_source, confidence=0.98),
            FileHit('jpeg', next_offset),
        ],
        tmp_path,
        500 * 1024 * 1024,
        False,
        StringIO(),
    )
    mm.close()

    assert result == {'jpeg': 2, 'avi': 0, 'thumbnails': 0, 'errors': 0}
    assert (tmp_path / 'jpeg' / f'0x{next_offset:08X}.jpg').read_bytes() == following


def test_process_saves_thumbnail_when_flagged(tmp_path):
    """--save-thumbnails 플래그 시 썸네일을 jpeg_thumbnails/에 저장한다."""
    padding = b'\x00' * 100
    parent, relative_thumb_offset = jpeg_with_embedded_thumbnail()
    img = padding + parent + b'\x00' * 100
    mm = make_mmap(img)
    make_output_dirs(tmp_path, save_thumbnails=True)

    thumb_offset = 100 + relative_thumb_offset
    hits = [FileHit('jpeg', 100), FileHit('jpeg', thumb_offset)]

    result = process(mm, hits, tmp_path, 500 * 1024 * 1024, True, StringIO())
    mm.close()

    assert result['thumbnails'] == 1
    thumbnail = tmp_path / 'jpeg_thumbnails' / f'0x{thumb_offset:08X}.jpg'
    assert thumbnail.read_bytes() == MINIMAL_JPEG


@pytest.mark.parametrize('source', ['exact', 'damaged_jpeg_header'])
def test_process_does_not_label_non_exif_nested_jpeg_as_thumbnail(tmp_path, source):
    """APP2 안의 nested JPEG는 embedded hit이지만 Exif 썸네일로 세지 않는다."""
    payload = b'profile' + MINIMAL_JPEG + b'\x00' * 8
    app2 = b'\xff\xe2' + struct.pack('>H', len(payload) + 2) + payload
    parent = b'\xff\xd8' + app2 + b'\xff\xd9'
    offset = 100
    child_offset = offset + parent.index(MINIMAL_JPEG)
    mm = make_mmap(b'\x00' * offset + parent + b'\x00' * 32)
    make_output_dirs(tmp_path, save_thumbnails=True)

    result = process(
        mm,
        [
            FileHit('jpeg', offset),
            FileHit('jpeg', child_offset, source=source, confidence=0.85),
        ],
        tmp_path,
        500 * 1024 * 1024,
        True,
        StringIO(),
    )
    mm.close()

    assert result['jpeg'] == 1
    assert result['thumbnails'] == 0
    assert not (tmp_path / 'jpeg_thumbnails' / f'0x{child_offset:08X}.jpg').exists()


def test_process_preserves_jpeg_inside_semantically_invalid_dqt(tmp_path):
    """깨진 DQT의 선언 길이가 뒤 JPEG를 덮어도 독립 후보를 숨기지 않는다."""
    dqt_payload = b'\x00' + b'\x00' * 8 + MINIMAL_JPEG
    dqt_payload += b'\x01' * (65 - len(dqt_payload))
    dqt = b'\xff\xdb\x00\x43' + dqt_payload
    parent = b'\xff\xd8' + dqt + b'\xff\xd9'
    offset = 100
    child_offset = offset + parent.index(MINIMAL_JPEG)
    mm = make_mmap(b'\x00' * offset + parent + b'\x00' * 32)
    make_output_dirs(tmp_path, save_thumbnails=False)

    result = process(
        mm,
        [FileHit('jpeg', offset), FileHit('jpeg', child_offset)],
        tmp_path,
        500 * 1024 * 1024,
        False,
        StringIO(),
    )
    mm.close()

    assert result == {'jpeg': 2, 'avi': 0, 'thumbnails': 0, 'errors': 0}
    assert (tmp_path / 'jpeg' / f'0x{child_offset:08X}.jpg').read_bytes() == MINIMAL_JPEG


def test_process_caps_thumbnail_write_at_parent_end(tmp_path, monkeypatch):
    """썸네일 자체 경계가 잘못 커도 부모 JPEG 밖의 바이트를 저장하지 않는다."""
    parent, relative_thumb_offset = jpeg_with_embedded_thumbnail()
    offset = 100
    thumb_offset = offset + relative_thumb_offset
    mm = make_mmap(b'\x00' * offset + parent + b'outside-data' * 20)
    make_output_dirs(tmp_path, save_thumbnails=True)
    monkeypatch.setattr(carving, '_thumbnail_boundary', lambda *args, **kwargs: len(mm))

    result = process(
        mm,
        [FileHit('jpeg', offset), FileHit('jpeg', thumb_offset)],
        tmp_path,
        500 * 1024 * 1024,
        True,
        StringIO(),
    )
    app1_end = offset + len(parent) - 2
    saved = (tmp_path / 'jpeg_thumbnails' / f'0x{thumb_offset:08X}.jpg').read_bytes()
    mm.close()

    assert result['thumbnails'] == 1
    assert len(saved) == app1_end - thumb_offset


def test_process_deduplicates_identical_hits(tmp_path):
    """동일 type/offset hit이 반복돼도 한 번만 저장하고 센다."""
    offset = 32
    mm = make_mmap(b'\x00' * offset + MINIMAL_JPEG + b'\x00' * 16)
    make_output_dirs(tmp_path, save_thumbnails=False)

    result = process(
        mm,
        [FileHit('jpeg', offset), FileHit('jpeg', offset)],
        tmp_path,
        500 * 1024 * 1024,
        False,
        StringIO(),
    )
    mm.close()

    assert result['jpeg'] == 1
    assert result['errors'] == 0


def test_process_extracts_structurally_inferred_hit_inside_parent_overlap(
    tmp_path,
    monkeypatch,
):
    """손상 시작 근거가 있는 hit은 부모 범위를 보존하면서 중첩 추출한다."""
    data = b'\x00' * 128
    mm = make_mmap(data)
    make_output_dirs(tmp_path, save_thumbnails=False)

    def fake_boundary(_data, hit, *args, **kwargs):
        return ((100, True) if hit.offset == 0 else (80, False))

    monkeypatch.setattr(carving, '_jpeg_boundary', fake_boundary)
    hits = [
        FileHit('jpeg', 0),
        FileHit('jpeg', 50, source='damaged_jpeg_header', confidence=0.85),
    ]

    result = process(
        mm,
        hits,
        tmp_path,
        500 * 1024 * 1024,
        False,
        StringIO(),
    )
    mm.close()

    assert result['jpeg'] == 2
    assert (tmp_path / 'jpeg' / '0x00000000.jpg').stat().st_size == 100
    assert (tmp_path / 'jpeg' / '0x00000032.jpg').stat().st_size == 30


def test_thumbnail_fallback_respects_requested_jpeg_limit(monkeypatch):
    """썸네일 파싱 예외의 fallback도 호출자가 지정한 JPEG 상한을 쓴다."""
    monkeypatch.setattr(
        carving,
        '_jpeg_boundary',
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('bad header')),
    )
    data = b'\x00' * 512

    end = carving._thumbnail_boundary(
        data,
        FileHit('jpeg', 100),
        None,
        max_jpeg_bytes=64,
    )

    assert end == 164


@pytest.mark.parametrize('source', ['exact', 'damaged_jpeg_header'])
def test_process_does_not_count_mjpeg_frame_as_thumbnail(tmp_path, source):
    """AVI 범위 안의 JPEG는 MJPEG 프레임이며 EXIF 썸네일로 세거나 저장하지 않는다."""
    movi = _riff_chunk(b'LIST', b'movi' + _riff_chunk(b'00dc', MINIMAL_JPEG))
    avi_payload = b'AVI ' + _HDRL + movi
    avi = b'RIFF' + struct.pack('<I', len(avi_payload)) + avi_payload
    padding = b'\x00' * 100
    img = padding + avi + b'\x00' * 100
    mm = make_mmap(img)
    make_output_dirs(tmp_path, save_thumbnails=True)

    frame_offset = 100 + avi.index(MINIMAL_JPEG)
    hits = [
        FileHit('avi', 100),
        FileHit('jpeg', frame_offset, source=source, confidence=0.85),
    ]

    result = process(mm, hits, tmp_path, 500 * 1024 * 1024, True, StringIO())
    mm.close()

    assert result['avi'] == 1
    assert result['jpeg'] == 0
    assert result['thumbnails'] == 0
    assert not (tmp_path / 'jpeg_thumbnails' / f'0x{frame_offset:08X}.jpg').exists()


def test_process_failed_parent_write_does_not_claim_range(tmp_path, monkeypatch):
    """부모 저장 실패 범위가 뒤의 실제 JPEG hit을 embedded로 숨기지 않는다."""
    padding = b'\x00' * 100
    parent, relative_thumb_offset = jpeg_with_embedded_thumbnail()
    thumb_offset = 100 + relative_thumb_offset
    mm = make_mmap(padding + parent + b'\x00' * 100)
    make_output_dirs(tmp_path, save_thumbnails=False)

    real_write_range = carving.write_range
    calls = 0

    def fail_first_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError('simulated write failure')
        return real_write_range(*args, **kwargs)

    monkeypatch.setattr(carving, 'write_range', fail_first_write)
    hits = [FileHit('jpeg', 100), FileHit('jpeg', thumb_offset)]

    result = process(mm, hits, tmp_path, 500 * 1024 * 1024, False, StringIO())
    mm.close()

    assert result == {'jpeg': 1, 'avi': 0, 'thumbnails': 0, 'errors': 1}
    assert not (tmp_path / 'jpeg' / '0x00000064.jpg').exists()
    assert (tmp_path / 'jpeg' / f'0x{thumb_offset:08X}.jpg').read_bytes() == MINIMAL_JPEG


def test_write_range_uses_fixed_size_chunks(tmp_path):
    class TrackingData:
        def __init__(self, payload: bytes):
            self.payload = payload
            self.slice_sizes: list[int] = []

        def __len__(self):
            return len(self.payload)

        def __getitem__(self, key):
            self.slice_sizes.append(key.stop - key.start)
            return self.payload[key]

    data = TrackingData(b'0123456789')
    out_path = tmp_path / 'chunked.bin'

    write_range(data, 1, 9, out_path, chunk_size=3)

    assert out_path.read_bytes() == b'12345678'
    assert data.slice_sizes == [3, 3, 2]


def test_write_range_failure_preserves_existing_output(tmp_path):
    class FailingData:
        def __len__(self):
            return 8

        def __getitem__(self, key):
            if key.start >= 4:
                raise OSError('simulated read failure')
            return b'new!'

    out_path = tmp_path / 'atomic.bin'
    out_path.write_bytes(b'old')

    with pytest.raises(OSError, match='simulated read failure'):
        write_range(FailingData(), 0, 8, out_path, chunk_size=4)

    assert out_path.read_bytes() == b'old'
    assert list(tmp_path.glob('.atomic.bin.*.tmp')) == []


def test_process_counts_errors_without_crash(tmp_path):
    """추출 중 예외가 발생해도 프로그램이 중단되지 않고 error로 기록한다."""
    img = b'\x00' * 200
    mm = make_mmap(img)
    make_output_dirs(tmp_path, save_thumbnails=False)

    # RIFF 없는 위치에 avi hit → avi_end가 ValueError 발생
    hits = [FileHit('avi', 50)]

    result = process(mm, hits, tmp_path, 500 * 1024 * 1024, False, StringIO())
    mm.close()

    assert result['errors'] == 1
    assert result['avi'] == 0


def test_cli_forwards_jpeg_and_avi_size_limits(tmp_path, monkeypatch):
    image_path = tmp_path / 'disk.img'
    image_path.write_bytes(MINIMAL_JPEG)
    output_path = tmp_path / 'out'
    captured = {}

    monkeypatch.setattr(carve_cli, 'find_all_hits', lambda mm: [FileHit('jpeg', 0)])

    def fake_process(mm, hits, output_dir, max_avi, save_thumbnails, error_log, max_jpeg):
        captured.update(max_avi=max_avi, max_jpeg=max_jpeg, output_dir=output_dir)
        return {'jpeg': 0, 'avi': 0, 'thumbnails': 0, 'errors': 0}

    monkeypatch.setattr(carve_cli, 'process', fake_process)
    monkeypatch.setattr(
        'sys.argv',
        [
            'carve.py',
            str(image_path),
            '-o',
            str(output_path),
            '--max-avi-size',
            '3',
            '--max-jpeg-size',
            '2',
        ],
    )

    carve_cli.main()

    assert captured == {
        'max_avi': 3 * 1024 * 1024,
        'max_jpeg': 2 * 1024 * 1024,
        'output_dir': output_path,
    }


@pytest.mark.parametrize('option', ['--max-avi-size', '--max-jpeg-size'])
def test_cli_rejects_nonpositive_size_limit(tmp_path, monkeypatch, option):
    image_path = tmp_path / 'disk.img'
    image_path.write_bytes(MINIMAL_JPEG)
    monkeypatch.setattr('sys.argv', ['carve.py', str(image_path), option, '0'])

    with pytest.raises(SystemExit) as exc:
        carve_cli.main()

    assert exc.value.code == 1
