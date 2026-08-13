# `media-recovery carve` 동작 계약

이 문서는 현재 `media-recovery carve`의 사용자 관점 동작과 카빙 경계 계약을 설명한다.

## 개요

손상된 디스크 이미지에서 파일 시스템 메타데이터 없이 JPEG와 AVI의 시작·끝을 찾아 원본 바이트 범위를
별도 디렉터리에 저장한다. 런타임은 FAT를 읽지 않으며 디스크 이미지는 읽기 전용 mmap으로 연다.

## 인터페이스

| 이름 | 설명 | 타입 | 기본값 |
|---|---|---|---:|
| `image` | 디스크 이미지 파일 경로 | positional | — |
| `-o, --output` | 출력 디렉터리 | str | `output` |
| `--max-avi-size` | AVI 경계 탐색·추출 하드 상한(MB) | int | `500` |
| `--max-jpeg-size` | JPEG 경계 탐색·추출 하드 상한(MB) | int | `10` |
| `--save-thumbnails` | Exif APP1 내부 JPEG를 `jpeg_thumbnails/`에 저장 | flag | 생략 |

두 최대 크기는 0보다 커야 한다. 입력이 일반 파일이 아니거나 값이 0 이하이면 오류를 출력하고 exit code
1로 종료한다.

## 출력

```text
<output>/
  jpeg/
  avi/
  jpeg_thumbnails/    # --save-thumbnails 사용 시에만 생성
  errors.log
```

파일명은 `0x{시작 오프셋:08X}.jpg` 또는 `.avi`다. 같은 이름은 임시 파일을 완성한 뒤 교체하지만, 이번
실행에서 더 이상 나오지 않는 예전 파일은 자동 삭제하지 않는다. 재현 가능한 기준선에는 빈 출력 디렉터리를
사용한다. 항목 하나의 실패는 `errors.log`에 추가하고 다음 항목을 계속 처리한다.

## 파이프라인

### 1. 시작 후보 탐색

`media_recovery/discovery/scanner.py`는 다음 후보를 찾아 오프셋 순 `FileHit`으로 반환한다.

- 정확 JPEG: `FF D8 FF` 뒤 첫 길이형 marker의 종류와 선언 범위·sane 길이를 확인한다. APPn/COM으로
  시작한 후보는 뒤 연속 의미 walk가 실패해도 복구 대상으로 유지하고, walk가 성공하면 SOS payload
  시작을 `scan_start`에 기록한다. APP가 없는 table-first 시작은 DQT/DHT/SOF/SOS 구조가 이어져야 한다.
  marker fill `FF`도 허용한다.
- 손상 JPEG: JFIF/Exif 앵커에서 시작을 역산한다. SOI/APP core 1~2바이트 손상과 정확 core 뒤 APP 길이
  손상을 대상으로 하며, 연속 marker walk 또는 제한된 DQT→SOF→DHT→SOS 재동기와 EOI가 모두 있어야
  한다. 손상 core의 tolerant 재동기는 4 KiB 정렬 시작만 허용하고 marker 사이 간격은 최대 4096바이트다.
- 정확 AVI: `RIFF....AVI `.
- 손상 AVI: `RIFF` 또는 `AVI ` form type 중 한쪽이 1~2바이트 손상돼도 선언 범위의
  `LIST/hdrl`·`avih`·`LIST/movi` 구조가 완전하면 포함한다.

손상 JPEG와 AVI 후보를 먼저 모두 모은 뒤 그 전체 오프셋을 경계로 EOI를 검증한다. 따라서 앞 손상 JPEG가
뒤 JPEG나 AVI/MJPEG 프레임의 EOI를 빌려 완결된 것으로 오인하지 않는다. `FileHit.source`는 `exact`,
`damaged_jpeg_header`, `damaged_avi_header` 중 하나이며 `confidence`는 내부 근거 등급이지 확률이 아니다.
스캐너가 검증한 SOS payload 시작은 `scan_start`로 경계 계산기에 전달한다.
CLI의 `시작 후보 발견` 수는 중첩·내장 후보까지 포함하므로 최종 출력 파일 수가 아니다.

### 2. JPEG 경계

`media_recovery/formats/boundaries.py::jpeg_end`는 marker 상태와 entropy를 함께 걷는다.

- APP 없이 시작하는 JPEG, sequential/progressive 다중 scan, scan 사이 DHT/DQT/DAC/DNL/DRI/APP/COM/SOS를
  처리한다. 길이형 세그먼트 payload의 `FF D8`, `FF D9`, `RIFF...AVI `는 파일 경계가 아니다.
- 헤더 marker의 길이와 핵심 의미를 검증한다. DQT table id/precision/길이와 0이 아닌 양자화 계수,
  DHT code count, SOF 치수·컴포넌트, SOS 컴포넌트 수 등을 확인한다. 현재 sane 상한은 DHT 2200,
  DQT 600, DRI 10바이트이며 APP/COM은 16비트 전체 길이를 허용한다. 손상 길이는 다음 구조 검증
  후보·AVI·설정 상한으로 제한한다.
- SOS 뒤 `FF 00`은 stuffed data, `FF D0`~`D7`은 restart marker, `FF FF`는 fill로 처리한다.
- EOI 후보 뒤 최대 4096바이트를 본다. 즉시 64바이트 zero padding은 실제 끝으로 보고, stuffing 비율이
  0.3 미만이면 채택한다. 비율이 높고 FF 표본이 16개 이상이면 가까운 다음 경계가 있어도 가짜 EOI로
  본다. 표본이 희소할 때만 4 KiB 안의 구조 검증 경계를 보조 근거로 사용한다.
  EOI 뒤 검사 가능 구간이 128바이트 미만이면 반증이 부족하므로 후보를 채택한다.
