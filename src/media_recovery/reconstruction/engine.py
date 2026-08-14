"""기존 reconstruction 공개 호출 표면을 보존하는 호환 façade."""
from __future__ import annotations

from pathlib import Path

from media_recovery.reconstruction.entropy import recover, recover_bytes
from media_recovery.reconstruction.metrics import (
    gray_fraction,
    undecoded_fraction,
)

__all__ = [
    'gray_fraction',
    'recover',
    'recover_bytes',
    'recover_file',
    'undecoded_fraction',
]


def recover_file(src_path: Path, out_dir: Path, quality: int = 95,
                 time_budget=90.0, resync_near=300000, resync_full=True):
    """파일 하나를 계산한 뒤 기존 action 경로에 materialize한다."""
    from media_recovery.reconstruction import legacy_output, single_best

    data = src_path.read_bytes()
    result = single_best.reconstruct_single_best(
        data,
        time_budget=time_budget,
        resync_near=resync_near,
        resync_full=resync_full,
    )
    return legacy_output.materialize_result(
        result, src_path, out_dir, quality=quality
    )
