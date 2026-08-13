# 0008. JPEG 경계는 다음 AVI 시그니처에서 정지한다

- **날짜:** 2026-07-05
- **상태:** Accepted

## 배경

[ADR 0007](0007-carve-corrupt-header-boundary.md)의 JPEG 경계는 다음 JPEG 헤더와 10 MB 상한을 보지만
AVI 시작은 보지 않았다. 절단·손상 JPEG 바로 뒤에 AVI가 있으면 JPEG이 AVI 내부의 MJPEG 프레임이나
fallback 상한까지 확장되고, 그 범위 안의 AVI 히트는 embedded로 건너뛰어 출력에서 사라졌다.

## 결정

`_next_avi(data, start, hi)`로 `[start, hi)`의 첫 `RIFF` + 4바이트 크기 + `AVI ` 시그니처를 찾고,
SOS 엔트로피 상한과 손상 헤더 경계의 하드 정지점으로 사용한다.

이 12바이트 구조는 스캐너가 AVI를 판정하는 기준과 같다. 프로젝트는 이를 JPEG 내부의 정상 데이터가
아닌 컨테이너 경계로 취급하므로 SOF를 이미 통과한 JPEG에도 적용한다. 반면 일반 `next_sig`에는 EXIF
썸네일 JPEG가 포함될 수 있어 같은 방식으로 사용할 수 없다.

## 대안

| 대안 | 기각 이유 |
|---|---|
| AVI를 경계로 보지 않음 | 뒤따르는 AVI가 JPEG 범위에 묻혀 추출되지 않는다. |
| 모든 다음 시그니처를 하드 경계로 사용 | EXIF 썸네일에서 부모 JPEG를 잘라낼 수 있다. |
| 모든 파일 형식의 경계를 일반화 | 현재 지원 형식은 JPEG와 AVI뿐이며, 형식마다 JPEG 내부에 정상 등장하지 않는지 별도 검증이 필요하다. |
| AVI 인덱스 복구도 함께 수행 | `idx1` 부재와 재생시간 표시는 카빙 경계와 독립된 후처리 문제다. |

## 결과

당시 전체 데이터 재카빙에서 AVI가 39개에서 44개로 늘고, 사용 가능한 사진 884개는 유지됐다. 줄어든
JPEG 21개는 AVI 내부 MJPEG 프레임이 별도 JPEG로 잘못 분류되던 것이 바로잡힌 결과였다. 최신 기준선은
[evaluation.md](../evaluation.md)를 따른다.

AVI 인덱스 부재는 이 결정으로 해결되지 않는다. 지원 파일 형식이 추가되면 각 시그니처를 JPEG 경계로
사용해도 안전한지 검증해야 한다.

### 후속 보완 (2026-07-13)

scanner가 만든 exact·damaged AVI 전체 오프셋을 정렬 인덱스로 사용한다. 다만 JPEG의 pre-SOS 또는
inter-scan 길이형 segment payload 안 AVI-like hit은 metadata일 수 있으므로 외부 경계에서 제외한다.
AVI 자체의 RIFF size 신뢰, `movi` stream payload, fallback chunk walk와 OpenDML `AVIX` 연결 규칙은
[ADR 0010](0010-avi-structure-and-opendml-boundary.md)으로 확장했다.

## 관련 항목

- [ADR 0007](0007-carve-corrupt-header-boundary.md) — 확장 대상인 JPEG 손상 경계
- [ADR 0010](0010-avi-structure-and-opendml-boundary.md) — AVI 구조·OpenDML 경계
- [carve 명세](../specs/0001-carve.md)
- [포맷 메모](../format-notes.md)
