# 백로그

작업 중 발견한 **off-target 문제·버그** — 현재 목표와는 다르지만 나중에 손댈 가치가 있는 항목을 모은다.
즉흥적으로 쫓지 않고 여기 적어 두며, 현재 브랜치의 조사 기록은 한 가지 목표만 다루도록 유지한다.

- 항목을 새 브랜치로 다루기 시작하면, 그 설명이 [experiment-loop](../.claude/skills/experiment-loop/SKILL.md) 1단계(문제 정리)의 출발점이 된다.
- **포맷 사실**은 여기가 아니라 `reference/`에, **현재 목표 자체를 다시 잡게 만드는 발견**은 현재 조사 기록에 적는다.
- 2026-07-02 발견 세션 2회로 항목이 늘어, 의존성·영향 분석에 따라 **작업 순서(W1~W6)**로 재편했다(같은 날 정리).
  근거 실험은 [사각지대 조사](investigations/2026-07-02-recovery-tool-blindspots.md)·[blocky·밀림 조사](investigations/2026-07-02-blocky-shift-dc-offset.md).
- 아래에서 쓰는 엔진 용어(frontier·clean run·probe·masking·세그먼트·hole)의 정의는
  [recover 스펙 §복구 원리](specs/0002-recover.md#복구-원리-알고리즘), 포맷 불변량(DC 물리 범위·조밀
  코드 공간)은 [JPEG 엔트로피 코딩 레퍼런스](reference/jpeg-entropy-coding.md) 참조.

## 작업 순서 요약

| 순서 | 작업 | 종류 | 예상 영향 | 왜 이 위치인가 (의존성) |
|------|------|------|-----------|------------------------|
| ~~W1~~ | ~~지표·분류 정비~~ **완료 (2026-07-02)** | 기반 (저비용) | 새 베이스라인 확보 — 아래 상세 참조 | 없으면 개선·악화 측정이 부정확(악화 66건이 장기 미발견된 재발 방지). 빈 huff 검증 포함 |
| ~~W2~~ | ~~수락 임계 비례화 (+DC 물리 채점)~~ **완료 (2026-07-03)** | 복구율 (실증됨) | FAILED 66→15(전환 51), 파일별 회귀 0 — 아래 상세 참조 | 물리 채점은 수락 필터로 부적합 판명([ADR 0005](adr/0005-scaled-accept-threshold.md)) — 임계 비례화 단독으로 안전 실증. **W3 전제 충족** |
| ~~W3~~ | ~~헤더 복구 pass~~ **완료 (2026-07-04)** | 복구율 (실증됨) | SKIP 126→71·FAILED 15→11, 헤더 복구 61건 — 아래 상세 참조 | 진위 판정은 구조 신호로만([ADR 0006](adr/0006-header-recovery-structural-gates.md)) — 렌더 통계·상관은 밀림·캐스트 하에서 판별력 없음. **W4 전제 충족** |
| W4 | 재동기 세그먼트 밀림(shift) 보정 | 품질 (육안) | 2029세그/286파일(RECOVERED 51%)의 지배 결함 | W2·W3이 세그먼트 구성을 바꾸므로 그 뒤에(먼저 하면 재작업). 복구율 레버 다음의 품질 레버 |
| W5 | 색 캐스트 후처리 오프셋 보정 | 품질 (육안) | W4와 같은 범위의 2대 지배 결함 해소 | 오프셋 추정이 **밀림 보정된 경계 연속성을 전제**(W4 뒤). blocky 재평가 부수 |
| W6 | 단편화 probe 탐색 | 연구성 (불확실) | 성공 시 "데이터 한계" 파일 일부를 복구로 전환(유일 레버) | 불확실성 최대라 마지막. W2의 물리 채점기를 조각 판별에 재사용 |

> 순서의 원칙: ① 평가 기반 먼저(W1), ② 복구율(내용 자체)을 늘린 뒤 품질을 다듬는다(W2·W3 → W4·W5 —
> 반대로 하면 세그먼트 구성이 바뀌어 품질 작업을 재수행하게 된다), ③ 실증된 것을 불확실한 것보다
> 먼저(W2 실증 4/5 → W6 미검증), ④ 전제 관계 준수(W2→W3, W4→W5).

## 작업 상세

### ~~W1. 지표·분류 정비~~ — 완료 (2026-07-02, `feat/report-metrics`)

