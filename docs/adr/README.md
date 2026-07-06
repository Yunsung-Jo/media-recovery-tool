# ADR 목록

비자명한 기술 결정 기록. 작성·상태 전이(Accepted↔Superseded)는
[write-adr](../../.claude/skills/write-adr/SKILL.md), 문서 경계는
[문서 역할 경계](../../.claude/skills/shared/doc-roles.md).

## Accepted

| 번호 | 제목 | 결정 요약 | 날짜 |
|------|------|----------|------|
| [0001](0001-resync-recovery.md) | 손상 JPEG 복구를 자작 비트 디코더 + 바이트 오라클/resync로 수행 | 중화+강제디코딩 대신 비트 단위 디코더로 디싱크 지점을 짚고 바이트 편집/재동기로 정렬 복원 | 2026-06-03 |
| [0002](0002-carve-eoi-validation.md) | carve의 가짜 EOI 오인을 EOI-직후 엔트로피 검사로 방지 | 첫 `FF D9`에서 끝내지 않고 직후 stuffing 비율로 가짜 EOI를 건너뛰어, 누락됐던 데이터를 추출 | 2026-06-28 |
| [0003](0003-recover-perf-optimization.md) | recover 핫패스를 무손실로 최적화 (GPU·품질 trade-off 배제) | 삽입 후보의 np.insert 전체복사 제거 + _recv_extend 일괄추출. 출력 비트 동일, 실전 80분→9.7분(8.2배) | 2026-06-29 |
| [0004](0004-resync-dc-reset-recovery.md) | hole 잔존 회색을 resync-skip의 DC=0 리셋으로 복구 | 재동기 시 DC 캐리에 더해 전체 0 리셋 후보를 probe해 clean run 긴 쪽 채택. 무채색 착시는 undecoded 지표 병기로 분리. 복구본 undec 평균 0.100→0.092(회색 잔존 케이스는 대폭) | 2026-07-01 |
| [0005](0005-scaled-accept-threshold.md) | resync 수락 임계를 잔여 비례(0.35W)로 스케일하고 데이터-끝 완주 run을 수락 | 절대 임계(450)의 소형·간격<450 잠금을 창 비례 max(30, 0.35W)+버퍼끝 완주 규칙으로 해소. FAILED 66→15(전환 51), 파일별 회귀 0. DC 물리 범위는 수락 필터로 부적합 판명(재도입 금지 제약) | 2026-07-03 |
| [0006](0006-header-recovery-structural-gates.md) | 헤더 복구 후보의 진위는 구조 신호로만 판정하고 렌더 통계·상관·prior를 기각 필터로 쓰지 않는다 | DHT 이식(Annex-K)·DQT 스무딩·SOF/SOS 재구성을 probe 바닥·소비율 0.25~1.1·엔진 undec<0.95·own-우월 게이트로 채택. SKIP 126→71·FAILED 15→11, 유지 파일 회귀 0. 렌더 통계·썸 상관·코퍼스 prior는 밀림·캐스트 하에서 판별력 없음 | 2026-07-04 |
| [0007](0007-carve-corrupt-header-boundary.md) | carve 과다 카빙을 헤더 마커·길이 검증으로 방지하고 손상 첫 이미지 복원은 recover에 위임 | 마커 워크에 유효마커(mb≥0xC0)·마커별 길이 상한(DHT 1200·DQT 600·SOF 100·DRI 10)·손상 시 경계 축소(SOF 후=다음 헤더/SOF 전=next_sig) 추가. 재카빙 822→999, 사용가능 740→884(+144), 진짜 손실 0. SOS-aware·strict-landing·next_sig 일괄은 buried 삼킴/whack-a-mole로 기각. 회귀는 recover 파이프라인으로 판정 | 2026-07-05 |
| [0008](0008-jpeg-boundary-stops-at-avi.md) | JPEG carve 경계는 다음 AVI(RIFF) 시그니처에서도 정지한다 | 0007 확장 — 경계를 미는 세 경로(_next_header·SOS upper·_corrupt_boundary)가 AVI 시그니처를 무시해 JPEG이 뒤 AVI를 삼키던 것을 `_next_avi` 하드 경계로 방지(RIFF+AVI 12바이트는 JPEG 내부 정상 등장 불가라 saw_sof여도 안전). AVI 39→44, 사용가능 사진 884 회귀 0, JPEG −21은 AVI 내부 MJPEG 프레임의 정상 재분류. 541시간 재생표시는 카빙 무관(별도 C5) | 2026-07-05 |

## Deprecated / Superseded

| 번호 | 제목 | 결정 요약 | 대체 | 날짜 |
|------|------|----------|------|------|
