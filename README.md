# rawcarve

ddrescue 등으로 복구한 손상된 디스크 이미지(`.img`)에서 **JPEG 이미지**와 **AVI 영상** 파일을 추출하고, 손상된 JPEG를 복구하는 파일 카빙 도구.

`mmap` 기반 시그니처 탐색으로 대용량 이미지를 효율적으로 처리한다.

## 설치

```bash
pip install -r requirements.txt
```

Python 3.10 이상 권장.

## 사용법

### 1단계: 디스크 이미지에서 파일 추출

```bash
python carve.py <이미지 파일> [옵션]
```

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `-o, --output DIR` | 출력 디렉토리 | `./output` |
| `--max-avi-size MB` | AVI 최대 크기 (MB) | `500` |
| `--save-thumbnails` | 썸네일을 `jpeg_thumbnails/`에 저장 | 건너뜀 |

```bash
python carve.py usb.img -o output/
python carve.py usb.img -o output/ --max-avi-size 200 --save-thumbnails
```

### 2단계: 추출된 JPEG 복구 (선택)

```bash
python recover.py <입력 디렉토리> [옵션]
```

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `-o, --output DIR` | 출력 디렉토리 | `<input>_recovered` |
| `-q, --quality N` | 복구본 JPEG 품질 | `95` |
| `-j, --jobs N` | 병렬 프로세스 수 (`0`=CPU 수, `1`=순차) | `0` |
| `--fast` | 빠른 모드(부분 복구 감수) | 꺼짐(철저) |
| `--time-budget SEC` | 파일당 시간 상한(초, `0`=무제한) | 철저 90 / `--fast` 20 |

```bash
python recover.py output/jpeg/ -o output/jpeg_recovered/          # 철저(기본)
python recover.py output/jpeg/ --fast                             # 빠르게
python recover.py output/jpeg/ --time-budget 0                    # 무제한(밤새 실행)
```

**철저↔속도**: 기본은 **철저 모드**(먼 구멍까지 재동기 탐색 → 복구율↑, 느림). 빠르게 보려면
`--fast`, 더 끝까지 짜내려면 `--time-budget 0`. 손상이 심해 복구 불가한 영역은 어느 모드든
가짜로 채우지 않고 회색으로 남긴다.

## 출력 구조

```
output/
├── jpeg/               # 추출된 JPEG 파일 (0x{오프셋}.jpg)
├── jpeg_thumbnails/    # 내장 썸네일 (--save-thumbnails 사용 시)
├── avi/                # 추출된 AVI 파일 (0x{오프셋}.avi)
├── errors.log          # 추출 실패 오프셋 및 오류 내역
└── jpeg_recovered/     # recover.py 출력
    ├── report.csv          # 전체 복구 결과 (6종 분류 모두 기록, header_fix 컬럼 포함)
    ├── recovered/          # 엔진 재동기 복구본 (재인코딩 JPEG)
    ├── header_recovered/   # 헤더 재구성 복구본 (재인코딩 JPEG, header_fix에 교체 세그먼트)
    ├── clean/              # 손상 없던 원본 복사
    ├── failed/             # 복구 무행동 원본 복사 (회색 재인코딩본 대신 원본 보존)
    ├── skip_undecodable/   # 디코드 실패 원본 복사 (헤더 복구 후보도 못 찾은 파일)
    └── error/              # 워커 예외 원본 복사
```

파일명의 16진수 오프셋은 디스크 이미지 내 원본 위치를 나타낸다.

## 복구 전략 (resync 엔진)

손상된 엔트로피 스트림은 바이트 손상으로 **비트 정렬이 어긋나(디싱크)** 표준 디코더가
회색(스캔 중단) 또는 깨진(어긋난 채 진행) 출력을 낸다. resync 엔진은 비트 단위 디코더로
디싱크 지점을 정확히 짚고 정렬을 복원한다.

| 단계 | 동작 |
|------|------|
| 바이트 오라클 | 손상 지점 부근 바이트를 치환/삭제/삽입해 정렬 복원 (단일바이트 손상) |
| resync-skip | 다중바이트 손상/구멍은 재개 비트위치를 탐색해 건너뜀. DC 캐리/0 리셋을 함께 시도해 재동기 실패(hole)를 복구 |
| 헤더 복구 | 헤더(DHT/DQT/SOF/SOS) 손상으로 디코드가 불가한 파일은 도너 테이블 이식·양자화표 교정·SOF/SOS 재구성으로 헤더를 복원해 엔진에 태움. 확신 있는 후보가 없으면 채우지 않음 |
| 회색 유지 | 물리적으로 소실됐거나 데이터를 소진한 영역은 가짜로 채우지 않고 회색으로 남김 |

색 캐스트(DC=0 리셋의 무채색 포함)·이미지 밀림은 복구 대상이 아니다(구조 복원에 집중).
자세한 근거는 [ADR 0001](docs/adr/0001-resync-recovery.md)·[ADR 0004](docs/adr/0004-resync-dc-reset-recovery.md)·[ADR 0006](docs/adr/0006-header-recovery-structural-gates.md), [recover 스펙](docs/specs/0002-recover.md) 참조.

## 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```
