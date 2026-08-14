---
id: T-0004
title: 포렌식 도메인 모델과 NPZ schema
status: completed
type: feature
depends_on: [T-0003]
---

# T-0004. 포렌식 도메인 모델과 NPZ schema

## 문제

T-0003은 source hash case, stage run lineage·lifecycle, strict JSON/JSONL과 completion seal을 구현했지만
`objects.jsonl`, `results.jsonl`, `candidates.jsonl`의 record와 coefficient·validity array 계약은 정의하지
않았다. 현재 reconstruction engine의 tuple·NumPy array는 source 좌표와 provenance를 충분히 표현하지
않고, 대규모 array를 재감사 가능한 형식으로 저장하거나 manifest와 대조하는 reader/writer도 없다.

## 목표

- object, hypothesis, candidate, result, decode segment, source span, virtual edit, placement와 provenance를
  표현하는 불변 Python domain model을 구현한다.
- object/result/candidate record와 coefficient manifest schema 이름을 각각
  `media-recovery.object`, `media-recovery.result`, `media-recovery.candidate`,
  `media-recovery.coefficient-manifest`, 초기 version `1.0`으로 확정한다.
- component별 DQT 적용 전 quantized coefficient, block/coefficient validity, source span 참조와 placement
  owner를 object/candidate별 압축 NPZ로 저장하고 검증한다.
- 배포 JSON Schema, Python reader/writer, manifest와 NPZ가 동일한 필수 field·enum·shape·dtype 계약을
  검증하게 한다.
- T-0003의 canonical JSONL, 같은 major reader 규칙, run 상대 경로 안전성, atomic replace와 completion
  seal 안에서 독립적으로 사용할 수 있게 한다.

## 범위

- `src/media_recovery/domain/`의 forensic enum·불변 dataclass, dict round-trip과 의미 검증
- `src/media_recovery/artifacts/`의 object/result/candidate JSONL reader/writer와 결정적 NPZ I/O
- 루트 `schemas/`의 Draft 2020-12 object/result/candidate/coefficient-manifest schema와 wheel 배포
- 합성 record·작은 NumPy array를 사용한 schema, round-trip, 변조, 결정성, atomicity, completion 회귀 test
- 실제 확정 계약에 대한 `docs/design.md`, `docs/artifacts.md`, `docs/status.md`, `docs/evaluation.md` 갱신
- 승인 방향과 Task 완료 상태만 반영하는 최소 transition/task 안내 갱신

### 식별자와 fingerprint

- object ID는 `<media-type>-<disk absolute start offset의 16자리 소문자 hex>`다. media type은 `jpeg`와
  `avi`, offset은 `0..2^64-1`이고 case/discovery run 안에서 유효하다.
- candidate ordinal은 `0..999`이며 ID는 `cand-000..cand-999`다. candidate ID는
  case/run/object 안에서만 유효하다.
- candidate fingerprint는 SHA-256 전체 64자리 소문자 hex다. 입력은
  `media-recovery.candidate-fingerprint` `1.0`, object ID, ID순으로 정규화한 hypothesis·source span·
  decode segment·virtual edit·placement를 포함한 strict canonical JSON UTF-8 bytes이며 마지막 LF도 hash에
  포함한다. ordinal/ID, 선택 점수, engine/policy version과 NPZ 물리 hash는 제외한다.

### 좌표와 provenance

- provenance enum은 `observed`, `decoded`, `inferred`, `generated`다. 한 assertion/value는 정확히 한
  provenance만 갖는다. observed `SourceSpan`, decoded `DecodeSegment`, inferred `VirtualEdit`·`Placement`의
  고정 provenance를 다른 값으로 바꿀 수 없다. hypothesis assertion은 각각 observed 또는 inferred다.
- source range는 half-open `[start,end)`다. 좌표 공간은 `disk_byte`, `object_raw_byte`,
  `raw_entropy_bit`, `destuffed_bit`, `virtual_work_bit`를 서로 다른 enum으로 둔다. MCU/component/source
  block/raster block/coefficient 위치는 별도 typed field와 NPZ 축으로 표현한다.
