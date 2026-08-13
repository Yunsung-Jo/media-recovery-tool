# 산출물과 provenance

이 문서는 명령이 만드는 파일, 결과 분류와 provenance 계약의 지속 정본이다. CLI 인자와 edge case의 세부
Current 계약은 [carve spec](specs/0001-carve.md)과 [reconstruct spec](specs/0002-recover.md)을 함께 본다.

`Current`는 현재 구현이 실제로 쓰는 디렉터리·CSV와 T-0003에서 추가한 격리된 case/run 기반이다.
`Planned`는 [전환 계획](transition-plan.md)의 forensic artifact 목표이며 아직 구현되지 않았다. case/run
Python API는 구현됐지만 기존 CLI에 자동 연결되지 않았으므로 둘의 출력 계약을 구분한다.

## Current

### 공통 성격

- 현재 `carve`, `reconstruct`, `enhance` 실행은 case나 run을 자동 등록하지 않고 사용자가 지정한 출력
  디렉터리에 직접 쓴다.
- `src/media_recovery/artifacts/`에 case/run, strict JSON/JSONL과 completion seal 기반이 있지만 기존 명령의
  output을 감싸거나 migration하지 않는다.
- NPZ, forensic object/result/candidate record와 현재 CLI output의 artifact manifest는 없다.
- 카빙 파일명은 디스크 이미지 안의 시작 offset을 보존하지만 입력 image hash, 실행 옵션과 git 상태를
  산출물 자체에 기록하지 않는다.
- reconstruct와 enhance는 CSV 보고서를 쓰지만 선택하지 않은 후보, source bit span, virtual edit와
  component validity를 canonical record로 보존하지 않는다.
- 같은 출력 디렉터리를 재사용할 수 있다. 같은 파일명은 교체되지만 이번 실행에서 사라진 이전 파일을
  자동 삭제하지 않으므로 재현 기준선은 빈 출력 디렉터리에서 만든다.

### `work/`, case와 run 기반

[`artifacts`](../src/media_recovery/artifacts/) package는 후속 stage가 사용할 내부 persistence API다. 기본
work root는 호출 시점 현재 디렉터리의 `./work`이며 명시적인 경로로 재정의할 수 있다. 등록할 source는
전체 SHA-256을 계산하지만 기본적으로 복사하지 않는다.

```text
work/
├── cache/
├── tmp/
└── cases/
    └── <case-id>/
        ├── case.json
        └── runs/
            └── <run-id>/
                ├── run.json
                ├── completed.json       # completed run에만 존재하는 seal
                ├── provenance/
                │   └── dirty.patch      # dirty=true인 경우 필수
                └── <stage artifact>     # caller가 running 동안 기록
```

정식 fixture나 문서는 `work/`에 두지 않고 저장소 `/work/` 전체를 Git에서 제외한다. case와 run, `cache/`와
`tmp/`를 자동 삭제하는 API는 제공하지 않는다.

#### Schema와 식별자

| record | schema | version | 배포 schema |
|---|---|---|---|
| `case.json` | `media-recovery.case` | `1.0` | [`media-recovery.case-1.0.schema.json`](../schemas/media-recovery.case-1.0.schema.json) |
| `run.json` | `media-recovery.run` | `1.0` | [`media-recovery.run-1.0.schema.json`](../schemas/media-recovery.run-1.0.schema.json) |
| `completed.json` | `media-recovery.run-completion` | `1.0` | [`media-recovery.run-completion-1.0.schema.json`](../schemas/media-recovery.run-completion-1.0.schema.json) |

`case.json`은 `case_id`, label, 생성 시각과 source의 platform-native 절대 경로·전체 SHA-256·byte 크기를
기록한다. case ID는 `case-<SHA-256 앞 20자리>`다. 같은 ID 디렉터리가 존재하면 전체 hash와 크기를 읽어
같은 source이면 기존 case를 반환하고 전체 hash가 다르면 prefix 충돌로 거부한다. 기존 metadata는 자동
갱신하거나 덮어쓰지 않는다.

