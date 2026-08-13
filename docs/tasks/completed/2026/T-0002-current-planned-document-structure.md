---
id: T-0002
title: Current와 Planned 지속 문서 구조
status: completed
type: documentation
depends_on: [T-0001]
---

# T-0002. Current와 Planned 지속 문서 구조

## 문제

현재 구현의 구조와 동작 계약은 `architecture.md`와 `specs/`, 기준선·실험 결과·알려진 한계는
`current-state.md`, 결정 이유는 ADR에 나뉘어 있다. 같은 사실이 여러 문서에 반복되거나 현재 동작,
역사적 결과, 승인된 목표가 가까운 문맥에 섞여 있어 독자가 구현 완료 여부와 정본을 구분하기 어렵다.

전환 계획은 `design.md`, `artifacts.md`, `evaluation.md`, `status.md`를 지속 문서로 정했지만 아직 골격이
없다. 기존 문서를 성급히 제거하면 현재 CLI·출력·복구 계약과 결정 근거, 과거 실험의 역사적 맥락을 잃을
수 있으므로 점진적인 이관 규칙과 문서별 Current/Planned 경계를 먼저 세워야 한다.

## 목표

- 현재 구현과 승인된 목표를 각각 `Current`와 `Planned`로 명확히 구분하는 지속 문서 골격을 만든다.
- `docs/design.md`, `docs/artifacts.md`, `docs/evaluation.md`, `docs/status.md`의 책임과 정본 범위를 정한다.
- `architecture.md`, `current-state.md`, specs와 ADR의 지속 지식을 이관표에 따라 점진적으로 옮긴다.
- 현재 계약과 역사적 결정 근거를 잃지 않으면서 중복과 정본 경쟁을 줄인다.
- `docs/README.md`와 관련 문서의 안내·링크를 실제 문서 구조와 정본 우선순위에 맞춘다.
- 구현되지 않은 case/run, forensic artifact, N-best, render 등은 문서마다 명시적으로 `Planned`로 표시한다.

## 범위

- 다음 지속 문서 생성과 역할 정립
  - `docs/design.md`: Current 파이프라인·모듈 책임·핵심 불변조건과 Planned 설계 방향
  - `docs/artifacts.md`: Current 출력·분류·보고서 계약과 Planned case/run·forensic artifact 계약
  - `docs/evaluation.md`: Current 자동 검증·역사적 기준선·고정 표본 원칙과 Planned 평가 체계
  - `docs/status.md`: Current 검증 범위·알려진 한계와 Planned 우선 작업
- `docs/architecture.md`, `docs/current-state.md`, `docs/specs/`, `docs/adr/`의 내용·inbound link·코드 참조 조사
- 현재 코드·테스트와 원문으로 확인한 내용만 새 지속 문서에 이관
- 완전히 흡수하지 못했거나 현재 계약의 상세 정본인 기존 spec·ADR은 유지하고 새 지속 문서에서 연결
- `docs/README.md`, `AGENTS.md`, `docs/tasks/README.md`와 관련 문서의 역할 설명·탐색 링크 갱신
- 로컬 Markdown 링크, 주요 코드 경로, Current/Planned 표현과 문서 간 모순 검증
- 실제 이관 범위, 유지한 legacy 문서, 검증 명령·수치, 생략 항목과 후속 작업을 이 Task 결과에 기록

## 비범위

- Python 코드와 복구 알고리즘 변경
- CLI·출력·결과 분류 계약 변경
- case/run, JSONL, NPZ 또는 forensic artifact 구현
- 기존 ADR/spec의 일괄 삭제
- 과거 ADR을 완료 Task로 변환
- 확인되지 않은 legacy 수치나 외부 자료 경로 추측
- `usb.img` 전수 처리
- `.gitignore`의 `/output*/`, `/shift_experiments*/`, `.mcp.json`,
  `.claude/settings.local.json` 정리
- `work/`·run 구조와 legacy inventory의 구현 또는 확정. `/output*/`와 `/shift_experiments*/`의 필요성은
  T-0003에서 해당 구조와 inventory를 검토한 뒤 다시 판단한다.
- 새 ADR 작성, artifact schema·enum·버전의 조기 확정
- commit, merge, push, stash 또는 기존 브랜치 삭제

## 유지할 불변조건

- 코드와 테스트가 정의하는 현재 동작을 문서 변경으로 바꾸지 않는다.
- 현재 CLI·출력·분류·복구 계약은 이를 완전히 흡수한 새 정본이 검증되기 전까지 기존 spec에 보존한다.
- 기존 ADR/spec을 제거하려면 새 지속 문서가 Current 계약과 결정 근거를 완전히 흡수했고 inbound link와
  코드 참조가 0임을 먼저 확인한다. 하나라도 불완전하면 기존 문서를 유지하고 새 정본으로 연결한다.
