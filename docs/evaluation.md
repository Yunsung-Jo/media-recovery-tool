# 평가와 기준선

이 문서는 Media Recovery Tool의 평가 방법, 재현 가능한 기준선과 결과를 해석하는 규칙의 지속 정본이다.
현재 구현 범위와 다음 우선순위는 [status.md](status.md), 알고리즘 불변조건은 [design.md](design.md)를
함께 본다.

`Current`는 지금 실행할 수 있는 자동 검증과 확인된 기준선이다. `Historical`은 당시 입력·코드·옵션에서
관측한 결과이며 현재 성능으로 승격하지 않는다. `Planned`는 아직 구축하지 않은 평가 체계다.

## Current

### 자동 회귀 기준선

T-0002 시작점 `faff8f47c26c908f83f6d0820024bad749d78dab`에서 프로젝트 `.venv`로 다음을 실행했다.

```powershell
.venv\Scripts\python.exe -m pytest
```

- Python 3.12.13
- pytest 9.1.1
- 수집 248개
- `248 passed in 16.61s`

248개 pytest는 CLI dispatch, 카빙 후보·경계·저장, baseline decoder, resync·header 복구·placement와
thumbnail-guided enhancement를 포함한다. Windows `-j 2` 병렬 spawn은 이 248개에 포함된 자동 테스트가
아니라 아래 T-0001 패키지 전환에서 별도로 실행한 smoke 검증이다. 248개 통과는 중요한 코드 회귀 기준이지만
실제 손상 매체에서 객체 누락 0, 최적 복구 또는 source 정확도를 증명하지 않는다.

### T-0003 persistence 기준선

T-0003은 같은 Python 3.12.13 환경에서 case/run·strict JSON/JSONL test 40개를 추가했다. 임시 디렉터리와
작은 합성 source만 사용하며 외부 legacy 자료나 `usb.img`를 처리하지 않는다.

- 전체: `288 passed in 15.84s`
- 새 근접 test: `40 passed in 2.86s`
- 검증 범위: default/override work root, case hash·prefix 충돌, run ID·lineage, lifecycle·resume,
  source start 재검증, completion seal·staging failure, dirty patch, 실제 JSON Schema validation,
  UTF-8/LF ordering·sort key 전순서와 atomic failure
- wheel: project `.venv`의 격리 build 성공, schema JSON 3개 포함 확인

기존 CLI·discovery·reconstruction·enhancement source는 변경하지 않았고 기존 248개가 새 전체 suite 안에서
계속 통과했다. 이 기준선은 persistence 불변조건과 기존 동작 회귀 0을 보여주지만 forensic domain이나 실제
복구 결과 schema가 구현됐다는 뜻은 아니다.

### T-0004 forensic domain·NPZ 기준선

T-0004는 같은 Python 3.12.13 환경에서 forensic domain, Draft 2020-12 object/result/candidate/manifest
schema와 결정적 NPZ test 58개를 추가했다. 임시 case/run과 작은 합성 Y/Cb/Cr array만 사용했고 기존
reconstruction engine, `usb.img`와 외부 legacy 자료는 처리하지 않았다.

- 근접: `.venv\Scripts\python.exe -m pytest tests\test_forensic_artifacts.py -q` →
  `58 passed in 8.51s`
- T-0003 포함 근접: `.venv\Scripts\python.exe -m pytest tests\test_artifacts.py tests\test_forensic_artifacts.py -q`
  → `98 passed in 10.30s`
- 전체: `.venv\Scripts\python.exe -m pytest` → `346 passed in 22.44s`
- 검증 범위: immutable domain/dict·JSONL round-trip, object/candidate ID·fingerprint 결정성, 명시적 좌표와
  불연속 source span, provenance·result 상태 거부, coefficient/block validity·component·span-ref·owner
  정합성, endian canonicalization, pickle/object/unknown/missing array 거부, manifest/NPZ 변조, deterministic
  ZIP metadata·byte, staging/replace atomicity, version/required feature와 기존 completion seal, case source
  bounds, source 좌표 중복, object parent 해소·cycle·깊은 chain, parent discovery object 해소,
  filesystem-normalized owner별 NPZ path 고유성, canonical schema/NPY version, intervention 중복,
  safe path schema, control character·colon/NTFS ADS·symlink와 owner-stage 거부, decode segment reference의
  immutable tuple snapshot
