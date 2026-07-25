# recover.py
from __future__ import annotations
import argparse
import csv
import os
import sys
from functools import partial
from multiprocessing import Pool
from pathlib import Path

from tqdm import tqdm

from carver.resync import recover_file


def _work(path: Path, out_dir: Path, quality: int, time_budget, near: int, full: bool):
    """워커: 파일 1개 복구. 예외는 ERROR 액션으로 변환해 반환."""
    try:
        _out, action, info = recover_file(
            path, out_dir, quality=quality,
            time_budget=time_budget, resync_near=near, resync_full=full)
        return path.name, action, info, None
    except Exception as e:  # noqa: BLE001 — 배치 견고성 위해 모든 예외 포착
        try:
            err_path = out_dir / 'error' / path.name
            err_path.parent.mkdir(parents=True, exist_ok=True)
            err_path.write_bytes(path.read_bytes())
        except Exception:  # noqa: BLE001 — 복사 실패해도 분류·기록은 유지
            pass
        return path.name, 'ERROR', {}, str(e)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='rawcarve가 추출한 손상 JPEG를 resync 엔진으로 복구합니다.'
    )
    parser.add_argument('input', help='입력 디렉토리 (예: output/jpeg/)')
    parser.add_argument('-o', '--output', default=None,
                        help='출력 디렉토리 (기본: <input>_recovered)')
    parser.add_argument('-q', '--quality', type=int, default=95,
                        help='복구본 JPEG 품질 (기본: 95)')
    parser.add_argument('-j', '--jobs', type=int, default=0,
                        help='병렬 프로세스 수 (기본: 0=CPU 수, 1=순차)')
    parser.add_argument('--fast', action='store_true',
                        help='빠른 모드(부분 복구 감수). 기본은 철저 모드(복구율↑, 느림)')
    parser.add_argument('--time-budget', type=float, default=None, metavar='SEC',
                        help='복구 탐색 1회당 시간 상한(초). 0=무제한. 기본: 철저 90, --fast 20')
    args = parser.parse_args()

    # 철저(기본)↔빠른(--fast) 프리셋
    if args.fast:
        near, full, default_budget = 160000, False, 20.0
    else:
        near, full, default_budget = 300000, True, 90.0
    budget = args.time_budget if args.time_budget is not None else default_budget
    if budget is not None and budget <= 0:
        budget = None  # 무제한

    in_dir = Path(args.input)
    if not in_dir.is_dir():
        print(f'오류: 디렉토리를 찾을 수 없습니다: {in_dir}', file=sys.stderr)
        sys.exit(1)

    out_dir = (Path(args.output) if args.output
               else in_dir.parent / (in_dir.name + '_recovered'))
    out_dir.mkdir(parents=True, exist_ok=True)
    for sub in ('recovered', 'header_recovered', 'clean', 'failed',
                'skip_undecodable', 'error'):
        (out_dir / sub).mkdir(exist_ok=True)

    jpeg_files = sorted(in_dir.glob('*.jpg'))
    if not jpeg_files:
        print('JPEG 파일을 찾을 수 없습니다.')
        return

    fieldnames = [
        'filename', 'action', 'gray_before', 'gray_after',
        'undec_before', 'undec_after', 'undec_delta', 'worse', 'recover_sec',
        'ops', 'sub', 'del', 'ins', 'resync', 'hole', 'mcus', 'image_size',
        'shifted', 'mcu_ins', 'mcu_drop', 'shift_margin', 'shift_reject',
        'spatial_changed', 'row_global_passes', 'row_global_changes',
        'row_local_cuts',
        'header_fix',
    ]
    counts: dict[str, int] = {}
    stat_rows: list[tuple[str, int, float, float]] = []  # (action, mcus, undec_before, undec_after)
    # spatial_changed, shifted bands, inserted, dropped, rejected
    shift_rows: list[tuple[int, int, int, int, int]] = []
    hdr_fixes: list[str] = []  # 헤더 복구된 파일의 header_fix 태그
    jobs = args.jobs if args.jobs > 0 else (os.cpu_count() or 4)
    work = partial(_work, out_dir=out_dir, quality=args.quality,
                   time_budget=budget, near=near, full=full)

    def emit(name, action, info, err, writer):
        row = {k: '' for k in fieldnames}
        row['filename'] = name
        row['action'] = action
        if info:
            delta = info['undec_after'] - info['undec_before']
            row['gray_before'] = f"{info['gray_before']:.3f}"
            row['gray_after'] = f"{info['gray_after']:.3f}"
            row['undec_before'] = f"{info['undec_before']:.3f}"
            row['undec_after'] = f"{info['undec_after']:.3f}"
            row['undec_delta'] = f"{delta:+.3f}"
            row['worse'] = '1' if delta > 0.01 else ''
            row['recover_sec'] = f"{info['recover_sec']:.2f}"
            row['ops'] = info['ops']
            row['sub'] = info['sub']
            row['del'] = info['dele']
            row['ins'] = info['ins']
            row['resync'] = info['resync']
            row['hole'] = info['hole']
            row['mcus'] = info['mcus']
            row['image_size'] = f"{info['width']}x{info['height']}"
            row['shifted'] = info.get('shifted', 0)
            row['mcu_ins'] = info.get('mcu_ins', 0)
            row['mcu_drop'] = info.get('mcu_drop', 0)
            row['shift_margin'] = f"{info.get('shift_margin', 0.0):.3f}"
            row['shift_reject'] = info.get('shift_reject', 0) or ''
            spatial_changed = int(info.get('spatial_changed', bool(
                info.get('shifted', 0)
                or info.get('row_global_passes', 0)
                or info.get('row_local_cuts', 0)
                or info.get('row_shifted', 0))))
            row['spatial_changed'] = spatial_changed
            row['row_global_passes'] = int(
                info.get('row_global_passes', 0))
            row['row_global_changes'] = int(
                info.get('row_global_changes', 0))
            row['row_local_cuts'] = int(info.get('row_local_cuts', 0))
            row['header_fix'] = info.get('header_fix', '')
            stat_rows.append((action, info['mcus'],
                              info['undec_before'], info['undec_after']))
            if info.get('header_fix'):
                hdr_fixes.append(info['header_fix'])
            shift_rows.append((
                spatial_changed,
                int(info.get('shifted', 0)), int(info.get('mcu_ins', 0)),
                int(info.get('mcu_drop', 0)), int(info.get('shift_reject', 0))))
        writer.writerow(row)
        counts[action] = counts.get(action, 0) + 1
        if err:
            tqdm.write(f'[ERROR] {name}: {err}')

    with open(out_dir / 'report.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        bar = tqdm(total=len(jpeg_files), desc='복구 중', unit='파일')
        if jobs == 1:
            for path in jpeg_files:
                emit(*_work(path, out_dir, args.quality, budget, near, full), writer)
                bar.update(1)
        else:
            with Pool(jobs) as pool:
                for result in pool.imap_unordered(work, jpeg_files, chunksize=4):
                    emit(*result, writer)
                    bar.update(1)
        bar.close()

    print(f"\n완료. 리포트: {out_dir / 'report.csv'}")
    for action, cnt in sorted(counts.items()):
        print(f'  {action}: {cnt}개')
    corrected_files = sum(1 for changed, *_rest in shift_rows if changed)
    if corrected_files or any(row[4] for row in shift_rows):
        print('MCU 밀림 보정: '
              f'{corrected_files}개 파일, '
              f'{sum(row[1] for row in shift_rows)}개 밴드, '
              f'삽입 {sum(row[2] for row in shift_rows)} MCU, '
              f'유실 {sum(row[3] for row in shift_rows)} MCU, '
              f'안전 한도 기각 {sum(row[4] for row in shift_rows)}개')

    # 헤더 복구 요약 — 교체 세그먼트 조합별 건수 + undec 평균
    if hdr_fixes:
        from collections import Counter
        combos = ', '.join(f'{tag or "(무변경)"} {n}개'
                           for tag, n in sorted(Counter(hdr_fixes).items()))
        hr = [r for r in stat_rows if r[0] == 'HEADER_RECOVERED']
        hua = f", undec_after 평균 {sum(r[3] for r in hr) / len(hr):.3f}" if hr else ''
        print(f'헤더 복구: {len(hdr_fixes)}개{hua} - {combos}')

    # 층화 요약 — 악화·크기 대역이 평균에 가려지지 않게 한다
    rec = [r for r in stat_rows if r[0] == 'RECOVERED']
    if rec:
        ub = sum(r[2] for r in rec) / len(rec)
        ua = sum(r[3] for r in rec) / len(rec)
        print(f'RECOVERED undec 평균: {ub:.3f} → {ua:.3f}')
    worse = [(a, m, b, x) for a, m, b, x in stat_rows if x - b > 0.01]
    print(f'악화(Δundec > +0.01): {len(worse)}개'
          + (f' - ' + ', '.join(f'{a} {n}개' for a, n in sorted(
              {a: sum(1 for w in worse if w[0] == a) for a in {w[0] for w in worse}}.items()))
             if worse else ''))
    bands = [('<120', 0, 120), ('120-449', 120, 450),
             ('450-899', 450, 900), ('>=900', 900, 1 << 60)]
    graded = [r for r in stat_rows if r[0] in ('RECOVERED', 'HEADER_RECOVERED', 'FAILED')]
    if graded:
        print('MCU 대역별 (RECOVERED+HEADER_RECOVERED+FAILED):')
        for label, lo, hi in bands:
            grp = [r for r in graded if lo <= r[1] < hi]
            if not grp:
                continue
            mean_ua = sum(r[3] for r in grp) / len(grp)
            n_bad = sum(1 for r in grp if r[3] > 0.15)
            print(f'  {label:>8}: {len(grp):4d}개, undec_after 평균 {mean_ua:.3f}, >0.15 {n_bad}개')


if __name__ == '__main__':
    main()
