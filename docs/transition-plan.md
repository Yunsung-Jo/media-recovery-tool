# Media Recovery Tool 전환 계획

> 상태: 승인된 목표 방향. 아직 구현되지 않은 내용이 포함되어 있다.
>
> 현재 코드의 실제 동작은 `design.md`·`artifacts.md`의 Current 절과 `specs/`를 따르고, 현재 검증
> 범위는 `status.md`·`evaluation.md`, 전환 작업의 범위는 `tasks/active/`의 활성 Task를 따른다.
> Planned 목표 구조를 현재 구현으로 오해하지 않는다.

이 문서는 `rawcarve`를 **Media Recovery Tool**로 전환하기 위해 사용자와 합의한 방향을 보존한다.
전환이 완료되면 지속할 내용은 `design.md`, `artifacts.md`, `evaluation.md`, `status.md`에 흡수하고 이
문서는 제거할 수 있다.

## 1. 문제와 목표

현재 구현에는 의미 있는 카빙·복구 기능과 248개의 회귀 테스트가 있지만 다음 문제가 있다.

- `src/media_recovery/reconstruction/engine.py` 한 파일이 엔트로피 탐색, 디코드, 공간 배치, 평가,
  분류와 저장을 함께 담당한다.
- 복구 상태가 튜플, NumPy 배열, 가변 `dict`에 암묵적으로 전달되어 관측값·추론·표시값을 구분하기 어렵다.
- 단일 선택 결과와 재인코딩 JPEG 중심이어서 선택되지 않은 후보와 선택 근거를 나중에 재감사하기 어렵다.
- `output_cN`, `jpeg_recovered_*`, `shift_experiments` 같은 이름만으로 입력·코드·옵션의 관계를 복원하기
  어렵다.
- ADR, 기능 명세, 현재 상태, 실험 기록 사이에 내용이 중복되고 문서의 역할이 흐려졌다.

전환의 목표는 파일을 작게 나누는 것 자체가 아니다. 다음을 명시적으로 표현하는 포렌식 복구 도구를 만든다.

- 디스크에서 직접 관측한 byte와 절대 offset
- 객체의 소속 관계와 복수 경계 가설
- header, entropy, segment, placement 가설
- 실제로 소비한 source bit span과 virtual edit
- block/component별 유효성, gap, overlap, 반증과 불확실성
- 원본 근거가 있는 복구 결과와 표시·향상을 위한 생성값의 차이

## 2. 확정한 프로젝트 정체성

| 용도 | 이름 |
|---|---|
| 표시 이름 | `Media Recovery Tool` |
| 저장소 디렉터리 | `media-recovery-tool` |
| Python 배포 이름 | `media-recovery-tool` |
| Python 패키지 | `media_recovery` |
| CLI 명령 | `media-recovery` |

README의 지원 범위는 이름보다 좁고 정확하게 쓴다.

> 손상된 디스크 이미지에서 JPEG와 AVI를 카빙하고, baseline JPEG를 구조적으로 복구하는 도구

AVI는 현재 경계를 계산해 추출할 뿐 영상 스트림이나 인덱스를 복구하지 않는다.

### 호환성 결정

- 기존 `carver` import 호환 계층을 두지 않는다.
- 기존 루트 `carve.py`, `recover.py`, `thumbref.py` wrapper를 남기지 않는다.
- `recover` CLI alias를 두지 않고 `reconstruct`를 사용한다.
- 패키지 이전 뒤 내부 import는 `media_recovery`만 사용한다.
- 저장소 루트 이름 변경은 이 계획을 준비한 세션 종료 뒤 수행하고, 다음 세션은 새 경로에서 연다. 활성
  Codex 프로세스가 Windows 디렉터리 handle을 잡고 있으면 현재 세션 안에서 rename을 우회하지 않는다.

## 3. 용어와 단계의 경계

| 용어 | 의미 |
|---|---|
| `carve` | 디스크에서 객체 후보와 범위를 찾고 원본 byte를 추출한다. |
| `reconstruct` | source에 근거해 header, entropy, segment와 placement 후보를 복원·선택한다. |
| `preview` | 포렌식 결과를 일반 이미지 뷰어에서 볼 수 있도록 충실하게 시각화한 파생물이다. |
| `enhance` | 썸네일, 주변 문맥 또는 AI 등으로 보기 좋은 값을 추정하는 선택적 파생 단계다. |

`preview`는 원본 복구가 아니다. 예를 들어 Cb/Cr가 없을 때 중립값 128을 넣거나 gap을 회색으로 표시하는
것은 화면 표시를 위한 결정이다. 포렌식 artifact에는 해당 component가 `missing`이라는 사실을 그대로
남긴다. `enhance`가 만든 값은 어떤 경우에도 source에서 복구한 값으로 표시하지 않는다.

장기 CLI는 `carve`, `reconstruct`, `render`, `enhance`를 사용한다. T-0001은 현재 기능을 옮기는
`carve`·`reconstruct`·`enhance`만 만들고, forensic artifact에서 preview를 다시 만드는 `render`는
T-0010에서 추가한다.

## 4. 목표 저장소 구조

```text
media-recovery-tool/
├── pyproject.toml
├── work/                  # Git 비추적 기본 작업 공간
├── src/
│   └── media_recovery/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli/
│       ├── domain/
│       ├── discovery/
│       ├── formats/
│       ├── reconstruction/
│       ├── artifacts/
│       ├── rendering/
│       └── enhancement/
├── tests/
├── docs/
│   └── tasks/
└── schemas/
```

