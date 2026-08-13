# 산출물과 provenance

이 문서는 명령이 만드는 파일, 결과 분류와 provenance 계약의 지속 정본이다. CLI 인자와 edge case의 세부
Current 계약은 [carve spec](specs/0001-carve.md)과 [reconstruct spec](specs/0002-recover.md)을 함께 본다.

`Current`는 현재 구현이 실제로 쓰는 디렉터리와 CSV다. `Planned`는 [전환 계획](transition-plan.md)의
case/run·forensic artifact 목표이며 아직 구현되지 않았다. Planned의 예시 경로, record와 식별자는 현재
CLI 출력이나 확정 schema가 아니다.

## Current

### 공통 성격

- 현재 실행은 case나 run을 등록하지 않고 사용자가 지정한 출력 디렉터리에 직접 쓴다.
- JSON, JSONL, NPZ, schema version, artifact manifest와 parent run lineage는 없다.
- 카빙 파일명은 디스크 이미지 안의 시작 offset을 보존하지만 입력 image hash, 실행 옵션과 git 상태를
  산출물 자체에 기록하지 않는다.
- reconstruct와 enhance는 CSV 보고서를 쓰지만 선택하지 않은 후보, source bit span, virtual edit와
  component validity를 canonical record로 보존하지 않는다.
- 같은 출력 디렉터리를 재사용할 수 있다. 같은 파일명은 교체되지만 이번 실행에서 사라진 이전 파일을
  자동 삭제하지 않으므로 재현 기준선은 빈 출력 디렉터리에서 만든다.

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
이것이 Planned case/run provenance가 필요한 이유다.

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

- 입력 image SHA-256과 크기, tool revision, dirty patch, 옵션과 환경을 run metadata로 묶지 않는다.
- 같은 offset의 객체가 여러 boundary/header 가설을 가질 수 있다는 구조가 없다.
- 재인코딩 JPEG와 CSV가 중심이며 quantized coefficient, block/component validity, gap owner와 source span을
  보존하지 않는다.
- worker 완료 순서와 CSV row ordering을 canonical deterministic record로 규정하지 않는다.
- 완료 출력의 불변 lifecycle, resume 조건과 parent-child lineage가 없다.
- preview, forensic result와 enhancement가 별도 provenance 계층으로 정규화되어 있지 않다.

## Planned — 아직 구현되지 않음

이 절은 목표 계약의 방향만 보존한다. T-0003은 case/run과 JSONL, T-0004는 대규모 array와 forensic domain
schema를 테스트와 함께 확정한다. 그전까지 아래 이름과 예시는 현재 writer/reader가 지원하는 형식이 아니다.

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

### case와 stage run

기본 작업 공간은 저장소에서 실행할 경우 `./work`가 되는 `--work-root` 아래를 목표로 한다. 원본 image는
기본적으로 복사하지 않고 절대 경로, 크기와 SHA-256으로 case에 등록하며, 사용자가 명시적으로 import할
때만 case 아래 source 보관을 허용할 계획이다.

```text
work/
├── cache/
├── tmp/
└── cases/<case-id>/
    ├── case.json
    └── runs/
        ├── <discovery-run-id>/
        ├── <reconstruction-run-id>/
        ├── <rendering-run-id>/
        └── <enhancement-run-id>/
```

각 stage run은 `stage`와 `parent_run_ids`를 가지고 완료 run을 덮어쓰지 않는 방향이다. lifecycle은
`created → running → completed`와 `interrupted`·`error`를 구분하고, resume는 source hash, code·policy·
schema와 옵션이 모두 같고 해당 stage가 지원할 때만 허용할 계획이다. 구체적인 디렉터리, 완료 marker와
원자적 확정 절차는 T-0003에서 확정한다.

### Planned canonical record

초기 방향은 다음과 같다.

- `case.json`, `run.json`: 단일 metadata JSON
- `objects.jsonl`, `results.jsonl`, `candidates.jsonl`: record 단위 streaming JSONL
- coefficient와 validity: object/candidate별 압축 NPZ
- CSV와 preview: 사람이 보는 파생 산출물, canonical record 아님

JSONL UTF-8/LF, non-finite number 금지, coordinator의 결정적 ordering과 임시 파일 후 원자적 교체,
NPZ `allow_pickle=False`와 고정 dtype/byte order가 목표 불변조건이다. 실제 schema version, required
features, array 이름·shape·dtype과 enum 조합은 구현 Task 전까지 Planned다.

### Planned 식별과 재현 정보

전환 계획은 source hash 기반 case ID, UTC 시각과 random suffix의 run ID, media type과 disk absolute
offset 기반 object ID, run 내부 candidate ordinal을 제안한다. ID는 재현 정보를 압축하지 않으며 run
metadata에 다음을 별도 기록하는 방향이다.

- stage와 parent run
- 전체 source SHA-256과 크기
- git commit, dirty 여부와 실제 patch artifact
- tool·engine·policy·schema version
- 옵션, Task ID, 시간, 환경과 random seed
- artifact hash와 candidate fingerprint

정확한 문자열 형식과 충돌 규칙은 T-0003/T-0004에서 확정하기 전까지 공개 Current 계약이 아니다.

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
  현재 case ID로 간주하지 않는다. 접근 가능한 legacy inventory와 `work/` 정책은 T-0003에서 검토한다.
