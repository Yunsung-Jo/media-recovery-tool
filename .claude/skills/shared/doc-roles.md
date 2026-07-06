# 문서 역할 경계 (정본)

문서 유형별 **무엇을 담고 무엇은 담지 않는가**, **살아있는 문서인가 스냅샷인가**, **언제 생성하고
언제 갱신하는가**의 단일 정본. 각 `write-*` 스킬·`experiment-loop`·`review-doc`·`CLAUDE.md`는 이
경계를 재서술하지 말고 이 문서를 링크한다. 산문·제목 문체는 별도 정본 [doc-style](doc-style.md)에 있다.

> 루프 중 **어느 시점에** 어떤 문서를 쓰는가(타이밍)는 여기가 아니라
> [experiment-loop](../experiment-loop/SKILL.md) 소관이다. 여기는 **경계**(무엇을 어디에), 거기는 **시점**.

## 유형별 경계 — 담는 것 / 담지 않는 것

| 유형 | 담는 것 | 담지 않는 것 (→ 어디로) | 작성 스킬 |
|------|---------|------------------------|-----------|
| **spec** | 현재 구현의 **계약** — 무엇이 존재·어떻게 동작·입출력·예외·알려진 한계·의존 모듈 | 왜 이 설계인가(→ADR) · 코드 walkthrough·구현 순서 · 측정 수치(→report) · 포맷 사실(→reference) | [write-spec](../write-spec/SKILL.md) |
| **ADR** | **왜 이 방향을 택했나** · 버린 대안과 그 이유 · 이 결정이 유지되는가(제약) | 정량 결과·수치(→report 링크) · 과정 서사(→investigation 링크) · 동작 상세(→spec) | [write-adr](../write-adr/SKILL.md) |
| **investigation** | 디버깅·분석 **과정** — 가설·예측·실험·증거·막다른 길 | 확정 결론만 따로 추출(→adr/spec/reference) · 결과 보고(→report) | [write-investigation](../write-investigation/SKILL.md) |
| **report** | **측정 결과**(before/after) · **그래서 다음 뭘 할지(권고)** | 과정·막다른 길(→investigation) · 설계 이유(→ADR) | [write-report](../write-report/SKILL.md) |
| **reference** | 포맷·스펙·API **사실**(외부 + 프로젝트 실측) | 프로젝트 동작(→spec) · 결정(→ADR) | [write-reference](../write-reference/SKILL.md) |

한 문장 정리: **왜 그렇게 설계했나=ADR, 왜 그렇게 발견했나=investigation, 측정 결과·다음 할 일=report,
포맷 사실=reference, 현재 구현의 계약=spec.**

## 살아있는 문서 vs 스냅샷 vs 개정

- **살아있는 문서 (코드/지식과 동기화)** — `spec`·`reference`. 대상이 바뀌면 그 문서를 **갱신**한다.
  어긋난 채 방치된 살아있는 문서는 없는 것보다 위험하다(독자가 신뢰하고 틀린다).
- **스냅샷 (동결)** — `investigation`. 작업이 끝나면 동결하고 이후 코드가 바뀌어도 **갱신하지 않는다**
  (새 기록을 쓴다). 랩 노트는 그 시점의 판단을 보존하는 가치가 있다.
- **개정·상태 전이** — `report`·`ADR`. 산출물이 갱신되면 새 문서를 쓰거나 개정한다(ADR은 상태 전이로).

## 생성 vs 갱신 규칙

| 유형 | 새로 만든다 | 기존을 갱신한다 |
|------|-------------|-----------------|
| **spec** | 새 프로그램일 때만 | 코드 변경으로 관찰 동작이 바뀌면 해당 spec 갱신(+날짜) |
| **reference** | 기존 문서가 그 사실을 자연스럽게 담지 못할 때 | 기존 문서가 자연스럽게 담을 수 있으면 절을 덧붙여 확장 (한 문서가 비대·산만해지면 쪼갠다) |
| **ADR** | 새 결정마다 (번호 +1) | 대체 시 기존 ADR을 `Superseded by NNNN`으로, 확정 시 `Accepted`로 상태 전이 |
| **investigation** | 새 조사마다 | 갱신하지 않는다 — 코드가 바뀌면 새 기록 |
| **report** | 새 산출물·평가마다 | 같은 산출물 개정이면 본문 개정(+날짜 갱신) |

## 상태(Status)를 1급으로 쓴다 — ADR·spec

문서 상단 상태 필드는 장식이 아니라 라이프사이클 신호다. 전이를 미루지 않는다.

- **ADR**: `Proposed`(논의 중) → `Accepted`(확정·구현됨) → `Superseded by NNNN`(대체됨) / `Deprecated`(폐기).
  새 결정이 기존을 대체하면 **양쪽 다** 갱신한다(새 ADR 작성 + 기존 상태 전이).
- **spec**: `Draft`(논의 중) → `Accepted`(확정) → `Superseded`(대체). 프로그램이 사라지면 Superseded/Deprecated.

## 공통 작성 절차

모든 유형 공통 — 각 `write-*` 스킬은 이 절차를 참조하고 자기 유형 고유 단계만 덧붙인다.

1. 템플릿 `<유형>/0000-template.md`를 아래 파일명으로 복사한다.
2. 본문을 채운다(유형별 규칙은 해당 `write-*` 스킬).
3. 해당 `README.md` 목록 표에 한 줄 추가한다 — 날짜 로그(reports·investigations)는 **맨 위(최신순)**,
   번호순(adr·spec)은 아래에. 상태를 전이하면 README의 상태 구획도 옮긴다.

| 유형 | 파일명 | 번호 |
|------|--------|------|
| spec | `NNNN-프로그램명.md` | 디렉터리 최신 +1 |
| ADR | `NNNN-짧은-제목.md` | 디렉터리 최신 +1 |
| investigation | `YYYY-MM-DD-짧은-제목.md` | 날짜 |
| report | `YYYY-MM-DD-짧은-제목.md` | 날짜 |
| reference | `<주제>.md` (소문자·하이픈) | 없음 |