`src`는 설치 대상 소스의 루트이고 `media_recovery`가 Python namespace다. `src` 바로 아래에
`discovery`, `rendering` 같은 일반 이름의 최상위 Python 패키지를 만들지 않는다.

`work/`는 저장소 안에 있지만 `/work/` 전체를 Git에서 제외하는 로컬 실행 데이터 영역이다. 정식 문서나
fixture를 `work/`에 두지 않는다. 기본 위치는 명령을 실행한 현재 디렉터리의 `./work`이고, 큰 자료를 다른
장치에 둘 때는 `--work-root`로 바꿀 수 있어야 한다.

### 목표 책임

- `cli/`: 명령 등록, 인자 검증, 사용자 출력
- `domain/`: 객체, 가설, segment, evidence, provenance 모델
- `discovery/`: 디스크 후보 탐색, 객체 그래프, 형식 간 경계 조정, 추출
- `formats/jpeg/`: JPEG marker, Exif, entropy와 baseline decoder
- `formats/avi/`: RIFF, AVI, OpenDML 구조와 경계
- `reconstruction/`: header 가설, entropy 탐색, segment, placement와 후보 평가
- `artifacts/`: case, run, manifest와 포렌식 artifact 읽기·쓰기
- `rendering/`: 포렌식 결과에서 preview 생성
- `enhancement/`: 썸네일 기반 보정과 향후 선택적 AI 파생 처리

### 초기 지원 범위

프로젝트 이름이 지원 범위보다 넓으므로 초기 reconstruction 경계를 명시한다.

- 8-bit, 3-component baseline sequential JPEG를 구조 복구 대상으로 한다.
- progressive JPEG와 grayscale/CMYK 등 비 3컴포넌트 JPEG는 초기 reconstruction 대상이 아니다.
- AVI는 객체 경계를 찾아 추출하지만 stream·index·재생 구조를 복구하지 않는다.
- 비연속 클러스터에 저장된 단편화 객체를 자동으로 이어 붙이지 않는다.
- 다만 장기 object model은 한 객체가 여러 source span을 가질 수 있도록 확장 가능해야 한다.

초기 패키지 이전에서는 현재 파일을 일대일로 이동하고 내부 알고리즘을 동시에 재작성하지 않는다.
특히 `reconstruction/engine.py`의 책임 분리는 별도 Task에서 수행한다.

## 5. 목표 파이프라인

```text
손상 디스크 이미지
  → 객체 후보와 소속 관계
  → 복수 경계·header 가설
  → entropy decode와 resync 후보
  → decode segment와 component validity
  → MCU/segment placement와 gap
  → 후보 평가와 필요 시 이전 단계 재탐색
  → 포렌식 artifact
  → preview
  → 선택적 thumbnail/AI enhancement
```

`decode → resync → placement → evaluation`은 한 번만 지나는 직선 단계가 아니다. 제한된 N-best 후보를
유지하며 배치 결과가 더 앞선 resync 또는 header 가설의 재평가를 요청할 수 있다. AI 결과는 이 반복에
들어오지 않는다.

### 5.1 객체 탐색과 경계

- 입력은 개별 JPEG가 아니라 손상 디스크 이미지다.
- 모든 후보와 산출물은 디스크 절대 offset으로 원본 위치를 역추적할 수 있어야 한다.
- 첫 `SOI`부터 첫 `EOI`까지를 최종 JPEG 경계로 확정하지 않는다.
- AVI 내부 MJPEG와 APP1 내부 thumbnail을 독립 사진으로 카빙하지 않는다.
- APP/COM payload 안의 signature와 외부 객체를 구분한다.
- 객체는 부모·자식 관계, 최소·최대 가능 범위, 경계 근거와 반증을 가진다.

### 5.2 Exif와 thumbnail

- APP1의 소유 JPEG와 내장 thumbnail 범위를 보존한다.
- 다른 JPEG의 APP1, GPS, MakerNote, width/height를 donor로 복사하지 않는다.
- thumbnail이 없어도 reconstruction이 동작해야 한다.
- 초기 구현에서 thumbnail은 enhancement에만 사용한다. 자세한 정책은 9절을 따른다.

### 5.3 Header hypothesis

하나의 header를 성급히 확정하지 않고 width/height, sampling, component mapping, DHT, DQT, SOS, DRI의
출처와 가정을 기록한 복수 후보를 만든다.

- 파일 자체에서 읽은 값
- 손상 구조에서 복구한 값
- 표준 기본값
- donor assumption
- 탐색으로 추정한 값

DHT와 DQT donor는 사용할 수 있지만 원본 값이 복구됐다고 주장하지 않는다. DHT는 entropy symbol 해석,
DQT는 quantized coefficient를 영상으로 해석하는 역할이므로 출처와 영향도도 별도로 기록한다.

### 5.4 Entropy와 segment

장기적으로 decoder는 다음 기능을 제공해야 한다.

- 임의 bit 위치 시작
- state clone, checkpoint와 rollback
- DC predictor 보존
- MCU/block 중간 실패
- block별 source bit 범위
- 복수 후보 동시 유지