- result 상태: execution/support/decode/selection/header/artifact의 가능한 enum 조합 전체에서 배포 JSON
  Schema와 Python validator의 허용 여부가 정확히 같은지 검사
- wheel: 격리 `pip wheel . --no-deps` build 성공; package의 새 domain/artifact module과 기존 3개를 포함한
  JSON Schema 8개가 wheel data에 포함됨

첫 `--no-build-isolation` wheel 시도는 project `.venv`에 setuptools가 없어 실패했고, 첫 격리 시도는
sandbox network가 build dependency 다운로드를 막아 실패했다. 허용된 network의 격리 build에서
`setuptools>=68` 선언을 사용해 성공했다. 이는 schema/model/I/O 계약과 기존 code 회귀 0을 검증하지만 현행
engine이 forensic record를 실제 출력하거나 N-best를 구현했다는 뜻은 아니다.

### T-0005 reconstruction 책임 분리 기준선

T-0005는 개인 자료 없이 Pillow로 만든 baseline JPEG와 결정적 entropy/header 손상, truncation과
monkeypatch한 공간 보정·worker 예외를 사용해 책임 이동 전 snapshot을 먼저 고정했다. 비교는 모두
`time_budget=0`이고 `recover_sec`만 제외했다.

- 리팩터링 전/후 normalized snapshot SHA-256:
  `d16d2092beb65ba6aa723d6c9d9d54d37ed839f4ef42cd66c467c1e9738bdf95`
- 리팩터링 전 HEAD와 현재 `-j 1`·`-j 2`의 정규화 CLI snapshot SHA-256:
  `becf2619a73034728d9c096ba68ac54189d26f1e194987c85e79d566867db89c`
- 여섯 action·worker error·placement·header 콘솔과 CSV를 포함한 CLI summary snapshot SHA-256:
  `ba4c1e89ad619c1a3c83f73c389ef79daab6198fc47575e5ece90f298e1d0cf8`
- snapshot 범위: `CLEAN`, entropy `RECOVERED`, 공간-only `RECOVERED`, `HEADER_RECOVERED`, `FAILED`,
  `SKIP_UNDECODABLE`, worker `ERROR`; action·상대 경로·원본 보존 여부·info, `recover()` RGB shape/dtype/byte·
  segment·stats와 각 output SHA-256
- output SHA-256은 clean `a6692934…1e17`, entropy `04c62696…e8c`, spatial `db8d02d8…6f75`, header
  `a47fb760…e3ad`, failed `ddc6d7a…2964`, skip `cee221e7…2dd`, error `3462e4ef…e854`로 전후 같다. full hash는
  [고정 baseline](../tests/fixtures/reconstruction/t0005-engine-baseline.json)에 있다.
- 근접: reconstruction façade·result·writer·CLI test 105개 통과
- T-0003/T-0004 artifact: 98개 통과
- 전체: Python 3.12.13에서 363개 통과
- 실제 CLI: 같은 합성 3파일을 `--time-budget 0 -j 1`과 Windows spawn `-j 2`로 실행해 `recover_sec`와
  worker record 순서만 정규화한 CSV, output hash와 action/header 안정 콘솔 요약이 같았다.
- wheel: 격리 build에 새 reconstruction Python module 8개와 기존 배포 schema 8개 포함을 확인했다.
- 내부 result는 `Mapping.items()`를 유지하고 `info_copy()`가 strict JSON artifact handoff를 통과한다. 원본과
  pickle round-trip 뒤의 RGB·nested array 모두 NumPy write flag 재활성화를 거부한다. Mapping 내용 동등성,
  pre-frozen wrapper·bytearray·segment DC의 재-snapshot과 unsupported mutable 값 거부도 검증한다.

이 기준선은 single-best 알고리즘·legacy byte가 책임 이동 전후 같고 계산 결과와 저장 경계가 분리됐음을
보여준다. 복구 정확도 개선, forensic record 실제 출력이나 N-best 구현을 뜻하지 않는다.

