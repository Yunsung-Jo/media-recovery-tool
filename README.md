# rawcarve

파일 시스템 정보가 사라진 디스크 이미지에서 JPEG·AVI를 찾아 추출하고, 손상된 JPEG를 가능한 범위까지
복구하는 Python 도구다. 원본 이미지는 읽기 전용으로 열며 결과는 별도 출력 디렉터리에 저장한다.

## 요구 사항

- Python 3.10 이상

가상환경 사용을 권장한다.

```bash
python -m venv .venv
```

```powershell
# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```bash
# Linux / macOS
.venv/bin/python -m pip install -r requirements.txt
```

이후 예시에서는 가상환경을 활성화했거나 `python`을 위 가상환경의 실행 파일로 바꿔 사용한다고 가정한다.

## 1. 디스크 이미지 카빙

```bash
python carve.py <디스크 이미지> [옵션]
```

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `-o, --output DIR` | 출력 디렉터리 | `output` |
| `--max-avi-size MB` | AVI fallback 최대 크기 | `500` |
| `--save-thumbnails` | 추출 범위 안의 임베디드 JPEG도 저장 | 사용하지 않음 |

예시:

```bash
python carve.py usb.img -o output
python carve.py usb.img -o output --max-avi-size 200 --save-thumbnails
```

출력 구조:

```text
output/
├── jpeg/               # 추출한 JPEG
├── avi/                # 추출한 AVI
├── jpeg_thumbnails/    # --save-thumbnails 사용 시에만 생성
└── errors.log          # 항목별 추출 오류
```

파일명은 디스크 이미지 안의 시작 오프셋을 나타낸다. 예: `0x01A2B000.jpg`.

## 2. JPEG 복구

카빙한 JPEG를 추가로 복구하려면 `jpeg/` 디렉터리를 입력한다.

```bash
python recover.py <JPEG 디렉터리> [옵션]
```

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `-o, --output DIR` | 출력 디렉터리 | 입력 옆의 `<이름>_recovered` |
| `-q, --quality N` | 재인코딩 복구본의 JPEG 품질 | `95` |
| `-j, --jobs N` | 병렬 프로세스 수 (`0`=CPU 수, `1`=순차) | `0` |
| `--fast` | 가까운 손상만 탐색하는 빠른 모드 | 철저 모드 |
| `--time-budget SEC` | 복구 탐색 1회당 시간 상한 (`0`=무제한) | 철저 90초 / fast 20초 |

예시:

```bash
python recover.py output/jpeg
python recover.py output/jpeg --fast
python recover.py output/jpeg -o output/jpeg_recovered --time-budget 0
```

입력 디렉터리 최상위의 소문자 확장자 `*.jpg`만 처리한다. `--fast`는 처리 시간을 줄이는 대신 먼
재동기 지점을 놓칠 수 있다. 비교 실험이나 전체 기준선 재계산에는 시간에 따른 결과 차이를 없애기 위해
`--time-budget 0`을 사용한다.

시간 상한은 파일 전체가 아니라 개별 복구 탐색에 적용된다. 헤더가 손상된 파일은 여러 후보를 평가하므로
한 파일의 실제 처리 시간이 상한보다 길 수 있다.

## 복구 결과

복구 출력 루트에는 `report.csv`와 다음 디렉터리가 생성된다.

| 분류 | 저장 위치 | 의미 |
|---|---|---|
| `RECOVERED` | `recovered/` | 바이트 편집 또는 재동기를 적용한 복구본 |
| `HEADER_RECOVERED` | `header_recovered/` | 손상된 헤더를 재구성한 복구본 |
| `CLEAN` | `clean/` | 추가 복구가 필요 없었던 원본 |
| `FAILED` | `failed/` | 복구 연산을 적용하지 못해 원본을 보존한 파일 |
| `SKIP_UNDECODABLE` | `skip_undecodable/` | 디코더 구성과 헤더 복구가 모두 실패한 원본 |
| `ERROR` | `error/` | 처리 중 예외가 발생한 원본 |

`RECOVERED`와 `HEADER_RECOVERED`는 JPEG로 재인코딩된다. `CLEAN`, `FAILED`, `SKIP_UNDECODABLE`은
입력 바이트를 그대로 보존한다. 물리적으로 없거나 진위를 확인할 수 없는 영역은 임의로 생성하지 않고
회색으로 남긴다.

## 지원 범위와 한계

- 카빙은 JPEG와 AVI 시그니처를 지원한다.
- JPEG 복구 엔진은 3컴포넌트 baseline JPEG를 대상으로 한다.
- AVI는 추출만 하며 영상 스트림이나 인덱스를 수리하지 않는다.
- JPEG 재동기 결과에는 수평 밀림이나 색 캐스트가 남을 수 있다.

현재 전체 데이터 기준선과 후속 작업은 [현재 상태](docs/current-state.md)를 확인한다.

## 개발

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

- [문서 안내](docs/README.md)
- [아키텍처](docs/architecture.md)
- [carve 동작 계약](docs/specs/0001-carve.md)
- [recover 동작 계약](docs/specs/0002-recover.md)
- [아키텍처 결정 기록](docs/adr/README.md)