각 `DecodeSegment`는 실제 소비한 source bit span, skip, virtual edit, MCU 범위, DC 상태,
block/component validity, evidence와 header hypothesis를 가진다. source bit는 항상 단조 증가하고 이미 확정한
source span을 부당하게 재사용하지 않는다.

#### 좌표계와 provenance

`source_bit_start` 같은 숫자는 좌표계를 명시하지 않으면 포렌식 의미가 없다. 다음 공간을 구분하고 각
변환을 추적한다.

```text
disk byte offset
→ object 내부 raw byte offset
→ raw entropy byte/bit offset
→ byte stuffing 제거 후 entropy bit offset
→ virtual edit가 적용된 작업 bit offset
→ MCU / component / block / coefficient 위치
```

- observed source span의 정본 좌표는 수정되지 않은 disk/object raw byte다.
- destuff mapping은 `FF 00`, fill, restart marker를 제거·해석한 위치를 원본 raw byte로 역변환할 수 있어야
  한다.
- virtual edit는 편집 전 source 좌표, 편집 뒤 작업 좌표, 종류와 가정값을 모두 기록한다.
- 하나의 block이 여러 불연속 source span에 대응하면 단일 범위로 합치지 않고 span 목록으로 보존한다.
- placement는 source 좌표를 바꾸지 않고 source block을 raster 위치에 연결하는 별도 mapping이다.
- 원본 또는 carved byte를 직접 수정하지 않고 편집은 항상 가상 작업 stream에서만 수행한다.

component/block validity는 실제로 source 위치가 입증된 범위만 유효로 표시한다. 예를 들어 Cb block 실패 뒤
Cr처럼 보이는 decode가 가능하더라도 Cr 시작 bit가 독립적으로 다시 입증되지 않았다면 Cr을 유효하다고
표시하지 않는다.

#### 손상 판정과 재동기 trigger

실제 손상 위치와 decoder가 명시적으로 멈춘 위치는 다를 수 있다. 손상 뒤의 잘못된 bit 정렬에서도 우연히
유효한 Huffman code가 이어질 수 있으므로, 나중에 발생한 hard failure만 손상 시작으로 확정하지 않는다.

- hard trigger: Huffman symbol을 더 읽을 수 없음, coefficient/run length 또는 block 범위 위반,
  component·marker 상태 모순, 객체 경계 밖 접근처럼 현재 decode를 그대로 계속할 수 없는 경우
- soft trigger: Y/Cb/Cr 관계나 MCU 격자의 국소 불연속, bit 소비 패턴의 급변, 반복되는 row-wrap,
  coefficient 통계와 인접 segment 연결성 악화처럼 decode는 계속되지만 더 앞선 손상이 의심되는 경우

soft trigger 하나만으로 현재 경로를 손상으로 확정하거나 폐기하지 않는다. 의심점 앞뒤의 checkpoint에서
대체 bit alignment·skip·virtual edit 후보를 추가하고 기존 후보와 함께 평가한다. 다른 resync 후보에서
이상이 사라지는지는 중요한 비교 근거지만, RGB 모양이나 단일 통계만으로 source 진위를 결정하지 않는다.

변화량이나 entropy가 낮은 정상 영역은 그 사실만으로 손상으로 분류하지 않는다. source span 타당성,
marker/MCU 구조, component 간 관계, bit alignment confidence와 대체 후보의 반증을 함께 사용한다.

여러 행에 걸쳐 같은 위치에서 반복되는 전역 wrap이나 지속적인 geometry 모순은 LocalBand로 밀어 맞추지
않고 width/height·sampling·component mapping을 포함한 header hypothesis 단계로 되돌린다. LocalBand는
위·아래 경계가 함께 개선되는 얇고 국소적인 구간에만 적용한다.

### 5.5 Placement와 LocalBand

배치는 source 순서, marker/RST/MCU 구조, component validity, coefficient 연속성, Y/Cb/Cr 관계,
row-wrap과 seam을 순서대로 고려한다. RGB seam은 중요하지만 단독 source truth가 아니다.

복구 불가능한 MCU는 명시적 `gap`으로 남길 수 있다. 뒤 segment를 앞 segment 바로 뒤에 억지로 붙이지
않는다. 얇은 국소 구간은 별도 후처리 엔진이 아니라 제한된 placement proposal로 표현한다.

```text
KEEP  SHIFT  SPLIT  DROP  GAP  DEFER
```

국소 offset은 해당 범위 밖으로 자동 전파하지 않고 위·아래 seam이 함께 개선되는지 평가한다.

## 6. 포렌식 데이터 계층

모든 결과는 다음 출처 상태를 구분한다.

| 상태 | 의미 |
|---|---|
| `observed` | 디스크에서 직접 획득한 byte와 span |
| `decoded` | source bits에 대응해 실제 디코드한 coefficient/component |
| `inferred` | header, placement, DC reset 등 추론한 값 |
| `generated` | 중립 채움, 회색 gap, 보간, 썸네일 보정, AI 생성 |

포렌식 결과는 재인코딩 JPEG가 아니라 다음을 포함하는 artifact bundle이다.

- 원본 disk offset과 byte span
- object boundary와 header hypothesis
- DCT coefficient와 Y/Cb/Cr component validity
- decode segment, source bit span, virtual edit
- MCU placement, gap, overlap와 owner
- evidence, 반증, 선택 이유
- 선택하지 않은 주요 후보

