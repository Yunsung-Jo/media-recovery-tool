---
name: write-report
description: Use when writing a results/comparison/evaluation report in docs/reports/ for a non-technical audience (PM, ops, management) — conclusion-first and quantitative. Trigger when presenting before/after metrics or the value of completed work. Emphasizes verifying every number against raw data, since report numbers are wrong more often than expected.
---

# 보고서 작성

**목적:** 이 작업이 얼마나 가치 있었는지를 숫자와 데이터로 증명한다. 독자는 **개발 배경이 얕은 PM·운영·
관리자** — 코드가 아니라 숫자와 임팩트를 본다. 절 구성은 `0000-template.md`, 문서 경계는
[doc-roles](../shared/doc-roles.md), 문체·제목은 [doc-style](../shared/doc-style.md). 루프가 아니라 작업이
다 끝난 뒤(리파인)에 쓴다 — 최종 수치가 확정돼야 의미가 있다.

## 규칙

### 1. [필수] 철저한 두괄식 — 맨 위 한 줄 요약 + 핵심 표/그림
바쁜 독자가 거기까지만 읽어도 결론·임팩트를 알게 한다. 결론을 끝으로 미루지 않는다.

### 2. [필수] 정량 우선 — 각 수치에 의미 한 줄
- ✗ "회색이 크게 줄었습니다."
- ✓ "회색 잔존(gray≥30%) 101개 → 78개. 의심 92개 중 26개 복구, 악화 0." (악화 0 = 정상 파일 안 망침.)

비전문 독자는 raw number만으론 "그래서 좋은지" 모른다 — 결과마다 해석 한 줄을 붙인다.

### 3. [필수] 모든 핵심 수치를 원자료에서 재계산해 대조
기억·중간 메모가 아니라 원자료(`report.csv`·파일 크기·로그)에서 다시 뽑는다. 합·개수·비율·중앙값은 특히
자주 어긋난다(규모 크면 스크립트로 독립 재산출). 각 수치의 출처를 부록에 남기고, 비교가 같은 표본·기준
(같은 92개, 같은 budget)인지 확인한다. **보고서 수치는 생각보다 자주 틀린다 — 이 패스를 건너뛰지 않는다.**

### 4. [필수] 결론·권고 — 다음 할 일까지
수치 나열로 끝내지 않는다. 남은 문제·다음 작업 후보(backlog·후속 브랜치)·권고를 담아, 독자가 후속
조치를 여기서 파악하게 한다.

### 5. [필수] 비기술 독자 어휘 — 약어는 첫 등장 시 풀이
- ✗ "gray≥30% 101→78, hole 92개, resync 미복원."
- ✓ "회색(데이터가 없어 복원 못 한 영역)이 30% 이상인 파일이 101→78개로 줄었다."

내부 식별자(`FF D9`·MCU·destuff)가 꼭 필요하면 괄호로 한 줄 풀이. 민감정보(개인 사진)는 블러.

### 6. [권장] 한계를 명시한다
표본·측정의 한계, 일반화 시 주의점. 과장 없는 보고가 신뢰를 만든다.

## 대표 그림 — 비교 몽타주

before/after **격자형 몽타주**가 표준이다. 레이아웃·폰트·크기는 `tools/montage.py`가 처리하므로 매번
정하지 않는다 — 변하는 것(표본·조건·라벨·이미지·블러)만 JSON 스펙으로 넘긴다.

**작성 전 반드시 질문**(`AskUserQuestion`): ① 표본(임팩트 큰 before→after 1~2개) ② 블러 여부(개인정보;
회색·글리치처럼 식별 불가면 불필요). **형식은 묻지 않는다.**

각 조건의 손상 JPEG를 동일 크롭·배율로 디코딩 → 스펙(행=표본, 열=조건 좌→우 시간순)을 채워
`python tools/montage.py <스펙>.json` → `assets/<날짜>-<주제>-comparison.webp`. 참고 예시:
`docs/reports/assets/*-comparison.webp`.

```json
{
  "title": "<작업명> — <맥락(커밋/수정)>",
  "columns": ["기존 (carve 조기종료)", "신규 (EOI 검증 + budget 0)"],
  "rows": [
    {"label": "0x9906A000.jpg", "sub": "회색 0.918 → 0.002", "cells": ["a.png", "b.png"]}
  ],
  "blur": 3,
  "out": "docs/reports/assets/2026-06-29-carve-eoi-comparison.webp"
}
```
`columns` 2~3개, `cells` 길이=`columns`. `sub`(before→after 지표) 선택. `blur` 기본 반지름(0=없음, 보통
3; 행마다 덮어쓰기 가능). 고정 스타일 상수는 `tools/montage.py` 상단.

## 절차

[공통 작성 절차](../shared/doc-roles.md)를 따르되, 본문 작성 후 **수치 검증 패스**(규칙 3)를 반드시 돌린다.

## 자가점검

- [ ] 맨 위 한 줄 요약 + 핵심 표/그림만 읽어도 결론·임팩트를 아나? "좋아졌다"류가 수치로 바뀌었나?
- [ ] **모든 핵심 수치를 원자료에서 재계산**하고 출처를 부록에 남겼나? 비교가 같은 표본·기준인가?
- [ ] 결론에 권고·다음 할 일이 있나? 각 수치에 의미 한 줄이 있나?
- [ ] 약어를 첫 등장 시 풀었나? 대표 그림 표본·블러를 작성 전에 물었고 민감정보를 블러했나?
