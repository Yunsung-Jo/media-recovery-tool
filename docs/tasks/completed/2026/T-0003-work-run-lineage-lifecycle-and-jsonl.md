---
id: T-0003
title: work와 run lineage·lifecycle 및 JSONL 기반
status: completed
type: feature
depends_on: [T-0002]
---

# T-0003. work와 run lineage·lifecycle 및 JSONL 기반

## 문제

현재 `carve`, `reconstruct`, `enhance`는 사용자가 지정한 출력 디렉터리에 파일과 CSV를 직접 쓴다. 입력
image의 전체 hash, tool·engine·policy·schema·환경·옵션, 부모 실행과 완료 여부를 한 계약으로 묶지 않으므로 같은
이름의 결과를 어느 입력과 실행에서 만들었는지 자동으로 재구성하기 어렵다. 출력 디렉터리를 재사용할 수
있고 완료 결과의 불변성, 중단 실행의 안전한 resume 조건, worker 순서와 무관한 canonical record도 없다.

전환 계획은 `work/` 아래 source hash 기반 case와 stage별 run을 두도록 정했지만, 정확한 case/run schema,
reader/writer, lifecycle과 atomicity는 아직 구현되지 않았다. 이 기반 없이 T-0004 이후 forensic artifact를
추가하면 provenance와 호환성 규칙이 각 단계에 중복될 수 있다.

## 목표

- 현재 작업 디렉터리의 `./work`를 기본값으로 하고 호출자가 재정의할 수 있는 work root 계약을 구현한다.
- source 전체 SHA-256 기반 case 등록, 안정적인 case ID와 로컬 `case.json` metadata를 구현한다.
- stage별 run ID, 부모 lineage, lifecycle과 완료 run 불변성 검증을 구현한다.
- 호환되는 `interrupted`·`error` run에 한해서만 stage가 명시적으로 지원하는 resume 기반을 제공한다.
- non-finite 값을 거부하는 strict JSON과 UTF-8/LF deterministic JSONL reader/writer를 구현한다.
- canonical JSONL을 coordinator가 안정된 key로 정렬해 임시 파일에 쓴 뒤 원자적으로 확정하게 한다.
- case/run schema 이름과 version, 같은 major reader 호환 정책을 테스트와 문서로 확정한다.
- 접근 가능한 범위의 legacy 기준 자료 inventory를 만들고 확인할 수 없는 값은 `unverified`로 남긴다.
- `work/`와 inventory를 근거로 `/output*/`, `/shift_experiments*/` ignore 규칙을 각각 재검토한다.
- 실제 구현 결과를 `docs/artifacts.md`, `docs/design.md`, `docs/status.md`와 관련 안내에 반영한다.

## 범위

- `src/media_recovery/artifacts/`의 다음 응집된 내부 책임
  - strict JSON/JSONL encoding, decoding, deterministic ordering과 같은 디렉터리 내 atomic replace
  - work root 해석, source hashing과 case 등록·읽기
  - run 생성·읽기, parent stage 검증, lifecycle 전이, resume compatibility와 completion seal 검증
- `case.json`과 `run.json`의 초기 schema 및 version 호환 reader
- 완료 marker가 봉인한 `run.json`과 파일 inventory를 대조하는 completed run 불변성
- 임시 디렉터리와 작은 합성 source를 사용하는 unit test
- 접근 가능한 저장소 내부 문서·Git metadata만 근거로 한 legacy inventory
- `.gitignore`의 `/work/`, `/output*/`, `/shift_experiments*/` 판단
- Task와 지속 문서의 Current/Planned 경계 갱신

기존 CLI 형태는 아직 case/run 전환 명령과 기존 출력의 migration 정책이 확정되지 않았으므로 T-0003에서는
새 기반을 격리된 Python API로 제공한다. `carve`, `reconstruct`, `enhance`에 자동 연결하지 않는 선택은
기존 공개 CLI·output·action·CSV 계약을 보존하고 T-0004 이후 artifact 내용을 성급히 확정하지 않기
위함이다.

## 비범위

- JPEG/AVI 탐지·경계·복구 알고리즘 변경
- 기존 `carve`, `reconstruct`, `enhance` 인자, action, CSV와 출력 디렉터리 계약 변경
- coefficient·validity NPZ와 대규모 array 모델
- 포렌식 domain 전체와 object/result/candidate 세부 schema 또는 직교 결과 enum 확정
- 기존 복구 engine 책임 분리와 single-best forensic 결과 출력
- N-best boundary/header/resync/placement 구현
- `render` 명령과 preview/enhancement 분리
- dirty worktree를 자동 수집하는 Git 실행 통합. 다만 dirty provenance 입력을 허용할 때 실제 patch
  artifact를 함께 요구하고 보존하는 계약은 포함한다.
