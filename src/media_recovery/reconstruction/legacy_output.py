"""현행 action별 JPEG/original-byte output materialization."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from media_recovery.reconstruction.single_best import SingleBestResult


ACTION_DIRECTORIES = {
    "RECOVERED": "recovered",
    "HEADER_RECOVERED": "header_recovered",
    "CLEAN": "clean",
    "FAILED": "failed",
    "SKIP_UNDECODABLE": "skip_undecodable",
    "ERROR": "error",
}
_ENCODED_ACTIONS = frozenset({"RECOVERED", "HEADER_RECOVERED"})


def _to_jpeg(rgb: np.ndarray, quality: int) -> bytes:
    output = io.BytesIO()
    Image.fromarray(rgb).save(output, format="JPEG", quality=quality)
    return output.getvalue()


def materialize_result(
    result: SingleBestResult,
    src_path: Path,
    out_dir: Path,
    *,
    quality: int = 95,
):
    """SingleBestResult를 기존 상대 경로·byte 계약으로 저장한다."""
    directory = ACTION_DIRECTORIES[result.action]
    output_path = out_dir / directory / (src_path.stem + ".jpg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if result.action in _ENCODED_ACTIONS:
        if result.rgb is None:
            raise ValueError(f"{result.action} 결과에는 RGB가 필요하다")
        output_bytes = _to_jpeg(result.rgb, quality)
    else:
        output_bytes = result.source_bytes
    output_path.write_bytes(output_bytes)
    return output_path, result.action, result.info_copy()
