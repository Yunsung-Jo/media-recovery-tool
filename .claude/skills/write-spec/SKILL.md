---
name: write-spec
description: Use when documenting or updating how a program/tool behaves in docs/specs/ — its interface, output, pipeline, edge cases, and known limits. Trigger when adding a new program, or when a code change alters a program's observable behavior. A spec is a living document kept in sync with the code; it describes what the program does, not why the design was chosen (that is an ADR).
---

# 스펙 작성

**목적:** 코드를 다 읽지 않고도 이 프로그램이 무엇을·어떻게 동작하는지 파악하고, 모듈 수정·삭제 시
영향받는 프로그램을 짚게 한다. spec = **현재 구현의 계약**(무엇이 존재·어떻게 동작·입출력·예외·한계·
의존 모듈). 절 구성은 `0000-template.md`, 문서 경계·살아있음·생성/갱신은 [doc-roles](../shared/doc-roles.md),
문체는 [doc-style](../shared/doc-style.md).

## 언제 쓰나

새 프로그램을 추가할 때, 또는 코드 변경으로 **관찰 가능한 동작**(인터페이스·출력·파이프라인·엣지)이
바뀔 때. 루프 중이 아니라 동작이 출하된 뒤(리파인)에 쓴다 — 동작이 확정돼야 기술할 수 있다
([experiment-loop](../experiment-loop/SKILL.md)).

## 규칙

### 1. [필수] 계약이지 코드 walkthrough가 아니다 — 근거(why)는 ADR로
각 단계가 **무슨 책임을 갖는지**(입력 → 보장하는 출력·불변식)와 **무슨 조건에 반응하는지**(예: "비트레이트
평균 4배 초과면 디싱크로 본다", "재개점이 거의 같은 비트면 가짜 복구로 거부")를 적는다. 내부 함수 호출
순서·구현 흐름은 베끼지 않는다(코드가 산다). "왜 이 방식인가 / 무엇을 기각했나"만 ADR로 분리·링크한다
(예: `0002-recover`가 ADR 0001을 링크). 복잡하면 `복구 원리(알고리즘)` 같은 절을 적응적으로 추가해도
된다 — 메커니즘의 책임을 적는 한.

### 2. [필수] 살아있는 문서 — 코드와 동기화
관찰 동작이 바뀌면 spec을 갱신한다. **어긋난 spec은 없는 것보다 위험하다** — 독자가 신뢰하고 틀린다.

### 3. [필수] 엣지 케이스·알려진 한계는 필수
정상 경로만 적지 않는다. 비정상 입력·실패 모드·fallback·**처리하지 못하는 것**을 남긴다. 한계를
숨기지 않는다(예: `0002-recover`의 "물리적 소실은 회색으로 남김"). 양이 많으면 별도 절(`미해결`)로 뺀다.

### 4. [필수] 의존 모듈을 빠짐없이 적는다
모듈을 수정·삭제할 때 영향받는 프로그램을 역추적하게 한다 — spec의 운영 가치다.

### 5. [권장] 포맷 지식은 reference로 링크 (인라인 금지)
전제하는 마커 표·청크 구조 등은 `docs/reference/`로 링크한다. 복붙한 중복은 언젠가 어긋난다.

## 절차

[공통 작성 절차](../shared/doc-roles.md)를 따른다. **갱신일 때:** 동작이 바뀐 기존
프로그램이면 새 파일을 만들지 말고 해당 spec을 갱신하고 날짜를 고친다.

## 자가점검

- [ ] 코드 안 읽은 사람이 인터페이스·출력·동작을 파악하나? 계약·책임으로 적고 walkthrough를 안 베꼈나?
- [ ] 기술한 동작이 현재 코드와 일치하나(drift 없음)? 엣지 케이스·알려진 한계를 적었나?
- [ ] "사용하는 모듈"이 실제 `import`와 일치하나(grep 대조)?
