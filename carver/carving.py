import mmap
import os
import struct
import tempfile
from pathlib import Path
from typing import Sequence, TextIO

from tqdm import tqdm

from carver.extractors import (
    JPEG_MAX_FALLBACK_SIZE,
    _segment_semantics_valid,
    avi_end,
    jpeg_end,
)
from carver.models import FileHit


WRITE_CHUNK_SIZE = 8 * 1024 * 1024


def make_output_dirs(output_dir: Path, save_thumbnails: bool) -> None:
    (output_dir / 'jpeg').mkdir(parents=True, exist_ok=True)
    (output_dir / 'avi').mkdir(parents=True, exist_ok=True)
    if save_thumbnails:
        (output_dir / 'jpeg_thumbnails').mkdir(parents=True, exist_ok=True)


def is_in_range(offset: int, ranges: list[tuple[int, int]]) -> bool:
    """기존 공개 API 호환용 범위 판정 함수.

    실제 카빙 순회는 정렬된 히트와 단일 활성 범위를 이용해 O(1)로 판정한다.
    """
    return any(start < offset < end for start, end in ranges)


def write_range(
    data: bytes | bytearray | mmap.mmap,
    start: int,
    end: int,
    out_path: Path,
    *,
    chunk_size: int = WRITE_CHUNK_SIZE,
) -> None:
    """data[start:end]를 고정 크기 청크로 임시 파일에 저장한 뒤 교체한다.

    같은 디렉터리의 임시 파일을 완성한 뒤 교체하므로 쓰기 실패 시 기존 결과나
    부분 출력 파일을 최종 경로에 남기지 않는다. fsync 기반 전원 장애 내구성까지
    보장하는 저장 프로토콜은 아니다.
    """
    if chunk_size <= 0:
        raise ValueError('chunk_size는 0보다 커야 합니다')
    if start < 0 or end < start or end > len(data):
        raise ValueError(f'잘못된 출력 범위: {start:#x}..{end:#x}')

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='wb',
            dir=out_path.parent,
            prefix=f'.{out_path.name}.',
            suffix='.tmp',
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            pos = start
            while pos < end:
                chunk_end = min(pos + chunk_size, end)
                temp_file.write(data[pos:chunk_end])
                pos = chunk_end

        os.replace(temp_path, out_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _hit_offset(hit: FileHit) -> int:
    return int(getattr(hit, 'offset'))


def _hit_type(hit: FileHit) -> str:
    return str(getattr(hit, 'file_type'))


def _ordered_hits(hits: Sequence[FileHit]) -> list[FileHit]:
    """히트를 정렬하고 동일 type/offset 중 가장 강한 근거 하나만 남긴다."""
    unique: dict[tuple[str, int], FileHit] = {}
    for hit in hits:
        key = (_hit_type(hit), _hit_offset(hit))
        previous = unique.get(key)
        if previous is None:
            unique[key] = hit
            continue
        previous_source = str(getattr(previous, 'source', 'exact'))
        source = str(getattr(hit, 'source', 'exact'))
        previous_rank = (
            previous_source == 'exact',
            float(getattr(previous, 'confidence', 0.0)),
        )
        rank = (source == 'exact', float(getattr(hit, 'confidence', 0.0)))
        if rank > previous_rank:
            unique[key] = hit
    return sorted(unique.values(), key=lambda hit: (_hit_offset(hit), _hit_type(hit)))


def _jpeg_boundary(
    data: bytes | bytearray | mmap.mmap,
    hit: FileHit,
    next_sig: int | None,
    boundary_offsets: Sequence[int] | None = None,
    avi_offsets: Sequence[int] | None = None,
    max_jpeg_bytes: int = JPEG_MAX_FALLBACK_SIZE,
) -> tuple[int, bool]:
    source = str(getattr(hit, 'source', 'exact'))
    damaged_header = bool(getattr(hit, 'damaged_header', False))
    return jpeg_end(
        data,
        _hit_offset(hit),
        next_sig,
        allow_corrupt_header=(damaged_header or source == 'damaged_jpeg_header'),
        boundary_offsets=boundary_offsets,
        avi_offsets=avi_offsets,
        max_size=max_jpeg_bytes,
        validated_scan_start=getattr(hit, 'scan_start', None),
    )


def _thumbnail_boundary(
    data: bytes | bytearray | mmap.mmap,
    hit: FileHit,
    next_sig: int | None,
    boundary_offsets: Sequence[int] | None = None,
    avi_offsets: Sequence[int] | None = None,
    max_jpeg_bytes: int = JPEG_MAX_FALLBACK_SIZE,
) -> int:
    offset = _hit_offset(hit)
    try:
        end, _ = _jpeg_boundary(
            data,
            hit,
            next_sig,
            boundary_offsets,
            avi_offsets,
            max_jpeg_bytes,
        )
        return end
    except (ValueError, struct.error):
        fallback = next_sig if next_sig is not None else offset + max_jpeg_bytes
        return min(fallback, len(data))


def _header_segment_container(
    data: bytes | bytearray | mmap.mmap,
    parent_start: int,
    parent_end: int,
    child_offset: int,
) -> tuple[int, bool] | None:
    """child_offset을 포함한 pre-SOS 길이형 세그먼트 끝과 Exif 여부를 반환한다."""
    if data[parent_start:parent_start + 2] != b'\xff\xd8':
        return None
    pos = parent_start + 2
    stop = min(parent_end, len(data))
    while pos + 1 < stop and pos < child_offset:
        if data[pos] != 0xFF:
            return None
        marker_pos = pos + 1
        while marker_pos < stop and data[marker_pos] == 0xFF:
            marker_pos += 1
        if marker_pos >= stop:
            return None
        marker = int(data[marker_pos])
        if marker in (0xD9, 0xDA):
            return None
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            pos = marker_pos + 1
            continue
        if marker_pos + 3 > stop:
            return None
        seg_len = struct.unpack('>H', data[marker_pos + 1:marker_pos + 3])[0]
        if seg_len < 2:
            return None
        segment_end = marker_pos + 1 + seg_len
        if segment_end > stop:
            return None
        if not _segment_semantics_valid(
            data,
            marker_pos,
            marker,
            seg_len,
            segment_end,
        ):
            return None
        payload_start = marker_pos + 3
        if payload_start <= child_offset < segment_end:
            is_exif = (
                marker == 0xE1
                and data[payload_start:payload_start + 6] == b'Exif\x00\x00'
            )
            return segment_end, is_exif
        pos = segment_end
    return None


def _exif_container_end(
    data: bytes | bytearray | mmap.mmap,
    parent_start: int,
    parent_end: int,
    child_offset: int,
) -> int | None:
    """child_offset을 포함한 Exif APP1의 exclusive 끝을 반환한다."""
    container = _header_segment_container(
        data,
        parent_start,
        parent_end,
        child_offset,
    )
    if container is None:
        return None
    segment_end, is_exif = container
    return segment_end if is_exif else None


def _is_exif_thumbnail_offset(
    data: bytes | bytearray | mmap.mmap,
    parent_start: int,
    parent_end: int,
    child_offset: int,
) -> bool:
    """기존 내부 호출·테스트 호환용 Exif containment predicate."""
    return _exif_container_end(data, parent_start, parent_end, child_offset) is not None


def _next_external_hit_offset(
    data: bytes | bytearray | mmap.mmap,
    ordered_hits: Sequence[FileHit],
    index: int,
    max_jpeg_bytes: int,
) -> int | None:
    """현재 JPEG의 pre-SOS 세그먼트 안쪽 hit을 건너뛴 다음 후보를 반환한다.

    APP/테이블 payload 안의 내장 시그니처가 정렬상 바로 다음 hit이면, 그 값을
    잘린 부모의 fallback 경계로 넘길 수 없다. 선언된 세그먼트를 파싱해 내부
    hit만 건너뛰고 뒤의 독립 후보는 경계로 보존한다.
    """
    parent = ordered_hits[index]
    parent_offset = _hit_offset(parent)
    parent_is_jpeg = _hit_type(parent) == 'jpeg'
    parent_cap = min(parent_offset + max_jpeg_bytes, len(data))

    for candidate_index in range(index + 1, len(ordered_hits)):
        candidate = ordered_hits[candidate_index]
        candidate_offset = _hit_offset(candidate)
        if (
            parent_is_jpeg
            and _header_segment_container(
                data,
                parent_offset,
                parent_cap,
                candidate_offset,
            ) is not None
        ):
            continue
        return candidate_offset
    return None


def process(
    mm: mmap.mmap,
    hits: Sequence[FileHit],
    output_dir: Path,
    max_avi_bytes: int,
    save_thumbnails: bool,
    error_log: TextIO,
    max_jpeg_bytes: int = JPEG_MAX_FALLBACK_SIZE,
) -> dict[str, int]:
    ordered_hits = _ordered_hits(hits)
    # 스캐너가 구조 검증한 JPEG 시작을 모두 넘긴다. extractor는 inter-scan
    # APP/COM payload를 먼저 건너뛴 뒤 이 인덱스를 적용하므로 Exif 내부 hit은
    # 부모를 자르지 않으면서 DHT-start·손상 시작도 외부 경계로 보존된다.
    boundary_offsets = tuple(
        _hit_offset(hit)
        for hit in ordered_hits
        if _hit_type(hit) == 'jpeg'
    )
    avi_offsets = tuple(
        _hit_offset(hit) for hit in ordered_hits if _hit_type(hit) == 'avi'
    )
    jpeg_count = avi_count = thumb_count = error_count = 0

    # 히트가 정렬되어 있고 성공한 최상위 추출 범위는 서로 겹치지 않으므로
    # 전체 범위 목록 대신 마지막 활성 범위만 보면 된다.
    active_start = active_end = -1
    active_owner: str | None = None

    for i, hit in enumerate(tqdm(ordered_hits, desc='추출 중', unit='파일')):
        next_sig = _next_external_hit_offset(
            mm,
            ordered_hits,
            i,
            max_jpeg_bytes,
        )
        offset = _hit_offset(hit)
        file_type = _hit_type(hit)

        try:
            embedded = active_start < offset < active_end
            source = str(getattr(hit, 'source', 'exact'))
            header_container = (
                _header_segment_container(
                    mm,
                    active_start,
                    active_end,
                    offset,
                )
                if embedded and active_owner == 'jpeg'
                else None
            )
            exif_end = (
                header_container[0]
                if header_container is not None and header_container[1]
                else None
            )
            is_exif_thumbnail = file_type == 'jpeg' and exif_end is not None
            inferred_overlap = (
                embedded
                and active_owner == 'jpeg'
                and source in ('damaged_jpeg_header', 'damaged_avi_header')
                and header_container is None
            )

            if embedded and not inferred_overlap:
                # JPEG 안의 EXIF 썸네일만 썸네일이다. AVI 내부 JPEG는 MJPEG 프레임이므로
                # 별도 이미지나 썸네일로 세지 않는다.
                if is_exif_thumbnail:
                    if save_thumbnails:
                        end = min(
                            _thumbnail_boundary(
                                mm,
                                hit,
                                next_sig,
                                boundary_offsets,
                                avi_offsets,
                                max_jpeg_bytes,
                            ),
                            active_end,
                            exif_end,
                        )
                        out_path = output_dir / 'jpeg_thumbnails' / f'0x{offset:08X}.jpg'
                        write_range(mm, offset, end, out_path)
                        thumb_count += 1
                        tqdm.write(f'[THUMB] JPEG at 0x{offset:08X} → {out_path}')
                    else:
                        thumb_count += 1
                        tqdm.write(
                            f'[THUMB] JPEG at 0x{offset:08X} '
                            '→ skipped (embedded thumbnail)'
                        )
                continue

            if file_type == 'jpeg':
                end, complete = _jpeg_boundary(
                    mm,
                    hit,
                    next_sig,
                    boundary_offsets,
                    avi_offsets,
                    max_jpeg_bytes,
                )
                out_path = output_dir / 'jpeg' / f'0x{offset:08X}.jpg'
                write_range(mm, offset, end, out_path)

                # 저장이 끝난 범위만 이후 히트를 덮는 것으로 간주한다.
                if inferred_overlap:
                    active_end = max(active_end, end)
                else:
                    active_start, active_end, active_owner = offset, end, 'jpeg'
                jpeg_count += 1
                warn = '' if complete else ' [불완전, fallback 사용]'
                tqdm.write(
                    f'[FOUND] JPEG at 0x{offset:08X} → {out_path} '
                    f'({(end - offset) / 1024:.1f} KB){warn}'
                )

            elif file_type == 'avi':
                end, used_header = avi_end(
                    mm,
                    offset,
                    max_avi_bytes,
                    next_sig,
                    allow_corrupt_header=(source == 'damaged_avi_header'),
                )
                out_path = output_dir / 'avi' / f'0x{offset:08X}.avi'
                write_range(mm, offset, end, out_path)

                if inferred_overlap:
                    active_end = max(active_end, end)
                else:
                    active_start, active_end, active_owner = offset, end, 'avi'
                avi_count += 1
                warn = '' if used_header else ' [fallback 사용]'
                tqdm.write(
                    f'[FOUND] AVI  at 0x{offset:08X} → {out_path} '
                    f'({(end - offset) / 1024 / 1024:.1f} MB){warn}'
                )

        except Exception as e:
            error_count += 1
            msg = f'오류 at 0x{offset:08X} ({file_type}): {e}'
            tqdm.write(f'[ERROR] {msg}')
            error_log.write(msg + '\n')

    return {
        'jpeg': jpeg_count,
        'avi': avi_count,
        'thumbnails': thumb_count,
        'errors': error_count,
    }
