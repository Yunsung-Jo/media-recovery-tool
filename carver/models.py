from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FileHit:
    file_type: str  # "jpeg" | "avi"
    offset: int
    source: str = "exact"
    confidence: float = 1.0
    scan_start: int | None = None
