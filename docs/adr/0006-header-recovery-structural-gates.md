# 0006. 헤더 복구 후보의 진위는 구조 신호로만 판정하고 렌더 통계·상관·prior를 기각 필터로 쓰지 않는다

- **날짜:** 2026-07-04
- **상태:** Accepted

---

## 배경

resync 엔진은 엔트로피 스트림(`dec.buf`)만 편집한다. 헤더(DQT·DHT·SOF·SOS)가 손상돼
`Decoder` 구성이 실패하면 그 파일은 통째로 복구 범위 밖이었다 — W2 시점 SKIP_UNDECODABLE
126건(전체 15%, **산출물 0**)이 전부 헤더 손상이다(진짜 비-baseline 1건). 선행 조사에서 이 축을
복구할 재료가 코퍼스에 있음을 확인했다: DHT 소실·손상 파일은 다수파 테이블(822건 중 704건이 값 단위
일치, ITU T.81 Annex-K 전형 — [레퍼런스 §허프만 테이블의 출처](../reference/jpeg-entropy-coding.md))을
이식하면 디코드가 복원되고, DQT·SOF·SOS는 같은 카메라의 정형 헤더(SOF len 17·prec 8·comp ids 1,2,3·qids
0,1,1, 표준 SOS body 등)를 템플릿·근접 가족으로 재구성할 수 있다.

재구성 pass의 본질적 위험은 **쓰레기 채움**이다. JPEG 엔트로피의 Huffman 코드 공간은 조밀해서
(Annex-K 전형 테이블은 코드 공간을 거의 빈틈없이 채운다 — [레퍼런스 §디싱크 판별의 불변량](../reference/jpeg-entropy-coding.md)),
**틀린 헤더로도 스트림이 "무효 코드 없이" 디코드된다.** 잘못된 SOF 해상도·DQT·정렬로 디코드된
결과는 `undecoded_fraction`(회색 비율)을 낮추므로, undec만 보면 쓰레기 채움을 개선으로 오측정한다.
따라서 "어느 헤더 후보가 진짜인가"를 가르는 **진위 판정 신호**의 선택이 이 pass의 성패를 가른다.

착수 초기에는 렌더 콘텐츠 신호(크로마 그래디언트·평활 블록 비율)나 EXIF 썸네일과의 상관을
게이트 후보로 삼았다. 이들은 모두 실험에서 기각됐다(아래 대안 표). 상세 측정·기각 과정은
[W3 조사 기록](../investigations/2026-07-04-w3-header-recovery.md) 4c·4d·4e단계 참조.

## 결정

헤더 복구 후보의 채택/기각은 **구조 신호로만** 판정한다. `carver/headerfix.py::reconstruct`가
관용 스캔으로 헤더 후보(SOS·SOF·DQT·DHT)를 모아 변형을 열거하고, 다음 게이트를 통과한 것만
채택한다. 어느 변형도 통과하지 못하면 `None`을 반환해 SKIP을 유지한다(가짜 채움 금지).

1. **트리거** — `Decoder(data)` 구성 실패, **또는** 개구부 probe(bit 0·MCU 0·DC 0에서 경계+rate
   clean run)가 바닥 `min(30, (총MCU+1)//2)` 미만(첫 MCU부터 어긋남 = 헤더 손상 의심).
2. **선택** — 자체 헤더가 구조 완비면 자체 구조+테이블 교체 변형을 최우선(**own-first**),
   이어 치수 픽셀 수 내림차순 그룹. 각 그룹에서 probe 바닥 통과 후보를 run 계층(tier)별로
   plain (fit, undec)로 세부 순위한 뒤 후보별 게이트:
   - `fit==1`(전 MCU 디코드): **소비율 ∈ [0.25, 1.1]** (분모 = scan_start→그 뒤 첫 FFD9, 없으면 EOF)
   - 엔진 `recover()` 후 **undec_after < 0.95**
   - 완결 주장(undec_after < 0.05)이면 소비율 ≥ 0.25 재확인
3. **Decoder-OK 파일의 회귀 방지** — Decoder가 구성됐다는 것은 자체 헤더로도 디코드가 되긴
   한다는 뜻이므로 정상 경로가 강한 베이스라인이다. 트리거된 Decoder-OK 파일은 정상 경로를
   함께 계산해 **헤더 복구 undec < 정상 undec − 0.01일 때만** 채택한다.

임계값 근거:

