# Media Recovery Tool 전환 작업 지침

이 파일은 이 저장소에서 작업하는 에이전트의 공통 정본이다. 도구별 설정이나 보조 문서와 충돌하면
이 파일을 우선한다.

## 현재 상태와 작업 정본

이 저장소는 **Media Recovery Tool**로 전환 중이다. 손상된 디스크 이미지에서 JPEG·AVI를 카빙하고
baseline JPEG를 구조적으로 복구한다. 저장소와 Python package 정체성은 각각
`media-recovery-tool`과 `src/media_recovery`다.

새 세션은 다음 순서로 문서를 읽는다.

1. 이 파일
2. [`docs/transition-plan.md`](docs/transition-plan.md) — 승인된 목표 방향과 Task 로드맵
3. [`docs/tasks/active/`](docs/tasks/active/) — 현재 작업의 범위와 완료 조건
4. 현재 구현을 이해할 때 `docs/design.md`, `docs/artifacts.md`, `docs/status.md`, 필요하면 `docs/specs/`

현재 활성 Task는 없다. 완료한
[`T-0003 work와 run lineage·lifecycle 및 JSONL 기반`](docs/tasks/completed/2026/T-0003-work-run-lineage-lifecycle-and-jsonl.md)은
기존 CLI를 바꾸지 않고 `work/`, source hash case, 시작 전 source 재검증, stage run, strict JSON/JSONL과
completion seal을 구현하고 legacy inventory를 만들었다. case/run은 아직 기존 명령에 연결되지 않았고
forensic domain·NPZ는 구현되지 않았다. 다음 계획 작업은 T-0004이고 실제 시작할 때 합의한 범위로 활성
Task를 만든다.

문서가 충돌하면 다음 우선순위를 사용한다.

1. 이 `AGENTS.md`의 안전·작업 규칙
2. 활성 Task의 범위·불변조건·완료 조건
3. `docs/transition-plan.md`의 목표 방향
4. 현재 코드와 테스트, `docs/design.md`·`docs/artifacts.md`의 Current와 현행 spec
5. 과거 ADR과 완료 Task의 역사적 설명

## 현재 구현

- `media-recovery carve`: 디스크 이미지에서 JPEG·AVI 추출
- `media-recovery reconstruct`: 추출한 JPEG를 resync·헤더 복구로 복원
- `media-recovery enhance`: EXIF thumbnail 기반 선택적 후처리
- `src/media_recovery/artifacts/`: 격리된 case/run lineage·lifecycle, strict JSON/JSONL과 completion seal
- `src/media_recovery/`: CLI, 스캐너, 추출기, JPEG 디코더, 복구 엔진
- `tests/`: 회귀 테스트
- `docs/`: Current/Planned 설계·산출물·평가·상태 정본, 전환 계획, Task, 현행 spec, 역사 ADR, 포맷 메모

## 안전과 범위

- `*.img` 원본과 저장소 밖으로 이동한 기존 `output*`·`shift_experiments` 자료는 사용자가 명시적으로
  요청하지 않는 한 수정·삭제하지 않는다. 외부 위치를 추측하거나 재배치하지 않는다.
- 목표 구조의 `work/`는 Git 비추적 기본 실행 데이터다. case와 완료 run을 자동 정리하거나 덮어쓰지
  않고, `cache/`·`tmp/` 삭제도 사용자의 요청 또는 해당 Task의 명시적 범위 안에서만 수행한다.
- 개인 사진의 장면·인물·위치 등 시각적 내용을 문서나 응답에 묘사하지 않는다. 파일 식별자, 수치,
  색 캐스트·밀림·미복구 영역 같은 기술적 결함만 기록한다.
- 작업 시작 시 `git status`를 확인하고 기존 사용자 변경을 보존한다.
- 목표 밖에서 발견한 문제는 바로 구현하지 않는다. 재발 가능성이 높거나 다음 작업을 실제로 제약하는
  내용만 활성 Task와 관련 지속 문서에 짧게 반영한다. 현재 목표를 바꾸는 발견은 사용자와 합의한 뒤
  Task 범위를 조정한다.

## 구현과 검증

- 로컬 실행은 프로젝트의 `.venv`를 우선 사용한다. 처음에는 `python -m venv .venv` 후 Windows는
  `.venv\Scripts\python.exe -m pip install -e ".[dev]"`, POSIX는
  `.venv/bin/python -m pip install -e ".[dev]"`로 설치한다.
- 전체 테스트는 가상환경의 Python으로 `python -m pytest`를 실행한다. 공통 옵션과 임시 경로는
  `pytest.ini`가 관리한다.
