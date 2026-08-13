"""reconstruct CLI 워커 동작 검증."""
import csv
from pathlib import Path

from media_recovery.cli import reconstruct


def test_work_error_copies_original(tmp_path, monkeypatch):
    """recover_file이 예외를 던지면 원본을 error/ 폴더에 복사하고 ERROR를 반환한다."""
    src = tmp_path / '0xBADF00D.jpg'
    raw = b'\xff\xd8not-a-real-jpeg\xff\xd9'
    src.write_bytes(raw)

    def boom(*args, **kwargs):
        raise RuntimeError('decode blew up')

    monkeypatch.setattr(reconstruct, 'recover_file', boom)

    name, action, info, err = reconstruct._work(
        src, tmp_path, quality=95, time_budget=None, near=300000, full=True)

    assert action == 'ERROR'
    assert err == 'decode blew up'
    copied = tmp_path / 'error' / '0xBADF00D.jpg'
    assert copied.read_bytes() == raw


def test_report_counts_global_only_spatial_correction(
        tmp_path, monkeypatch, capsys):
    in_dir = tmp_path / 'input'
    out_dir = tmp_path / 'output'
    in_dir.mkdir()
    (in_dir / '0xA11A1A11.jpg').write_bytes(b'placeholder')
    info = {
        'gray_before': 0.0, 'gray_after': 0.0,
        'undec_before': 0.0, 'undec_after': 0.0,
        'recover_sec': 1.25, 'ops': 0,
        'sub': 0, 'dele': 0, 'ins': 0, 'resync': 0, 'hole': 0,
        'mcus': 100, 'width': 160, 'height': 160,
        'shifted': 0, 'mcu_ins': 5, 'mcu_drop': 5,
        'shift_margin': 0.0, 'shift_reject': 0,
        'row_global_passes': 2, 'row_global_changes': 3,
        'row_local_cuts': 0,
    }

    class QuietBar:
        def __init__(self, *args, **kwargs):
            pass

        def update(self, *_args):
            pass

        def close(self):
            pass

        @staticmethod
        def write(*_args, **_kwargs):
            pass

    monkeypatch.setattr(
        reconstruct, '_work',
        lambda path, *_args, **_kwargs: (
            path.name, 'RECOVERED', info, None))
    monkeypatch.setattr(reconstruct, 'tqdm', QuietBar)
    monkeypatch.setattr(reconstruct.sys, 'argv', [
        'media-recovery reconstruct', str(in_dir), '-o', str(out_dir),
        '-j', '1', '--time-budget', '0',
    ])

    reconstruct.main()

    with (out_dir / 'report.csv').open(
            newline='', encoding='utf-8') as report_file:
        row = next(csv.DictReader(report_file))
    assert row['spatial_changed'] == '1'
    assert row['row_global_passes'] == '2'
    assert row['row_global_changes'] == '3'
    assert row['row_local_cuts'] == '0'
    assert 'MCU 밀림 보정: 1개 파일' in capsys.readouterr().out
