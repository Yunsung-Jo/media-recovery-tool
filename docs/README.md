# 문서 맵

| 종류 | 경로 | 용도 | 템플릿 | 목록 | 작성 스킬 |
|------|------|------|--------|------|-----------|
| 프로젝트 개요 | `../README.md` | 사용법, 옵션, 실행 예시 | — | — | — |
| 아키텍처 | `architecture.md` | 폴더 구조, 모듈 책임 | — | — | — |
| 스펙 | `specs/` | 프로그램 단위 동작 방식 | `specs/0000-template.md` | `specs/README.md` | `write-spec` |
| ADR | `adr/` | 비자명한 기술 결정 기록 | `adr/0000-template.md` | `adr/README.md` | `write-adr` |
| 레퍼런스 | `reference/` | 외부 포맷·스펙 지식, 작업 중 확립한 포맷 사실 | `reference/0000-template.md` | `reference/README.md` | `write-reference` |
| 보고서 | `reports/` | 결과·비교·평가의 결론 지향 보고 | `reports/0000-template.md` | `reports/README.md` | `write-report` |
| 조사 기록 | `investigations/` | 분석·디버깅 과정의 시점 기록(랩 노트) | `investigations/0000-template.md` | `investigations/README.md` | `write-investigation` |
| 백로그 | `backlog.md` | off-target 발견(나중에 손댈 문제·버그) 모음 | — | — | — |

> 문서 유형별 **역할 경계·살아있음·생성/갱신 규칙**의 정본은 `.claude/skills/shared/doc-roles.md`,
> 산문·제목 문체는 `.claude/skills/shared/doc-style.md`.
> 템플릿은 **구조**(절 구성)를, 작성 스킬은 **작성 품질**(목표·독자·문체·제목·자가점검)을 다룬다.
> 스펙·ADR·레퍼런스·보고서·조사 기록을 작성·갱신할 때는 해당 작성 스킬을 호출한다.
> 작성·수정 후에는 `review-doc` 스킬로 검토한다 — 수치 재계산·코드 참조·링크·단정/추론·검증 실행을 능동 확인한다(모든 유형 공통).
