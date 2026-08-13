# Legacy 기준 자료 inventory

이 문서는 T-0003 시점에 접근 가능한 원자료와 [평가 기록](evaluation.md)에 남은 과거 기준선을 연결한다.
외부로 이동한 자료의 위치를 탐색하거나 추측하지 않았고 개인 자료의 시각적 내용은 기록하지 않았다.

## 확인 수준

| 표기 | 의미 |
|---|---|
| `verified` | 2026-08-13 현재 저장소에서 원자료나 filesystem metadata를 직접 확인했다. |
| `documented` | 지속 문서에 당시 결과가 남아 있지만 이번 Task에서 외부 원자료로 다시 확인하지 못했다. |
| `unverified` | 값 또는 현재 원자료를 확인할 수 없으며 추측해 채우지 않았다. |

저장소 루트의 `usb.img`는 접근 가능하고 크기가 3,517,120,512 byte임을 filesystem metadata로 확인했다.
전체 SHA-256 계산은 3.5GB 원본을 모두 읽어야 하고 T-0003 구현 검증에 필요하지 않아 실행하지 않았다.
따라서 아래 모든 legacy 실행의 입력 image SHA-256은 `unverified`다. 현재 `usb.img`가 당시 사용한 byte와
같다는 것도 hash 없이 단정하지 않는다.

## Inventory

### `legacy-output-c2-20260705`

| 항목 | 값 | 확인 수준 |
|---|---|---|
| 설명 | `output_c2` full-recover 기준선 | documented |
| 입력 image SHA-256 | unverified | unverified |
| 입력 image 크기 | 3,517,120,512 byte인 `usb.img`를 사용했다는 기록; 현재 파일 크기는 동일 | documented / verified-size-only |
| JPEG / AVI | 978 / 44 | documented |
| 보고서 record | `report.csv` 978; `report_thumbref.csv` 해당 없음 | documented |
| 보고서 SHA-256 | 둘 다 unverified | unverified |
| 생성 git commit / dirty | unverified / unverified | unverified |
| 주요 옵션 | 당시 `recover.py --time-budget 0`; 나머지 옵션 unverified | documented |
| 실행 날짜 | 2026-07-05 | documented |
| 고정 손상 표본 / 정상 guard | unverified / unverified | unverified |
| 현재 원자료 접근 | 외부 legacy output은 접근 불가; 저장소의 `usb.img`만 접근 가능 | verified |

### `legacy-output-c3-reconstruct-20260721`

| 항목 | 값 | 확인 수준 |
|---|---|---|
| 설명 | `output_c3/jpeg`와 밀림 보정 전 `jpeg_recovered/report.csv` 기준선 | documented |
| 입력 image SHA-256 / 크기 | unverified / 3,517,120,512 byte 기록 | unverified / documented |
| JPEG / AVI | JPEG 970 / AVI unverified | documented / unverified |
| 보고서 record | `report.csv` 970; `report_thumbref.csv` 해당 없음 | documented |
| 보고서 SHA-256 | 둘 다 unverified | unverified |
| 생성 git commit / dirty | unverified / unverified | unverified |
| 주요 옵션 | 무제한 reconstruction 기준선이라는 기록; 정확한 전체 명령 unverified | documented / unverified |
| 실행 날짜 | 2026-07-21 | documented |
| 고정 손상 표본 / 정상 guard | unverified / unverified | unverified |
| 현재 원자료 접근 | 외부 legacy input·output 모두 접근 불가 | verified |

### `legacy-output-c3-v7-aligned-20260722`

| 항목 | 값 | 확인 수준 |
|---|---|---|
| 설명 | `output_c3/jpeg_recovered_aligned_final` v7 전수 기준선 | documented |
| 입력 image SHA-256 / 크기 | unverified / 3,517,120,512 byte 기록 | unverified / documented |
| JPEG / AVI | reconstruct 입력·출력 JPEG 각각 970 / 해당 없음 | documented |
| 보고서 record | `report.csv` 970; `report_thumbref.csv` 해당 없음 | documented |
| 보고서 SHA-256 | 둘 다 unverified | unverified |
| 생성 git commit / dirty | unverified / unverified | unverified |
| 주요 옵션 | `recover.py output_c3/jpeg -o output_c3/jpeg_recovered_aligned_final --time-budget 0 -j 8` | documented |
| 실행 날짜 | 2026-07-22 | documented |
| 고정 손상 표본 | `0x49D4B000`, `0x9C2FA000`, `0xB137F000` 후속 감사 | documented |
| 정상 guard | unverified | unverified |
| 현재 원자료 접근 | 외부 legacy input·output 모두 접근 불가 | verified |

### `legacy-shift-v8-global-hard3-20260723`