- ADR/spec 삭제 또는 과거 ADR의 Task 변환
- 외부 legacy 자료의 위치 탐색·추측·이동·수정·삭제
- `usb.img` hash 계산이나 전수 처리
- 개인 자료의 장면·인물·위치 기록
- `.mcp.json`, `.claude/settings.local.json` ignore 제거
- commit, merge, push, stash 또는 기존 branch 삭제

## 유지할 불변조건

- 기본 work root는 명령을 실행한 현재 디렉터리의 `./work`이며 저장소 `/work/` 전체를 Git에서 제외한다.
- 원본 image는 기본적으로 복사하지 않는다. case에는 source 절대 경로, 전체 SHA-256과 byte 크기를 기록한다.
- case ID는 `case-`와 source SHA-256 앞 20자리이며 같은 prefix가 존재하면 전체 hash를 비교한다. 전체 hash가
  다르면 거부하고 기존 metadata를 덮어쓰지 않는다.
- run ID는 `run-<UTC YYYYMMDDThhmmssZ>-<6자리 소문자 base32>`다. 경로 충돌 시 suffix를 다시 생성한다.
- stage는 `discovery`, `reconstruction`, `rendering`, `enhancement`이고 `parent_run_ids`를 가진다.
- discovery는 부모가 없고 reconstruction은 discovery, rendering은 reconstruction, enhancement는
  reconstruction 또는 rendering 부모만 허용한다. parent는 같은 case의 유효한 run이어야 한다.
- lifecycle은 `created → running → completed`를 기본으로 하고 running에서 `interrupted`·`error`로 종료할
  수 있다. terminal run을 일반 전이로 다시 열지 않는다.
- 새 run은 running 전 source hash를 다시 검증한다. interrupted/error resume는 source hash,
  tool·engine·policy·schema version, environment와 options가 모두 같고 호출 stage가 명시적으로 resume
  지원을 선언할 때만 같은 run ID에서 허용한다.
- completed run은 API를 통해 덮어쓰기·resume·파일 추가를 할 수 없고 completion seal과 실제 파일 집합·
  hash가 달라지면 completed로 읽지 않는다.
- 완료 marker와 `run.json` status가 모두 일치할 때만 completed로 읽는다.
- JSON/JSONL은 UTF-8, JSONL newline은 LF이며 `NaN`, `Infinity`, 중복 object key를 허용하지 않는다.
- canonical JSONL은 worker가 공유 파일에 직접 쓰는 interface를 제공하지 않는다. coordinator가 안정된
  sort key로 정렬해 임시 파일에 쓴 뒤 `os.replace`로 확정한다.
- 동일 records와 sort key의 canonical byte는 입력·worker 완료 순서와 무관하다.
- dirty provenance를 기록하면 diff hash만 허용하지 않고 실제 patch bytes를 run artifact로 보존한다.
- Windows와 POSIX의 절대 경로 표현은 `pathlib`와 현재 platform을 따르며 한쪽 구분자를 schema에 고정하지
  않는다.
- 아직 구현하지 않은 NPZ, forensic result, N-best와 render를 Current나 완료 결과로 표현하지 않는다.

## 작업 시작 기준선

- 시작 branch: `main`
- 시작 HEAD: `6ff808992f3730880f20f538c5c2a3b9c75a82a1`
- 원격 차이: `main...origin/main [ahead 4]`
- 시작 worktree: clean
- 작업 branch: `codex/t-0003-work-run-jsonl`
- 프로젝트와 `.venv`의 executable·prefix·설치 package 경로가 모두
  `C:\Users\Yunsung\Desktop\media-recovery-tool` 아래임을 확인
- 시작 전체 테스트: Python 3.12.13, pytest 9.1.1에서 `248 passed in 13.71s`

## 작업 계획

1. schema 이름·version과 JSON field를 작게 확정하고 strict codec과 version reader를 먼저 구현한다.
2. work root 해석, chunked SHA-256, case ID 충돌 검증과 atomic `case.json` 등록을 구현한다.
3. run spec, ID 생성, parent stage/case 검증과 atomic `run.json` 생성을 구현한다.
4. 허용 lifecycle 전이, 명시적인 resume compatibility, completion seal과 완료 artifact inventory 검증을
   구현한다.