- 다음 JPEG/AVI 오프셋은 고정 raw 상한이 아니라 entropy marker와 함께 cursor로 비교한다. APP/COM이 먼저
  나오면 payload 끝까지 건너뛴 뒤 내부 후보를 무시하고, 외부 후보만 불완전 경계로 채택한다.
- EOI를 찾지 못하면 `--max-jpeg-size` 또는 더 이른 외부 경계에서 `complete=False`로 끝난다.

### 3. AVI 경계

`avi_end`는 RIFF size를 무조건 신뢰하지 않는다. 선언 범위 안에서 `hdrl`·`avih`·`movi` core를 확인한
경우에만 선언 끝을 사용한다. `hdrl` LIST id의 1~2바이트 손상은 구조가 이어지면 허용하고, `movi` 뒤의
opaque padding/벤더 청크는 선언 범위에 보존한다.

size가 너무 작거나 0·상한 초과·외부 다음 후보를 가로지르면 top-level chunk walk로 끝을 복원한다.
fallback은 `movi/rec`의 `NNdc`·`NNdb` 등 stream payload 안 JPEG를 외부 경계로 쓰지 않는다. 연속 OpenDML
`RIFF...AVIX`는 선언 범위를 완전히 걷고 `LIST/movi`를 확인한 경우만 붙이며 `ix##` standard-index를
허용한다.

### 4. 중첩 분류와 저장

`media_recovery/discovery/materializer.py::process`는 동일 type/offset 후보를 근거가 강한 하나로 합치고 정렬한다. 성공한 마지막
최상위 범위를 활성 범위로 유지하므로 일반 포함 판정은 O(1)이다.

- 유효한 pre-SOS 길이형 세그먼트 payload 안의 hit은 파일 종류와 source에 관계없이 건너뛴다.
- Exif APP1 내부 JPEG만 썸네일로 센다. 저장할 때는 JPEG 자체 끝, APP1 끝, 부모 끝 중 가장 이른 곳에서
  자른다.
- AVI 내부 JPEG는 MJPEG 프레임이므로 JPEG나 썸네일로 별도 저장하지 않는다.
- 부모 JPEG의 일반 데이터 범위 안에서 별도 구조 근거를 가진 손상 시작은 중첩 최상위 후보로 보존한다.
- mmap 범위는 8 MiB 청크로 같은 디렉터리의 임시 파일에 쓴 뒤 `os.replace`한다. 일반 쓰기 예외에서 최종
  경로에 부분 파일을 노출하지 않지만 fsync 기반 전원 장애 내구성까지 보장하지는 않는다.

## 주요 엣지 케이스

| 상황 | 동작 |
|---|---|
| Exif APP1 내부 JPEG | 썸네일로 분류; `--save-thumbnails`가 없으면 저장하지 않음 |
| APP2/COM/DQT 등 유효 header payload의 JPEG·AVI-like hit | 부모의 metadata로 보고 건너뜀 |
| 0인 양자화 계수 등 의미가 무효인 header segment가 뒤 JPEG를 덮음 | 선언 길이를 버리고 뒤 구조 후보를 별도 파일로 보존; 앞 조각도 저장 |
| AVI `movi` 내부 exact/손상 JPEG hit | MJPEG frame으로 보고 건너뜀 |
| entropy의 가짜 EOI 뒤 조밀한 `FF 00` | 다음 EOI 또는 외부 경계까지 진행 |
| 절단 JPEG 뒤 DHT-start/손상 JPEG | 구조 검증 hit에서 앞 파일을 끝내고 둘 다 후보로 보존 |
| JPEG 뒤 AVI 또는 손상 AVI | AVI 시작에서 앞 JPEG를 끝냄; inter-scan APP/Exif payload 안 AVI는 예외 |
| 너무 작은 RIFF size가 `movi` 전에서 끝남 | size를 버리고 chunk walk로 `movi` 끝까지 복원 |
| RIFF size가 뒤 독립 JPEG를 가로지름 | size를 버리고 외부 후보 전에서 끝냄 |
| 구조 없는 `RIFF...AVIX` | 현재 AVI에 붙이지 않음 |

## 알려진 탐지 한계

- 손상 JPEG 자동 탐지는 JFIF/Exif 앵커와 EOI를 요구한다. APP 없는 table-first JPEG에서 SOI/첫 marker와
  EOI가 함께 손상된 경우는 놓칠 수 있다.
- RIFF 시작과 `AVI ` form type이 동시에 손상된 AVI는 현재 앵커가 없어 놓칠 수 있다. `AVI ` 자체가
  손상된 상태에서 RIFF까지 없는 경우도 같다.
- 기본 10 MiB/500 MiB보다 큰 정상 파일은 옵션을 늘리지 않으면 잘린다.
- 비연속 클러스터에 저장된 단편화 파일을 이어 붙이지 않는다. JFIF/Exif 앵커와 EOI까지 함께 손상되거나
  시작·후속 구조가 덮어써진 파일은 탐지 모델 밖이다.
- AVI stream/index 자체는 수리하지 않는다.

포맷 근거는 [포맷 메모](../format-notes.md), 결정 이유는 [ADR 목록](../adr/README.md)을 따른다.