- observed `SourceSpan`의 정본은 수정되지 않은 disk byte와 object raw byte의 짝이다. optional raw entropy
  bit·destuffed bit mapping을 같은 span에 둘 수 있지만 virtual work 좌표는 source span이 아니다.
  불연속 범위는 순차 `span-000000...` record 여러 개로 보존하고 합쳐 쓰지 않는다.
- virtual edit는 편집 전 source 좌표 range, 편집 뒤 `virtual_work_bit` range, 종류와 non-empty assumption
  object를 기록한다. insertion은 source zero-length, deletion은 work zero-length를 허용한다.
- placement는 component와 source block index를 raster block row/column에 연결할 뿐 source 좌표나 span을
  변경하지 않는다.

### coefficient·validity·placement 배열

- component는 `y`, `cb`, `cr` 세 개이며 모든 NPZ에 세 component의 고정 array 이름을 모두 둔다.
- `coef_<c>`는 shape `(source_block_count,8,8)`, dtype little-endian `int32`이고 natural DCT `(v,u)` 순서의
  DQT 적용 전 quantized coefficient다. invalid coefficient의 저장 sentinel은 `0`이다.
- `coefficient_validity_<c>`는 같은 shape의 `uint8`: `0=missing`, `1=source_backed`다. source 위치가
  독립적으로 입증되지 않으면 decode 가능 여부와 관계없이 `0`이다.
- `block_validity_<c>`는 shape `(source_block_count,)`의 `uint8`: `0=missing`, `1=partial`,
  `2=complete`다. coefficient validity가 각각 0개, 1..63개, 64개인 상태와 정확히 일치해야 한다.
- `source_span_ref_range_<c>`는 shape `(source_block_count,8,8,2)`, dtype little-endian `int64`이며 마지막
  축은 component별 `source_span_refs_<c>`의 `(start,count)`다. missing coefficient는 `(-1,0)`,
  source-backed coefficient는 1개 이상의 순서 있는 span index를 참조한다. 참조 구간은 coefficient 순으로
  빈틈·중복 없이 canonical하게 이어지고 span index는 manifest의 normalized source span table 범위 안이다.
- `source_span_refs_<c>`는 shape `(reference_count,)`, dtype little-endian `int32`다. 한 coefficient의
  불연속 span을 여러 index로 보존하며 하나의 연속 범위로 합치지 않는다.
- `placement_owner_<c>`는 shape `(raster_block_rows,raster_block_columns)`, dtype little-endian `int32`다.
  `>=0`은 해당 component의 source block index, `-1`은 gap(소유 block 없음), `-2`는 overlap(둘 이상의
  placement가 같은 slot을 주장해 단일 owner 미확정)이다. 다른 음수 sentinel은 금지한다.

### NPZ와 manifest

- NPZ는 object 또는 candidate owner 하나에 귀속한다. manifest는 owner, normalized source span ID table,
  NPZ run-relative POSIX path, SHA-256, byte 크기와 이름순 array name·shape·dtype을 기록한다.
- writer와 reader는 고정 18개 array 외의 unknown/missing array, object dtype과 잘못된 kind/itemsize를
  거부한다. writer는 big-endian fixed-width integer를 canonical little-endian/C-contiguous array로
  변환하고 reader는 canonical byte order만 허용한다.
- reader는 먼저 path/hash/size/ZIP entry 집합을 검증하고 반드시 `numpy.load(..., allow_pickle=False)`로
  연다. manifest와 실제 array 집합·shape·dtype 및 coefficient/validity/span/owner 의미 불변조건을 모두
  대조한다.
- writer는 이름순 NPY member, 고정 1980-01-01 ZIP timestamp·권한·compression level과 NPY 2.0 header를
  사용한다. 같은 canonical array에서 byte가 같아야 하며 ZIP metadata도 test한다.
- writer는 destination과 같은 디렉터리의 staging file을 fsync한 뒤 `os.replace`한다. 검증·쓰기·replace
  실패는 기존 destination을 보존하고 staging file을 정리한다.