canonical coefficient는 Huffman entropy에서 디코드한 **DQT 적용 전 quantized DCT coefficient**다. 어떤
DQT hypothesis로 dequantize했는지는 별도 참조로 기록한다. dequantized coefficient, spatial Y/Cb/Cr와
RGB는 해당 DQT·sampling·표시 정책에서 생성된 파생값이다. T-0004는 validity의 block/coefficient 단위,
부분 block 표현과 각 block의 source span 형식을 명시해야 한다.

preview와 enhanced 이미지는 이 bundle에서 생성되는 파생물이다.

### Preview 계약

- 기본 preview는 재압축 영향을 피하기 위해 무손실 PNG로 만든다.
- 편의용 JPEG는 선택적 파생물이며 포렌식 정본이나 품질 비교 입력으로 사용하지 않는다.
- 유효한 Y와 missing Cb/Cr 조합은 중립 chroma 128로 표시할 수 있지만 missing 상태는 validity mask에
  그대로 남긴다.
- gap과 missing component를 어떻게 채우는지는 버전된 rendering policy로 기록한다.
- 진단용 색 overlay와 annotation은 기본 preview를 덮지 않고 별도 파일로 만든다.
- preview에는 원본 Exif/GPS/MakerNote를 자동 복사하지 않는다. 원본 metadata byte는 forensic artifact에서
  observed data로 별도 보존한다.
- reconstruction 명령은 기본 preview를 함께 생성할 수 있다. 별도 `render` 명령은 reconstruction을 다시
  실행하지 않고 같은 forensic artifact에 다른 표시 정책을 적용할 때 사용한다.
- preview 생성 실패는 reconstruction evidence나 선택 결과를 바꾸지 않는다.

## 7. Case, run과 artifact 형식

### 7.1 저장 위치

기본 work root는 명령을 실행한 디렉터리의 `./work`다. 저장소에서 실행하면
`media-recovery-tool/work/`가 되며 `/work/` 전체는 Git 비추적이다. `--work-root`로 외부 디스크를 지정할
수 있지만 외부 위치를 필수로 하지 않는다.

원본 이미지는 기본적으로 `work/`에 복사하지 않고 `case.json`에서 절대 경로, 크기와 SHA-256으로
등록한다. 사용자가 명시적으로 import한 경우만 case 아래 source 보관을 허용한다. case/run은 자동
삭제하지 않고 `cache/`와 `tmp/` 정리도 명시적 명령에서만 수행한다. 기존 `output*`과
`shift_experiments`는 2026-08-12에 사용자가 외부로 이동했으며 legacy 자료로 보존한다.

로컬 `case.json`은 source 절대 경로를 가질 수 있지만 공유용 export는 사용자명·디렉터리 구조를 노출하지
않도록 경로를 label과 hash 기반 참조로 redaction한다. forensic observed metadata와 공유용 preview/export의
개인 metadata 정책을 분리한다.

```text
work/
├── cache/
├── tmp/
└── cases/
    └── <case-id>/
        ├── case.json
        ├── source/                  # 명시적으로 import한 경우만
        └── runs/
            ├── <discovery-run-id>/
            │   ├── run.json
            │   ├── objects.jsonl
            │   ├── carved/
            │   ├── reports/
            │   └── logs/
            ├── <reconstruction-run-id>/
            │   ├── run.json
            │   ├── results.jsonl
            │   ├── candidates.jsonl
            │   ├── forensic/
            │   ├── preview/         # 같은 run의 기본 preview
            │   ├── reports/
            │   └── logs/
            ├── <rendering-run-id>/
            │   ├── run.json
            │   └── preview/
            └── <enhancement-run-id>/
                ├── run.json
                └── enhanced/
```

객체 탐지 결과는 scanner·옵션에 따라 바뀌므로 `objects.jsonl`과 `carved/`를 case 루트에서 갱신하지 않고
discovery run에 귀속한다. 각 `run.json`은 `stage`와 `parent_run_ids`를 가진다. reconstruction은 사용한
discovery run, rendering과 enhancement는 사용한 reconstruction 또는 rendering run을 명시한다. 이
lineage는 독립적인 재탐색·재복구·재렌더를 가능하게 하며 완료 run을 덮어쓰지 않는다.

### 7.2 Canonical record

확정한 초기 형식은 다음과 같다.

- `case.json`, `run.json`: 단일 메타데이터이므로 JSON
- discovery run의 `objects.jsonl`, reconstruction run의 `results.jsonl`·`candidates.jsonl`: record 단위
  스트리밍을 위해 JSONL
- coefficient와 validity 같은 대규모 typed array: object/candidate별 압축 NPZ
- CSV: 사람이 보는 파생 보고서이며 canonical record가 아님

JSONL은 UTF-8/LF를 사용하고 `NaN`과 `Infinity`를 허용하지 않는다. worker가 한 파일에 동시에 쓰지 않고
coordinator가 결정적인 순서로 모아 임시 파일에 쓴 뒤 `os.replace`로 확정한다. artifact hash를 record에
기록한다.

NPZ는 load 시 `allow_pickle=False`를 강제하고 object dtype을 저장하지 않으며 고정 dtype/byte order를
사용한다. manifest에 배열 이름, shape, dtype과 파일 SHA-256을 기록한다. 초기 예시는 다음과 같고 정확한
shape와 validity enum은 T-0004에서 확정한다.

```text
coef_y             int32
coef_cb            int32
coef_cr            int32
valid_y            uint8
valid_cb            uint8
valid_cr            uint8
source_bit_start    int64
source_bit_end      int64
placement_owner     int32
```

