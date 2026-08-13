# 현재 상태와 우선 작업

이 문서는 현재 구현·검증 범위, 알려진 한계와 다음 우선순위의 지속 정본이다. 구체적인 파이프라인과
불변조건은 [design.md](design.md), 산출물 계약은 [artifacts.md](artifacts.md), 수치의 입력·시점과 평가
방법은 [evaluation.md](evaluation.md)를 본다.

`Current`는 현재 code와 검증으로 확인된 상태다. `Planned`는 아직 구현되지 않은 로드맵이며 활성 Task가
실제 범위를 정한다. 과거 dataset 수치는 이 문서에서 현재 성능으로 표현하지 않는다.

## Current

### 스냅샷

| 항목 | 현재 상태 |
|---|---|
| 저장소 | `media-recovery-tool` |
| Python 배포 | `media-recovery-tool` 0.1.0 |
| package | `src/media_recovery` |
| CLI | `media-recovery carve`, `reconstruct`, `enhance` |
| Python 지원 | `>=3.10` |
| 자동 회귀 기준선 | 2026-08-13, Python 3.12.13에서 288개 통과 |
| 현재 출력 | command별 디렉터리 트리, JPEG/AVI와 CSV |
| case/run 기반 | 내부 Python API, schema 1.0과 completion seal 구현; 기존 CLI에는 미연결 |
| forensic object/result·NPZ | 구현되지 않음 |
| `render` 명령 | 구현되지 않음 |

T-0001은 package와 CLI 경로를 이전했지만 알고리즘, 여섯 action과 현재 출력 의미를 바꾸지 않았다.
T-0002는 Python code를 변경하지 않고 문서 정본을 Current/Planned로 나누고 기존 `architecture.md`와
`current-state.md`를 완전히 이관해 삭제했다. T-0003은 격리된 `work/`·case/run persistence, strict
JSON/JSONL과 legacy inventory를 구현했다. 다음 계획 작업은 T-0004다.

### 확인된 기능 범위

- 디스크 image의 exact JPEG·AVI signature와 제한된 JFIF/Exif·RIFF/form-type 손상 시작 탐색
- JPEG marker/entropy, 다음 외부 후보와 AVI RIFF/OpenDML 구조를 이용한 경계 계산
- Exif APP1 thumbnail, AVI MJPEG와 일반 중첩 후보 구분
- 손상 JPEG의 byte substitution/deletion/insertion과 bit resync
- DC carry/0 reset 후보, hole 보존과 잔여 MCU placement
- DHT/DQT/SOF/SOS 손상 header의 구조 gate 기반 후보 재구성
- 수락한 편집·resync 절단점 기반 MCU band와 상단 고정 전역 행 위상 보정
- 선택적 Exif thumbnail 참조 밀림·색 cast 후처리와 self-check
- action 6종, `report.csv`, `report_thumbref.csv` 출력
- source SHA-256 case 등록, stage parent lineage, lifecycle·resume와 completed run file seal
- UTF-8/LF strict JSONL의 coordinator ordering과 atomic replace

### 현재 검증 경계

- 전체 자동 테스트 288개가 통과한다. T-0003은 case/run과 artifact I/O test 40개를 추가했다.
- T-0001 합성 fixture에서 package 이전 전후 파일 집합, artifact hash, action과 CSV 안정 필드가 같았다.
- v8 전역 행 placement는 고난도·구조·정상 고정 표본을 통과했지만 `output_c3/jpeg` 970개를 최신 code로
  다시 전수 처리하지 않았다.
- 2026-07-13의 `usb.img` 감사는 읽기 전용 scanner·boundary 실행이었고 full reconstruct나 출력 저장을
  수행하지 않았다.
- 역사적 `output_c2`·`output_c3`·`shift_experiments`는 저장소 밖의 legacy 자료 이름이다. 외부 위치와
  hash를 현재 문서에서 확인하거나 추측하지 않았다. 접근 가능한 `usb.img`는 크기와 접근 여부만 확인하고
  비용 큰 전체 hash는 계산하지 않았다([Legacy inventory](legacy-inventory.md)).

정확한 역사 수치와 해석 제한은 [evaluation.md](evaluation.md)에 있다.

### 알려진 한계

1. **v8 전수 회귀 미실행**
   최신 전역 행 placement는 고정 표본과 정상 guard를 통과했지만 970개 legacy input에서 v7 full-run과
   action, header 선택, identity, 손실 예산을 다시 대조하지 않았다. 원자료 접근과 실행 비용을 확인한 뒤
   별도 범위로 수행해야 한다.

2. **잔여 색 cast**
   thumbnail이 있는 파일은 `enhance`가 행 단위 색 band를 보정할 수 있다. thumbnail이 없거나 segment가
   행 중간에서 갈리는 x방향 변화는 남는다. 2026-07-24에 segment 단위 색을 세 방식으로 시도했지만 모두
   실패했다. 색 cast 경계와 resync segment 경계의 Cb jump가 9.4배로 일치하는 것은 확인했지만, 보정에는
   engine의 실제 segment 경계·thumbnail 절대 기준·밀림 뒤 좌표가 모두 필요하다. pixel signal만으로
   segment를 재탐지한 접근은 정상 guard를 오염시켰으므로 placement mapping이 노출되기 전에는 반복하지
   않는다.