- 절대 경로, `..`, colon/NTFS alternate data stream, symlink를 통한 run 밖 경로와 manifest의 run 밖
  artifact 참조를 거부한다.

### forensic result 상태 조합

- top-level 직교 field는 `execution_status`, `support_status`, `decode_extent`, `selection_status`,
  `header_basis`, `artifact_status`다. enum은 전환 계획 8절의 확정 이름을 사용한다.
- `unsupported` completed 결과는 `not_attempted/not_applicable/none/unavailable`만 허용한다.
- 선택된 source/reconstruction candidate는 completed execution, complete/partial decode, non-null candidate
  reference와 complete/partial artifact를 요구한다. source candidate는 `header_basis=source`다.
- `no_supported_candidate`는 completed execution, none/partial decode, null candidate reference와
  partial/unavailable artifact만 허용한다. `not_applicable`은 unsupported 또는 미완료 execution에서만 쓴다.
- interrupted/error는 complete decode와 candidate selection을 허용하지 않고 artifact는
  partial/unavailable만 허용한다. partial decode는 non-`none` header를 요구한다.
- complete/partial decode는 non-`none` header, `not_attempted` decode는 `header_basis=none`을 요구한다.
  complete artifact는 selected candidate가 있을 때만 허용한다.
- JSON Schema가 표현하는 조합과 Python reader의 상태 조합 검증을 같은 parametrized test로 대조한다.

## 비범위

- 기존 `carve`, `reconstruct`, `enhance` CLI 변경 또는 case/run 연결
- 기존 reconstruction engine 책임 분리와 현재 single-best 실제 artifact 출력
- boundary/header N-best, entropy beam, 새로운 resync·placement·선택 알고리즘
- 복구율·영상 품질 개선, preview/render/enhancement 구현
- 기존 output migration/import/export, source path redaction
- 원본 `*.img` 전수 처리와 외부 legacy 자료 접근
- ADR/spec 삭제, commit, merge, push, stash와 branch 삭제

## 유지할 불변조건

- 기존 288개 자동 test와 현재 CLI·복구 결과 계약을 바꾸지 않는다.
- observed byte와 carved byte를 수정하지 않고 disk/object raw source 좌표를 정본으로 둔다.
- observed, decoded, inferred, generated를 한 값의 provenance로 혼합하거나 generated 값을 source-backed로
  계산하지 않는다.
- schema, engine, policy version은 독립 field다. 같은 major의 더 높은 minor는 읽되 알 수 없는
  `required_features`, 더 높은 major와 알 수 없는 enum은 거부한다.
- 완료 run은 수정하지 않고 T-0003 completion seal 검증을 우회하지 않는다.
- case/run과 완료 artifact를 자동 정리·덮어쓰지 않으며 `*.img`와 외부 legacy 자료를 접근하지 않는다.

## 작업 계획

1. domain enum·dataclass·ID/fingerprint·상태 조합 validator를 구현한다.
2. record/manifest Draft 2020-12 schema와 JSONL reader/writer를 구현한다.
3. 결정적 NPZ writer, strict reader와 manifest 대조를 구현한다.
4. domain/schema/NPZ/atomicity/completion 회귀 test를 작성하고 근접 test를 반복한다.
5. 실제 계약을 지속 문서 Current에 반영하고 전체 검증 뒤 Task를 완료 위치로 이동한다.

## 검증

- domain model과 JSON/JSONL round-trip, object/candidate ID·fingerprint 결정성
- 좌표 범위·불연속 span 보존, provenance와 상태 조합의 허용/거부
- coefficient/validity/component shape와 partial block·source proof 정합성
- dtype/byte order canonicalization, object/pickle/unknown/missing array 거부
- NPZ/manifest hash·크기·array 집합·shape·dtype 변조 탐지
- 입력 mapping 순서와 무관한 canonical JSONL/NPZ bytes, 고정 ZIP metadata
- staging/write/replace 실패 atomicity와 임시 파일 정리
- schema self-validation, writer output validation, same-major/required-feature 호환성
- T-0003 run에 record/NPZ를 쓴 뒤 completion seal과 함께 다시 읽기
- 근접 pytest, 전체 pytest, wheel build와 schema 포함, Markdown local link, `git diff --check`