5. coordinator 전용 canonical JSONL writer에 stable ordering, UTF-8/LF, non-finite 거부와 실패 시 기존
   destination 보존을 고정한다.
6. dirty provenance 입력 시 실제 patch artifact 보존과 hash 일치를 검증한다.
7. 작은 합성 source로 default/override root, collision, lineage, lifecycle, resume, atomicity와 schema
   reader 회귀 테스트를 작성한다.
8. 외부 위치를 탐색하지 않고 현재 저장소 문서와 접근 가능한 Git metadata에서 legacy inventory를 작성한다.
9. inventory와 `/work/` 구조를 근거로 두 legacy ignore 규칙을 각각 판단하고 보호 규칙을 유지한다.
10. 지속 문서의 구현 완료 범위와 남은 Planned 범위를 갱신한다.
11. 근접 테스트, 전체 테스트, Markdown link·code path, Git 추적·diff와 기존 CLI help/통합 테스트를
    검증하고 실제 결과를 기록한다.

## 검증

필수 자동 검증:

- default/override work root와 source 미복사
- case ID 안정성, 전체 hash 저장·재검증과 prefix 충돌 거부
- run ID format과 경로 충돌 suffix 재생성
- parent lineage와 same-case·stage 검증
- lifecycle 허용·금지 전이
- completed run 수정·resume·추가 artifact 거부와 seal 불일치 탐지
- 새 run start의 source 재검증과 interrupted/error resume의
  source/tool/engine/policy/schema/environment/options 일치 및 stage 지원 조건
- strict JSON의 non-finite·중복 key 거부
- JSONL UTF-8/LF와 입력 순서에 무관한 deterministic ordering
- writer 실패 시 부분 canonical destination 비노출과 기존 destination 보존
- 완료 marker와 `run.json` 상태 불일치 거부
- dirty patch provenance의 실제 patch 보존과 hash 검증
- schema/version reader와 실제 writer 일치
- 기존 248개 테스트 회귀 0

완료 전에는 다음도 확인한다.

- 모든 로컬 Markdown link와 주요 새 code path가 유효하다.
- Windows/POSIX path separator를 schema나 validation에 불필요하게 고정하지 않았다.
- `/work/`와 테스트 산출물이 Git 추적 대상이 아니다.
- 기존 CLI help와 합성 통합 test가 통과하며 output·action·CSV code 변경이 없다.
- completed atomic finalization과 immutable seal이 테스트로 고정됐다.
- Python·문서 변경이 이 Task 범위 안이고 ADR/spec 삭제가 0이다.
- `.mcp.json`, `.claude/settings.local.json` 보호 규칙이 유지된다.
- 비용 큰 원본 처리와 외부 자료 접근은 수행하지 않는다.

## 결과

2026-08-13에 `work/`·case/run persistence와 strict JSON/JSONL 기반을 구현했다. 기존 세 CLI에는 연결하지
않아 현재 인자·output·action·CSV 계약을 그대로 유지했다.

### 확정한 구조와 schema

```text
<work-root>/
├── cache/
├── tmp/
└── cases/<case-id>/
    ├── case.json
    └── runs/<run-id>/
        ├── run.json
        ├── completed.json          # completed run seal
        ├── provenance/dirty.patch  # dirty run에만 존재
        └── <caller stage artifact>
```

- 기본 work root는 호출 현재 디렉터리의 `./work`, override는 호출자가 준 경로의 platform-native 절대
  경로다. root 초기화는 `cache/`, `tmp/`, `cases/`만 만들고 기존 내용을 정리하지 않는다.
- source를 복사하지 않고 `case.json`에 절대 경로, 전체 SHA-256과 byte 크기를 기록한다.
- 확정 schema는 `media-recovery.case` 1.0, `media-recovery.run` 1.0,
  `media-recovery.run-completion` 1.0이다. JSON Schema 3개를 루트 `schemas/`에 두고 wheel의 `schemas/`
  data files에도 포함했다.
- reader는 같은 major의 minor를 읽고 알 수 없는 선택 field를 보존한다. 더 높은 major, 알 수 없는
  `required_features`와 enum은 거부한다. field 제거·이름/의미·참조 규칙 변경은 major, 기존 의미를
  유지하는 선택 field 추가는 minor 변경이다.
