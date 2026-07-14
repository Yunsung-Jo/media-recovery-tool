import argparse
import mmap
import sys
from pathlib import Path

from carver.carving import is_in_range, make_output_dirs, process
from carver.extractors import (
    JPEG_MAX_FALLBACK_SIZE,
    MAX_AVI_SIZE_DEFAULT,
    avi_end,
    jpeg_end,
)
from carver.models import FileHit
from carver.scanner import find_all_hits


def main() -> None:
    parser = argparse.ArgumentParser(
        description='손상된 디스크 이미지에서 JPEG/AVI 파일을 추출합니다.'
    )
    parser.add_argument('image', help='디스크 이미지 파일 경로')
    parser.add_argument('-o', '--output', default='output', help='출력 디렉터리 (기본: output)')
    parser.add_argument('--max-avi-size', type=int,
                        default=MAX_AVI_SIZE_DEFAULT // 1024 // 1024, metavar='MB',
                        help='AVI 최대 크기 MB (기본: 500)')
    parser.add_argument(
        '--max-jpeg-size',
        type=int,
        default=JPEG_MAX_FALLBACK_SIZE // 1024 // 1024,
        metavar='MB',
        help='JPEG 경계 탐색 및 추출 최대 크기 MB (기본: 10)',
    )
    parser.add_argument(
        '--save-thumbnails',
        action='store_true',
        help='Exif APP1 내부 JPEG도 jpeg_thumbnails/에 저장',
    )
    args = parser.parse_args()

    image_path = Path(args.image)
    output_dir = Path(args.output)
    max_avi_bytes = args.max_avi_size * 1024 * 1024
    max_jpeg_bytes = args.max_jpeg_size * 1024 * 1024

    if not image_path.is_file():
        print(f'오류: 파일을 찾을 수 없습니다: {image_path}', file=sys.stderr)
        sys.exit(1)
    if max_avi_bytes <= 0 or max_jpeg_bytes <= 0:
        print('오류: 최대 파일 크기는 0보다 커야 합니다.', file=sys.stderr)
        sys.exit(1)

    make_output_dirs(output_dir, args.save_thumbnails)

    image_size = image_path.stat().st_size
    print(f'Scanning {image_path} ({image_size / 1024 / 1024:.2f} MB)...')
    print('시그니처 탐색 중...')

    with open(image_path, 'rb') as image_file:
        with mmap.mmap(image_file.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            hits = find_all_hits(mm)
            print(f'시작 후보 발견: {len(hits)}개')

            with open(output_dir / 'errors.log', 'a', encoding='utf-8') as error_log:
                result = process(
                    mm,
                    hits,
                    output_dir,
                    max_avi_bytes,
                    args.save_thumbnails,
                    error_log,
                    max_jpeg_bytes,
                )

    print(
        f'\nScan complete. '
        f"JPEG: {result['jpeg']}, "
        f"AVI: {result['avi']}, "
        f"Thumbnails: {result['thumbnails']}, "
        f"Errors: {result['errors']}"
    )


if __name__ == '__main__':
    main()