- **구현:** ① `report.csv`에 `undec_delta`·`worse`(Δ>+0.01)·`mcus` 컬럼 + 실행 종료 시 층화 요약(악화 건수·MCU 대역별), ② 무행동(ops 0·hole≥1·비CLEAN) → `FAILED` 분류·`failed/`에 원본 보존, ③ `Decoder`가 컴포넌트 참조 허프만 테이블 부재 시 거부(빈/부분 DHT 손상 → SKIP 정분류). 테스트 2종 추가(DHT 소실 거부·FAILED 원본 보존), 상세는 [recover 스펙](specs/0002-recover.md).
- **새 베이스라인** (`output/jpeg_recovered_w1/report.csv`, 822건, `--time-budget 0`): **RECOVERED 487 / CLEAN 143 / FAILED 66 / SKIP 126**. 이동은 전부 예상 부류 — RECOVERED→FAILED 66(무행동), RECOVERED→SKIP 4(DHT 완전 소실 2 + 부분 소실 2, 구 undec 0.963~1.000). **유지 파일의 undec 값 불일치 0** = 엔진 무변경(분류만 변경) 전수 검증.
- **주의:** RECOVERED undec_after 평균 0.092→**0.028**은 구성 변화(고회색 무행동 66건이 FAILED로 분리) 효과이지 엔진 개선이 아니다. W2 이후 비교는 이 새 베이스라인 기준: 악화(worse) 67건(FAILED 52·RECOVERED 14·CLEAN 1), MCU 대역별 undec_after 평균 — <120: 0.155(40건) / 120–449: 0.121(153건) / 450–899: 0.176(51건) / ≥900: 0.044(309건).

### ~~W2. DC 물리 범위 채점 + 수락 임계 잔여 비례화~~ — 완료 (2026-07-03, `feat/w2-scaled-accept`)

- **구현:** 수락 임계 잔여 비례화만 이식 — probe 캡 `W=min(900,잔여)`, 재동기 수락 `max(30, 0.35W)` **또는** 버퍼끝 완주 & run≥30, 편집 수락 `min(120, max(20, 0.4·잔여))`. 상세는 [ADR 0005](adr/0005-scaled-accept-threshold.md)·[recover 스펙](specs/0002-recover.md).
- **결과** (전수 822건, `--time-budget 0`): **FAILED 66→15**(RECOVERED 전환 51 + CLEAN→RECOVERED 완전 복구 1), **파일별 undec 악화 0건**, RECOVERED+FAILED undec 합 47.38→24.05, 대역 120–449 평균 0.121→0.018. 전환 표본 12건 육안 masking 0건. 잔여 FAILED 15 = 헤더 손상·초소형 11(→W3) + 사실상 정상 1 + 절단·데이터 한계 3.
- **전제 수정:** ① **DC 물리 범위는 수락/기각 필터로 부적합 판명**(정당 수락 후보 52건 중 31건이 위반 — 리셋 오프셋·잔존 드리프트; 재도입 금지 제약은 ADR 0005) — 임계 인하는 채점 없이 안전(표본 내 불량 best-run 후보 0건 + 전수 회귀 0으로 검증). ② 0xC8069000 "데이터 소실 의심"은 다중 손상 사슬로 정정(0.843→0.220 복구). 과정은 [W2 조사](investigations/2026-07-03-w2-scaled-accept-dc-scoring.md).
- **파생:** DC 후보 동률 tie-break → W5로 라우팅(아래), W6의 물리 채점기 재사용 전제 재검토 필요.

### ~~W3. 헤더 복구 pass~~ — 완료 (2026-07-04, `feat/w3-header-recovery`)

- **구현:** `carver/headerfix.py` — 관용 스캔(SOS 정형 body·SOF `00 11 08`·DQT 재파싱)으로 헤더 후보를 모아, 도너 Annex-K 이식·DQT 이웃 스무딩·템플릿 SOF/SOS로 교정한 변형을 구조 게이트(probe 바닥·소비율 0.25~1.1·엔진 undec<0.95·own-우월)로 대결시켜 채택. 전 후보 실패 시 SKIP 유지. 상세는 [ADR 0006](adr/0006-header-recovery-structural-gates.md)·[recover 스펙](specs/0002-recover.md)·[W3 조사](investigations/2026-07-04-w3-header-recovery.md).
- **결과** (전수 822건, `--time-budget 0`): **SKIP 126→71 / FAILED 15→11 / RECOVERED 539→537 + HEADER_RECOVERED 61**(신규 분류·`header_recovered/` 폴더). 헤더 복구 61건 = SKIP 55 + FAILED 4 + RECOVERED 2 전환(undec_after <0.05가 45건, 태그 sof 15·dht 11·dht+dqt+sof+sos 13 등). **유지 파일 undec 악화 0건**, 전환 대표 육안 쓰레기 0. 잔여 SKIP 71 = 재료 부족 68(비카메라 대역 carve 위양성·조각 의심) + SOF 정상꼴 3. 잔여 FAILED 11 = 절단·초소형 엔트로피 손상.
- **전제 수정:** ① **도너 DHT는 코퍼스 전역 단일**(Annex-K, 685/696) — "DQT 가족별 매칭" 기각. ② FAILED 직접 대상은 11건이 아니라 지문 이탈 3건. ③ [사각지대] 8단계 "계수 경계 quant-비례 문제"는 0xCA9AA000 근거로는 성립 안 함(경계는 어긋난 스트림에 정당 발동 — DQT 오진). ④ **진위 판정을 렌더 통계·픽셀 상관·코퍼스 prior로 하면 안 됨**(밀림·캐스트 하 판별력 없음 — [ADR 0006](adr/0006-header-recovery-structural-gates.md) 재도입 금지 제약).
- **파생:** DQT 이상치 스무딩(양자화표 이웃 중앙값)은 W3에서 확립 — 데이터 소실 판정과 무관. AC 경계 마진은 W2 8단계에서 종결(CLEAN max|AC| 3240, 여유 1.85배).

