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
| `carve.py` | 디스크 이미지에서 JPEG/AVI 추출 진입점 |
| `recover.py` | 추출된 JPEG를 resync 엔진으로 복구하는 진입점 |
| `carver/models.py` | 파일 탐색 결과를 담는 `FileHit` 데이터 클래스 |
| `carver/scanner.py` | 디스크 이미지에서 파일 시그니처 탐색 |
| `carver/extractors.py` | 탐색 결과로부터 JPEG/AVI 파일 경계 계산. JPEG는 가짜 EOI를 건너뛰어 진짜 끝을 찾고([ADR 0002](adr/0002-carve-eoi-validation.md)), 손상된 헤더 길이·비마커 바이트를 검증해 다음 진짜 헤더로 경계를 끊어 과다 카빙(여러 이미지를 한 파일로 삼킴)을 막는다([ADR 0007](adr/0007-carve-corrupt-header-boundary.md)) |
| `carver/jpegdecode.py` | 비트 단위 제어가 가능한 baseline JPEG 디코더(numba). 임의 시작 비트위치/DC에서 재개, 디싱크 탐지 |
| `carver/resync.py` | 바이트 오라클(치환/삭제/삽입) + 세그먼트 resync로 디싱크를 복원하는 복구 엔진. 재동기 시 DC 캐리/0 리셋을 함께 시도해 hole을 복구한다([ADR 0004](adr/0004-resync-dc-reset-recovery.md)). 헤더 손상 파일은 `headerfix`로 헤더를 재구성한 뒤 태운다 |
| `carver/headerfix.py` | 헤더(DHT/DQT/SOF/SOS) 손상 파일의 헤더 재구성 pass. 관용 스캔·도너 Annex-K 이식·DQT 스무딩·템플릿 SOF/SOS로 후보를 만들어 구조 게이트로 채택한다([ADR 0006](adr/0006-header-recovery-structural-gates.md)) |

## 복구 파이프라인 (recover.py)

`recover.py` → `carver/resync.py::recover_file` → `carver/jpegdecode.py::Decoder`.
손상 지점마다 바이트 편집 또는 비트위치 재동기를 적용해 정렬을 복원하고,
복구 불가 영역은 회색으로 남긴다. 근거는 [ADR 0001](adr/0001-resync-recovery.md).
디코더 구성이 실패하거나 첫 MCU부터 어긋나는 헤더 손상 파일은
`carver/headerfix.py`가 헤더를 재구성해 엔진에 되돌린다([ADR 0006](adr/0006-header-recovery-structural-gates.md)).