run ID는 `run-<UTC YYYYMMDDThhmmssZ>-<6자리 소문자 base32>`다. 이미 같은 디렉터리가 있으면 random
suffix를 다시 생성한다. `run.json`은 다음을 기록한다.

- `stage`: `discovery`, `reconstruction`, `rendering`, `enhancement`
- 같은 case의 `parent_run_ids`
- case에서 복사한 source SHA-256과 크기
- tool·engine·policy·artifact schema version, non-empty environment object, options, random seed와 Task ID
- 생성·시작·종료 시각, status와 attempt별 outcome
- git commit·dirty 여부와 dirty일 때 실제 `provenance/dirty.patch`의 hash·크기

discovery는 부모가 없고 그 밖의 stage는 최소 한 부모가 필요하다. reconstruction은 completed discovery,
rendering은 completed reconstruction, enhancement는 completed reconstruction 또는 rendering만 부모로
받는다. 모든 부모는 같은 case 아래에서 completion seal 검증을 통과해야 한다.

reader는 같은 schema major의 minor version을 읽고 알 수 없는 선택 필드는 보존한다. 더 높은 major,
알 수 없는 `required_features`와 enum은 거부한다. schema field 제거·이름/의미·참조 규칙 변경은 major,
기존 의미를 유지하는 선택 field 추가는 minor 변경이다.

배포 run schema는 stage별 parent 존재 조건, lifecycle status와 timestamp·attempt 조합, attempt outcome과
종료 시각의 동시 설정, dirty flag와 patch 존재 관계를 검증한다. attempt 배열에서 현재 attempt가 마지막인지,
같은 case와 parent stage인지, completion file hash가 맞는지처럼 순서나 외부 파일을 읽어야 하는 관계는 Python
reader가 추가로 검증한다.

#### Lifecycle, resume와 completion seal

기본 lifecycle은 `created → running → completed`다. running은 `interrupted` 또는 `error`로 종료할 수 있다.
`created` run을 시작할 때도 등록 source의 SHA-256과 크기를 다시 검증하며, 일반 상태 전이는 terminal run을
다시 열지 않는다. interrupted/error resume는 caller가 해당 stage의 지원을 명시하고 다음 값이 모두 같을
때만 같은 run ID를 `running`으로 되돌린다.

- 등록 source를 다시 계산한 SHA-256과 크기
- tool·engine·policy·artifact schema version과 environment
- canonical JSON으로 비교한 options

resume attempt는 이전 terminal attempt를 지우지 않고 `attempts`에 추가한다. completed run은 resume할 수
없다.

완료할 때 coordinator는 prospective completed `run.json`과 run 안의 모든 파일 경로·크기·SHA-256을
정렬해 `completed.json` seal을 만든다. 두 파일의 staging을 하나의 정리 범위에서 마친 뒤 seal을 먼저 원자
교체하고 completed `run.json`을 마지막에 원자 교체한다. staging 실패는 임시 파일을 남기지 않아 재시도할
수 있고, 두 번째 교체 실패는 completed가 아니라 marker/status 불일치로 읽힌다. reader는 다음 조건이 모두
맞을 때만 completed로 반환한다.

- `run.json` status와 마지막 attempt가 모두 `completed`
- `completed.json`의 run ID와 종료 시각이 `run.json`과 일치
- seal의 파일 집합·크기·SHA-256이 실제 run과 일치

따라서 API는 completed run에 쓰기를 거부하고, 외부에서 기존 파일을 수정·삭제하거나 파일을 추가해도 다음
read에서 seal 불일치로 거부한다. 이는 같은 filesystem에서 일반 쓰기 실패에 대한 atomic visibility를
보장하지만 directory fsync를 포함한 전원 장애 내구성까지 보장하지 않는다.