### 패키지 전환 동등성

T-0001은 같은 Python 3.12.13, Pillow 12.3.0, NumPy 2.4.6, Numba 0.66.0 환경에서 이전 전 243개와 이전 후
248개 테스트를 통과했다. 개인 자료가 아닌 합성 640×480 baseline JPEG, Exif thumbnail과 최소 AVI fixture로
이전 CLI와 현재 통합 CLI를 비교했다.

- 비교: 전체 파일 집합 9개, JPEG/AVI 산출물 6개의 SHA-256, action, CSV field·record와 안정 필드
- 제외: `recover_sec`, `secs`, 병렬 worker 완료 순서
- 정규화 snapshot SHA-256:
  `BA0D846972DB205C57A1250D596A1D40481125CD0A40EF71389295D2F7506C18`
- 이전과 이후 snapshot 일치
- `reconstruct --time-budget 0 -j 2`, `enhance -j 2` Windows spawn 성공

이 기준선은 package·CLI 이전의 동작 동등성을 보여주며 복구 정확도 자체의 ground truth가 아니다.

### 평가 원칙

1. 비교 실험과 품질 기준선의 reconstruction은 시간 제한에 따른 후보 수 차이를 없애기 위해
   `media-recovery reconstruct --time-budget 0`을 사용한다.
2. 성능 최적화는 같은 입력의 파일 집합, action, 안정 report 필드와 산출물 hash가 일치할 때만 동작
   동일로 판단한다. wall-time budget 실행은 속도 변화가 결과를 바꿀 수 있으므로 동일성 입력으로 쓰지
   않는다([ADR 0003](adr/0003-recover-perf-optimization.md)).
3. 정답 원본이 없는 실제 손상 표본은 구조 타당성, 자동 지표와 기술적 육안 판정을 함께 사용한다. 자동
   지표 하나나 RGB가 좋아 보인다는 사실만으로 source 복구 성공을 주장하지 않는다.
4. 실패 양상을 대표하는 고정 손상 표본과 정상 회귀 guard를 함께 사용하고, sample별 예상 결과를 실험
   전에 정한다.
5. header 후보는 픽셀 통계가 아니라 clean run, entropy 소비율과 자체 header 우월 비교 같은 구조 gate로
   평가한다([ADR 0006](adr/0006-header-recovery-structural-gates.md)).
6. placement는 잔차 감소뿐 아니라 owner 유일·단조성, 원래 크기, `ceil(전체 MCU·0.05)` 손실 한도와
   정상 identity를 함께 검증한다. 현재 구현은 소형 이미지에도 한 MCU 행 예외를 두지 않는다
   ([ADR 0011](adr/0011-resync-segment-mcu-alignment.md)).
7. thumbnail-guided 보정은 적용 행 self-check와 정상 identity를 함께 사용하며 개선되지 않은 회차를
   rollback한다([ADR 0012](adr/0012-thumbnail-reference-correction.md)).
8. 고정 표본과 정상 guard가 일치한 뒤 실제 필요성이 있을 때만 비용 큰 전수 처리를 수행한다.

### 현재 지표의 해석

| 지표 | 현재 의미 | 증명하지 않는 것 |
|---|---|---|
| `gray_*` | 평탄하고 무채색인 pixel 비율 | source 미복구 범위 그 자체 |
| `undec_*` | RGB 128 부근의 현재 미복구 회색 비율 | coefficient 또는 source span 정확도 |
| `ops` | byte 편집과 resync 횟수 합 | 공간 placement 변화량 |
| `mcu_ins`, `mcu_drop` | 최종 배치의 빈 slot과 유실 source MCU | 실제 물리 손실 원인 |
| `action` | 현재 single-result 저장 분기 | 실제 무손상·원본 복구의 forensic 증명 |
| thumbnail match/self-check | 후처리 수락 근거 | header/resync source 진위 |

DC=0 resync는 실제 콘텐츠를 디코드해도 chroma offset 때문에 `gray`를 높일 수 있어 `undec`를 함께
도입했다. 반대로 `undec` 감소만으로 source bit와 placement가 옳다고 단정하지 않는다.

