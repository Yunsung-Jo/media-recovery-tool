# 0009. 손상 시작은 앵커와 후속 구조를 함께 검증한다

- **날짜:** 2026-07-13
- **상태:** Accepted

## 배경

정확한 `FF D8 FF`와 `RIFF....AVI `만 찾으면 손상 매체에서 시작 marker 일부가 덮어써진 파일을 놓친다.
반대로 디스크 전체에 fuzzy signature를 적용하면 엔트로피와 임의 데이터의 우연 일치가 대량 후보가 되고,
앞 후보가 뒤 파일의 EOI를 빌려 완결된 것으로 오인할 수 있다.

## 결정

정확 시그니처 탐색은 유지하고, 손상 시작은 안정적인 앵커와 후속 구조가 함께 맞을 때만 추가한다.

1. JPEG는 JFIF/Exif 앵커에서 SOI/APP core를 역산한다. core 1~2바이트 손상 또는 정확 core 뒤 APP 길이
   손상을 대상으로 한다.
2. 연속 marker walk를 우선하고, 실패한 손상 core는 4 KiB 정렬 시작에서 marker 간 최대 4096바이트의
   DQT→SOF→DHT→SOS 재동기만 허용한다. 검증된 SOS payload 시작은 `FileHit.scan_start`에 보존한다.
3. JPEG 손상 후보를 모두 모은 뒤 exact·damaged JPEG와 AVI 전체 오프셋을 경계로 EOI를 다시 검증한다.
   앞 후보가 뒤 후보의 EOI를 차용하면 채택하지 않는다.
4. AVI는 `RIFF`와 `AVI ` form type 중 한쪽만 1~2바이트 손상됐고, 선언 범위의 `LIST/hdrl`·`avih`·
   `LIST/movi` 구조가 완전할 때만 손상 시작으로 추가한다.
5. 같은 type/offset의 exact hit이 구조 추론 hit보다 우선한다. `confidence`는 근거 등급이며 확률이 아니다.

FAT 디렉터리 엔트리는 독립 감사에는 사용할 수 있지만 런타임 후보 생성에는 사용하지 않는다. 파일 시스템
메타데이터 자체가 손상됐거나 삭제됐을 수 있고, raw 카빙 도구의 동작이 특정 FAT 배치에 종속되기 때문이다.

## 대안

| 대안 | 기각 이유 |
|---|---|
| 정확 시그니처만 탐색 | 시작 marker가 일부 손상된 연속 파일을 놓친다. |
| 디스크 전체 Hamming-distance fuzzy scan | 엔트로피 우연 일치가 많고 후보 진위 근거가 약하다. |
| FAT 엔트리에서만 시작 복원 | 삭제·손상 메타데이터와 비-FAT 입력을 다루지 못한다. |
| 후보를 발견하는 즉시 EOI 검증 | 뒤 손상 후보를 아직 몰라 그 파일의 EOI를 빌릴 수 있다. |
| RIFF와 form type 동시 손상까지 허용 | 현재는 시작을 고정할 독립 앵커가 부족하다. |

## 결과

시작 marker 일부가 손상된 연속 JPEG·AVI를 보수적으로 후보화하면서 raw fuzzy scan의 위양성을 피한다.
구조 검증 때문에 scanner 시간은 늘지만 각 JPEG 검색 범위와 marker gap은 제한되어 있다. AVI 후보별 선언
RIFF walk는 적대적 입력에서 후보 수와 이미지 크기의 곱까지 커질 수 있다.

JFIF/Exif 앵커와 EOI가 함께 손상된 JPEG, SOI 없는 APP-less table-first JPEG, RIFF와 form type이 함께
손상된 AVI, 비연속 단편화 파일은 여전히 자동 탐지 범위 밖이다. 정답 원본이 없는 감사 결과를 “누락 0”으로
표현하지 않는다.

## 관련 항목

- [ADR 0007](0007-carve-corrupt-header-boundary.md) — JPEG 경계의 손상 marker 처리
- [ADR 0010](0010-avi-structure-and-opendml-boundary.md) — AVI size와 OpenDML 경계
- [carve 명세](../specs/0001-carve.md)
- [현재 상태](../current-state.md)