#### Strict JSON과 canonical JSONL

JSON과 JSONL은 UTF-8로 쓰며 JSONL record마다 LF를 사용한다. writer와 reader는 `NaN`·`Infinity`, 중복
object key, 비 UTF-8, CRLF, 빈 line과 마지막 LF가 없는 JSONL을 거부한다. key를 정렬한 compact JSON으로
직렬화한다.

canonical JSONL API는 records를 모두 수집한 coordinator가 caller의 stable key와 canonical record byte를
tie-break로 정렬한 뒤 같은 디렉터리의 임시 파일에 쓰고 `os.replace`로 확정한다. sort key는 null, boolean,
integer, finite float, string과 이들의 sequence만 허용해 전순서를 보장한다. record 수집·key 검증·직렬화·
쓰기·replace 실패 시 부분 destination을 노출하지 않고 기존 destination이 있으면 보존한다. worker용
공유-file append API는 제공하지 않는다. 어떤 stage record를 `objects.jsonl`, `results.jsonl`,
`candidates.jsonl`에 쓸지와 field schema는 T-0004 이후 범위다.

### `media-recovery carve`

```text
<output>/
├── jpeg/
├── avi/
├── jpeg_thumbnails/    # --save-thumbnails를 사용할 때만 생성
└── errors.log
```

- 기본 출력 루트는 `output`이다.
- 최상위 JPEG와 AVI 이름은 `0x{시작 offset:08X}.jpg`와 `.avi`다.
- Exif APP1 내부 JPEG는 `--save-thumbnails`를 지정할 때만 `jpeg_thumbnails/`에 쓴다.
- `errors.log`는 항목별 추출 실패를 append하고 나머지 후보 처리를 계속한다.
- 파일 범위는 8 MiB 청크로 같은 디렉터리의 임시 파일에 쓴 뒤 `os.replace`로 확정한다. 일반 쓰기 예외에서
  부분 최종 파일을 노출하지 않지만 fsync 기반 전원 장애 내구성까지 보장하지 않는다.

파일명 offset만으로는 어떤 disk image, scanner 옵션 또는 code revision에서 나온 파일인지 확정할 수 없다.
case/run 기반은 이 정보를 기록할 수 있지만 기존 `carve`가 아직 연결되지 않은 것이 남은 provenance
간극이다.

### `media-recovery reconstruct`

실행을 시작하면 다음 여섯 action 디렉터리를 만든다. 입력 최상위에서 `Path.glob("*.jpg")`와 일치하는
파일만 처리하며 하위 디렉터리와 `*.jpeg`는 처리하지 않는다. 확장자 대소문자 일치는 파일 시스템에
따라 달라 Windows에서는 일반적으로 `.JPG`도 일치하고 대소문자를 구분하는 POSIX에서는 일치하지 않는다.
입력이 비어 있으면 디렉터리는 만들지만 `report.csv`는 만들지 않는다.

| action | 디렉터리 | 현재 저장 계약 |
|---|---|---|
| `RECOVERED` | `recovered/` | byte 편집, bit resync 또는 공간 보정을 적용한 재인코딩 JPEG |
| `HEADER_RECOVERED` | `header_recovered/` | 구조 gate를 통과한 header 재구성·복구 재인코딩 JPEG |
| `CLEAN` | `clean/` | 편집·resync가 없고 회색 판정 기준을 통과한 입력 byte |
| `FAILED` | `failed/` | 복구 연산을 채택하지 못하고 hole에서 멈춘 입력 byte |
| `SKIP_UNDECODABLE` | `skip_undecodable/` | decoder 구성과 header 재구성이 모두 실패한 입력 byte |
| `ERROR` | `error/` | worker 예외 시 가능한 범위에서 보존한 입력 byte |