- `transition-plan.md`는 최소 T-0010 완료까지 승인된 목표 방향의 정본으로 유지한다.
- 과거 실험 수치는 실행 시점·dataset/run·검증 범위를 유지하며 현재 성능으로 바꾸어 표현하지 않는다.
- 확인할 수 없는 legacy 자료는 추측하지 않고 `unverified` 또는 미확인으로 남긴다. 외부 절대 경로를
  영구 문서 계약으로 만들지 않는다.
- 구현되지 않은 설계는 모든 관련 문서에서 `Planned`로 표시하고 Current 서술과 같은 문맥에서 섞지 않는다.
- `*.img`, 외부 `output*`·`shift_experiments`, 로컬 설정 파일을 수정·삭제하지 않는다.
- 개인 사진의 장면·인물·위치를 기록하지 않고 파일 식별자와 기술적 결함만 사용한다.
- Python 소스, 테스트, `pyproject.toml`과 실행 계약은 변경하지 않는다.

## 작업 시작 기준선

- 시작 브랜치: `main`
- 시작 HEAD: `faff8f47c26c908f83f6d0820024bad749d78dab`
- 원격 차이: `main...origin/main [ahead 3]`
- 작업 트리: clean
- 작업 브랜치: `codex/t-0002-document-structure`
- `.venv` 실행 파일·prefix와 설치된 `media_recovery` 경로가 모두
  `C:\Users\Yunsung\Desktop\media-recovery-tool` 아래임을 확인
- 시작 전체 테스트: `248 passed in 16.61s`

## 작업 계획

1. 전환 계획의 ADR/spec 이관표를 기준으로 기존 문서의 Current 계약, 결정 근거, 역사적 결과와 Planned
   내용을 분류한다.
2. 코드·테스트·CLI help와 원문을 대조해 경로, 기본값, 분류, 테스트 수와 기준선 수치의 성격을 확인한다.
3. 네 지속 문서를 만들고 각 문서 앞부분에 역할, Current 정본 범위, Planned의 비구현 상태와 기존 문서
   관계를 명시한다.
4. `architecture.md`의 현재 데이터 흐름·모듈 책임과 ADR의 장기 불변조건을 `design.md`로 점진 이관한다.
5. spec의 현재 출력·분류·보고서 계약은 `artifacts.md`에서 요약·연결하고, Planned artifact는 별도 절로
   분리한다. 상세 계약을 완전히 흡수하지 않는 한 spec을 유지한다.
6. `current-state.md`의 검증된 기준선·실험 사실·평가 원칙을 `evaluation.md`와 `status.md`로 나누되,
   역사적 수치를 현재 성능으로 승격하지 않는다.
7. ADR별 결정 근거가 새 문서에서 추적되는지 확인하고, 모든 ADR을 역사 자료로 유지하면서 새 정본 링크를
   제공한다.
8. `docs/README.md`와 관련 안내 문서의 읽기 순서·정본 역할·링크를 실제 구조에 맞게 갱신한다.
9. Markdown 링크와 주요 코드 경로를 자동 검사하고, 문서별 Current/Planned 표현과 중복·모순을 수동
   검토한다.
10. Python 변경 0, ADR/spec 손실 0, 전체 248개 테스트 통과를 확인한 뒤 실제 결과와 생략·후속 작업을
    이 Task에 기록한다.

## 검증

필수 조건:

- 모든 로컬 Markdown 링크가 실제 파일·anchor를 가리킨다.
- 새 지속 문서가 참조하는 주요 `src/media_recovery`와 테스트 경로가 존재한다.
- Current 주장은 현재 구현·테스트·현행 spec과 일치한다.
- Planned 내용이 구현 완료나 현행 공개 계약처럼 표현되지 않는다.
- ADR/spec 삭제가 없고 역사적 결정·실험 맥락이 유지된다.
- 이전 문서와 새 정본 사이에 설명되지 않은 모순, 불필요한 전문 중복, 끊어진 참조가 없다.
- Python 코드와 테스트 변경이 0이다.
- 전체 테스트가 계속 `248 passed`다.

검증 명령은 결과에 실제 실행형과 수치를 기록한다. 최소 다음을 포함한다.

```powershell
git status --short
git diff --name-status
git ls-files --others --exclude-standard
rg --files docs
.venv\Scripts\python.exe -m pytest
```

Markdown 링크·anchor와 코드 경로는 저장소 로컬 검사 스크립트 또는 동등한 일회성 읽기 전용 검사로
검증한다. `usb.img` 전수 처리와 외부 legacy 자료 검증은 수행하지 않는다.

## 결과