- 변경 범위에 가까운 테스트를 먼저 실행하고, 완료 전 가능하면 전체 테스트를 실행한다.
- 테스트를 실행하지 못했거나 전체 데이터 검증을 생략했다면 이유를 결과에 명시한다.
- 3.5GB 원본 이미지 전수 처리처럼 비용이 큰 작업은 필요성과 예상 범위를 먼저 알린다.
- 버그 수정에는 가능하면 실패를 재현하는 회귀 테스트를 추가한다.
- 단순 진단 요청에서는 원인을 조사하고 설명하되, 사용자가 수정을 요청하지 않았다면 코드를 바꾸지 않는다.

## 정답 원본이 없는 복구 실험

헤더 복구, 색상 보정, 이미지 밀림, 디싱크, 복구율 개선처럼 정답 원본이 없는 작업에만 적용한다.

1. 목표와 성공 기준을 정한다. 구조 타당성·기존 정상 표본 회귀 0을 필수 기준으로 둔다.
2. 실패 양상을 대표하는 고정 샘플과 정상 회귀 가드를 함께 선택하고 베이스라인을 저장한다.
3. 원인을 먼저 조사하고, 실험 전에 가설과 파일별 예상 결과를 정한다.
4. 고정 샘플에서 자동 지표와 육안 판정을 함께 사용한다. 자동 지표 하나만으로 성공을 단정하지 않는다.
5. 검증된 방법만 본체에 구현한 뒤 같은 샘플로 재검증한다.
6. 샘플 결과가 일치하면 사용자에게 알리고 필요한 경우에만 전수 검증한다.

비교 실험과 품질 베이스라인의 복구 실행은 시간 제한 때문에 결과가 달라지지 않도록
`media-recovery reconstruct --time-budget 0`을 사용한다. 일상적인 빠른 확인에는 이 규칙을 강제하지 않는다.

## Git

- `main`에 작업 커밋을 직접 만들지 않는다. 사용자가 커밋을 요청했고 현재 브랜치가 `main`이면
  먼저 `<타입>/<짧은-설명>` 브랜치를 만든다(예: `feat/shift-correction`, `fix/jpeg-boundary`).
- 사용자의 명시적 요청 없이 commit, merge, push, stash, 브랜치 삭제를 하지 않는다.
- 커밋 메시지는 Conventional Commits 형식의 한글로 작성한다. 제목은 간결하게 하고 본문에는 변경 이유와
  검증 결과를 적는다.
- 사용자가 로컬 머지를 요청하면 `git merge --squash <branch>`를 사용하고, 결과 확인 전 브랜치를 삭제하지 않는다.

## Task와 문서화

모든 계획 작업은 [`docs/tasks/README.md`](docs/tasks/README.md)의 규칙을 따른다. Task는 `docs/tasks/`
아래에 두며 목표·범위·비범위·불변조건·검증·결과를 기록한다. Task가 필요하다는 이유만으로 모든 작업에
새 문서를 만들지는 않는다.

전환 중에는 다음 문서 역할을 사용한다.

| 변경 | 문서 |
|------|------|
| 설치·사용법·CLI 예시 변경 | `README.md` |
| 모듈 책임·데이터 흐름·핵심 불변조건 변경 | `docs/design.md` |
| 현재 산출물·분류·provenance 계약 변경 | `docs/artifacts.md`와 관련 `docs/specs/` |
| CLI 인자·세부 동작 계약 변경 | 관련 `docs/specs/`와 `README.md` |
| 현재 활성 작업의 목표·범위·검증 | `docs/tasks/active/` |
| 승인된 전체 전환 방향 | `docs/transition-plan.md` |
| 평가 방법·기준선 변경 | `docs/evaluation.md` |
| 현재 검증 범위·알려진 한계·후속 작업 변경 | `docs/status.md` |
| 구현 판단에 필요한 JPEG·AVI 사실 | `docs/format-notes.md` 갱신 |

새 ADR은 만들지 않는다. 기존 `docs/adr/`는 결정 당시 근거를 보존하는 역사 자료이고 `docs/specs/`는 새
지속 문서가 완전히 흡수하지 않은 현재 세부 계약이다. `docs/architecture.md`와 `docs/current-state.md`는
T-0002에서 새 정본으로 완전히 이관하고 inbound link와 code 참조 0을 확인한 뒤 삭제했다. 같은 조건을
확인하지 않고 spec·ADR을 삭제하거나 과거 ADR을 완료 Task로 변환하지 않는다.

문서의 수치·코드 참조·링크·검증 주장은 실제 원자료와 현재 코드로 확인한다. 기술 문서는 사실과 인과를
중심으로 쓰되 문체 자체를 과도하게 규제하지 않는다.
