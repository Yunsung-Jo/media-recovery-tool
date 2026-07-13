# 아키텍처

## 폴더 구조

```
rawcarve/
├── carve.py
├── recover.py
├── carver/
│   ├── models.py
│   ├── extractors.py
│   ├── scanner.py
│   ├── jpegdecode.py
│   ├── resync.py
│   └── headerfix.py
├── output/
│   ├── jpeg/
│   ├── jpeg_thumbnails/
│   ├── jpeg_recovered/
│   └── avi/
└── tests/
```

## 모듈 책임

| 모듈 | 책임 |
|------|------|
| `carve.py` | 디스크 이미지 mmap, 히트 순회, 임베디드 판별, 추출물 저장과 오류 기록 |
| `recover.py` | 파일 목록과 워커 풀 관리, `report.csv` 작성과 실행 요약 출력 |
| `carver/models.py` | 파일 탐색 결과를 담는 `FileHit` 데이터 클래스 |
| `carver/scanner.py` | 디스크 이미지에서 파일 시그니처 탐색 |
| `carver/extractors.py` | 탐색 결과로부터 JPEG/AVI 파일 경계 계산. JPEG는 가짜 EOI를 건너뛰어 진짜 끝을 찾고([ADR 0002](adr/0002-carve-eoi-validation.md)), 손상된 헤더 길이·비마커 바이트를 검증해 다음 진짜 헤더로 경계를 끊어 과다 카빙(여러 이미지를 한 파일로 삼킴)을 막는다([ADR 0007](adr/0007-carve-corrupt-header-boundary.md)). 경계를 미는 계산은 다음 AVI(RIFF) 시그니처에서도 정지해 뒤따르는 영상을 삼키지 않는다([ADR 0008](adr/0008-jpeg-boundary-stops-at-avi.md)) |
| `carver/jpegdecode.py` | 비트 단위 제어가 가능한 baseline JPEG 디코더(numba). 임의 시작 비트위치/DC에서 재개, 디싱크 탐지 |
| `carver/resync.py` | 바이트 오라클(치환/삭제/삽입) + 세그먼트 resync로 디싱크를 복원하는 복구 엔진. 재동기 시 DC 캐리/0 리셋을 함께 시도해 hole을 복구한다([ADR 0004](adr/0004-resync-dc-reset-recovery.md)). 헤더 손상 파일은 `headerfix`로 헤더를 재구성한 뒤 태운다 |
| `carver/headerfix.py` | 헤더(DHT/DQT/SOF/SOS) 손상 파일의 헤더 재구성 pass. 관용 스캔·도너 Annex-K 이식·DQT 스무딩·템플릿 SOF/SOS로 후보를 만들어 구조 게이트로 채택한다([ADR 0006](adr/0006-header-recovery-structural-gates.md)) |

## 데이터 흐름

카빙은 다음 흐름으로 동작한다.

`디스크 이미지` → `scanner.find_all_hits` → 오프셋 순 `FileHit` → `extractors.jpeg_end/avi_end` →
`jpeg/`, `avi/`, 선택적 `jpeg_thumbnails/`

복구는 다음 흐름으로 동작한다.

`recover.py` → `carver/resync.py::recover_file` → `carver/jpegdecode.py::Decoder`.
손상 지점마다 바이트 편집 또는 비트위치 재동기를 적용해 정렬을 복원하고,
복구 불가 영역은 회색으로 남긴다. 근거는 [ADR 0001](adr/0001-resync-recovery.md).
디코더 구성이 실패하거나 첫 MCU부터 어긋나는 헤더 손상 파일은
`carver/headerfix.py`가 헤더를 재구성해 엔진에 되돌린다([ADR 0006](adr/0006-header-recovery-structural-gates.md)).
`recover_file`이 저장 위치와 action을 결정하고, `recover.py`가 모든 결과를 `report.csv`로 합친다.

## 지원 경계

- 카빙 입력은 파일 시스템 구조가 아니라 바이트 시그니처만 사용한다.
- 복구 엔진은 3컴포넌트 baseline JPEG를 대상으로 한다.
- AVI는 경계를 계산해 추출하지만 영상 스트림을 디코드하거나 수리하지 않는다.
