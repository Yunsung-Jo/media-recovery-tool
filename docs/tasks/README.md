# Task 운영 규칙

Task는 한 작업의 문제, 목표, 범위, 불변조건, 검증과 실제 결과를 묶는 실행 계약이다. Task는 문서이므로
`docs/tasks/` 아래에서 관리한다.

## Task와 지속 문서의 차이

- Task: 이번 작업에서 무엇을 왜 바꾸고 어떻게 검증하는가
- `design.md`: 현재 시스템이 어떻게 동작하고 왜 그런 구조인가
- `artifacts.md`: 현재 산출물과 provenance 계약
- `evaluation.md`: 현재 평가 방법과 기준선
- `status.md`: 현재 검증 범위, 한계와 다음 우선순위
- 테스트: 반드시 유지해야 하는 실행 동작
- run artifact: 대량 실행의 입력, 설정, 상세 결과와 로그

완료 Task는 당시 작업 기록이다. 현재 코드나 지속 문서보다 우선하지 않는다. Task에서 확정된 장기 지식은
완료 전에 해당 지속 문서로 옮긴다.

## 디렉터리

```text
docs/tasks/
├── README.md
├── active/
└── completed/
    └── 2026/
        ├── T-0001-project-identity-and-package-layout.md
        ├── T-0002-current-planned-document-structure.md
        └── T-0003-work-run-lineage-lifecycle-and-jsonl.md
```

- 동시에 활성화하는 Task 수를 최소화한다.
- 완료한 Task는 완료 연도 아래로 이동한다.
- 미래 Task 파일을 상세 내용 없이 미리 대량 생성하지 않는다.
- Task ID는 `T-`와 4자리 숫자를 사용하며 재사용하지 않는다.

## 상태

front matter의 `status`는 다음 중 하나를 사용한다.

- `proposed`: 방향 검토 중
- `ready`: 범위와 완료 조건이 합의되어 시작 가능
- `in_progress`: 구현 또는 검증 중
- `blocked`: 외부 결정이나 자료 없이는 진행 불가
- `completed`: 목표와 필수 검증 완료
- `cancelled`: 수행하지 않기로 결정

상태만으로 완료를 주장하지 않는다. `completed` Task에는 실제 결과와 검증 명령·수치를 기록한다.

## 기본 템플릿

```markdown
---
id: T-0000
title: 작업 제목
status: proposed
type: refactor
depends_on: []
---

# T-0000. 작업 제목

## 문제

## 목표

## 범위

## 비범위

## 유지할 불변조건

## 작업 계획

## 검증

## 결과

## 지속 문서 반영

## 후속 작업
```

복구율, header, resync, placement, 색 또는 영상 품질처럼 정답 원본이 없는 실험에는 다음을 추가한다.

```markdown
## 가설

## 고정 손상 표본

## 정상 회귀 가드

## 파일별 예상 결과

## 자동 지표

## 기술적 육안 판정

## 결론
```

## 작성 원칙

1. 목표와 성공 기준을 구현 전에 쓴다.
2. `범위`와 `비범위`를 모두 써서 관련 있어 보이는 작업의 자동 유입을 막는다.
3. 유지해야 할 기존 동작과 안전 조건을 불변조건으로 쓴다.
4. 검증하지 못한 항목은 완료로 표시하지 않고 이유를 기록한다.
5. 비교 실험은 입력, 옵션, 코드 버전과 baseline을 식별할 수 있어야 한다.
6. 대량 로그, 이미지와 배열은 Task에 넣지 않고 run artifact를 참조한다.
7. 실패 실험은 재시도 방지 또는 후속 설계 제약 가치가 있을 때만 결론을 보존한다.
8. Task 범위를 바꾸는 발견은 사용자와 합의한 뒤 문서를 갱신한다.
9. 단순 리팩터링에 불필요한 실험 양식을 강제하지 않는다.
10. 문서 수치, 코드 링크와 검증 주장은 실제 원자료로 확인한다.

## 완료 절차

1. 범위에 해당하는 구현을 마친다.
2. 가까운 테스트와 가능한 전체 테스트를 실행한다.
3. 실제 결과와 생략한 검증을 Task에 기록한다.
4. 장기 지식을 지속 문서에 반영한다.
5. 남은 일은 현재 Task를 억지로 확장하지 않고 후속 Task로 분리한다.
6. `status: completed`로 바꾸고 `completed/<연도>/`로 이동한다.

## 계획된 로드맵

전체 방향과 각 Task의 비범위는 [`transition-plan.md`](../transition-plan.md)를 따른다.

| Task | 제목 |
|---|---|
| [T-0001](completed/2026/T-0001-project-identity-and-package-layout.md) | 프로젝트 정체성과 패키지 구조 |
| [T-0002](completed/2026/T-0002-current-planned-document-structure.md) | Current/Planned 문서 골격과 기존 지식의 점진적 이관 |
| [T-0003](completed/2026/T-0003-work-run-lineage-lifecycle-and-jsonl.md) | `work/`, stage run lineage·lifecycle과 JSONL artifact 계약 |
| T-0004 | 포렌식 도메인 모델과 NPZ schema |
| T-0005 | 기존 복구 엔진의 동작 보존 책임 분리 |
| T-0006 | single-best 포렌식 artifact 출력 |
| T-0007 | boundary/header N-best |
| T-0008 | entropy beam search |
| T-0009 | placement 반복 평가 |
| T-0010 | preview와 enhancement 분리 |

현재 활성 Task는 없다. 다음 계획 작업은 T-0004다.
