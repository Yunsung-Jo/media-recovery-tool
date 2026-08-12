# 문서 안내

이 저장소는 Media Recovery Tool로 전환 중이다. 새 세션은 프로젝트 루트의
[`AGENTS.md`](../AGENTS.md)를 확인한 뒤 목적에 따라 아래 문서를 읽는다.

| 순서 | 문서 | 답하는 질문 | 성격 |
|---|---|---|---|
| 1 | [`transition-plan.md`](transition-plan.md) | 프로젝트가 어디로 전환되며 무엇이 확정됐는가? | 승인된 목표 방향 |
| 2 | [`tasks/active/`](tasks/active/) | 지금 무엇을 어디까지 변경해야 하는가? | 활성 작업 계약 |
| 3 | [`tasks/README.md`](tasks/README.md) | Task를 어떻게 작성하고 완료하는가? | 작업 운영 규칙 |
| 4 | [`current-state.md`](current-state.md) | 현재 구현은 어디까지 검증됐는가? | 현재 기준선 |
| 5 | [`architecture.md`](architecture.md) | 현재 코드는 어떻게 나뉘고 흐르는가? | 전환 전 현재 구조 |
| 6 | [`0001-carve.md`](specs/0001-carve.md), [`0002-recover.md`](specs/0002-recover.md) | 현재 명령과 결과가 무엇을 보장하는가? | 전환 전 현재 기능 명세 |
| 역사 확인 | [`adr/README.md`](adr/README.md) | 기존 선택은 어떤 맥락에서 생겼는가? | 이관 전 역사 자료 |
| 필요 시 | [`format-notes.md`](format-notes.md) | JPEG·AVI 처리에서 어떤 포맷 사실을 전제하는가? | 구현 참고 |

목표 방향과 현재 구현을 혼동하지 않는다. 전환 작업은 활성 Task의 범위를 따르고, 현재 코드의 실제 동작은
코드·테스트와 현행 기능 명세를 기준으로 한다. 기존 ADR/spec은 T-0002에서 지속 내용을 이관하기 전까지
삭제하지 않는다.