### W4. 재동기 세그먼트 밀림(shift) 보정 — 품질(육안 지배 결함 1/2)

- **내용:** `_resync_skip`이 재개 비트를 frontier MCU에 배정하나 hole의 실제 MCU 수(k)를 모름 → 세그먼트마다 수평 오프셋 누적. k 추정: 세그 경계 행 상관(상대) + EOI 앵커(절대 — 데이터 소진 파일은 부재, 0xBA5F6000 소비 100.0%; 상대 보정만으로 내부 정합 가능, 전역 1자유도 잔존).
- **근거·영향:** resync 2029세그/286파일 = RECOVERED 51%의 육안 지배 결함. [blocky 조사](investigations/2026-07-02-blocky-shift-dc-offset.md) 1·6단계.
- **의존:** W2·W3 뒤 — 세그먼트 구성이 확정된 뒤에 해야 재작업이 없다.

### W5. 색 캐스트 후처리 상수 오프셋 보정 — 품질(육안 지배 결함 2/2)

- **내용:** DC 차분의 선형성 덕에 재디코드 없이 **디코드 후 (세그먼트×컴포넌트)별 상수 오프셋 덧셈**으로 보정. 오프셋 추정은 밀림 보정된 세그 경계 행 연속성(W4 산출)으로. 실측 캐스트 규모: 세그 평균 Cb/Cr −1055~−1068 등.
- **부수:** blocky MCU 재평가 — 27.9bits/MCU 저엔트로피 구간(비중 0.3~0.5%, clean run 판별력 0)이라 독립 과제로는 낮음; 밀림·캐스트 보정 후 콘텐츠 신호(AC 분포·공간 연속성)로 재판별. [blocky 조사](investigations/2026-07-02-blocky-shift-dc-offset.md) 1·2·5·7단계.
- **W2에서 라우팅(2026-07-03):** 재동기 DC 후보(carry↔zero) **동률 tie-break** — 같은 비트의 두 DC 후보가 run 동률일 때 물리 절대위반 MCU 수(viol) 최소를 선택. 수락 집합은 불변(회귀 위험 0)이고 클리핑 손실 축소는 실측(0xC8069000 viol 55→1)됐으나, 캐스트 자체는 오프셋 미지라 어느 상수를 골라도 잔존해 **단독 육안 이득이 불명확** — W5 오프셋 보정과 결합해 재평가(클리핑은 상수 덧셈으로 복원 불가한 손실이므로 W5 시점에 켜는 것이 잠재 이득). [W2 조사](investigations/2026-07-03-w2-scaled-accept-dc-scoring.md) 7단계.
- **주의(기각 기록):** 위-행 렌더 DC를 재동기 단계에 되먹이는 원안은 실패(순환 오염) — 보정은 반드시 디코드 후 단계.

### W6. 단편화 probe 탐색 — 연구성(데이터 한계 파일의 유일 레버)

- **내용:** 데이터 소진형(소비 ~100%·MCU<100%) 파일의 frontier 상태(비트·DC)로 `usb.img`(3.5GB) 전 클러스터 후보(32KB 정렬 ~107K개)를 `decode_probe`해 이어지는 조각을 찾는 전수 탐색. 조각 판별 신호는 재설계 필요 — **W2에서 물리 불변량이 수락 필터로 부적합 판명**([ADR 0005](adr/0005-scaled-accept-threshold.md)): clean run 단독은 위양성 위험, 물리 채점은 정당 후보를 오기각. 후보 간 상대 비교·경계 행 연속성 등 콘텐츠 신호 검토.
- **근거:** 잔여 회색 대형 ~29건 + EOI 부재 절단 파일들의 유일한 추가 데이터 소스. 0x95AAD000 정지점은 섹터 비정렬로 그 파일은 단편 경계 아님 확인(가설 자체는 미검증으로 열림). [사각지대 조사](investigations/2026-07-02-recovery-tool-blindspots.md) 9단계.
- **성공 시:** "데이터 한계" 결론 일부를 "carve 연속성 가정의 한계"로 재정의.