NPZ 전체 로딩이 실제 병목으로 측정될 때만 Zarr나 HDF5를 검토한다.

### 7.3 식별자

초기 계약은 다음과 같다.

```text
case-<source SHA-256 앞 20 hex>
run-<UTC YYYYMMDDThhmmssZ>-<6자리 random base32>
jpeg-<16자리 absolute offset hex>
avi-<16자리 absolute offset hex>
cand-000
```

예:

```text
case-4f2c9a31d8e74210c98b
run-20260812T153045Z-k7m2qx
jpeg-0000000042e21000
cand-000
```

case ID는 source 내용에 안정적이며 `case.json`에 전체 SHA-256, 크기와 사용자 label을 저장한다. 같은
20자리 prefix의 case가 이미 있으면 전체 SHA-256을 비교해 충돌을 거부한다. run ID 경로가 이미 존재하면
random suffix를 다시 생성한다.

object ID는 discovery run 안에서 media type과 절대 시작 offset에 안정적이다. 여러 boundary/header 가설은
같은 object 아래 candidate로 둔다. 향후 단편화 객체는 여러 source span을 갖되 anchor가 같은 기존
object와 충돌하지 않는 확장 규칙을 별도 schema major에서 정한다.

`cand-000`은 `case/run/object` 안에서만 유효한 ordinal이다. 다른 run의 동일·유사 후보를 비교할 수 있도록
정규화한 header, source span, edit와 placement의 canonical serialization에서 계산한
`candidate_fingerprint`도 기록한다.

run ID는 식별자일 뿐 재현 정보를 압축하지 않는다. `run.json`에 stage, parent run, git commit, dirty 여부,
도구·엔진·정책 버전, 옵션, Task ID, 시간, 환경과 random seed를 별도로 기록한다. dirty 실행은 diff hash뿐
아니라 실제 patch artifact도 보존한다. baseline run은 원칙적으로 clean worktree에서 만든다.

### 7.4 Run lifecycle과 결정성

- run은 `created → running → completed`로 진행하며 `interrupted`와 `error` 종료를 구분한다.
- 실행 중 record는 run 내부 staging 위치에 기록하고 canonical JSONL은 coordinator가 결정적인 순서로
  정렬해 임시 파일에 쓴 뒤 원자적으로 확정한다.
- `completed` run은 불변이며 resume하거나 파일을 추가하지 않는다.
- 중단 run resume는 source hash, 코드·정책·schema, 옵션이 모두 같고 해당 stage가 명시적으로 지원할 때만
  같은 run ID를 사용한다. 그렇지 않으면 부모 run을 참조하는 새 run을 만든다.
- 완료 marker와 `run.json` 상태가 모두 일치할 때만 completed로 읽는다. 부분 JSONL은 canonical 결과로
  읽지 않는다.
- worker 완료 순서와 무관하게 object offset, candidate ranking과 안정된 tie-break key로 결과를 정렬한다.
- random을 쓰는 모든 단계는 seed를 기록하며 동일 입력·버전·정책·seed의 선택 결과는 worker 수와 무관해야
  한다.

## 8. 결과 상태 명명

기존 `CLEAN`, `RECOVERED`, `HEADER_RECOVERED`, `FAILED`, `SKIP_UNDECODABLE`, `ERROR` 하나로 결과를
대표하지 않는다. 특히 `CLEAN`과 `RECOVERED`는 실제 무손상 또는 원본 복구를 증명하는 것처럼 읽힌다.

canonical record는 다음 직교 상태를 사용한다.

```text
execution_status:     completed | interrupted | error
support_status:       supported | partially_supported | unsupported
decode_extent:        complete | partial | none | not_attempted
selection_status:     source_candidate_selected |
                      reconstruction_candidate_selected |
                      no_supported_candidate | not_applicable
header_basis:         source | source_repaired | standard_assumption |
                      donor_assumption | hypothesis | none
artifact_status:      complete | partial | unavailable
```

`interventions`에는 `byte_substitution`, `byte_deletion`, `byte_insertion`, `bit_resync`, `dc_reset`,
`mcu_placement` 등을 별도 목록으로 기록한다. 사용자 보고서의 요약 label은 이 필드에서 파생하며
`SOURCE_DECODED`, `RECONSTRUCTION_CANDIDATE_SELECTED`, `NO_SUPPORTED_CANDIDATE`, `UNSUPPORTED`,
`PROCESSING_ERROR`처럼 보수적으로 표현한다.

정확한 JSON Schema와 enum은 T-0003/T-0004에서 테스트와 함께 확정한다. 의미를 다시 하나의 `action`으로
축소하지 않는 원칙은 확정이다. JSON Schema는 필드별 enum뿐 아니라 허용되는 조합도 검증한다. 예를 들어
`execution_status=error`인 결과가 어떤 partial artifact를 보존할 수 있는지, `unsupported`와
`decode_extent=not_attempted`의 관계를 명시해야 한다.

## 9. Thumbnail 정책

초기 정책은 **thumbnail을 enhancement에만 사용**하는 것이다.

동일 APP1의 thumbnail은 같은 객체에서 관측한 source지만 다음 한계가 있다.

