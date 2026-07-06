---
name: review-doc
description: Use after writing or editing any docs/ document (spec, ADR, report, investigation, reference), before committing or finishing a branch. Runs common cross-type verification no write-* self-check has — recompute numbers (incl. figure/montage captions) from source, grep code refs, open links/assets, actually run the claimed validation, separate fact from inference, flag common prose-style violations. Type-specific checks stay in each write-* self-check, not here. Catches precision defects that survive authoring.
---

# 문서 검토

**목적:** 작성자가 자기 글에서 못 보는 정밀도 결함을, 의도를 모르는 검증자의 눈으로 잡는다. 작성
자가점검은 "갖췄나"의 수동 체크, 이 스킬은 **"직접 다시 해봤나"의 능동 검증**. **공통 검증만 한다** —
유형별 검사(spec 모듈 grep, ADR 대안/Superseded 등)는 각 write-* 자가점검의 몫이다.

## 언제 쓰나

write-*로 문서를 작성·수정한 직후, 그리고 커밋·머지(리파인 패스 — main `--squash` 직전) 전. **수치·코드
참조·검증 결과·링크를 건드린 변경**에 한정한다(오타·표현 한 줄 수정엔 과하다).

## 규칙 — "확인했다"가 아니라 "해봤다"

1–4는 직접 재실행, 5–6은 검증자의 눈으로 재독하되 필요하면 재실행한다.

### 1. [필수] 수치는 원자료에서 재계산 (본문 + 그림 캡션·몽타주 라벨)
`report.csv`·로그·파일 크기·타임스탬프에서 다시 산출해 대조한다. 곱·합·비율·배수가 특히 자주 틀린다
(규모 크면 스크립트로 독립 재산출).
- 예: "5,632 × 52 ≈ 22만"으로 적었으나 실측은 221,961회 — 어림식이 실측과 어긋나면 그 이유를 적는다.

### 2. [필수] 코드 참조는 grep으로
함수명·라인·시그니처·상수를 코드에서 직접 대조. 경로는 패키지 접두까지(예: `carver/jpegdecode.py:172`가
지금도 그 함수의 시작 줄인지).

### 3. [필수] 링크·asset은 대상을 연다
상호 참조·README 목록·reference 링크의 대상 파일·앵커, 이미지 asset 경로(`docs/reports/assets/*.webp`)가
실제 존재하는지. "아직 없는 미래 문서" 링크가 깨진 채 남기 쉽다.

### 4. [필수] 주장한 검증을 실제로 실행
"무손실·회귀 없음·통과·동일"은 해당 검증(테스트·빌드·diff)을 **지금 돌려** 뒷받침하고 결과를 기록한다.
- **investigation 예외:** 랩노트는 스냅샷이라 사후 검증을 삽입하지 않는다 — 미실행 주장은 "추론"으로
  약화하고 계산 오류·오타만 정정한다.

### 5. [필수] 단정과 추론을 구분
측정·실행으로 확인한 것만 단정. 정황 판단은 "추론"으로 표시하고 근거를 남긴다.
- ✗ (계측 없이) "대역폭 포화"로 단정.
- ✓ "진동 소멸 + 실전 배수 > 단일 배수로 추론."

### 6. [권장] 출처·단위·용어 일관
한 수치가 문서 전체에서 같은 출처·기준인지(계측 오버헤드 포함 193초 vs 순수 187.7초가 섞이지 않게),
같은 개념의 용어 표기("회색/gray/잔존")가 일관되는지 본다.

### 7. [필수] 공통 문체 위반 확인
[doc-style](../shared/doc-style.md) 위반(의인화·감정어·지어낸 비유·이미지 시각 내용 묘사·'규명'/'~에서
~로' 과정 포장 제목)이 없는지 훑는다.

## 절차

`git diff`로 건드린 문서를 추려 규칙 1–7을 실제로 수행하고 고친다. 바뀐 수치는 **인용하는 다른 문서·
README까지 전파**하되, investigation 스냅샷은 drift로 갱신하지 않는다(새 기록). 신규 문서가 해당 README
표에 올랐는지 확인한다.

## 자가점검

- [ ] 본문 + 캡션·라벨 수치를 원자료에서 재계산했나(곱·합·비율 포함)? 코드 참조를 grep으로 대조했나?
- [ ] 링크·asset 대상이 실존하나? "통과/무손실/동일" 검증을 실제로 실행했나?
- [ ] 단정이 모두 측정·실행으로 뒷받침되나(추론은 추론 표시)? 바뀐 수치를 인용 문서까지 전파했나?