| 항목 | 값 | 확인 수준 |
|---|---|---|
| 설명 | `shift_experiments/production_v8_global_hard3` 고정 표본 검증 | documented |
| 입력 image SHA-256 / 크기 | unverified / 3,517,120,512 byte 기록 | unverified / documented |
| JPEG / AVI | 고난도 JPEG 3; AVI 해당 없음 | documented |
| 보고서 record와 SHA-256 | report 형식·record 수·hash unverified | unverified |
| 생성 git commit / dirty | unverified / unverified | unverified |
| 주요 옵션 | `--time-budget 0`, 단일 worker; 전체 명령 unverified | documented / unverified |
| 실행 날짜 | 2026-07-23 | documented |
| 고정 손상 표본 | `0x42E21000`, `0xB8A28000`, `0xC11FF000`; 구조 fallback `0x16E3E000`, `0x98265000`, `0x95AAD000`, `0x42340000` | documented |
| 정상 guard | pre-row 6개: `0x42243000`, `0x49D4B000`, `0xC7FDF000`, `0xCAA08000`, `0xCB254000`, `0xD1800000`; 원본 5개: `0xC8132000`, `0xC813D000`, `0xC8129000`, `0xC811A000`, `0xC811D000` | documented |
| 현재 원자료 접근 | 외부 legacy input·output 모두 접근 불가 | verified |

### `legacy-shift-thumbref-refsdco-20260723-24`

| 항목 | 값 | 확인 수준 |
|---|---|---|
| 설명 | `shift_experiments/refsdco` thumbnail 참조 보정 전수 기준선 | documented |
| 입력 image SHA-256 / 크기 | unverified / 3,517,120,512 byte 기록 | unverified / documented |
| JPEG / AVI | JPEG 970 / 해당 없음 | documented |
| 보고서 record | `report_thumbref.csv`에 대응하는 상태 합계 970; `report.csv` 해당 없음 | documented |
| 보고서 SHA-256 | 둘 다 unverified | unverified |
| 생성 git commit / dirty | unverified / unverified | unverified |
| 주요 옵션 | `thumbref.py output_c3/jpeg output_c3/jpeg_recovered -o <출력> -j 6` | documented |
| 실행 날짜 | 2026-07-23~24 | documented |
| 고정 손상 표본 | `0x42340000`, `0xCA195000`, `0xB8A28000`, `0x43CE6000` | documented |
| 정상 guard | identity 2개라는 수만 기록됐고 object ID는 unverified | documented / unverified |
| 현재 원자료 접근 | 외부 legacy input·output 모두 접근 불가 | verified |

### `legacy-usb-readonly-audit-20260713`

| 항목 | 값 | 확인 수준 |
|---|---|---|
| 설명 | 출력 저장과 full reconstruct 없는 `usb.img` scanner·boundary 감사 | documented |
| 입력 image SHA-256 | unverified | unverified |
| 입력 image 크기 | 3,517,120,512 byte | verified |
| JPEG / AVI | dry top-level JPEG 970 / AVI 44 | documented |
| 보고서 record와 SHA-256 | canonical report 없음 | documented |
| 생성 git commit / dirty | unverified / unverified | unverified |
| 주요 옵션 | 출력 쓰기 없음, boundary·중첩 분류; 정확한 전체 명령 unverified | documented / unverified |
| 실행 날짜 | 2026-07-13 | documented |
| 고정 손상 표본 | damaged 시작 `0xC82F0000`, `0xC8ABD000` | documented |
| 정상 guard | unverified | unverified |
| 현재 원자료 접근 | 저장소의 `usb.img` 접근 가능; 외부 감사 산출물 없음 | verified |

## Ignore 재검토

- `/work/`는 case와 완료 run을 포함하는 기본 실행 데이터이므로 전체 ignore를 유지한다. 정식 fixture와
  문서는 이 경로에 두지 않는다.
- `/output*/`는 새 내부 artifact API가 기존 CLI에 아직 연결되지 않았고 `carve`의 기본 출력도 계속
  `output`이므로 유지한다. 제거하면 현행 CLI 실행 결과가 곧바로 Git 후보가 될 수 있다.
- `/shift_experiments*/`는 현재 writer의 기본 경로는 아니지만 외부 legacy 자료가 보존 중이고 migration·
  import를 수행하지 않았다. 자료가 작업 트리에 다시 놓일 때 우발적으로 추적되는 것을 막기 위해 유지한다.
- `.mcp.json`과 `.claude/settings.local.json` 보호 규칙도 유지한다.

`git check-ignore -v --no-index`로 각 규칙의 probe path가 의도한 줄에 일치함을 확인했다. 이 판단은 ignore
규칙만 유지하는 것이며 외부 자료를 이동·수정·삭제하거나 외부 위치를 영구 경로로 기록하지 않는다.