2026-08-13 1차 이관을 완료한 뒤, 사용자의 legacy 제거 결정에 따라 같은 날 다시 열었다. Python code,
CLI와 output 계약은 바꾸지 않고 `architecture.md`와 `current-state.md`의 내용을 완전히 이관해 삭제했다.
spec과 ADR은 현재 계약·결정 근거이므로 유지했다.

### 실제 이관 범위

- `docs/design.md`를 만들고 기존 `architecture.md`의 module 책임, carve/reconstruct/enhance 흐름, 지원·비용
  경계와 ADR 0001~0012에서 지속할 설계 불변조건을 Current로 이관했다. object graph, forensic provenance,
  N-best와 목표 module 분리는 Planned와 담당 Task를 명시했다.
- `docs/artifacts.md`를 만들고 현재 carve output, reconstruct action 6종과 `report.csv`, enhance output과
  `report_thumbref.csv`, 현재 provenance 한계를 기록했다. case/run, JSON/JSONL/NPZ, 직교 상태, preview와
  `render`는 구현되지 않은 Planned로 분리했다.
- `docs/evaluation.md`를 만들고 T-0001 합성 동등성, 전체 248개 테스트, 비교 시 `--time-budget 0`, 구조
  gate·정상 guard·출력 동일성 원칙을 Current로 정리했다. `output_c2`·`output_c3`, v7/v8, thumbnail 보정과
  `usb.img` 감사 수치는 실행 시점과 dataset을 붙인 Historical로 표시했다. 사용자의 legacy 제거 결정 뒤
  v8 파일별 잔차·fallback 좌표, v7 손실 감사, thumbnail 반복 표본, `usb.img` 후보 분해·FAT 독립 감사·
  hard limit과 성능까지 완전히 흡수했다. 통제 손상 corpus와 forensic 지표는 Planned로 분리했다.
- `docs/status.md`를 만들고 현재 package·CLI·확인된 기능·검증 경계·알려진 한계와 T-0003~T-0010 우선순위를
  Current/Planned로 나눴다.
- `docs/architecture.md`의 module·data flow·지원·비용 경계는 `design.md`가 완전히 흡수했다. inbound
  link와 code 참조를 새 정본으로 갱신한 뒤 파일을 삭제했다.
- `docs/current-state.md`의 검증 범위·상세 표·파일별 실험 맥락·실패 접근은 `status.md`와
  `evaluation.md`가 완전히 흡수했다. inbound link와 code 참조를 갱신한 뒤 파일을 삭제했다.
- `docs/README.md`, 루트 `README.md`, `AGENTS.md`, `docs/tasks/README.md`, `docs/transition-plan.md`와
  `docs/format-notes.md`의 문서 역할·읽기 순서·Current/Planned 안내를 실제 구조에 맞췄다.
- 두 spec은 세부 Current 계약 정본으로 유지하고 `design.md`, `artifacts.md`, `status.md`, `evaluation.md`로
  연결했다. ADR index에는 12개 결정의 지속 목적지를 기록했고, 기준선·후속 작업을 가리키던 ADR 8개의
  링크를 새 정본으로 갱신했다.
- 이관 감사와 후속 검토에서 현재 placement pipeline은 `ceil(전체 MCU·0.05)`를 사용하고 소형 이미지에도
  한 MCU 행 예외를 두지 않음을 code와 회귀 테스트로 확인했다. Historical v7의 한 행 예외는 당시 기록으로
  `evaluation.md`에만 유지하고, spec·README·design·evaluation의 Current 표현은 현재 code에 맞췄다.

### 삭제·유지 판단

- `docs/specs/0001-carve.md`, `docs/specs/0002-recover.md`: edge case, 임계와 report field를 새 문서가
  완전히 대체하지 않으므로 유지
- `docs/adr/0001`~`0012`: 결정 당시 배경·대안·과거 수치를 보존하기 위해 12개 모두 유지
- `docs/transition-plan.md`: 최소 T-0010 완료까지 승인된 목표 방향 정본으로 유지

`architecture.md`와 `current-state.md`는 완전 이관과 참조 0을 확인해 삭제했다. ADR/spec 삭제는 0이고
과거 ADR을 완료 Task로 변환하지 않았다. spec은 현재 세부 계약, ADR은 결정 근거가 아직 새 지속 문서에
완전히 대체되지 않았으므로 유지했다.

### 실제 검증

- 시작 확인:
  - branch `main`, HEAD `faff8f47c26c908f83f6d0820024bad749d78dab`
  - `main...origin/main [ahead 3]`, clean worktree
  - `.venv`의 executable·prefix·설치 package가 모두 새 저장소 아래
  - `.venv\Scripts\python.exe -m pytest` → `248 passed in 16.61s`