- `run.json`은 tool·engine·policy·artifact schema version을 분리하고 non-empty strict JSON
  `environment`를 필수로 기록한다. stage별 environment 표준 key는 stage integration에서 추가한다.

### ID와 lineage

- case ID: `case-<source SHA-256 앞 20 lowercase hex>`. 기존 prefix가 있으면 전체 hash와 크기를 비교한다.
  같은 source는 기존 case를 그대로 반환하고 다른 전체 hash면 `CaseConflictError`로 거부하며 metadata를
  덮어쓰지 않는다.
- run ID: `run-<UTC YYYYMMDDThhmmssZ>-<6 lowercase base32>`. 이미 같은 경로가 있으면 suffix를 최대
  128회 다시 생성한다.
- stage: `discovery`, `reconstruction`, `rendering`, `enhancement`. discovery는 부모가 없고 나머지는 최소
  한 부모가 필요하다. reconstruction←discovery, rendering←reconstruction,
  enhancement←reconstruction/rendering만 허용한다.
- 부모는 같은 case 안의 run ID로 해석하며 stage와 completion seal 검증을 모두 통과해야 한다. reader도
  lineage를 재검증하고 cycle을 거부한다.

### Lifecycle, resume와 atomicity

- lifecycle은 `created → running → completed`이며 running에서 `interrupted`·`error`로 끝낼 수 있다.
  새 run은 running 전 등록 source를 다시 hash한다. 시작·resume attempt는 `attempts`에 누적해 이전 실패를
  지우지 않는다.
- interrupted/error resume는 caller가 stage 지원을 명시하고, 등록 source를 다시 계산한 hash·크기,
  tool·engine·policy·artifact schema version, environment와 canonical options가 모두 같을 때만 허용한다.
  completed run은 resume할 수 없다.
- dirty run은 실제 non-empty patch bytes가 없으면 생성할 수 없다. patch를
  `provenance/dirty.patch`로 원자 저장하고 hash·크기를 `run.json`과 completion seal에서 대조한다.
- 완료 시 prospective completed `run.json`과 run의 모든 파일 경로·크기·SHA-256을 정렬해
  `completed.json`을 만든다. 두 파일 staging을 하나의 정리 범위에서 수행해 어느 staging이 실패해도 임시
  파일을 남기지 않는다. marker를 먼저, completed `run.json`을 마지막에 각각 원자 교체한다. 두 번째 교체가
  실패하면 marker/status 불일치인 미완료 run으로만 보이고 같은 finalization을 안전하게 재시도할 수 있다.
- completed reader는 status·마지막 attempt·marker의 ID/시각·전체 file set/hash/size가 모두 일치할 때만
  성공한다. API 쓰기·resume를 거부하고 외부 파일 수정·삭제·추가도 다음 read에서 탐지한다.
- strict JSON은 UTF-8, string object key와 finite number만 허용하고 중복 key를 거부한다. JSONL은 record별
  LF와 마지막 LF를 강제한다. coordinator writer는 null, boolean, integer, finite float, string과 sequence로
  제한한 전순서 stable key와 canonical record bytes로 tie-break한 뒤 임시 파일을 `os.replace`한다. 수집·
  key 검증·직렬화·쓰기·replace 실패 시 부분 destination을 노출하지 않고 기존 파일을 보존한다.
- 같은 records·version·policy·seed를 가진 두 run에서 record 입력 순서를 뒤집어도 canonical JSONL byte가
  같음을 test로 고정했다.

### Legacy inventory와 ignore 판단

[Legacy 기준 자료 inventory](../../../legacy-inventory.md)에 논리 dataset 6개를 기록했다.

- `verified`: 저장소의 `usb.img` 접근 가능 여부와 3,517,120,512 byte 크기, 외부 legacy output이 현재
  저장소에 없다는 상태
- `documented`: 기존 지속 문서에 남은 JPEG/AVI·report record 수, 실행 날짜·주요 옵션과 고정 object ID
- `unverified`: 3.5GB input SHA-256, 외부 `report.csv`·`report_thumbref.csv` hash, 생성 commit·dirty,
  문서에 없던 옵션·guard ID와 모든 외부 원자료 자체

`usb.img` 전체 hash나 전수 처리는 수행하지 않았다. 외부 자료 위치를 탐색·추측하지 않았고 이동·수정·
삭제하지 않았다.

