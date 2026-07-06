---
name: write-investigation
description: Use when recording the debugging or analysis process behind a finding in docs/investigations/ — a lab note of hypotheses, predictions, experiments, evidence, and dead ends so the next person does not repeat them. In the experiment-loop workflow this is written live, one entry per cycle, during the loop. Record in as much concrete detail as possible. Trigger throughout a non-trivial debugging or analysis session, before the conclusion is locked into an ADR or spec.
---

# 조사 기록 작성

**목적:** 미래의 나와 동료가 똑같은 삽질을 반복하지 않도록 과정을 보존한다. 독자는 **작업하지 않은
다른 개발자** — 비슷한 버그로 급히 검색해 들어온 사람이라 **논리 비약이 없고 다시 추적할 디테일**이
있어야 한다. 절 구성은 `0000-template.md`, 문서 경계·스냅샷 규칙은 [doc-roles](../shared/doc-roles.md),
문체·제목은 [doc-style](../shared/doc-style.md).

**루프 중 매 사이클 라이브로** 쓴다(끝난 뒤 몰아 쓰지 않는다 — 컨텍스트 압축으로 디테일이 사라진다).
스냅샷이라 이후 코드가 바뀌어도 갱신하지 않는다(새 기록을 쓴다).

## 규칙

### 1. [필수] 각 단계는 5요소를 갖추고, 가설은 직전 증거에서 따라나온다 (비약 금지)
각 단계(가설 블록) = **가설/세운 이유 · 실험 · 예측 · 증거 · 판단**(템플릿 슬롯과 동일). "왜 그 가설을
세웠는지"가 직전 단계 증거에서 따라나와야 검색해 들어온 사람이 한 단계도 건너뛰지 않고 따라온다.
흐름은 서사적으로, 문장은 건조하게.

### 2. [필수] 예측을 실험 전에 — 식별자·지표로
결과를 본 뒤 예측을 사후에 끼워맞추는 것을 막는다.
- ✗ "좋아질 것"
- ✓ "#3·#7은 gray 0.9→0.1, 회귀 가드 #1·#2는 불변"

### 3. [필수] 기각된 가설 / 막다른 길을 반드시 남긴다 — 가장 잊기 쉽고 가장 가치 있다
성공 경로만 남기고 실패를 지우면 다음 사람이 같은 길을 다시 헤맨다. **시도한 것 / 왜 틀렸는지**를 적고,
끝에 **다음에 같은 함정을 피할 일반화된 교훈 한 줄**을 붙인다.
- ✗ "rate_mult 2.5로 낮추니 gray 0.999로 붕괴."
- ✓ "rate_mult 2.5 이하면 gray 0.999로 붕괴 — **누적 평균 rate로는 국소 비트레이트를 분리 못 한다.**"

### 4. [필수] 구체 값으로 자세히, 이미지 시각 내용은 묘사 금지
- ✗ "시도했으나 안 됨"
- ✓ "FFD9에서 1B skip → gray 0.08→0.06, 200~340행 여전히 어긋남 → 하류 2차 디싱크 의심"

파일별로, 쓴 접근·스크립트·명령과 함께 적는다(길어도 된다 — 모호한 게 더 나쁘다). **핵심 로직은 "사용한
방법·도구"에 의사코드·요지로 남긴다**(소스 전문이 아니라) — scratchpad는 사라지므로 링크만으론 재현이
안 된다. **단 이미지의 시각적 내용(장면·인물·실제 색·구도)은 묘사하지 않는다**(개인 사진 포함) —
gray·skew·byte% 지표와 색 캐스트·밀림 행 범위 같은 결함 패턴으로만. ("자세히"는 *지표*를 자세히 적으라는
뜻이지 이미지를 묘사하란 뜻이 아니다.)

## 절차

1. **루프 시작 시 스텁 생성** — [공통 작성 절차](../shared/doc-roles.md). '한 줄' 필드에 증상 키워드
   (오프셋·지표·증상)를 넣는다 — 같은 버그로 grep해 들어온 사람이 이 기록을 찾는 지점이다.
2. **매 사이클 항목 추가** — "조사 과정"에 한 단계씩. 가설·실험·예측은 실험 전, 증거·판단은 실험 후.
3. **종료 후** — "기각된 가설 / 막다른 길"과 "결론"을 채운다. 확정 사실은 `specs/`·`adr/`·`reference/`로
   링크한다(리파인 패스에서).

## 자가점검

- [ ] 작업 안 한 개발자가 비약 없이 따라오나? 각 단계에 5요소가 있고 예측이 증거보다 먼저 적혔나?
- [ ] "기각된 가설 / 막다른 길" 절이 채워졌고, 각 막다른 길에 일반화된 교훈이 있나?
- [ ] "안 됨" 같은 요약 대신 구체 값·파일별로 적고, 이미지 시각 내용을 묘사하지 않았나?
