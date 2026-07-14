# 아키텍처

## 폴더 구조

```
rawcarve/
├── carve.py
├── recover.py
├── carver/
│   ├── models.py
│   ├── carving.py
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
| `carve.py` | CLI 인자 검증, 읽기 전용 mmap 생성, 카빙 파이프라인 호출과 실행 요약 출력 |
| `recover.py` | 파일 목록과 워커 풀 관리, `report.csv` 작성과 실행 요약 출력 |
| `carver/models.py` | 파일 종류·오프셋과 탐지 근거(`source`, `confidence`, `scan_start`)를 담는 불변 `FileHit` |
| `carver/scanner.py` | 정확 시그니처와 JFIF/Exif·AVI 구조 앵커 탐색. 손상 후보를 모두 모은 뒤 서로를 경계로 사용해 EOI 차용을 막는다 |
| `carver/extractors.py` | JPEG marker/entropy 상태와 RIFF chunk 구조로 exclusive 끝을 계산한다. inter-scan APP/COM payload의 내장 시그니처를 건너뛰고, 가짜 EOI·손상 길이·AVI/OpenDML 경계를 처리한다 |
| `carver/carving.py` | 히트 중복 제거·정렬, pre-SOS 세그먼트/Exif 썸네일/AVI MJPEG 중첩 분류, 경계 호출, 청크 저장과 통계·오류 관리 |
| `carver/jpegdecode.py` | 비트 단위 제어가 가능한 baseline JPEG 디코더(numba). 임의 시작 비트위치/DC에서 재개, 디싱크 탐지 |
| `carver/resync.py` | 바이트 오라클(치환/삭제/삽입) + 세그먼트 resync로 디싱크를 복원하는 복구 엔진. 재동기 시 DC 캐리/0 리셋을 함께 시도해 hole을 복구한다([ADR 0004](adr/0004-resync-dc-reset-recovery.md)). 헤더 손상 파일은 `headerfix`로 헤더를 재구성한 뒤 태운다 |
| `carver/headerfix.py` | 헤더(DHT/DQT/SOF/SOS) 손상 파일의 헤더 재구성 pass. 관용 스캔·도너 Annex-K 이식·DQT 스무딩·템플릿 SOF/SOS로 후보를 만들어 구조 게이트로 채택한다([ADR 0006](adr/0006-header-recovery-structural-gates.md)) |

CLI·파일 저장·중첩 분류는 `carver/carving.py`로 분리해 `carve.py`를 얇게 유지한다. 반면 JPEG 경계는 다음
AVI와 `movi` 포함 여부를 알아야 하고 AVI fallback도 다음 JPEG 구조를 알아야 하므로, 현재 두 경계 계산은
`extractors.py`에 함께 둔다. 지원 형식이 더 늘어나 이 상호 참조가 커질 때 형식별 모듈과 공통 boundary
registry로 나누는 편이 낫고, 지금 분리하면 순환 의존이나 얇은 forwarding 모듈만 늘어난다.

## 데이터 흐름

카빙은 다음 흐름으로 동작한다.

`디스크 이미지` → `scanner.find_all_hits` → 오프셋 순 `FileHit` → `carving.process` →
`extractors.jpeg_end/avi_end` → `jpeg/`, `avi/`, 선택적 `jpeg_thumbnails/`

`process`는 성공한 마지막 최상위 범위를 활성 범위로 유지한다. 정렬된 다음 hit의 포함 판정은 O(1)이며,
JPEG 부모 안의 구조 검증 손상 시작만 중첩 추출한다. 유효한 pre-SOS 길이형 세그먼트 내부 hit은 파일 종류와
무관하게 건너뛰고, Exif APP1 내부 JPEG만 썸네일로 분류한다. AVI 내부 JPEG는 MJPEG 프레임이므로 별도
JPEG나 썸네일로 세지 않는다. 반대로 0인 양자화 계수처럼 세그먼트 의미가 무효이면 선언 길이를 신뢰하지
않고 다음 구조 후보에서 부모 조각을 끝낸다. 무효 부모 조각도 버리지 않고 별도 출력한다.

출력은 mmap 범위를 8 MiB 청크로 같은 디렉터리의 임시 파일에 쓴 뒤 `os.replace`한다. 따라서 일반적인
쓰기 예외에서는 최종 경로에 부분 파일을 노출하지 않고 기존 파일을 보존한다. 이미지나 AVI 전체를 Python
heap에 한 번에 복사하지 않지만, mmap의 resident set과 OS page cache가 고정 크기라는 뜻은 아니다.

복구는 다음 흐름으로 동작한다.

`recover.py` → `carver/resync.py::recover_file` → `carver/jpegdecode.py::Decoder`.
손상 지점마다 바이트 편집 또는 비트위치 재동기를 적용해 정렬을 복원하고,
복구 불가 영역은 회색으로 남긴다. 근거는 [ADR 0001](adr/0001-resync-recovery.md).
디코더 구성이 실패하거나 첫 MCU부터 어긋나는 헤더 손상 파일은
`carver/headerfix.py`가 헤더를 재구성해 엔진에 되돌린다([ADR 0006](adr/0006-header-recovery-structural-gates.md)).
`recover_file`이 저장 위치와 action을 결정하고, `recover.py`가 모든 결과를 `report.csv`로 합친다.

## 지원 경계

- 카빙 입력은 파일 시스템 구조가 아니라 바이트 시그니처만 사용한다.
- FAT 디렉터리 엔트리는 `usb.img` 검증 감사에만 사용하며 런타임 탐지에는 사용하지 않는다.
- 히트 H개, 고유 히트 U개에서 중복 제거는 O(H), 정렬은 O(U log U), 메모리는 O(U)다. 스캐너의 고정
  시그니처 패스는 O(N)이지만 후보별 JPEG bounded 검증과 AVI RIFF 범위 walk가 추가된다. 손상 AVI가
  다수인 적대적 입력의 최악 비용은 후보 수와 이미지 크기의 곱까지 커질 수 있다.
- JPEG 경계 walk는 각 후보의 `--max-jpeg-size` 안에서 선형이고, scanner가 만든 JPEG·AVI 오프셋 인덱스를
  이진 탐색해 다음 외부 경계를 찾는다. 실제 추출 순회의 활성 범위 메모리는 O(1), 쓰기 버퍼의 추가
  Python heap은 최대 8 MiB다. mmap resident set과 OS page cache는 이 상한에 포함되지 않는다.
- 복구 엔진은 3컴포넌트 baseline JPEG를 대상으로 한다.
- AVI는 경계를 계산해 추출하지만 영상 스트림을 디코드하거나 수리하지 않는다.
