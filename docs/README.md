# 문서 안내

새 세션은 프로젝트 루트의 [`AGENTS.md`](../AGENTS.md)를 확인한 뒤 아래 순서로 필요한 문서만 읽는다.

| 순서 | 문서 | 답하는 질문 | 성격 |
|---|---|---|---|
| 1 | [`current-state.md`](current-state.md) | 지금 무엇이 동작하고, 무엇이 남아 있는가? | 현재 기준선 |
| 2 | [`architecture.md`](architecture.md) | 코드는 어떻게 나뉘고 데이터는 어떻게 흐르는가? | 현재 구조 |
| 3 | [`0001-carve.md`](specs/0001-carve.md), [`0002-recover.md`](specs/0002-recover.md) | 명령과 결과가 어떤 동작을 보장하는가? | 현재 기능 명세 |
| 4 | [`adr/README.md`](adr/README.md) | 중요한 선택을 왜 했는가? | 당시 맥락을 보존하는 결정 기록 |
| 필요 시 | [`format-notes.md`](format-notes.md) | JPEG·AVI 처리에서 어떤 포맷 사실을 전제하는가? | 구현 참고 |

전체 데이터 수치와 알려진 한계는 `current-state.md`, 현재 동작은 기능 명세, 선택의 이유는 ADR을 기준으로
한다.
