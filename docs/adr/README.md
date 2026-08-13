# 아키텍처 결정 기록 — 역사 자료

ADR은 현재 사용법·계약·기준선의 정본이 아니라 비자명한 선택이 생긴 당시의 배경, 대안과 결과를
보존한다. T-0002는 12개 ADR을 삭제하거나 완료 Task로 변환하지 않고, 지속할 원칙을
[design.md](../design.md), [evaluation.md](../evaluation.md)와 [format-notes.md](../format-notes.md)로
점진적으로 이관했다.

현재 동작은 [설계](../design.md), [산출물](../artifacts.md),
[carve spec](../specs/0001-carve.md)과 [reconstruct spec](../specs/0002-recover.md)을 우선한다. 최신 검증
범위와 수치는 [status.md](../status.md)와 [evaluation.md](../evaluation.md)를 우선한다.

| 번호 | 당시 결정 | 지속 정본의 핵심 목적지 | 상태 |
|---|---|---|---|
| [0001](0001-resync-recovery.md) | 손상 JPEG를 bit decoder와 byte 편집·resync로 복구 | design | Accepted |
| [0002](0002-carve-eoi-validation.md) | EOI 직후 entropy를 검사해 가짜 EOI를 건너뜀 | design, format notes | Accepted |
| [0003](0003-recover-perf-optimization.md) | 출력이 같은 hot path 최적화만 적용 | evaluation | Accepted |
| [0004](0004-resync-dc-reset-recovery.md) | resync 때 DC carry와 0 reset을 함께 평가 | design | Accepted |
| [0005](0005-scaled-accept-threshold.md) | resync 수락 임계를 남은 MCU에 비례 | evaluation, current spec | Accepted |
| [0006](0006-header-recovery-structural-gates.md) | header 후보를 구조 신호로 판정 | design, evaluation | Accepted |
| [0007](0007-carve-corrupt-header-boundary.md) | 손상 JPEG header의 marker·길이를 검증 | design | Accepted |
| [0008](0008-jpeg-boundary-stops-at-avi.md) | JPEG boundary를 다음 외부 AVI로 제한 | design | Accepted |
| [0009](0009-structural-damaged-starts.md) | 손상 시작은 anchor와 후속 구조를 함께 검증 | design | Accepted |
| [0010](0010-avi-structure-and-opendml-boundary.md) | AVI boundary를 RIFF 구조와 연속 OpenDML form으로 검증 | design, format notes | Accepted |
| [0011](0011-resync-segment-mcu-alignment.md) | 복구 절단 구간을 MCU 행 위상으로 재배치 | design, evaluation | Accepted |
| [0012](0012-thumbnail-reference-correction.md) | Exif thumbnail로 잔여 밀림·색 cast를 보정 | design, evaluation | Accepted |

모든 ADR 원문과 inbound link를 유지한다. 현재 임계와 세부 동작이 ADR의 당시 설명에서 후속 보완됐다면
code·test와 현행 spec이 우선한다. 과거 실험 수치는 현재 성능으로 바꾸어 읽지 않는다.

새 ADR은 만들지 않는다. 새로운 판단은 해당 활성 Task에서 검증한 뒤 `design.md`, `artifacts.md`,
`evaluation.md`, `status.md` 또는 `format-notes.md`의 적절한 정본에 반영한다.
