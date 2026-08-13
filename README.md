# Media Recovery Tool

손상된 디스크 이미지에서 JPEG와 AVI를 카빙하고, baseline JPEG를 구조적으로 복구하는 Python 도구다.
원본 이미지는 읽기 전용으로 열며 결과는 별도 출력 디렉터리에 저장한다. AVI는 현재 경계를 계산해
추출할 뿐 영상 스트림이나 인덱스를 복구하지 않는다.

## 요구 사항

- Python 3.10 이상

가상환경 사용을 권장한다.

```bash
python -m venv .venv
```

가상환경을 활성화한다.

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

```bash
# Linux / macOS
source .venv/bin/activate
```

그다음 package를 editable mode로 설치한다.

```bash
python -m pip install -e .
```

개발 환경에는 테스트 의존성까지 설치한다.

```bash
python -m pip install -e ".[dev]"
```

이후 예시에서는 활성화한 가상환경의 `media-recovery` console script를 사용한다고 가정한다.

## 1. 디스크 이미지 카빙

```bash
media-recovery carve <디스크 이미지> [옵션]
```

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `-o, --output DIR` | 출력 디렉터리 | `output` |
| `--max-avi-size MB` | AVI 경계 탐색·추출 최대 크기 | `500` |
| `--max-jpeg-size MB` | JPEG 경계 탐색·추출 최대 크기 | `10` |
| `--save-thumbnails` | JPEG의 Exif APP1 내부 미리보기 JPEG도 저장 | 사용하지 않음 |

예시:

```bash
media-recovery carve usb.img -o output
media-recovery carve usb.img -o output --max-jpeg-size 20 --max-avi-size 200 --save-thumbnails
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
최대 크기는 fallback만이 아니라 정상 파일에도 적용되는 하드 상한이다. 더 큰 파일이 예상되면 해당 옵션을
늘린다. 같은 출력 디렉터리를 재사용하면 같은 이름은 교체되지만 이번 실행에서 사라진 예전 파일은 자동으로
지우지 않으므로, 기준선을 만들 때는 비어 있는 새 디렉터리를 사용한다.

`시작 후보 발견`은 구조 검증 전·중첩 후보를 포함하므로 최종 파일 수가 아니다. 완료 요약의
`Thumbnails`는 `--save-thumbnails`를 쓰지 않아 저장을 생략한 Exif 미리보기도 센다.

## 2. JPEG 복구

카빙한 JPEG를 추가로 복구하려면 `jpeg/` 디렉터리를 입력한다.

```bash
media-recovery reconstruct <JPEG 디렉터리> [옵션]
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
media-recovery reconstruct output/jpeg
media-recovery reconstruct output/jpeg --fast
media-recovery reconstruct output/jpeg -o output/jpeg_recovered --time-budget 0
```

입력 디렉터리 최상위의 소문자 확장자 `*.jpg`만 처리한다. `--fast`는 처리 시간을 줄이는 대신 먼
재동기 지점을 놓칠 수 있다. 비교 실험이나 전체 기준선 재계산에는 시간에 따른 결과 차이를 없애기 위해
`--time-budget 0`을 사용한다.

시간 상한은 파일 전체가 아니라 개별 복구 탐색에 적용된다. 헤더가 손상된 파일은 여러 후보를 평가하므로
한 파일의 실제 처리 시간이 상한보다 길 수 있다.

## 3. 썸네일 참조 보정 (선택)

복구본에 남은 순환 MCU 밀림과 색 캐스트 밴드를, 카빙 원본의 EXIF 썸네일을 정답 근사로 사용해
추가 보정한다. 썸네일이 없거나 근거가 부족한 파일은 바이트 그대로 복사된다.

```bash
media-recovery enhance <카빙 원본 디렉터리> <reconstruct 출력 디렉터리> [옵션]
```

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `-o, --output DIR` | 출력 디렉터리 | 입력 옆의 `<이름>_thumbref` |
| `-j, --jobs N` | 병렬 프로세스 수 (`0`=CPU 수, `1`=순차) | `0` |

예시:

```bash
media-recovery enhance output/jpeg output/jpeg_recovered -j 6
```

출력 루트의 `report_thumbref.csv`에 파일별 판정(corrected/identity/rollback/skip_*)과 추정
지표가 기록된다. 보정본은 복구본의 양자화 테이블과 원본 서브샘플링으로 저장해 추가 손실을
최소화한다. 근거는 [ADR 0012](docs/adr/0012-thumbnail-reference-correction.md)를 본다.

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

- 카빙은 정확 JPEG·AVI 시작뿐 아니라 구조가 이어지는 일부 JFIF/Exif·RIFF/form-type 손상 시작을 찾는다.
- 손상 JPEG 탐지는 JFIF/Exif 앵커와 EOI를 요구한다. APP 없는 table-first JPEG의 시작과 EOI가 함께
  손상된 경우, `AVI ` form type과 RIFF 시작이 함께 손상된 경우는 자동 탐지하지 못할 수 있다.
- 비연속 클러스터에 저장된 단편화 파일을 이어 붙이지 않는다. 시작·앵커·후속 구조가 함께 덮어써진 파일의
  누락 여부도 원본 정답 없이 완전히 증명할 수 없다.
- JPEG 복구 엔진은 3컴포넌트 baseline JPEG를 대상으로 한다.
- AVI는 추출만 하며 영상 스트림이나 인덱스를 수리하지 않는다.
- JPEG 복구 결과는 먼저 편집·재동기 절단점의 MCU 밴드를 배치한 뒤, 상단 행을 기준으로 모든 MCU 행
  경계의 절대 위상을 최대 5회 함께 맞춘다. 전역 해가 없거나 안전 게이트에서 기각될 때만 절단점 주변의
  짧은 구조 밀림과 기존 보수적 행 스티치를 적용한다. 출력 크기와 원본 MCU 순서를 유지하면서 전체 MCU의
  5% 안에서 회색 MCU 삽입·유실을 허용한다. 안전 게이트까지 기각된 구간에는 밀림이 남을 수 있고,
  DC 재설정으로 생긴 색 캐스트는 아직 보정하지 않는다.

현재 전체 데이터 기준선과 후속 작업은 [현재 상태](docs/current-state.md)를 확인한다.

## 개발

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

- [문서 안내](docs/README.md)
- [아키텍처](docs/architecture.md)
- [carve 동작 계약](docs/specs/0001-carve.md)
- [recover 동작 계약](docs/specs/0002-recover.md)
- [아키텍처 결정 기록](docs/adr/README.md)