## Historical — 현재 성능으로 해석하지 않음

아래 수치는 실행 시점의 dataset/run 이름과 옵션을 붙여 보존한다. `output_c2`, `output_c3`와
`shift_experiments`는 2026-08-12 이후 저장소 밖에 보존된 legacy 자료의 논리 이름이다. 외부 절대 경로는
확인하지 않았고 영구 문서 계약으로 만들지 않는다. 이 절은 T-0001 시점 `current-state.md`의 검증 수치와
파일별 기술 기록을 완전히 흡수했으며, 해당 legacy 문서는 T-0002에서 삭제했다.
T-0003에서 확인한 원자료 접근성, hash·record의 확인 수준과 고정 object ID 목록은
[Legacy 기준 자료 inventory](legacy-inventory.md)에 있다.

### 2026-07-05 `output_c2` full-recover 기준선

당시 `usb.img`를 다시 카빙한 뒤 `recover.py --time-budget 0`으로 실행한 역사적 결과다. 현재 명령으로
대응하면 `media-recovery reconstruct --time-budget 0`이지만 현재 code를 다시 실행한 결과는 아니다.

| 항목 | 값 |
|---|---:|
| JPEG | 978 (AVI 내부 MJPEG frame 제외 후) |
| AVI | 44 (당시 알려진 AVI 손실 0) |
| usable (`RECOVERED + HEADER_RECOVERED + CLEAN`) | 884 |
| 처리 오류 | 0 |

action은 `CLEAN 187`, `RECOVERED 611`, `HEADER_RECOVERED 86`, `FAILED 25`,
`SKIP_UNDECODABLE 69`, `ERROR 0`이었다. 합계는 978이다. 이 값은 현행 usable 수치가 아니며 같은
`usb.img`, 원자료와 무제한 옵션으로 재계산하지 않고 최신 성능과 비교하지 않는다.

### 2026-07-21 `output_c3` reconstruct 입력 기준선

`output_c3/jpeg`와 밀림 보정 전 `jpeg_recovered/report.csv`는 파일명 집합이 같은 970개였다.

- action: `CLEAN 186`, `RECOVERED 613`, `HEADER_RECOVERED 92`, `FAILED 29`,
  `SKIP_UNDECODABLE 50`, `ERROR 0`
- usable: 891
- `output_c2`와 공통 파일: 942
- inventory 차이: 36개 제거, 28개 추가

따라서 884에서 891로 바뀐 값을 알고리즘 개선 7개로 해석하지 않는다. 입력 집합 자체가 다르다.

### 2026-07-22 v7 밀림 보정 전수

절단 band 중심 v7 구현으로 `output_c3/jpeg` 970개를 처리한 역사적 전수 기준선이다. 당시 명령은 이전
root wrapper를 사용했으며 현재 repository에는 그 wrapper가 없다. 당시 산출물의 논리 이름은
`output_c3/jpeg_recovered_aligned_final`이고 크기는 583.3 MiB였다.

```powershell
.venv\Scripts\python.exe recover.py output_c3\jpeg `
  -o output_c3\jpeg_recovered_aligned_final --time-budget 0 -j 8