- 손상됐거나 본 이미지와 crop·회전·색 처리 방식이 다를 수 있다.
- 편집된 파일에서는 오래된 thumbnail일 수 있다.
- 낮은 해상도라 MCU 단위 bit alignment를 직접 입증하지 못한다.
- 구조적으로 잘못됐지만 시각적으로 닮은 후보를 선택할 위험이 있다.

따라서 초기 reconstruction에서 thumbnail은 다음을 하지 않는다.

- header/resync 후보 생성
- 구조 게이트 실패 후보 구제
- 더 강한 source 구조 증거를 가진 후보 뒤집기

향후 통제 손상 코퍼스에서 top-1 정확도 개선과 정상 회귀 0을 입증한 별도 Task가 있을 때만, 구조적으로
동급인 후보의 마지막 tie-breaker로 검토한다. 이때도 `auxiliary_evidence`로 사용 사실과 영향도를 기록하고
thumbnail 없이 선택한 결과를 함께 보존한다.

### AI enhancement 경계

AI는 forensic artifact나 reconstruction 후보를 수정하지 않고 preview에서 파생된 별도 enhancement만
만든다.

- `M_model`: 모델이 주변 문맥을 보기 위한 넓은 마스크
- `M_replace`: 최종 enhanced 결과에서 실제 모델 출력으로 교체할 영역
- 모델이 `M_model` 전체를 다시 그려도 `M_replace` 밖은 원래 preview에서 복원한다.
- `M_replace`는 source가 없거나 손상으로 확정한 영역보다 자동으로 넓어지지 않는다.
- 모델, prompt, seed, 입력 preview와 두 mask의 hash를 enhancement run에 기록한다.
- AI가 만든 pixel은 항상 `generated`이며 원본 복구율이나 source-backed validity에 포함하지 않는다.

## 10. N-best 탐색과 후보 평가

N-best는 즉시 구현하지 않고 T-0007~T-0009에서 통제 손상 코퍼스로 보정한다. 초기 profile 제안은
다음과 같으며 **공개 동작 계약이 아니라 검증할 시작값**이다.

| 설정 | fast | balanced | forensic |
|---|---:|---:|---:|
| 구조 게이트 후 header 후보 | 2 | 4 | 8 |
| entropy beam width | 3 | 8 | 16 |
| 실패점당 신규 분기 | 2 | 4 | 6 |
| 전역 활성 상태 상한 | 8 | 32 | 96 |
| 최종 보존 후보 | 1 | 3 | 5 |
| 탈락 후보 배열 저장 | 아니오 | 아니오 | 예 |

hard trigger에서는 byte substitution/deletion/insertion, bit resync, DC carry/reset, gap 후 재개, 복구 종료
후보를 만들 수 있다. soft trigger는 현재 경로를 제거하지 않고 대체 후보만 추가한다.

다음은 점수가 아니라 hard gate다.

- source bit 역행 또는 부당한 source span 재사용
- 객체 범위 밖 접근
- 불가능한 component/Huffman mapping
- 구조적으로 모순되는 marker 관계
- 역순 MCU ownership
- artifact invariant 위반

단일 불투명 confidence 대신 다음 evidence vector를 보존한다.

```text
structural_consistency
source_backed_valid_blocks
component_completeness
stable_decode_coverage
soft_anomaly_count
virtual_edit_cost
skipped_source_bits
placement_gap
placement_overlap
visual_discontinuity
auxiliary_thumbnail_match
```

선택은 hard gate 통과 → 지배당하는 후보 제거 → 구조 모순 최소 → source-backed block 최대 → component
완전성·안정 decode 범위 최대 → 가정/edit/skip 최소 → placement 손실 최소 → coefficient/YCC/RGB 불연속
최소 순으로 한다. thumbnail은 정책이 별도로 검증된 경우에만 마지막 tie-breaker다. 유사한 resume 위치만
beam을 점유하지 않도록 header 종류와 source bit 구간별 diversity도 유지한다.

각 evidence의 단위, 정규화, 결측 처리와 dominance 비교는 `policy_version`에 귀속한다. record에는 정규화
점수만이 아니라 가능한 원시 측정값도 남긴다. 완전 동률은 `candidate_fingerprint` 같은 안정된 key로
결정해 worker 완료 순서가 선택을 바꾸지 않게 한다.

## 11. Artifact schema 호환 정책

schema, engine, policy 버전은 서로 분리한다.

아래 JSON은 필드의 역할만 보여주는 비규범적 예시다. 문자열을 실제 초기 버전으로 확정한 것이 아니며,
schema 값은 T-0003/T-0004, engine과 policy 값은 해당 artifact writer와 후보 평가를 도입하는 Task에서
각각 확정한다.

```json
{
  "schema": "media-recovery.result",
  "schema_version": "<major>.<minor>",
  "engine_version": "<engine-version>",
  "policy_version": "<policy-version>"
}
```

### 버전 규칙

- minor: 기존 의미를 유지하는 선택 필드 또는 선택 artifact 추가
- major: 필드 제거·이름 변경·의미 변경, array shape/dtype 의미 변경, enum 기존 의미 변경, 참조 규칙 변경
- on-disk 계약이 바뀌지 않는 문서·writer bug 수정: schema 버전 유지

### Reader 규칙

- 같은 major의 같거나 낮은 minor는 읽는다.
- 같은 major의 더 높은 minor는 알 수 없는 선택 필드를 무시할 수 있다.
- 알 수 없는 `required_features`가 있으면 거부한다.
- 더 높은 major는 자동 추정하지 않고 명시적으로 거부한다.
- 알 수 없는 enum을 기존 값으로 임의 치환하지 않는다.