- 구현 뒤 전체 테스트:
  - 1차 문서 이관 뒤 `.venv\Scripts\python.exe -m pytest` → `248 passed in 14.68s`
  - legacy 완전 이관·삭제 뒤 같은 명령 → `248 passed in 15.06s`
  - 후속 문서 정확성 검토 반영 뒤 같은 명령 → `248 passed in 14.77s`
- CLI 대조:
  - `.venv\Scripts\media-recovery.exe --help`와 `carve`, `reconstruct`, `enhance`의 `--help` 모두 exit 0
  - 실제 parser의 command, 기본값과 report field를 README·spec·새 지속 문서와 대조
- link·path:
  - `rg --files -g "*.md"`와 PowerShell regex/`Test-Path` 검사 → Markdown 28개, 로컬 링크 191개,
    broken 0; local anchor·image link는 0개
  - 새 `design.md`가 참조하는 주요 source path 9개 → missing 0
  - 삭제한 `architecture.md`·`current-state.md` inbound Markdown link 0, code·test·설정 참조 0
- 이관 완전성:
  - 삭제 전 `current-state.md`의 고유 파일 ID 27개 → 새 `evaluation.md`·`status.md`의 누락 0
  - 4자리 이상 고유 수치 token 68개 → 누락 0
  - 삭제 전 `architecture.md`의 고유 수치 token 12개 → 새 `design.md`·`artifacts.md`·`status.md`의 누락 0
  - module 책임·data flow·I/O·복잡도와 알려진 한계는 원문과 새 정본을 section별로 수동 대조
- 변경 범위:
  - `git diff --exit-code -- src tests pyproject.toml pytest.ini` → 차이 0
  - `git status --short`의 Python file 변경 → 0
  - `git diff --name-status` → tracked 변경 20개, `git ls-files --others --exclude-standard` → 새 문서 5개
  - ADR 12개, spec 2개 존재; 두 디렉터리의 삭제 → 0
  - `git diff --check`와 새 문서 trailing whitespace 검사 → 오류 0

후속 검토에서 Current placement 손실 한도를 code의 엄격한 5%와 맞추고 Historical v7의 한 행 예외와
분리했다. 248개 pytest와 별도 Windows 병렬 smoke의 검증 범위를 구분하고, `Path.glob("*.jpg")`의 확장자
대소문자 동작이 파일 시스템에 의존함을 명시했다. 삭제한 `architecture.md`에 있던 JPEG/AVI boundary
분리 조건과 현재 공동 배치 이유도 `design.md`에 보완했다. 미커밋 작업에서 빈 결과를 내는
`git diff --name-status main...HEAD`는 working tree와 untracked 파일을 각각 확인하는 명령으로 교체했다.

Current 문장은 code, test, CLI parser, 현행 spec과 원문을 대조했다. 네 지속 문서는 별도 `Planned — 아직
구현되지 않음` 절을 가지며 case/run, JSONL, NPZ, forensic artifact, `render`, N-best를 Current로 표현하지
않는다.

### 생략한 항목

- `usb.img` 전수 처리와 v8 970개 full-run은 문서 구조 변경의 필수 검증이 아니고 비용이 크므로 실행하지
  않았다.
- 외부로 이동한 `output*`·`shift_experiments`의 위치, hash와 record 수는 확인하지 않았다. 추측하지 않고
  T-0003 legacy inventory로 넘겼다.
- case/run, JSONL, NPZ, forensic artifact와 schema는 구현하지 않았다.
- `/output*/`, `/shift_experiments*/`, `.mcp.json`, `.claude/settings.local.json`을 포함한 `.gitignore`는
  변경하지 않았다.
- 첫 결과를 보고하기 전에는 commit, merge, push, stash와 branch 삭제를 수행하지 않았다. 이후 Git 작업은
  별도 사용자 요청에 따른다.

## 지속 문서 반영

- `design.md`: Current pipeline·module 책임·불변조건과 Planned 목표 구조
- `artifacts.md`: Current output·action·CSV·provenance 한계와 Planned artifact 계층
- `evaluation.md`: Current 검증 원칙·기준선, Historical 수치와 Planned 평가 체계
- `status.md`: Current 검증 범위·한계와 Planned 우선순위
- `docs/README.md`: 새 읽기 순서, 정본 우선순위와 완전 이관 뒤 legacy 삭제 정책
- 삭제한 `architecture.md`와 `current-state.md`의 지속 내용은 위 네 정본과 완료 Task에 반영

## 후속 작업

- T-0003: `work/`, stage run lineage·lifecycle, JSONL artifact 계약과 접근 가능한 legacy inventory
  - 이때 `/output*/`와 `/shift_experiments*/` ignore 필요성을 다시 판단한다.
- T-0004 이후: 포렌식 도메인·NPZ schema, 엔진 책임 분리와 artifact 출력
- T-0010: preview와 thumbnail enhancement artifact 분리