```

| 항목 | 값 |
|---|---:|
| 입력·report 행·출력 JPEG | 각각 970 |
| usable | 891 |
| 보정 적용 파일 | 412 |
| 보정 band | 5,985 |
| MCU 삽입 | 191,991 |
| MCU 유실 | 184,889 |
| 최종 안전 기각 | 6 |
| 처리 오류 | 0 |
| 실행 시간 | 40분 49초 |

마지막 multi-band winsorize gate는 기존 결과 중 그 분기에 도달한 `shift_reject` 9개만 같은 옵션으로
다시 처리해 반영했다. 3개는 보정을 수락하고 6개는 계속 기각됐으며 나머지 961개가 실행하는 경로에는
변경이 없었다.

action은 보정 전과 같은 `CLEAN 186`, `RECOVERED 613`, `HEADER_RECOVERED 92`, `FAILED 29`,
`SKIP_UNDECODABLE 50`이었다. 파일명 집합·action·entropy operation·header 선택 불일치는 0이다. 보정 0인
558개는 기존 결과와 SHA-256이 모두 같고 보정 412개는 모두 달라졌다. `RECOVERED`와
`HEADER_RECOVERED` 705개는 Pillow full load에 성공했다. 원본을 보존하는 `CLEAN` 8개와 `FAILED` 14개의
표준 개봉 실패, `SKIP_UNDECODABLE` 50개는 보정 전과 같은 기존 한계다.

`RECOVERED`의 평균 `undec`는 0.347에서 0.045로 줄었다. 전체 `worse`는 보정 전 61개에서 87개로 26개
늘었고, 보정 때문에 추가된 `undec_after`의 파일별 최대 증가는 0.050, 전체 평균은 0.0058이다. 이는 고정
canvas에서 밀림 제거를 우선해 허용한 MCU gap·loss다. 최대 파일별 손실 비율은 5.36%였고 해당 소형
파일의 한 MCU 행 21개 허용 범위 안이었다. header를 직접 읽을 수 있는 보정 파일 398개는 모두
`max(한 MCU 행, 전체 MCU의 5%)` 한도를 독립 재검산해 통과했다.

전수 안전 기각을 기술적으로 감사해 multi-band 밀림이 남은 `0x49D4B000`, `0x9C2FA000`,
`0xB137F000`을 winsorize gate로 추가 보정했다. 각각 2·6·4 band가 정렬됐고 삽입/유실은 44/44,
210/210, 32/24 MCU다. 나머지 6개는 2-band 자연 경계·손상선 또는 유효 영역 부족으로 기존 기각을
유지했다.

이 결과 뒤에 상단 고정 전역 행 보정을 추가한 v8은 970개 전수를 다시 실행하지 않았으므로 이 표를 최신
구현의 전수 성능으로 표현하지 않는다.

### 2026-07-23 v8 고정 표본과 정상 guard

사용자가 지정한 고난도 3개는 `--time-budget 0`, 단일 worker에서 상단 고정 전역 절대 행 위상 보정으로
검증했다. 논리 run 이름은 `shift_experiments/production_v8_global_hard3`다. 자동 지표와 기술적 육안
판정에서 반복 좌우·순환 밀림이 제거되고 상단 행이 유지됐다. 색/DC band, 얇은 손상선과 회색 gap은 별도
문제로 남았다.

| 파일 | `undec_before`→`undec_after` | entropy ops | global pass | MCU 삽입 | MCU 유실 |
|---|---:|---:|---:|---:|---:|
| `0x42E21000` | 0.434→0.001 | 6 | 2 | 30 | 30 |
| `0xB8A28000` | 0.994→0.045 | 45 | 5 | 1,561 | 1,535 |
| `0xC11FF000` | 0.908→0.034 | 22 | 3 | 2,113 | 2,042 |
| 합계 |  | 73 | 10 | 3,704 | 3,607 |

저장 JPEG를 원본 4:2:2 MCU grid(16×8)에서 같은 감사로 비교한 잔차 합은 다음과 같다. 열 순서는
`exact / soft / multistrip / relaxed`다.

| 파일 | 이전 v7 | v8 저장 JPEG |
|---|---:|---:|
| `0x42E21000` | 0 / 0 / 0 / 0.063431 | 0 / 0 / 0 / 0 |
| `0xB8A28000` | 0 / 0.232080 / 0.454703 / 0.834903 | 0 / 0 / 0 / 0 |
| `0xC11FF000` | 1.358856 / 1.628234 / 0.549468 / 1.732591 | 0 / 0 / 0 / 0.597739 |

`0xC11FF000`의 v8 raw RGB는 네 잔차가 모두 0이고 저장 JPEG의 relaxed 4건만 4:2:0 재인코딩 뒤
나타났다. exact·soft·multistrip은 저장 뒤에도 모두 0이며 저장 결과는 검증된 실험 출력과 pixel 단위로
같았다.

구조적 국소 fallback 표본 3개는 global 후보 0 뒤 기존 계획을 정확히 유지했다. `0x16E3E000`은
`(57,32,57,+74)`, `0x98265000`은 `(16,16,31,-14)`, `0x95AAD000`은
`(178,178,180,+8)`과 `(211,211,214,+2)`를 적용했다. `0x42340000`은 정상 guard가 아니라 row 181
아래에 실제 `+83` MCU 순환 밀림이 있던 양성 표본으로 재분류했고, 상단 `y<1448`을 pixel 단위로
보존하면서 세 감사 합을 0.179345에서 0으로 줄였다.

정상 pre-row guard 6개(`0x42243000`, `0x49D4B000`, `0xC7FDF000`, `0xCAA08000`,
`0xCB254000`, `0xD1800000`)는 global/local/legacy가 모두 identity였고 출력 PNG도 입력과 pixel 단위로
같았다. 정상 원본 guard 5개(`0xC8132000`, `0xC813D000`, `0xC8129000`, `0xC811A000`,
`0xC811D000`)도 최신 전체 경로에서 `spatial_changed=0`, MCU 삽입·유실 0이었다. 명시적인
`phase_cuts=[]`는 pixel 상관만으로 정상 이미지를 이동시키지 않도록 공간 단계를 우회했다.

이 결과는 고정 표본 검증이지 970개 전수 결과가 아니다. 장면·인물·위치는 문서화하지 않는다.

### 2026-07-23~24 thumbnail 참조 보정

당시 `thumbref.py output_c3/jpeg output_c3/jpeg_recovered -o <출력> -j 6`으로 970개를 처리했다.
현재 대응 명령은 `media-recovery enhance`지만 이 표는 당시 실행 결과다. 상세 실험의 논리 이름은
`shift_experiments/refsdco`다.

| 판정 | 수 | 비고 |
|---|---:|---|
| corrected | 250 | 밀림·색 보정 |
| identity | 2 | 무변경 guard 2개, 출력 byte 동일 확인 |
| rollback | 0 | self-check 실패 없음 |
| skip | 718 | thumbnail 없음·소형·SOF·정합·읽기 skip |

정합 skip 4개는 회색 지배·평탄·극손상으로 당시 기준에서 정당했다. 보정본은 복구본 quantization table과
원본 4:2:2 sampling을 사용해 저장했다. v8 참조가 있는 고정 표본 `0x42340000`에서 루마 MAE는
12.19→2.14, 행 일치율은 0.686→0.958로 개선됐다.

2026-07-24의 반복 보정 전 1회 `apply_shift`는 광범위 resync에서 큰 band를 놓쳤다. 예를 들어
`0xCA195000`은 79 MCU band가 남았다. 재추정→적용을 수렴까지 반복하되 각 회차 self-check가 개선될 때만
채택하도록 바꾼 뒤 상태 분포와 identity guard는 그대로여서 회귀가 없었다. corrected 250개 중 112개가
2회 이상 반복했다. 표본의 남은 밀림 `[행/최대 MCU]`는 `0xCA195000` 5/79→3/2,
`0xB8A28000` 9/38→0/0, `0x43CE6000` 112/78→4/42였다. 개선이 없는 큰 band는 정합 모호성과 행 전체
순환 roll의 표현력 한계 때문에 보수적으로 남겼다.

이 수치는 해당 legacy input과 당시 출력의 결과이며 현재 전체 성능 주장이 아니다.

### 2026-07-13 `usb.img` 읽기 전용 감사

3,517,120,512 byte input을 출력 저장과 full reconstruct 없이 감사했다. raw `FF D8 FF` 1,830개를 구조
gate로 줄이고 손상 anchor 후보를 더한 결과 시작 후보는 1,671개였다. 별도의 4 KiB 정렬
DQT/SOF/DHT/SOS 구조 census에서 일관된 JPEG 구조 680개를 찾았고 모두 당시 hit에 포함됐다.

| 항목 | 값 | 비고 |
|---|---:|---|
| exact JPEG 시작 | 1,625 | 구조 후보와 내장 JPEG 포함 |
| damaged JPEG 시작 | 2 | JFIF/Exif anchor·후속 구조·EOI 검증 |
| AVI 시작 | 44 | 모두 exact; 손상 RIFF/form 추가 0 |
| dry top-level JPEG | 970 | 파일 쓰기 없이 boundary와 중첩 분류만 실행 |
| dry top-level AVI | 44 | 기존 AVI 44개와 boundary 변경 0 |
| Exif 내부 JPEG | 556 | `--save-thumbnails` 없이 분류만 집계 |
| 처리 오류 | 0 | dry boundary 실행 |

이전 `output_c2`의 JPEG offset과 비교하면 36개가 빠지고 28개가 추가돼 `978 - 36 + 28 = 970`이다.

- 제거 36개: 비임베디드 `SKIP_UNDECODABLE` false positive 26개, Exif 내부 preview 재분류 10개
- 추가 28개: 무효 DQT/DHT 등 header segment가 덮던 exact JFIF 시작 25개, 기존 과다 범위에서 분리된
  exact JPEG 1개(`0xC15DE6CB`, 76,919 byte), damaged 시작 2개

header 의미 분할 후보 25개는 boundary 계산상 EOI 완결 17개, Pillow load 성공 16개, verify-only 3개,
실패 6개였다. 관련 부모 12개는 모두 strict 연속 header walk에 실패했고(DQT 9, DHT 3), tolerant
decoder로 열리는 부모 6개 중 5개는 내부 자식의 decode byte와 같았다. 부모 조각도 모두 별도 보존했으며
출력 범위 사이 8,745 byte gap에는 exact JPEG 시작이 없었다. 25개 모두 SOI 직전 12 byte가 같은 record
envelope 형식이고 `usb.img` 전체에 같은 형식의 exact JPEG hit이 281개 있었다. 이 후보들은 Exif/AVI
내부가 아니고 FAT JPG entry와 직접 mapping되지 않아 구조·decode 근거를 가진 보수적 raw 분리로
취급했다.

FAT32는 runtime 탐지에 쓰지 않고 독립 감사에만 사용했다. strict archive JPG entry 677개 중 명확한
149개 일치로 4,096 byte cluster와 data start `0x011FF000`을 역검증했다. damaged 시작
`0xC82F0000`과 `0xC8ABD000`은 모두 4 KiB 정렬이고 `scan_start=offset+0x26F`이며, 각각 FAT 선언 크기
21,938 byte·19,676 byte가 EOI exclusive 크기와 정확히 일치했다. 그러나 당시 자동 reconstruct 판정은
둘 다 `SKIP_UNDECODABLE`이므로 “사용 가능한 사진 2개 복구”로 해석하지 않는다.

exact RIFF 시작은 45개였고 그중 `AVI ` form은 44개였다. AVI 44개는 모두 `hdrl`/`avih` 구조를 다시
확인했으며 RIFF 또는 form type 한쪽의 1~2 byte 손상 model에서 추가 AVI는 없었다. 이는 RIFF와 form/구조가
함께 손상되거나 덮어써진 AVI, 단편화 AVI까지 포함한 누락 0 증명이 아니다.

기본 10 MiB JPEG hard limit에 닿은 exact 후보는 10개였다. 알려진 다음 외부 boundary는 모두 상한 밖이고
FAT JPG 선언 크기의 최대값은 5,936,612 byte였으므로 확인된 FAT 파일 누락 근거는 없었다. 다만 FAT에
mapping되지 않거나 단편화된 큰 파일은 옵션을 올려 별도 검증해야 한다.

출력 쓰기를 막은 동일 프로세스 반복에서 scanner 7.524~8.790초, boundary·중첩 분류
0.621~0.648초, 합계 8.172~9.411초였다. 비교 대상 이전 구현은 scanner 약 3.0초,
boundary·중첩 분류 약 39~41초, 합계 약 42~44초였다. 구조 기반 damaged 후보 검증으로 scanner 비용은
늘었지만 정렬 index·단일 active range·segment-aware 선형 entropy walk로 전체 시간은 줄었다. 밀집
`FF 00`과 반복 inter-scan 합성 입력도 크기 2배에 실행 시간이 대략 2배가 되는 선형 거동을 확인했다.
이 값은 OS cache와 장치 상태의 영향을 받으며 파일 저장 시간을 포함하지 않는 참고값이지 현재 일반 성능
보증이 아니다.

## 재시도하지 않을 평가 접근

- DC 물리 범위를 resync 후보의 수락·기각 filter로 사용하지 않는다. DC reset offset과 drift 때문에
  정당 후보도 자주 범위를 벗어났다([ADR 0005](adr/0005-scaled-accept-threshold.md)).
- 렌더 통계, pixel 상관 또는 corpus geometry 빈도로 header 후보를 기각하지 않는다. 손상·밀림이 진짜
  후보의 지표를 더 나쁘게 만들 수 있었다.
- 다음 JPEG signature를 무조건 부모 경계로 사용하지 않는다. Exif thumbnail과 metadata payload를
  외부 객체로 오인한다.
- plain decoder 개수만으로 carve 회귀를 판단하지 않는다. header 복구 보존과 AVI 내부 MJPEG 재분류를
  포함한 전체 경로를 비교한다.
- bit 소비량 또는 skew 하나로 resync 위치를 선택하지 않는다. 짧게 우연히 맞은 정렬이 정상 guard까지
  악화시켰다.
- DHT donor를 DQT 계열별로 고르지 않는다. 이 corpus의 DHT 후보는 Annex-K 전형 table이고 DQT는 파일별
  자원이다.
- segment 색을 pixel edge로 다시 찾거나 경계 연속성만 누적해 보정하지 않는다. 색 재탐지는 콘텐츠
  chroma edge와 segment 경계를 구분하지 못해 정상 guard를 오염시켰다. 경계 연속성 누적은 절대 기준이
  없어 자연 gradient에서 발산하고 segment 경계의 green jump를 바꿔 밀림 보정까지 건드렸다. engine의
  실제 segment 경계, thumbnail 절대 기준과 밀림 뒤 좌표를 함께 갖출 때만 다시 시도한다.

이 항목은 후속 Task가 같은 실패를 반복하지 않게 하는 설계 제약이다. 새로운 ground truth나 구조 evidence가
생기면 별도 Task에서 가설·guard와 함께 재검토한다.

## Planned — 아직 구현되지 않음

### 통제 손상 corpus

정상 baseline JPEG에 substitution, deletion, insertion, bit shift, marker 길이·DHT·DQT·SOF·SOS 손상,
truncation과 가짜 EOI를 seed와 함께 주입하는 합성 corpus를 계획한다. 이 corpus, generator와 정답 record는
현재 repository에 구현되어 있지 않다.

목표 지표는 다음과 같다.

- object precision/recall과 boundary error
- header hypothesis top-1/top-K
- coefficient와 block/component validity 정확도
- source span, virtual edit와 placement/gap 정확도
- 정상 입력의 자동 수정·회귀 0
- 동일 input·version·policy·seed에서 worker 수와 무관한 선택 결과

### 실제 손상 고정 표본

정답 원본이 없는 고정 표본은 구조 타당성, source span·owner 불변조건, 자동 지표와 기술적 육안 판정을
함께 사용한다. 개인 사진의 장면·인물·위치는 기록하지 않고 object ID와 색 cast·밀림·gap 같은 기술적
결함만 기록한다.

### 전수 검증 gate

후속 알고리즘 변경은 다음 순서를 목표로 한다.

1. 목표와 file별 예상 결과를 사전에 고정한다.
2. 통제 손상 corpus와 정상 guard에서 구조·정답 회귀 0을 확인한다.
3. 실제 고정 표본의 자동 지표와 기술적 육안 판정이 일치하는지 확인한다.
4. 필요성과 예상 비용을 알린 뒤에만 `--time-budget 0` 전수를 실행한다.
5. input hash, tool·engine·policy·schema version, environment, 옵션과 결과 artifact를 run provenance에 묶는다.

case/run과 legacy inventory가 구현되기 전에는 과거 dataset의 hash·record 수·report hash·git 상태를
추측해 채우지 않는다. 접근할 수 없는 값은 `unverified`로 남기며 이 작업은 T-0003 범위다.