## 결과

### 구현

- `src/media_recovery/domain/forensics.py`에 provenance·media/component·좌표·result enum과 불변
  `SourceSpan`, `Hypothesis`, `DecodeSegment`, `VirtualEdit`, `Placement`, `ObjectRecord`,
  `CandidateRecord`, `ResultRecord`, `CoefficientManifest` model을 구현했다.
- object ID `jpeg|avi-<16 hex>`, candidate ordinal `0..999`/`cand-000..999`, 구조적 canonical input 전체
  SHA-256 fingerprint와 dict round-trip·reference/state validator를 구현했다.
- `DecodeSegment`의 source span/edit ID collection은 생성 시 tuple snapshot으로 고정해 frozen domain과
  candidate fingerprint 입력이 caller의 mutable list 변경에 영향받지 않게 했다.
- `src/media_recovery/artifacts/forensics.py`에 discovery `objects.jsonl`, reconstruction
  `candidates.jsonl`·`results.jsonl`의 stage-aware canonical reader/writer를 구현했다. completed run read는
  T-0003 seal을 먼저 검증한다.
- record reader/writer는 observed disk span이 case source 크기 안에 있는지 검증한다. candidate의 첫
  source anchor는 object ID offset과 같아야 하며 candidate/result object ID는 부모 discovery object
  하나에 정확히 해소돼야 한다.
- 같은 record 안의 disk/object raw 및 optional raw entropy/destuffed source range 중복을 거부한다.
  `parent_object_id`는 같은 `objects.jsonl`에서 해소하고 입력 깊이에 무관한 반복형 parent graph 검증으로
  cycle을 거부한다.
- 세 component의 coefficient·coefficient validity·block validity·CSR 형태 source span reference·placement
  owner 총 18개 array를 object/candidate별 압축 NPZ로 기록한다. 고정 dtype/shape/byte order, component
  layout, partial block, gap `-1`, overlap `-2`와 record/owner 관계를 reader와 writer 양쪽에서 검증한다.
- 이름순 NPY 2.0 member, 1980 ZIP timestamp, 고정 Unix mode와 deflate level 9로 동일 canonical array의
  NPZ byte를 결정적으로 만들었다. object/pickle, unknown/missing array와 run 밖 path를 거부하고 같은
  디렉터리 staging·fsync·atomic replace 실패 시 기존 destination과 임시 파일 정리를 보존한다.
- manifest path의 `..`·`.`·빈 segment, backslash, colon/Windows drive·NTFS alternate data stream과 filesystem
  symlink를 거부한다. C0/DEL control character와 같은 record file의 owner 간 filesystem-normalized NPZ path 공유도 거부한다.
  owner는 runtime에서 `ArtifactOwner`인지 확인하며 object NPZ는 discovery, candidate NPZ는 reconstruction
  run에서만 읽고 쓴다.
- 모든 배포 schema와 Python domain/reader는 선행 0 없는 `major.minor` version만 허용한다. unknown
  component key는 raw enum 예외가 아니라 `ArtifactFormatError` 경계로 보고한다.
- reader는 schema의 `uniqueItems`와 같이 중복 intervention을 거부하고 각 NPZ member가 writer 계약과 같은
  NPY format version `2.0`인지 확인한다. higher-minor의 알 수 없는 선택 field는 승인된 호환 규칙에 따라
  typed forensic value에서 무시할 수 있다.
- 루트 `schemas/`에 object/result/candidate/coefficient-manifest와 공통 forensic definitions Draft 2020-12
  schema 5개를 추가했다. 기존 3개와 함께 wheel data에 8개가 포함된다.

### 확정한 reader/schema 경계

JSON Schema는 필수 field, enum, array 이름·rank·dtype와 result 상태 조합을 검증한다. 가능한 result enum
조합 전체에서 schema와 Python validator의 허용 집합이 같음을 test했다. 다음 관계는 외부 file이나 두 값
사이 계산이 필요하므로 Python reader가 추가로 검증한다.

