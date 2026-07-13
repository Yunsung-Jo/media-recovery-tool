# 아키텍처 결정 기록

ADR은 현재 사용법이나 기준선이 아니라, 이후 구현을 제약하는 비자명한 선택과 당시의 근거를 보존한다.
현재 동작은 [carve 명세](../specs/0001-carve.md)와 [recover 명세](../specs/0002-recover.md), 최신 수치와
남은 작업은 [현재 상태](../current-state.md)를 우선한다.

| 번호 | 결정 | 상태 |
|---|---|---|
| [0001](0001-resync-recovery.md) | 손상 JPEG를 비트 디코더와 바이트 편집·재동기로 복구한다 | Accepted |
| [0002](0002-carve-eoi-validation.md) | EOI 직후 엔트로피를 검사해 가짜 EOI를 건너뛴다 | Accepted |
| [0003](0003-recover-perf-optimization.md) | 출력이 동일한 핫패스 최적화만 적용한다 | Accepted |
| [0004](0004-resync-dc-reset-recovery.md) | 재동기 때 DC 캐리와 0 리셋을 함께 평가한다 | Accepted |
| [0005](0005-scaled-accept-threshold.md) | 재동기 수락 임계를 남은 MCU에 비례시킨다 | Accepted |
| [0006](0006-header-recovery-structural-gates.md) | 헤더 복구 후보는 구조 신호로 판정한다 | Accepted |
| [0007](0007-carve-corrupt-header-boundary.md) | 손상 JPEG 헤더의 마커·길이를 검증해 과다 카빙을 막는다 | Accepted |
| [0008](0008-jpeg-boundary-stops-at-avi.md) | JPEG 경계는 다음 AVI 시그니처에서 정지한다 | Accepted |

새 ADR이 정말 필요한 경우 `배경`, `결정`, `대안`, `결과`만 작성하고 가장 가까운 기존 ADR의 형식을
따른다. 실험 과정 전체나 현재 기준선은 ADR에 복제하지 않는다.
