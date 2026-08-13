# 아키텍처

## 폴더 구조

```
media-recovery-tool/
├── pyproject.toml
├── src/media_recovery/
│   ├── cli/
│   │   ├── carve.py
│   │   ├── reconstruct.py
│   │   └── enhance.py
│   ├── domain/objects.py
│   ├── discovery/
│   │   ├── scanner.py
│   │   └── materializer.py
│   ├── formats/
│   │   ├── boundaries.py
│   │   └── jpeg/baseline_decoder.py
│   ├── reconstruction/
│   │   ├── engine.py
│   │   └── header_hypotheses.py
│   └── enhancement/thumbnail_guided.py
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
| `cli/carve.py` | CLI 인자 검증, 읽기 전용 mmap 생성, 카빙 파이프라인 호출과 실행 요약 출력 |
| `cli/reconstruct.py` | 파일 목록과 워커 풀 관리, `report.csv` 작성과 실행 요약 출력 |
| `domain/objects.py` | 파일 종류·오프셋과 탐지 근거(`source`, `confidence`, `scan_start`)를 담는 불변 `FileHit` |
| `discovery/scanner.py` | 정확 시그니처와 JFIF/Exif·AVI 구조 앵커 탐색. 손상 후보를 모두 모은 뒤 서로를 경계로 사용해 EOI 차용을 막는다 |
| `formats/boundaries.py` | JPEG marker/entropy 상태와 RIFF chunk 구조로 exclusive 끝을 계산한다. inter-scan APP/COM payload의 내장 시그니처를 건너뛰고, 가짜 EOI·손상 길이·AVI/OpenDML 경계를 처리한다 |
| `discovery/materializer.py` | 히트 중복 제거·정렬, pre-SOS 세그먼트/Exif 썸네일/AVI MJPEG 중첩 분류, 경계 호출, 청크 저장과 통계·오류 관리 |
| `formats/jpeg/baseline_decoder.py` | 비트 단위 제어가 가능한 baseline JPEG 디코더(numba). 임의 시작 비트위치/DC에서 재개, 디싱크 탐지 |
| `reconstruction/engine.py` | 바이트 오라클(치환/삭제/삽입) + 세그먼트 resync로 디싱크를 복원하는 복구 엔진. 재동기 시 DC 캐리/0 리셋을 함께 시도해 hole을 복구한다. 수락한 편집·재동기의 실패 MCU를 공간 절단점으로 남기고, 절단 밴드 배치 뒤 상단 고정 전역 MCU 행 위상을 먼저 맞춘다. 전역 해가 없을 때만 절단점 주변의 구조적 국소 보정과 보수적 행 스티치로 후퇴한다([ADR 0004](adr/0004-resync-dc-reset-recovery.md), [ADR 0011](adr/0011-resync-segment-mcu-alignment.md)). 헤더 손상 파일은 `header_hypotheses`로 헤더를 재구성한 뒤 태운다 |
| `reconstruction/header_hypotheses.py` | 헤더(DHT/DQT/SOF/SOS) 손상 파일의 헤더 재구성 pass. 관용 스캔·도너 Annex-K 이식·DQT 스무딩·템플릿 SOF/SOS로 후보를 만들어 구조 게이트로 채택한다([ADR 0006](adr/0006-header-recovery-structural-gates.md)) |
| `cli/enhance.py` | 복구본 트리에 썸네일 참조 보정을 일괄 적용하는 선택적 후처리 CLI. 파일 순회·병렬 워커·`report_thumbref.csv` 작성을 맡는다 |
| `enhancement/thumbnail_guided.py` | 카빙 원본의 EXIF 썸네일을 참조 오라클로 잔여 순환 MCU 밀림·색 캐스트 밴드를 추정·보정한다. 전역 (scale, dy) 정합 → 행별 FFT 순환 상관 → 마진·블록 재검증 게이트 → 회차별 self-check로 개선될 때만 채택하는 밀림 반복 보정 → 색 보정 순서로 동작하며, 근거 없는 파일은 identity로 남긴다([ADR 0012](adr/0012-thumbnail-reference-correction.md)) |

CLI·파일 저장·중첩 분류는 `discovery/materializer.py`로 분리해 `cli/carve.py`를 얇게 유지한다. 반면 JPEG 경계는 다음
AVI와 `movi` 포함 여부를 알아야 하고 AVI fallback도 다음 JPEG 구조를 알아야 하므로, 현재 두 경계 계산은
`formats/boundaries.py`에 함께 둔다. 지원 형식이 더 늘어나 이 상호 참조가 커질 때 형식별 모듈과 공통 boundary
registry로 나누는 편이 낫고, 지금 분리하면 순환 의존이나 얇은 forwarding 모듈만 늘어난다. 이 중간 구조는
기존 구현을 일대일로 옮긴 것이며 책임 재분리는 후속 Task 범위다.

## 데이터 흐름

카빙은 다음 흐름으로 동작한다.

`디스크 이미지` → `discovery.scanner.find_all_hits` → 오프셋 순 `FileHit` →
`discovery.materializer.process` → `formats.boundaries.jpeg_end/avi_end` →
`jpeg/`, `avi/`, 선택적 `jpeg_thumbnails/`

`process`는 성공한 마지막 최상위 범위를 활성 범위로 유지한다. 정렬된 다음 hit의 포함 판정은 O(1)이며,
JPEG 부모 안의 구조 검증 손상 시작만 중첩 추출한다. 유효한 pre-SOS 길이형 세그먼트 내부 hit은 파일 종류와
무관하게 건너뛰고, Exif APP1 내부 JPEG만 썸네일로 분류한다. AVI 내부 JPEG는 MJPEG 프레임이므로 별도
JPEG나 썸네일로 세지 않는다. 반대로 0인 양자화 계수처럼 세그먼트 의미가 무효이면 선언 길이를 신뢰하지
않고 다음 구조 후보에서 부모 조각을 끝낸다. 무효 부모 조각도 버리지 않고 별도 출력한다.

출력은 mmap 범위를 8 MiB 청크로 같은 디렉터리의 임시 파일에 쓴 뒤 `os.replace`한다. 따라서 일반적인
쓰기 예외에서는 최종 경로에 부분 파일을 노출하지 않고 기존 파일을 보존한다. 이미지나 AVI 전체를 Python
heap에 한 번에 복사하지 않지만, mmap의 resident set과 OS page cache가 고정 크기라는 뜻은 아니다.

복구는 다음 흐름으로 동작한다.

`media-recovery reconstruct` → `reconstruction/engine.py::recover_file` →
`formats/jpeg/baseline_decoder.py::Decoder`.
손상 지점마다 바이트 편집 또는 비트위치 재동기를 적용하고 각 실패 MCU를 `phase_cuts`에 기록한다.
보정 전 렌더로 일반 경로와 헤더 복구 후보를 선택한 뒤, 선택된 렌더를 절단점에서 나눠 adaptive local
위상과 전체 폭 경계 시그니처로 1차 배치한다. 그 결과의 모든 MCU 행 경계를 1·2·3·4픽셀 strip으로
검사하고, 상단 행 위상 0에서 누적한 절대 위상 후보를 원래 1차 렌더에서 매번 다시 배치한다. 최대 5회
연쇄 탐색 중 손실·소유권 조건을 지킨 상태만 진행하며, 독립 잔차 감사까지 통과한 최선 상태만 반환한다.
전역 해가 없으면 절단점 주변 국소 보정과 기존 행 스티치로 후퇴한다. 복구 절단점이 명시적으로 없으면
픽셀 신호만으로 공간 이동을 만들지 않는다. 복구 불가 영역과 보정으로 생긴 제한된 간격은 회색으로 남긴다.
근거는 [ADR 0001](adr/0001-resync-recovery.md)과
[ADR 0011](adr/0011-resync-segment-mcu-alignment.md)에 있다.
디코더 구성이 실패하거나 첫 MCU부터 어긋나는 헤더 손상 파일은
`reconstruction/header_hypotheses.py`가 헤더를 재구성해 엔진에 되돌린다([ADR 0006](adr/0006-header-recovery-structural-gates.md)).
`recover_file`이 저장 위치와 action을 결정하고, `cli/reconstruct.py`가 모든 결과를 `report.csv`로 합친다.

선택적 후처리는 `media-recovery enhance` → `enhancement/thumbnail_guided.py::process_file`로 동작한다. 카빙 원본의 EXIF
썸네일과 복구본을 정합해 잔여 순환 밀림·색 밴드를 추정하고, 게이트를 통과한 보정만 픽셀 도메인에서
적용해 별도 출력 트리에 저장한다. 보정 근거가 없으면 입력 바이트를 그대로 복사한다([ADR 0012](adr/0012-thumbnail-reference-correction.md)).

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
