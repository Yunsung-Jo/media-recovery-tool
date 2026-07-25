# thumbref.py
"""recover 산출물에 썸네일 오라클 보정을 일괄 적용합니다.

카빙 원본의 EXIF 썸네일을 정답 근사로 사용해, 복구본에 남은 순환 MCU 밀림과
색 캐스트 밴드를 추정·보정합니다. 근거가 없는 파일은 바이트 그대로 복사하고,
시프트를 적용했는데 self-check가 개선되지 않으면 되돌립니다(rollback).

사용 예:
    python thumbref.py output_c3/jpeg output_c3/jpeg_recovered \
        -o output_c3/jpeg_recovered_thumbref -j 6
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import time
from functools import partial
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image
from tqdm import tqdm

from carver.thumbref import parse_mcu_size, process_file

# PIL subsampling 인자: 0=4:4:4, 1=4:2:2, 2=4:2:0
_SUBSAMPLING = {(8, 8): 0, (16, 8): 1, (16, 16): 2}


def _save_corrected(corrected, orig_path: Path, rec_path: Path, out_path: Path):
    """복구본 qtables + 원본 서브샘플링으로 저장해 추가 손실을 최소화한다."""
    im = Image.fromarray(np.clip(corrected, 0, 255).astype(np.uint8))
    kwargs = {}
    try:
        with Image.open(rec_path) as src:
            qt = getattr(src, "quantization", None)
            if qt:
                kwargs["qtables"] = qt
    except Exception:
        pass
    ms = parse_mcu_size(orig_path.read_bytes())
    sub = _SUBSAMPLING.get(ms) if ms else None
    if sub is not None:
        kwargs["subsampling"] = sub
    if "qtables" not in kwargs:
        kwargs["quality"] = 95
    im.save(out_path, "JPEG", **kwargs)


def _work(rec_path: Path, orig_dir: Path, rec_root: Path, out_root: Path):
    rel = rec_path.relative_to(rec_root)
    out_path = out_root / rel
    out_path.parent.mkdir(parents=True, exist_ok=True)
    orig_path = orig_dir / rec_path.name
    t0 = time.time()
    try:
        if not orig_path.exists():
            shutil.copyfile(rec_path, out_path)
            return rec_path.name, "skip_no_orig", {}, round(time.time() - t0, 1), None
        status, corrected, info = process_file(orig_path, rec_path)
        if status == "corrected":
            _save_corrected(corrected, orig_path, rec_path, out_path)
        else:
            shutil.copyfile(rec_path, out_path)
        return rec_path.name, status, info, round(time.time() - t0, 1), None
    except Exception as e:  # noqa: BLE001 — 배치 견고성 위해 모든 예외 포착
        try:
            shutil.copyfile(rec_path, out_path)
        except Exception:  # noqa: BLE001
            pass
        return rec_path.name, "error", {}, round(time.time() - t0, 1), str(e)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="recover 산출물에 EXIF 썸네일 오라클 밀림·색 보정을 적용합니다.")
    parser.add_argument("orig", help="카빙 원본 디렉토리 (예: output_c3/jpeg)")
    parser.add_argument("recovered", help="recover 출력 디렉토리 (예: output_c3/jpeg_recovered)")
    parser.add_argument("-o", "--output", default=None,
                        help="출력 디렉토리 (기본: <recovered>_thumbref)")
    parser.add_argument("-j", "--jobs", type=int, default=0,
                        help="병렬 프로세스 수 (기본: 0=CPU 수, 1=순차)")
    args = parser.parse_args()

    orig_dir = Path(args.orig)
    rec_root = Path(args.recovered)
    if not orig_dir.is_dir() or not rec_root.is_dir():
        print("오류: 입력 디렉토리를 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)
    out_root = (Path(args.output) if args.output
                else rec_root.parent / (rec_root.name + "_thumbref"))
    out_root.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in rec_root.rglob("*.jpg") if p.is_file())
    if not files:
        print("복구본 JPEG을 찾을 수 없습니다.")
        return

    jobs = args.jobs if args.jobs > 0 else (os.cpu_count() or 4)
    work = partial(_work, orig_dir=orig_dir, rec_root=rec_root, out_root=out_root)

    fieldnames = ["filename", "status", "reg_score", "s", "dy", "framing",
                  "shift_rows", "shift_iters", "max_units", "color_rows",
                  "band_std0", "band_res", "dmatch", "secs"]
    counts: dict[str, int] = {}
    with open(out_root / "report_thumbref.csv", "w", newline="",
              encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        bar = tqdm(total=len(files), desc="thumbref", unit="파일")

        def emit(name, status, info, secs, err):
            row = dict(filename=name, status=status, secs=secs, **info)
            writer.writerow(row)
            counts[status] = counts.get(status, 0) + 1
            if err:
                tqdm.write(f"[ERROR] {name}: {err}")
            bar.update(1)

        if jobs == 1:
            for p in files:
                emit(*work(p))
        else:
            with Pool(jobs) as pool:
                for result in pool.imap_unordered(work, files, chunksize=4):
                    emit(*result)
        bar.close()

    print(f"\n완료. 리포트: {out_root / 'report_thumbref.csv'}")
    for status, cnt in sorted(counts.items()):
        print(f"  {status}: {cnt}개")


if __name__ == "__main__":
    main()
