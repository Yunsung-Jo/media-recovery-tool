# 0010. AVI 경계는 RIFF 구조와 연속 OpenDML form으로 검증한다

- **날짜:** 2026-07-13
- **상태:** Accepted

## 배경

AVI의 RIFF size는 손상 매체에서 0·과소·과대값이 될 수 있다. 값을 무조건 신뢰하면 `movi` 전에 잘리거나
뒤 JPEG·AVI를 삼킨다. 반대로 첫 JPEG 시그니처에서 자르면 MJPEG stream frame을 독립 사진으로 오인한다.
OpenDML AVI는 첫 `RIFF...AVI ` 뒤의 `RIFF...AVIX` form에 stream data와 index를 이어 저장할 수 있다.

## 결정

1. exact scanner는 `RIFF`와 `AVI ` 12바이트를 시작 후보로 잡되, RIFF size를 끝으로 신뢰하려면 선언
   범위의 top-level walk에서 `LIST/hdrl` 안 `avih`와 뒤 `LIST/movi`를 확인한다.
2. size가 16 미만·상한 초과·이미지 밖이거나 구조 walk가 실패하거나 다음 외부 후보를 가로지르면 size를
   버린다. 이때 허용된 top-level chunk를 순서대로 걸어 `movi`까지 확인한 마지막 끝을 사용한다.
3. `hdrl` LIST id의 1~2바이트 손상은 list type과 내부 `avih` 구조가 이어질 때만 허용한다. 유효한 RIFF
   선언 범위의 `movi` 뒤 opaque padding·vendor chunk는 보존한다.
4. JPEG 후보가 `movi`/`rec `의 `NNdc`·`NNdb` 등 stream payload 안에 있으면 AVI 내부 frame으로 보고
   외부 경계로 쓰지 않는다. 그 밖의 다음 구조 후보가 선언 끝 안에 있으면 size 손상으로 본다.
5. 인접 `RIFF...AVIX`는 선언 범위를 끝까지 chunk-walk하고 `LIST/movi`를 확인한 경우만 같은 출력에
   붙인다. `ix##` standard-index chunk를 허용하며, 구조 없는 raw AVIX는 붙이지 않는다.

## 대안

| 대안 | 기각 이유 |
|---|---|
| RIFF size를 항상 신뢰 | 과소·과대 손상에서 stream 손실 또는 뒤 파일 포함이 발생한다. |
| size를 항상 버리고 다음 시그니처 사용 | 정상 opaque tail과 MJPEG frame 때문에 조기 종료할 수 있다. |
| 첫 `FF D8`에서 AVI 종료 | `movi` 안의 정상 JPEG frame을 외부 사진으로 오인한다. |
| 모든 `RIFF...AVIX`를 무조건 연결 | 우연 시그니처나 별도 손상 파일을 현재 AVI에 붙일 수 있다. |
| AVI stream/index까지 수리 | 카빙 경계와 독립된 후처리 문제이며 현재 도구 범위를 넓힌다. |

## 결과

정상 RIFF size와 opaque tail은 보존하면서 손상 size를 구조로 복원하고, MJPEG frame과 OpenDML 연속 form을
구분한다. exact 12바이트 시작 후보 자체는 완전한 AVI 확정이 아니며, 손상 시작 추론과 size 신뢰에서 더
강한 구조 게이트를 적용한다.

RIFF와 `AVI ` form type이 동시에 손상됐거나 top-level chunk 길이까지 복구 불가능한 AVI, 비연속
단편화 AVI는 놓칠 수 있다. `idx1`/`indx` 재구성과 영상 stream 수리는 이 결정에 포함하지 않는다.

## 관련 항목

- [ADR 0008](0008-jpeg-boundary-stops-at-avi.md) — JPEG가 AVI 시작에서 멈추는 원칙
- [ADR 0009](0009-structural-damaged-starts.md) — 손상 AVI 시작 후보화
- [carve 명세](../specs/0001-carve.md)
- [포맷 메모](../format-notes.md)