- `/work/`: case와 완료 run을 포함한 기본 실행 데이터이므로 유지
- `/output*/`: 기존 `carve`가 여전히 `output`을 기본으로 쓰므로 유지
- `/shift_experiments*/`: 외부 legacy 자료를 migration하지 않았고 다시 놓일 때 우발 추적을 막기 위해 유지
- `.mcp.json`, `.claude/settings.local.json`: 보호 규칙 유지

`.gitignore` 자체는 변경하지 않았다.

### 실제 검증

- 시작 기준선:
  - `main`, HEAD `6ff808992f3730880f20f538c5c2a3b9c75a82a1`, `origin/main`보다 4 commit 앞,
    clean worktree
  - 프로젝트·`.venv` 실행 파일·prefix·editable package 경로 모두 현재 저장소 아래
  - `.venv\Scripts\python.exe -m pytest` → `248 passed in 13.71s`
- 새 근접 test:
  - `.venv\Scripts\python.exe -m pytest tests\test_artifacts.py -q` → `40 passed in 2.86s`
  - default/override root, case hash·충돌, run ID·lineage, lifecycle·resume, strict codec, ordering,
    source start 재검증, failure atomicity, completion staging 정리, dirty patch, JSON Schema validation 포함
- 최종 전체 test:
  - `.venv\Scripts\python.exe -m pytest` → `288 passed in 15.84s`
  - 기존 248개와 새 40개가 모두 통과
- schema와 packaging:
  - jsonschema 4.26.0 Draft 2020-12 validator로 schema 자체, writer output 3종과 reader-invalid run 조합을 test
  - project `.venv`에서 `pip wheel --no-deps` 격리 build 성공
  - wheel 안에 package code와 schema JSON 3개가 모두 있음을 확인
  - project `.venv`에 승인된 network 설치로 dev dependency와 editable package 설치 성공
- 문서·경로:
  - Markdown 30개, local link 205개, broken 0; local anchor link 0
  - 새 주요 code/schema path 8개, missing 0
- Git·호환성:
  - `git ls-files`에서 `work/`, `output*`, `shift_experiments*` 추적 파일 0
  - `git check-ignore -v --no-index`로 `/work/`, `/output*/`, `/shift_experiments*/`, `.mcp.json`,
    `.claude/settings.local.json` 규칙 모두 일치
  - 기존 CLI·discovery·format·reconstruction·enhancement source, 기존 test, spec, ADR와 `.gitignore`에
    `git diff --exit-code` 결과 차이 0
  - 기존 CLI help와 합성 carve→reconstruct→enhance integration은 전체 test 안에서 통과
  - `git diff --check` 오류 0

### 생략한 항목

- 기존 CLI에 case/run을 자동 연결하거나 새 CLI 명령·`--work-root` 인자를 추가하지 않았다. interface와
  migration 정책은 실제 stage artifact schema가 준비되는 후속 Task에서 정한다.
- source 명시적 import, 공유 export의 절대 경로 redaction과 자동 Git provenance 수집은 구현하지 않았다.
  dirty provenance를 caller가 제공할 때 실제 patch를 강제하는 기반만 구현했다.
- object/result/candidate field, forensic 직교 상태, source span, coefficient·validity NPZ, N-best와
  `render`는 확정하거나 구현하지 않았다.
- `usb.img` hash·전수 처리, 외부 legacy artifact hash·record 재검증과 실제 복구 품질 평가는 수행하지
  않았다.
- ADR/spec 삭제는 0이고 commit, merge, push, stash와 branch 삭제를 수행하지 않았다.

## 지속 문서 반영

- `docs/design.md`: Current artifact module 책임, case/run 데이터 흐름과 lifecycle 불변조건
- `docs/artifacts.md`: Current work/case/run 구조, schema·ID·JSONL·completion seal 계약
- `docs/status.md`: 구현·검증 범위, legacy inventory 상태와 다음 우선순위
- `docs/evaluation.md`: 40개 근접·288개 전체 test와 packaging 기준선
- `docs/legacy-inventory.md`: verified/documented/unverified legacy inventory와 ignore 판단
- `docs/README.md`, `docs/tasks/README.md`, `AGENTS.md`, `docs/transition-plan.md`: 완료 상태와 다음 Task 안내

## 후속 작업

- T-0004: forensic domain, object/result/candidate와 coefficient·validity NPZ schema
- T-0005 이후: 기존 engine 책임 분리와 실제 stage pipeline 연결
- T-0006 이후: current single-best와 N-best forensic artifact 출력
- T-0010: artifact 기반 render와 preview/enhancement provenance 분리