`CLEAN`과 `RECOVERED` 같은 이름은 현재 구현의 단일 action label이다. 실제 무손상이나 source 원본 복구를
증명하는 정규화된 forensic 상태로 해석하지 않는다. Planned artifact는 이를 여러 직교 상태와 evidence로
분해한다.

`report.csv`는 입력별 한 행을 기록한다. 현재 필드는 다음 묶음으로 나뉜다.

| 묶음 | 필드 |
|---|---|
| 식별·분류 | `filename`, `action` |
| 표시·미복구 지표 | `gray_before`, `gray_after`, `undec_before`, `undec_after`, `undec_delta`, `worse` |
| 실행·entropy 연산 | `recover_sec`, `ops`, `sub`, `del`, `ins`, `resync`, `hole` |
| geometry | `mcus`, `image_size` |
| placement | `shifted`, `mcu_ins`, `mcu_drop`, `shift_margin`, `shift_reject`, `spatial_changed`, `row_global_passes`, `row_global_changes`, `row_local_cuts` |
| header | `header_fix` |

`sub`·`del`·`ins`는 entropy 작업 stream의 byte 연산이고 `mcu_ins`·`mcu_drop`은 최종 공간 배치 통계다.
`ops`에는 공간 배치를 포함하지 않는다. 필드별 계산과 빈 값 규칙은
[reconstruct spec](specs/0002-recover.md)이 세부 정본이다.

### `media-recovery enhance`

현재 enhance는 reconstruct 출력 트리 안의 모든 `*.jpg`를 상대 경로 그대로 별도 출력 트리에 쓴다.
보정이 채택되면 복구본의 양자화 테이블과 원본 sampling을 사용해 JPEG로 저장하고, 보정이 없거나 오류가
나면 가능한 범위에서 입력 byte를 복사한다.

출력 루트의 `report_thumbref.csv`는 다음 필드를 가진다.

```text
filename, status, reg_score, s, dy, framing,
shift_rows, shift_iters, max_units, color_rows,
band_std0, band_res, dmatch, secs
```

`status`는 `corrected`, `identity`, `rollback`, `skip_*`, `error` 계열이다. 이 CSV와 보정 JPEG는 현재
선택적 후처리 산출물이며 forensic source record가 아니다. thumbnail byte 자체를 생성 pixel로 복제하지
않지만, 현재 형식은 observed·decoded·generated provenance를 machine-readable하게 분리하지 않는다.

### Current 산출물의 한계

- case/run API는 입력 image SHA-256과 크기, tool·engine·policy·schema version, environment·dirty patch·
  옵션과 lineage를 기록하지만 현재 CLI는 아직 이 API를 호출하지 않는다.
- 같은 offset의 객체가 여러 boundary/header 가설을 가질 수 있다는 구조가 없다.
- 재인코딩 JPEG와 CSV가 중심이며 quantized coefficient, block/component validity, gap owner와 source span을
  보존하지 않는다.
- strict JSONL 기반은 worker 완료 순서와 무관한 ordering을 제공하지만 현재 CSV ordering은 canonical
  record로 규정하지 않는다.
- case/run lifecycle은 구현됐지만 현재 CLI 출력 디렉터리는 완료 run 불변성과 lineage로 보호되지 않는다.
- preview, forensic result와 enhancement가 별도 provenance 계층으로 정규화되어 있지 않다.

## Planned — 아직 구현되지 않음

이 절은 T-0003이 구현한 provenance 기반 위에 남은 목표 계약을 보존한다. T-0004는 대규모 array와
forensic domain schema를 테스트와 함께 확정한다. 아래 forensic record 이름과 field는 현재
writer/reader가 지원하는 schema가 아니다.

### provenance 계층

장기 artifact는 값의 출처 상태를 분리한다.

| Planned 상태 | 의미 |
|---|---|
| `observed` | 수정되지 않은 disk/object byte와 span |
| `decoded` | source bit에 대응해 실제 디코드한 coefficient/component |
| `inferred` | header, DC reset, placement처럼 근거에서 추론한 값 |
| `generated` | 중립 채움, gap 표시, 보간, thumbnail/AI enhancement 값 |

