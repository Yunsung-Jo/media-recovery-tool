# 문서 안내

이 저장소는 Media Recovery Tool로 전환 중이다. 새 세션은 프로젝트 루트의
[`AGENTS.md`](../AGENTS.md)를 먼저 읽고, 다음 표에서 필요한 정본으로 이동한다.

## 읽기 순서

| 순서 | 문서 | 답하는 질문 | 상태 |
|---|---|---|---|
| 1 | [전환 계획](transition-plan.md) | 어디로 전환하며 어떤 원칙이 승인됐는가? | Current와 분리된 목표 방향 |
| 2 | [활성 Task](tasks/active/) | 지금 무엇을 어디까지 바꾸고 검증하는가? | 현재 실행 계약 |
| 3 | [Task 운영 규칙](tasks/README.md) | Task를 어떻게 작성하고 완료하는가? | 운영 정본 |
| 4 | [설계](design.md) | 현재 pipeline·module·불변조건은 무엇이며 Planned는 무엇인가? | 지속 정본 |
| 5 | [산출물](artifacts.md) | 현재 output·action·CSV와 Planned provenance는 무엇인가? | 지속 정본 |
| 6 | [평가](evaluation.md) | 무엇을 어떻게 검증했고 수치를 어떻게 해석하는가? | 지속 정본 |
| 7 | [상태](status.md) | 현재 검증 범위·한계와 다음 우선순위는 무엇인가? | 지속 정본 |
| 필요 시 | [carve spec](specs/0001-carve.md), [reconstruct spec](specs/0002-recover.md) | 현재 CLI와 세부 edge case가 무엇을 보장하는가? | 유지 중인 Current 계약 |
| 필요 시 | [포맷 메모](format-notes.md) | JPEG·AVI 처리에서 어떤 객관적 사실을 전제하는가? | 구현 참고 정본 |
| 역사 확인 | [ADR 목록](adr/README.md) | 비자명한 선택은 어떤 배경·대안에서 나왔는가? | 유지 중인 역사 자료 |

## Current와 Planned

- `design.md`, `artifacts.md`, `evaluation.md`, `status.md`는 각 문서 안에서 `Current`와 `Planned`를
  명시적으로 나눈다.
- `Current`는 code·test와 현재 spec으로 확인한 동작이다.
- `Planned`는 아직 구현되지 않은 목표이며 현재 CLI, output 또는 schema 계약으로 사용하지 않는다.
- `transition-plan.md`는 최소 T-0010 완료까지 승인된 목표 방향의 정본이다.
- 활성 Task는 이번 변경의 실제 범위와 완료 조건에서 전환 계획보다 구체적인 정본이다.

## 이관된 문서와 유지 자료

- 기존 `architecture.md`의 구조·불변조건은 `design.md`가 완전히 흡수했고 inbound link를 갱신한 뒤 파일을
  삭제했다.
- 기존 `current-state.md`의 검증 범위·상세 수치·실험 표·실패 접근은 `status.md`와 `evaluation.md`가
  완전히 흡수했고 inbound link를 갱신한 뒤 파일을 삭제했다.
- 두 spec은 세부 Current 계약을 새 문서가 완전히 대체하지 않으므로 유지한다.
- 12개 ADR은 결정 당시 배경·대안·결과를 보존하는 역사 자료로 유지한다. 지속할 불변조건과 평가 원칙은
  새 정본으로 연결했다.
- 기존 ADR/spec을 제거하려면 새 지속 문서의 완전한 흡수, inbound link와 code 참조 0을 먼저 확인한다.
  불완전하면 삭제하지 않고 연결을 유지한다.

삭제한 문서의 이전 상태는 완료 Task와 Git history에서 확인할 수 있지만 현재 정본보다 우선하지 않는다.
확인되지 않은 legacy 수치와 외부 자료 경로는 추측하지 않는다.