JSON Schema는 `schemas/`에서 코드와 함께 배포한다. 완료된 run은 수정하지 않고 migration도 기존 run을
덮어쓰지 않는다. migration은 원본 run ID, artifact hash와 migration 도구 버전을 기록한 새 파생 bundle로
만든다. 알고리즘 변경으로 결과가 바뀌는 것은 schema migration이 아니라 새로운 reconstruction run이다.

## 12. 문서 체계와 기존 문서 이관

T-0002에서 다음 지속 문서 골격을 만들었다. 각 문서는 Current와 Planned를 명시적으로 구분하며 아직
구현되지 않은 목표를 현재 계약처럼 표현하지 않는다.

```text
docs/
├── README.md
├── design.md
├── artifacts.md
├── evaluation.md
├── status.md
├── format-notes.md
└── tasks/
```

- `design.md`: Current 파이프라인·모듈 책임·핵심 불변조건과 Planned 설계 방향
- `artifacts.md`: Current 출력·분류·provenance 한계와 Planned case/run·forensic artifact 계약
- `evaluation.md`: Current 자동 검증·역사 기준선·평가 원칙과 Planned corpus·전수 검증 조건
- `status.md`: Current 검증 범위·알려진 한계와 Planned 우선 작업
- `format-notes.md`: JPEG·AVI의 객관적인 형식 사실
- `tasks/`: 작업 목표·범위·검증·결과

새 ADR은 만들지 않는다. T-0002는 새 문서 골격을 만들고 현재 문서의 중복을 줄이는 작업을 시작하지만,
기존 ADR/spec을 그 Task에서 반드시 모두 제거하는 것을 목표로 하지 않는다.

T-0002는 `architecture.md`의 구조·불변조건을 `design.md`로, `current-state.md`의 검증 범위·상세 수치·
실험 맥락을 `status.md`와 `evaluation.md`로 완전히 이관했다. inbound link와 code 참조가 남지 않은 것을
검증한 뒤 두 legacy 파일을 삭제했다. 세부 Current 계약을 완전히 흡수하지 않은 두 spec과 결정 당시
배경·대안을 보존하는 12개 ADR은 유지한다.

- 현재 계약을 새 `design.md`·`artifacts.md`가 완전히 흡수하기 전에는 해당 spec을 유지한다.
- 아직 구현되지 않은 내용은 문서마다 `Planned`로 표시하고 `Current` 동작과 섞지 않는다.
- `transition-plan.md`는 최소 T-0010 완료까지 목표 방향의 정본으로 유지한다.
- ADR/spec을 제거하기 전 inbound link, 코드 참조와 아래 이관표의 누락을 검사한다.
- 역사 자료를 삭제하는 것 자체를 완료 조건으로 삼지 않는다.

### ADR 이관표

| 기존 문서 | 목적지 | 보존할 핵심 |
|---|---|---|
| ADR 0001 | `design.md` | 직접 baseline decoder와 bit resync가 필요한 이유 |
| ADR 0002 | `design.md`, `format-notes.md` | 첫 EOI를 확정 경계로 쓰지 않는 원칙과 marker 사실 |
| ADR 0003 | `evaluation.md` | 출력 동일성을 지키는 성능 측정 원칙 |
| ADR 0004 | `design.md` | DC reset은 추론이며 색 offset을 만들 수 있음 |
| ADR 0005 | `evaluation.md` | 고정 수락 임계가 짧은 파일·꼬리에서 실패한 사실 |
| ADR 0006 | `design.md`, `evaluation.md` | header hypothesis 재료와 구조 게이트 |
| ADR 0007 | `design.md` | 무효 segment 길이가 뒤 객체를 덮지 못하게 함 |
| ADR 0008 | `design.md` | JPEG와 AVI 사이 object boundary 조정 |
| ADR 0009 | `design.md` | 손상 시작은 anchor와 후속 구조를 함께 검증 |
| ADR 0010 | `design.md`, `format-notes.md` | AVI 경계 정책과 RIFF/OpenDML 사실 |
| ADR 0011 | `design.md`, `evaluation.md` | gap, owner 단조성, 배치 손실 제한 |
| ADR 0012 | `design.md`, `evaluation.md` | thumbnail-guided enhancement와 rollback |

### Specs와 기타 문서 이관표

| 기존 문서/내용 | 목적지 |
|---|---|
| carve/reconstruct CLI 사용법 | 루트 `README.md` |
| 객체 탐색·복구 흐름·불변조건 | `design.md` |
| 출력 구조·상태·artifact | `artifacts.md` |
| 후보 평가·기준선·비교 절차 | `evaluation.md` |
| 알려진 한계·후속 우선순위 | `status.md` |
| JPEG·AVI 객관적 사실 | `format-notes.md` |
| 재현 가능한 edge case | 테스트 |
| `architecture.md` (이관 후 삭제) | `design.md` |
| `current-state.md` (이관 후 삭제) | `status.md`, `evaluation.md` |

정확한 과거 임계값과 실험 일지 전체를 새 설계 문서에 복제하지 않는다. 현재 임계값의 정본은 코드와
테스트이며, 후속 구현을 실제로 제약하는 이유만 `evaluation.md`에 남긴다. 과거 ADR을 가짜 완료 Task로
변환하지 않는다.