3. **잔여 순환 밀림**
   MCU 0 resync 근거가 없으면 최상단을 이동하지 않고 `phase_cuts=[]`이면 공간 보정을 생략한다. 이 보수적
   계약 밖의 숨은 밀림은 thumbnail이 있는 파일에서만 후처리할 수 있다. 광범위 resync는 행 전체 roll의
   표현력 한계가 있다.

4. **단편화 객체 탐색 없음**
   비연속 cluster에 저장된 JPEG·AVI를 자동으로 이어 붙이지 않는다. 시작·anchor·후속 구조가 함께
   손상된 객체도 현재 자동 탐지 범위 밖이다.

5. **`CLEAN` 이름과 표준 decoder 차이**
   현재 `CLEAN`은 engine action이며 실제 무손상 증명이 아니다. tolerant decoder는 열지만 표준 decoder가
   열지 못하는 입력 byte가 저장될 수 있다. 이 의미는 Planned 직교 상태로 분해하기 전까지 spec의 현행
   계약으로 유지한다.

6. **SOF 시작 후보의 제한**
   `sof_candidates`의 고정 `00 11 08` SOF pattern은 길이 상위 byte까지 손상된 일부 경우를 놓칠 수
   있다. 주변 marker와 dimension gate를 결합한 확장은 별도 알고리즘 Task가 필요하다.

7. **AVI stream/index 수리 없음**
   AVI는 경계를 추출하지만 `idx1`·OpenDML index를 재구성하거나 audio/video stream을 수리하지 않는다.
   일부 player의 재생 시간 오독은 카빙 경계와 별도 문제다.

8. **현재 CLI provenance 연결 부족**
   case/run 기반은 input hash, tool·engine·policy·schema version, environment, options와 lineage를 보존하지만
   기존 CLI가 아직 사용하지 않는다.
   따라서 현재 output directory와 CSV에는 source bit span과 candidate evidence가 없고 같은 이름의 output
   directory 재사용은 이전 파일을 남길 수 있다.

### 현재 사용 시 주의

- 재현 기준선은 빈 출력 디렉터리에서 만들고 reconstruction 비교는 `--time-budget 0`을 사용한다.
- 10 MiB JPEG와 500 MiB AVI 기본 상한보다 큰 파일은 옵션을 올리지 않으면 잘릴 수 있다.
- 개인 자료의 장면·인물·위치는 Task나 보고서에 기록하지 않는다.
- 비용 큰 원본 전수 처리는 고정 표본과 정상 guard 뒤 필요성·범위를 먼저 합의한다.

## Planned — 아직 구현되지 않음

### 다음 우선순위

| 순서 | Planned Task | 목표 | 이 Task 전에는 없는 것 |
|---|---|---|---|
| 1 | T-0004 | forensic domain과 coefficient·validity NPZ schema | source span과 typed array record |
| 2 | T-0005 | 현행 single-best 동작을 보존한 engine 책임 분리 | 새 algorithm이나 결과 개선 |
| 3 | T-0006 | 현재 single-best 결과의 forensic artifact 출력 | N-best 선택 |
| 4 | T-0007 | object boundary와 header N-best | entropy beam |
| 5 | T-0008 | entropy beam search와 component validity | thumbnail 판단 사용 |
| 6 | T-0009 | 반복 placement와 evidence 평가 | AI enhancement |
| 7 | T-0010 | artifact 기반 preview와 thumbnail enhancement 분리 | enhancement를 source로 주장 |

T-0003은 `work/`·run 구조와 접근 가능한 legacy inventory를 함께 검토했다. `/output*/`는 현행 CLI의 기본
출력 보호, `/shift_experiments*/`는 미이관 외부 자료의 우발 추적 방지를 위해 각각 유지했다. `/work/`,
`.mcp.json`, `.claude/settings.local.json` 보호 규칙도 유지한다.

### 장기 지원 방향

- observed byte와 disk absolute offset에서 모든 결과를 역추적한다.
- object boundary, header, entropy, segment와 placement의 복수 hypothesis를 보존한다.
- source bit span, virtual edit, block/component validity, gap·overlap과 반증을 forensic artifact에 기록한다.
- preview와 enhancement를 source-backed 결과에서 분리하고 generated 값을 명시한다.
- 통제 손상 corpus에서 top-1/top-K, source span과 validity를 평가한 뒤 N-best 정책을 확정한다.

이 항목은 승인된 방향이지 현재 완료 상태가 아니다. 구체적인 schema, enum, 기본값과 성능 목표는 각 Task의
구현·검증 전에는 확정된 공개 계약으로 사용하지 않는다.