- range `end >= start`, disk/object raw span 길이와 object/candidate ID 파생 관계
- normalized span/entity ordinal과 내부 reference, fingerprint 재계산
- component간 block/placement shape와 coefficient/block validity·span ref 의미
- manifest owner와 object/candidate 일치, NPZ path/hash/size/ZIP/array 실제 내용
- `results.jsonl` candidate count와 selected ID/fingerprint resolution
- case source 크기와 observed disk span, reconstruction parent의 object ID 해소, filesystem symlink 여부
- source span 좌표 중복, 깊이에 무관한 object parent 해소·cycle, record file 안 filesystem path 고유성
- NPY member version과 result intervention 중복

### 검증 결과

- 근접: `.venv\Scripts\python.exe -m pytest tests\test_forensic_artifacts.py -q` →
  `58 passed in 8.51s`
- T-0003 포함 근접: `.venv\Scripts\python.exe -m pytest tests\test_artifacts.py tests\test_forensic_artifacts.py -q`
  → `98 passed in 10.30s`
- 전체: `.venv\Scripts\python.exe -m pytest` → `346 passed in 22.44s`
- schema: 신규 포함 JSON Schema 8개 `Draft202012Validator.check_schema` 통과; 실제 object/candidate/result/
  manifest writer output validation 통과
- wheel: `.venv\Scripts\python.exe -m pip wheel . --no-deps --wheel-dir .tmp-wheel-t0004-review4` 격리 build 성공;
  새 Python module 2개와 schema JSON 8개 포함 확인 뒤 임시 wheel directory 삭제
- 문서: `rg --files -g '*.md'` 기반 Markdown 31개, local link 216개, broken 0
- Git: `git diff --check` 오류 0; 기존 CLI·engine·spec·ADR와 원본/legacy 자료 변경 0

비격리 wheel 첫 시도는 project `.venv`에 setuptools가 없어 실패했고, 격리 build 첫 시도는 sandbox가
build dependency network를 막아 실패했다. 허용된 격리 build에서 선언된 `setuptools>=68`로 성공했다.
T-0004/T-0003 결합 test와 전체 test를 동시에 실행한 한 시도는 두 pytest process가 공용 basetemp를
정리하며 Windows file lock setup error를 냈다. 해당 test temp만 검증 후 정리하고 전체 test를 단독으로
재실행해 346개 통과를 확인했다.

### 생략한 항목

- 기존 CLI와 case/run 연결, 현재 engine 값의 record 변환과 single-best 실제 출력은 구현하지 않았다.
- reconstruction engine 책임 분리, N-best·새 resync/placement/selection algorithm과 복구 품질 변경은 0이다.
- preview/render/enhancement, migration/import/export와 source path redaction은 구현하지 않았다.
- `*.img`와 외부 legacy 자료를 접근·전수 처리하지 않았고 ADR/spec을 삭제하지 않았다.
- commit, merge, push, stash와 branch 삭제를 수행하지 않았다.

## 지속 문서 반영

- `docs/design.md`: Current forensic domain·NPZ 책임, 데이터 흐름과 핵심 불변조건
- `docs/artifacts.md`: schema/ID/fingerprint, 좌표·provenance, result 상태, 18개 array와 reader/schema 경계
- `docs/status.md`, `docs/evaluation.md`: 58개 근접·346개 전체 test, wheel 검증과 남은 integration 한계
- `docs/transition-plan.md`, `docs/README.md`, `docs/tasks/README.md`, `AGENTS.md`: T-0004 완료와 T-0005 안내

구현되지 않은 engine integration과 N-best는 Planned로 유지했다.

## 후속 작업

- T-0005: 기존 reconstruction engine의 동작 보존 책임 분리
- T-0006: 현재 single-best 결과를 새 forensic artifact로 실제 출력
- T-0007~T-0009: boundary/header N-best, entropy beam, 반복 placement와 evidence 평가
- T-0010: artifact 기반 render와 enhancement provenance 분리