### Legacy 기준 자료 inventory

외부로 이동한 자료의 절대 경로는 영구 문서 계약으로 만들지 않지만, 기준선과 원자료의 연결은 잃지
않는다. T-0003에서 접근 가능한 legacy 자료마다 다음 inventory를 만든다.

- 논리 dataset ID와 설명
- 입력 이미지 전체 SHA-256과 크기
- JPEG/AVI와 보고서 record 수
- 기준 `report.csv`·`report_thumbref.csv` SHA-256
- 생성한 git commit과 dirty 여부
- 주요 CLI 옵션과 실행 날짜
- 고정 손상 표본·정상 가드 object ID
- 현재 원자료 접근 가능 여부

자료에 접근할 수 없어 확인하지 못한 값은 과거 문서에서 추측해 채우지 않고 `unverified`로 표시한다.

## 13. 검증 전략

243개 테스트 통과는 중요한 회귀 기준이지만 복구 정확도의 증명은 아니다.

1. 단위 테스트: 파싱, boundary, decoder state, source span, placement, schema
2. 통제 손상 코퍼스: 정상 JPEG에 substitution/deletion/insertion, bit shift, marker 길이·DHT/DQT/SOF/SOS
   손상, truncation과 가짜 EOI를 재현 가능하게 주입
3. 실제 손상 고정 표본: 구조 타당성, 자동 지표와 기술적 육안 판정
4. 정상 가드: 자동 수정과 회귀 0
5. 전수 검증: 고정 표본과 정상 가드를 통과하고 실제 필요성이 있을 때만 수행

통제 손상에서는 object precision/recall, 경계 오차, coefficient 및 component validity, source span,
placement/gap, top-1/top-K 정확도를 측정한다. 정답 원본이 없는 실제 표본에서는 자동 지표 하나만으로
성공을 단정하지 않는다.

개인 사진의 장면·인물·위치는 문서화하지 않고 파일 식별자와 기술적 결함만 기록한다.

## 14. Task 로드맵

| Task | 목표 | 핵심 비범위 |
|---|---|---|
| T-0001 | 프로젝트 정체성, `src/media_recovery`, 통합 CLI | 알고리즘·출력 계약 변경 |
| T-0002 | Current/Planned 문서 골격과 기존 지식의 점진적 이관 | 복구 로직 변경·성급한 역사 문서 삭제 |
| T-0003 | `work/`, stage run lineage·lifecycle과 JSONL artifact 계약 | 대규모 배열 모델 구현 |
| T-0004 | 포렌식 도메인 모델과 NPZ schema | N-best 탐색 |
| T-0005 | 기존 엔진의 동작 보존 책임 분리 | 결과 개선 |
| T-0006 | 현재 single-best 결과의 포렌식 artifact 출력 | N-best 선택 |
| T-0007 | object boundary와 header N-best | entropy beam 확장 |
| T-0008 | entropy beam search와 component validity | thumbnail 판단 사용 |
| T-0009 | 반복 placement와 evidence 평가 | AI enhancement |
| T-0010 | preview와 thumbnail enhancement 분리 | enhancement를 source로 주장 |

Task 번호는 의존관계를 설명하며 구현 중 발견만으로 범위를 자동 확장하지 않는다. T-0001과 T-0002는
완료됐으며 다음 계획 작업은 T-0003이다. 새 활성 Task 문서는 실제 작업을 시작할 때 만든다.

## 15. 전환 중 지켜야 할 금지 사항

- 패키지 이전과 복구 알고리즘 변경을 한 Task에 섞지 않는다.
- 12개 파이프라인 단계를 기계적으로 12개 파일로 나누지 않는다.
- RGB가 좋아 보인다는 이유만으로 source 복구 성공을 판정하지 않는다.
- confidence 하나만 남기고 evidence와 반증을 버리지 않는다.
- 다른 사진의 개인 metadata와 geometry를 donor로 복사하지 않는다.
- AI 또는 thumbnail enhancement 결과를 reconstruction 반복에 넣지 않는다.
- 기존 `*.img`, 외부 legacy 출력과 실험 결과를 수정·삭제하지 않는다.
- 비용 큰 전수 실행부터 시작하지 않는다.
- 이관 전 ADR/spec을 일괄 삭제하지 않는다.

## 16. 현재 인수인계 상태

- 2026-08-13에 저장소 경로가 `media-recovery-tool`로 바뀌었고 T-0001 package/CLI 이전을 완료했다.
- 설치 대상은 `src/media_recovery`, 배포 이름은 `media-recovery-tool` 0.1.0, 단일 CLI는
  `media-recovery`다.
- 기존 `carver` 호환 계층, 루트 CLI wrapper와 `recover` alias는 두지 않았다.
- 이전 전 243개와 이전 후 248개 테스트가 통과했고 합성 fixture의 정규화 snapshot이 일치했다.
- `output*`과 `shift_experiments`는 사용자가 저장소 밖으로 이동한 상태를 유지한다.
- `usb.img`는 Git 비추적 원본으로 보존하며 T-0001에서는 전수 처리하지 않았다.
- T-0002에서 Current/Planned 지속 문서 골격을 만들고 기존 architecture, current-state, spec과 ADR을
  점진적으로 연결했다. 다음 계획 작업은 T-0003의 `work/`·run·JSONL artifact 계약과 legacy inventory다.
