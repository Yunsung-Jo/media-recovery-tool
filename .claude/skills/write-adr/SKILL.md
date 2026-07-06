---
name: write-adr
description: Use when recording a non-trivial technical decision in docs/adr/ — a chosen design alternative, fallback strategy, format-handling approach, or any decision whose rationale is not obvious from the code. In the experiment-loop workflow, distill the ADR from the live investigation during the refinement pass (right before the squash-merge to main). Carry decision-relevant conclusions in full but link the process narrative to the investigation. Trigger when finalizing or superseding an ADR.
---

# ADR 작성

**목적:** 처음 보는 사람이 3분 안에 "무엇을 결정했고 왜 그렇게 했는지"를 납득하게 한다.
ADR이 담는 것 = **왜 이 방향, 버린 대안과 그 이유, 이 결정이 유지되는가.** 절 구성은
`0000-template.md`, 문서 경계·상태 전이는 [doc-roles](../shared/doc-roles.md), 문체·제목은
[doc-style](../shared/doc-style.md).

## 언제 쓰나

지속 제약(예: "출력 비트동일만")이나 결과가 오래 남는 진짜 갈림길이 있는 결정일 때. 루프 중이 아니라
리파인 패스에서 조사 기록으로부터 증류한다. **단순 병목 수정처럼 지속 제약도 갈림길도 없으면 조사
기록으로 충분** — ADR로 만들지 않는다.

## 규칙

### 1. [필수] Why > How — "배경"·"대안" 절이 "결정" 절보다 충실하게
코드로 알 수 있는 구현 방법은 적게, 코드로 알 수 없는 것(왜 택했나·무엇을 왜 버렸나·당시 제약)을 많이.
결론은 ADR만 읽고 평가하도록 self-contained하게, 단 결론에 이른 **과정(단계별 실험·삽질)은 베끼지 말고
조사 기록을 링크**한다.

### 2. [필수] 버린 대안마다 "택했다면 무엇이 나빠지나"를 수치로
"대안" 표의 각 행에 그 대안의 구체적 손해를. "복잡해서"·"별로여서"는 이유가 아니다.
- ✗ `통째로 추출 | 부정확함`
- ✓ `통째로 추출 | 상한이 헐거워 잉여 포함. raw 5→9 MB면 gray 0.144→0.517로 악화`

### 3. [필수] 임계값·상수는 "왜 그 값인지"를 분포·실험으로 정당화
- ✗ "0.3으로 정했다."
- ✓ "분포가 겹쳐 깨끗한 경계가 없고 오판이 무해(비대칭)하므로 낮은 고정값 0.3."

근거 없는 결정은 다음 사람이 마음대로 뒤집는다.

### 4. [필수] 성과 집계 수치는 report의 몫
before/after 개수·비율(사용가능 개수, FAILED 전환 수 등)은 ADR에 나열하지 말고 report를 링크한다.
ADR엔 **결정을 정당화하는 판별 수치**(규칙 3)만 남긴다. 경계: 성과 집계=report, 결정 근거=ADR.

### 5. [필수] 상태(Status)를 전이한다
확정=`Accepted`, 논의 중=`Proposed`, 폐기=`Deprecated`. 대체 시 **새 ADR 작성 + 기존을
`Superseded by NNNN`으로** 양쪽 다 갱신한다.

## 절차

[공통 작성 절차](../shared/doc-roles.md)를 따른다. 포맷·스펙 지식은 인라인하지 말고
`docs/reference/`를 링크한다.

## 자가점검

- [ ] "왜"(배경·대안)가 "어떻게"보다 분량이 많나? 처음 보는 사람이 3분 안에 결정·이유를 아나?
- [ ] 기각한 각 대안에 구체적(가능하면 정량) 손해가 적혔나?
- [ ] 성과 집계 수치는 report로 링크하고, 대체 시 기존 ADR을 `Superseded`로 전이했나?