canonical coefficient는 DQT 적용 전 quantized DCT coefficient로 두고, DQT hypothesis와 RGB·preview는 별도
파생 참조로 표현할 계획이다. 정확한 block/coefficient validity와 span shape는 T-0004 전에는 확정된 Current
계약이 아니다.

### case/run과 현재 CLI 연결

case 등록과 stage run lifecycle은 내부 API로 구현됐지만 CLI interface와 기존 output import·migration
정책은 확정하지 않았다. 후속 Task는 기존 `carve`, `reconstruct`, `enhance` 계약을 깨지 않고 어느
시점부터 stage artifact를 work run에 쓰는지 별도로 정해야 한다. 명시적 source import와 공유용 case path
redaction도 아직 구현하지 않았다.

### Planned canonical record

T-0003의 JSON/JSONL encoding 위에 다음 stage record와 대규모 artifact를 추가할 계획이다.

- `objects.jsonl`, `results.jsonl`, `candidates.jsonl`: record 단위 streaming JSONL
- coefficient와 validity: object/candidate별 압축 NPZ
- CSV와 preview: 사람이 보는 파생 산출물, canonical record 아님

JSONL UTF-8/LF, non-finite number 금지, coordinator의 결정적 ordering과 임시 파일 후 원자적 교체는
Current 기반이다. `objects.jsonl`, `results.jsonl`, `candidates.jsonl`의 record schema와 NPZ
`allow_pickle=False`, 고정 dtype/byte order, array 이름·shape·dtype과 enum 조합은 구현 Task 전까지
Planned다.

### Planned 식별과 재현 정보

source hash 기반 case ID와 UTC 시각·random suffix run ID, run metadata의 source·version·options·seed·
dirty patch는 Current다. media type과 disk absolute offset 기반 object ID, run 내부 candidate ordinal과
candidate fingerprint는 아직 Planned다. ID는 재현 정보를 압축하지 않는다.

- 후속 domain record는 object ID와 candidate fingerprint를 정의한다.
- `environment`는 현재 non-empty strict JSON object이고 platform·Python·library 등 stage별 표준 key는 실제
  stage integration Task에서 확정한다.
- object/candidate artifact hash는 해당 record schema와 함께 확정한다.

### Planned 결과·preview·enhancement 분리

현재 여섯 action을 하나의 canonical 상태로 유지하지 않고 execution, support, decode extent, selection,
header basis와 artifact availability를 직교 필드로 나누는 방향이다. 사용자용 요약 label은 이 필드에서
파생한다. 정확한 enum과 허용 조합은 schema Task가 확정한다.

forensic bundle은 source span, hypothesis, coefficient·validity, edit, placement와 evidence를 보존한다.
preview는 이 bundle을 PNG 등으로 표시하는 파생물이고, enhancement는 preview에서 다시 파생된 generated
값이다. Planned `render`는 reconstruction을 다시 실행하지 않고 같은 artifact에 다른 표시 policy를
적용하는 명령이며 T-0010 전에는 구현되어 있지 않다.

## Legacy 문서 유지

- [carve spec](specs/0001-carve.md)과 [reconstruct spec](specs/0002-recover.md)은 Current CLI·출력의 세부
  정본이고 이 문서가 모든 edge case와 필드 의미를 완전히 대체하지 않으므로 유지한다.
- [ADR](adr/README.md)은 현재 사용법이 아니라 결정 근거의 역사 자료로 모두 유지한다.
- 과거 `output_c2`, `output_c3`, `shift_experiments`는 논리 dataset/run 이름이다. 외부 위치를 추측하거나
  현재 case ID로 간주하지 않는다. 접근성과 확인 수준은
  [Legacy 기준 자료 inventory](legacy-inventory.md)에 기록했다.