## 신규 발견 (2026-07-04, W3 후속 감사 — 순서 미정)

> 사용자 3개 질문(CLEAN 미개봉·skip 비정상 크기·헤더 미복구 원인)에서 출발한 감사가 노출한 항목.
> 근거·수치·재현은 [과다 카빙 묻힌 이미지 조사](investigations/2026-07-04-overcarve-buried-images.md).
> W1~W6과 달리 **C1은 carve 단계** 문제라 성격이 다르다 — 순서는 사용자 승인 대기.

### ~~C1. 과다 카빙 embedded-skip 이미지 손실 — carve 정밀도~~ — 완료 (2026-07-05, `feat/carve-overcarve`)

- **문제:** `jpeg_end` 마커 워크가 손상된 세그먼트 길이·비마커 바이트(`FF00` 등)를 신뢰해 뒤따르는
  진짜 이미지 위로 점프, carve가 그 범위 내부 `FFD8FF` 히트를 embedded로 건너뛰어(carve.py:44-59)
  연속 이미지가 한 파일로 뭉치고 내부 진짜 JPEG이 소거됐다.
- **구현:** `jpeg_end`에 유효마커(mb≥0xC0)·마커별 길이 상한(DHT 1200·DQT 600·SOF 100·DRI 10)·손상 시
  경계 축소(SOF 후=다음 진짜 헤더 / SOF 전=next_sig) 추가. `parse_header` 길이 캡(크래시 수정). 상세는
  [ADR 0007](adr/0007-carve-corrupt-header-boundary.md)·[carve 스펙](specs/0001-carve.md)·[C1 조사](investigations/2026-07-05-carve-overcarve-fix.md)·[C1 보고서](reports/2026-07-05-carve-overcarve-fix.md).
- **결과** (usb.img 재카빙 822→999 + `recover.py --time-budget 0`): **사용가능(REC+HR+CLEAN) 740→884(+144)**,
  ERROR 0. 진짜 손실 0(사라진 6=EXIF 썸네일은 부모 HEADER_RECOVERED로 보존, 다운그레이드 5=mis-framed
  블록 복구를 C1이 분할한 정정). 회귀 테스트 4개, 전체 65 통과.
- **기각:** SOS-aware 경계(순증감 +102<+106, buried 삼킴)·strict marker-landing(전수 손실 8)·모든 손상을
  next_sig(EXIF 썸네일 붕괴)·de-bury(root fix가 커버). **회귀는 plain 디코더가 아니라 recover
  파이프라인으로 판정**(부모 header-recovery 보존을 plain은 손실로 오판).
- **잔여(무해):** base-swap 소수 손실(값싼 소형)·순수 위양성의 10MB 확대(디스크 낭비).

### C2. CLEAN strict 개봉 검증 + 재인코딩 — recover 정밀도

- **문제:** CLEAN이 `ops==0 and before<0.02`에 원본 바이트 복사(resync.py:370-374). 관용 디코더가
  통과시킨 표준 미개봉 파일이 그대로 저장됨 — CLEAN 142 중 6장이 PIL 미개봉(2 헤더 오염 마커, 4 EOI 뒤
  쓰레기+스캔 미완결). 트림·접합 불충분, 재인코딩만이 6장 전부 정규화.
- **수정 후보:** CLEAN 저장 전 strict 디코더(PIL 등) 개봉 검증 → 실패 시 재인코딩본 저장(원본 대신).

### C3. sof_candidates 길이 오염 허용 — 헤더 복구 확장

- **문제:** `sof_candidates`(headerfix.py:99-111)가 `00 11 08`(len=0x0011·precision=8) 고정 패턴이라
  **SOF 길이 상위바이트 오염**을 못 넘는다. skip 8건 중 3건(0xCA067000·0xB15F1BCC·0xBD2E03F2) SOF후보=0.
  0xCA067000 진짜 SOF0@0x12A=`FF C0 08 11 08 07 28 09 90`(len 상위 0x08, dims 2448x1832 정상) — 수동
  dims로 opening_probe run=900.
- **수정 후보:** `11 08`(precision) 앵커 + FFC0/FFC2 근접 + dims sane 검증으로 길이 오염 허용. 나머지 5건은
  DQT후보=0·probe 미달·과다카빙(C1)이라 별도.

## 보류 (독립 항목으로 유지하지 않음)

| 항목 | 처리 |
|------|------|
| 0xC91B9000 DQT 전 스텝 1(무양자화, 2248bits/MCU) | 1건 관찰 — W3(헤더 복구)에서 DQT 이식 후보로 함께 검토 |
| 0xC8~0xC9 대역 비표준 해상도·EOI 부재 절단군 | 데이터 한계(절단) — W6 외 레버 없음. 별도 작업 없음 |