- **소비율 하한 0.25 / 상한 1.1**: 관측 쓰레기(치수 과소 해석)의 소비율은 0.048·0.096(0xCE2C4000
  주 SOS 해석), 진짜 후보는 0.45~1.0. 하한 0.25는 그 사이(로그 중앙)다. 상한 1.1은 스터핑 규칙
  근거 — 진짜 엔트로피 내부에는 FFD9가 나타날 수 없으므로(destuff가 첫 EOI에서 멈춤), fit==1
  완주가 "첫 FFD9를 크게 넘겨" 소비된 해석은 위해석이다(0xCF6E8000 s21: 소비율 5.7 = 첫 FFD9를
  5.7배 관통). 상한 없이는 이 위해석이 s22(진짜, 소비율 정상)를 밀어낸다.
- **엔진 undec 0.95**: 사실상 전량 회색(재구성 실패)만 거른다. 통과분 중 최고 잔여는 0.949
  (0xD0E2A000 — 헤더는 복구됐으나 엔트로피 데이터 소실). undec는 report에 투명 기록되므로
  데이터 한계 케이스를 숨기지 않는다.
- **바닥 min(30, 총//2)·회귀 마진 0.01**: [ADR 0005](0005-scaled-accept-threshold.md)의 수락
  바닥 30과 정합(그보다 짧은 run은 진위 신호 없음). 마진 0.01은 report의 악화 플래그 임계와 동일.

**도너 DHT는 Annex-K 4테이블을 코드에 하드코딩**한다(`headerfix.DONOR_HUFF`, 지문 050df0dc —
코퍼스 다수파 685/696·PIL 인코더와 값 단위 일치). 코퍼스에서 뽑지 않으므로 자기완결·결정적이다.

전수 822건(`--time-budget 0`): SKIP 126→71 / FAILED 15→11 / RECOVERED 539→537 +
**HEADER_RECOVERED 61**(신규 분류·`header_recovered/` 폴더). 헤더 복구 61건 = SKIP 55 + FAILED 4 +
RECOVERED 2 전환(undec_after <0.05가 45건). **유지 파일 undec 악화 0건.** 전환 대표 육안 검증
쓰레기 0. 상세는 [W3 조사 기록](../investigations/2026-07-04-w3-header-recovery.md).

## 대안

| 대안 | 기각 이유 |
|------|----------|
| 렌더 콘텐츠 통계(크로마 수평 그래디언트·평활 블록 비율)로 진짜/쓰레기 분리 | 심하게 밀림·색 캐스트된 **진짜 부분 복구**가 쓰레기와 역전. c_grad: 진짜 0xCF6E8000 19.02 vs 쓰레기 0xCE2C4000 13.85. smooth%: 쓰레기 0.153 vs 진짜 0x92EBE2CC 0.157·0xCF6E8000 0.031. W4(밀림)·W5(캐스트) 보정 전에는 렌더 신호에 판별력이 없다 |
| EXIF 썸네일과 주 렌더의 픽셀 상관으로 인증 | 세그먼트 수평 밀림이 픽셀 정렬을 파괴 — **완전 복구본 0x95AAD000조차 corr −0.04**, 쓰레기 0xCE2C4000 0.19로 역전 |
| 코퍼스 해상도 분포를 치수 후보 기각 필터로 사용 | 비관측 해상도가 진짜인 사례 존재 — 0x99EDA000은 2816×**2240**(코퍼스 미관측)이 EOI 앵커(파일 끝 정확 위치)·undec 0.034로 확정. prior로 기각하면 이 복구를 놓친다(순위 힌트로만 사용) |
| 엔트로피 길이/총MCU ≤ 3000 bits/MCU 절대 게이트(코퍼스 max 2555) | EOI 소실 오버런 carve에서 분모(scan_start→첫 FFD9)가 이종 데이터로 오염 — 진짜 0xCF6E8000(주 SOS 뒤 첫 FFD9까지 7.4MB+)를 오기각해 SKIP 회귀 |
| fit==1 후보에 probe 포화(run==W) 요구 | 진짜-경미손상 썸(0xCE2C4000 sos@0x4c9, probe 31/80·소비율 0.856)을 오기각. 쓰레기/진짜를 가르는 것은 포화가 아니라 소비율 |
| own-first 없이 SOF 교차 변형을 함께 대결 | 자체 SOF(96×72)가 온전한 파일이 96×96 캔버스 교차 변형으로 채택돼 같은 콘텐츠에 회색만 증가(0xB164690C undec 0.259→0.444) |
| Decoder-OK 트리거 파일도 헤더 복구로 무조건 대체 | 정상 경로가 더 나은 파일에서 회귀 — 0xC93D5000(정상 0.080)이 잘못된 SOF 재구성 0.920으로 악화(실측). 정상 경로와 비교해 우월할 때만 채택해야 함 |
| DHT 후보 중복 제거를 counts만 비교 | 심볼 바이트만 손상된 DHT(counts는 도너와 동일)가 도너 후보를 삼켜 재구성 실패(0xB164690C). counts+symbols로 비교해야 함 |
| DC 물리 범위 불변량으로 정렬 채점 | [ADR 0005](0005-scaled-accept-threshold.md)에서 이미 수락 필터로 부적합 판명(복구 출력이 불변량을 일상 위반) — 헤더 복구에도 동일 |

## 결과

**실제 영향**

- `carver/headerfix.py` 신규(~410줄): 관용 스캔(`sos_candidates`·`sof_candidates`·
  `dqt_candidates`·`dht_candidates`), `DONOR_HUFF`(Annex-K), `smooth_repair`(DQT 이상치),
  `build_decoder`(헤더 오버라이드 수동 구성), `reconstruct`(게이트 대결).
- `carver/resync.py::recover_file`: Decoder 실패/probe<바닥 트리거, 헤더 복구 채택 시
  `HEADER_RECOVERED`(원본 바이트가 디코드 불가 → 렌더가 유일 산출, CLEAN/FAILED 분기 없음)로
  `header_recovered/` 폴더에 저장.
- `report.csv`에 `action=HEADER_RECOVERED`·`header_fix` 컬럼(교체 세그먼트, 예 `dht+dqt+sof+sos`),
  실행 종료 시 헤더 복구 건수·조합·undec 평균 요약.
- 전수: RECOVERED 537 / HEADER_RECOVERED 61 / CLEAN 142 / FAILED 11 / SKIP 71. 잔여 SKIP 71 =
  SOF 정상꼴 3 + 재료 부족 68(대부분 비카메라 이름 대역 carve 위양성·조각). 잔여 FAILED 11 = 절단·초소형 엔트로피
  손상(0xC9866000 0.729 등).

**감수한 트레이드오프**

- 헤더 복구본 61건이 W4 밀림·W5 캐스트 결함을 그대로 보유한다(회색 SKIP보다 콘텐츠+결함이
  낫다는 판단 — [ADR 0005](0005-scaled-accept-threshold.md)와 같은 입장, W4·W5의 입력이 된다).
- 고잔여 3건(undec 0.625~0.949)은 헤더만 복구되고 엔트로피는 데이터 한계다 — 재인코딩 렌더가
  대부분 회색이나, undec가 투명 기록되고 SKIP 대비 회귀는 아니다.
- Decoder-OK 트리거 파일은 정상 경로와 헤더 복구를 모두 계산한다(중복 비용) — 트리거되는
  Decoder-OK 파일이 소수(첫 MCU 손상)라 전수 실행 시간 영향은 작다.

**향후 고려사항**

- **렌더 신호 게이트는 W4(밀림)·W5(색 캐스트) 보정 이후 재평가 가능** — 밀림·캐스트가 제거되면
  공간 연속성·크로마 분포가 진짜/쓰레기를 가를 수 있다. 그 전에는 재도입 금지.
- 소비율 상한 1.1은 **스터핑 규칙(진짜 스트림 내 FFD9 부재)에 의존** — destuff의 EOI 처리
  방식이 바뀌면 이 게이트의 근거가 무너지므로 함께 재검토한다.
- 잔여 SKIP 71의 재료 부족 68건은 carve 위양성·조각 의심(비카메라 이름 대역·해상도 미상) —
  헤더 복구가 아니라 carve 정밀도·단편화 탐색(백로그 W6)의 영역이다.

## 관련 항목

- [ADR 0005](0005-scaled-accept-threshold.md) 수락 임계 잔여 비례화(헤더 복구가 이식하는 엔진의
  수락 규칙 — 소형·꼬리 수락이 헤더 복구 소형 파일의 재동기를 가능하게 함), [ADR 0001](0001-resync-recovery.md) resync 복구
- [W3 조사 기록](../investigations/2026-07-04-w3-header-recovery.md) — 실험·기각 과정 전체
- [사각지대 조사](../investigations/2026-07-02-recovery-tool-blindspots.md) 5~8단계 — SKIP 전수 분류·DHT 이식 실증
- [JPEG 엔트로피 코딩 레퍼런스](../reference/jpeg-entropy-coding.md) — Annex-K 도너 테이블·조밀 코드 공간
- [recover 스펙](../specs/0002-recover.md) — 동작 서술
