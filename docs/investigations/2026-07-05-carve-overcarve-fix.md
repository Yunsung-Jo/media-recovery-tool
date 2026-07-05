# 조사 기록 — 과다 카빙 수정: 손상 세그먼트 길이가 마커 워크를 묻힌 이미지 위로 건너뛰게 함

- **날짜:** 2026-07-05
- **한 줄:** carve `jpeg_end` 마커 워크가 손상된 세그먼트 길이(예: DHT len=32799)를 믿고 `pos += 2 + len`으로 점프해 뒤 이미지들을 건너뛰는 과다 카빙을, 마커별 길이 검증+보수적 경계로 고치는 루프. backlog C1.
- **결론 문서:** (리파인에서) ADR·[carve 스펙]·[backlog C1](../backlog.md). 선행 [과다 카빙 묻힌 이미지 조사](2026-07-04-overcarve-buried-images.md)

> **시점 기록(랩 노트).** experiment-loop 루프를 돌며 매 사이클 라이브로 기록. 스냅샷.
> 베이스라인 원장: scratchpad `c1_baseline.json`. recover 실행은 전부 `--time-budget 0`.

---

## 문제 정리 + 성공 기준 (루프 밖, 1회)

**문제:** carve `jpeg_end`(extractors.py:57-145)의 마커 워크는 세그먼트 길이 필드를 신뢰해
`pos += 2 + seg_len`로 전진한다. 캐리어 파일 첫 이미지의 헤더에 손상된 길이 필드가 있으면
(0xC8A59000: DHT len=32799 정상~181) 워크가 뒤따르는 진짜 이미지들을 세그먼트 본문으로
건너뛰고, 멀리 있는 EOI/10MB/next_sig fallback까지 경계를 잡는다. 그 범위 내부의 FFD8 히트는
carve가 embedded로 스킵(carve.py:44-59)해 손실된다.

**성공 기준:**
- **A 필수(자동):** 회귀 0 — 현재 정상 카빙되는 파일(특히 큰 APP1/Exif 썸네일 보유)의 경계가
  불변. 재카빙 산출물 구조 타당.
- **B 진척(반자동):** 별도 추출되는 디코드가능 이미지 수 증가(캐리어 5개에 묻힌 62장 → 독립 히트).
- **C 육안:** 새 추출물이 실제 사진(쓰레기 조각 아님), 베이스라인(현재 손실) 대비.

**고정 샘플** (`c1_baseline.json`):
- 목표: 캐리어 5개 — 0xC8A59000(DHT len 손상, 묻힌 22)·0xCA067000(SOF len 손상, 19)·
  0xCA9C5000(FFD8FFC0 시작, 3)·0xCE4CF000(10MB 캡, 1)·0x92EC750C(썸네일 17).
- 회귀 가드: 0x1704A000(2816x2112, APP1 46628)·0x44D9F000(2816x2112, APP1 45696)·
  0xC9FAC000(2448x1836, APP1 156). 전부 현재 comp=True·carve_len=파일크기.

## 조사 과정 (가설 → 예측 → 증거)

### 1단계 — 마커별 세그먼트 길이 검증

- **가설 / 세운 이유:** 문제 정리에서 0xC8A59000의 DHT len=32799(정상~181)가 마커 워크를 점프시킴을
  확인. 마커 종류별 sane 상한(DHT 1200·DQT 600·SOF 100·DRI 10, APPn/COM 무제한)을 두고 초과 시
  손상으로 보아 다음 진짜 이미지 헤더(`_next_header`)로 경계를 축소하면, 뒤 이미지가 별도 히트가 된다.
- **실험:** 스크래치 `jpeg_end_fixed`(원본 + 길이 검증)로 캐리어 5·가드 3의 경계 before→after 측정
  (`c1_cycle1.py`, usb.img 직접).
- **예측:** 캐리어 5개 경계 축소·묻힌 분리, 가드 3개 경계 불변.
- **증거:**
  - 0xC8A59000 1076→28.0KB [FFC4=32799], 0xCA067000 8247→1172KB [FFC0=2065],
    0xCA9C5000 7958→2284KB [FFC5=5310], 0xCE4CF000 10240→1276KB [FFC4=33186].
  - **0x92EC750C 1192→1192KB 불변** [eoi 경로 — 손상 길이 없이 +1192KB에서 진짜 EOI].
  - 가드 3개(0x1704A000·0x44D9F000·0xC9FAC000) 전부 불변(회귀 0).
  - 부분성: 0xCA9C5000 묻힌 3장(+28·+628·+4620KB) 중 새 경계 2284KB 안의 2장(+28·+628) 잔존,
    1장(+4620)만 분리.
- **판단:** 길이 검증은 유효(4/5 축소)하고 가드 회귀 0. 단 2차 메커니즘 노출 — 워크가 **유효 길이의
  임베디드 이미지들을 통과**해 멀리 있는 손상/EOI까지 간다(0xCA9C5000은 +2284KB의 FFC5까지, 0x92EC750C는
  +1192KB EOI까지). 다음: 두 잔존 캐리어 워크를 추적(2단계).

### 2단계 — 유효마커 검증 추가(0xC0 미만 = 엔트로피/쓰레기)

- **가설 / 세운 이유:** 1단계에서 잔존한 0x92EC750C·0xCA9C5000 워크 추적(`c1_trace2b.py`) 결과, 첫
  이미지의 불완전 헤더 뒤로 워크가 엔트로피에 진입해 `FF00`(스터핑)·`FF41`·`FFB0` 등 **0xC0 미만
  바이트를 길이 세그먼트로 오해**해 점프함을 확인. 정상 헤더 마커는 전부 0xC0 이상이므로, mb<0xC0을
  손상으로 보아 다음 진짜 헤더로 경계 축소하면 이 메커니즘도 잡힌다.
- **실험:** `jpeg_end_fixed`에 `mb<0xC0 → corrupt` 추가, 캐리어 5·가드 3 재측정(`c1_cycle1.py`),
  이어 캐리어 영역 carve 루프 시뮬로 분리·디코드 이미지 수 집계(`c1_sim_carve.py`).
- **예측:** 0xCA9C5000 2284→28KB(FF44), 0x92EC750C 1192→~3KB(FF41), 나머지 유지, 가드 불변.
- **증거:** 0xCA9C5000 →28.0KB[FF44], 0x92EC750C →3.1KB[FF41], 0xC8A59000 →28KB, 0xCA067000
  →1172KB, 0xCE4CF000 →1276KB. 가드 3개 불변. 시뮬: baseline 묻힌 62장(손실) → **fixed 분리 디코드
  60장**(0xC8A59000 22, 0xCA067000 19, 0xCA9C5000 3, 0xCE4CF000 1, 0x92EC750C 15).
- **판단:** 유효마커+길이 검증으로 5개 캐리어 모두 축소, 묻힌 62 중 60 분리. 다음: 전수 회귀(기준 A).

### 3단계 — 전수 회귀 검사 → 소형 캐리어 발견, 순 +106

- **가설 / 세운 이유:** 2단계 fix가 캐리어 외 정상 파일의 경계를 바꾸면 회귀다. 822 오프셋 전수에서
  경계 diff 필요.
- **실험:** 822 오프셋에서 원본 vs fixed 경계 비교, 변경 파일의 디코드 여부 검사(`c1_regression.py`),
  이어 변경 오프셋 전체의 디코드가능 이미지 순증감을 before(1파일) vs after(분할 시뮬)로 측정(`c1_net.py`).
- **예측:** 캐리어 5개만 변경, 정상 파일 불변.
- **증거:**
  - 경계 변경 71건 = 캐리어 5 + 그 외 66. 그 외 대부분 SKIP 위양성(무해)·HEADER_RECOVERED 소형.
  - 0xB164690C[HEADER_RECOVERED] 13→1.31KB, 원본 13KB는 96x72 디코드. 추적: `FF00`@+0.813KB(엔트로피
    스터핑, SOS +2.41KB 이전) 트리거. 그러나 13KB 안에 JFIF 서명 3개(+1.31·+7.25·+10.06KB) — **소형
    다중 캐리어**(1MB 필터가 놓침). base 96x72(std89) vs +7.25 조각(std50) 상관 −0.008 = 다른 실제 이미지.
  - 순증감(`c1_net.py`): 변경 오프셋 디코드가능 **before 2 → after 108(순 +106)**, 감소(개수 회귀) 0.
- **판단:** fix는 순 +106 디코드가능(소형 캐리어까지), 개수 회귀 0. 단 0xB164690C류는 base 고유
  이미지를 잃고 뒤 이미지를 얻는 **스왑**(count엔 안 잡힘) — SOS 이전 헤더(임베디드)에서 잘라 base scan
  손실. 다음: base 보존 정제(손상 시 현재 SOS 뒤 헤더로 경계) 시도(4단계).

### 4단계 — SOS-aware 손상 경계(base 보존 시도) 기각

- **가설 / 세운 이유:** 3단계에서 0xB164690C류가 SOS(+2.41KB) 이전 헤더(+1.31KB)에서 잘려 base scan을
  잃음. 손상 검출 시 현재 이미지의 SOS를 찾아 그 스캔 뒤 첫 진짜 헤더로 경계하면 base가 보존될 것.
- **실험:** `corrupt_bound`가 `data.find(FFDA)`로 SOS를 찾아 그 뒤 `_next_header`로 경계(예산 256KB).
  캐리어·0xB164690C·가드·순증감 재측정(`c1_cycle4.py`).
- **예측:** base[0:새경계] 디코드 True(보존), 캐리어 freed 유지, 순증감 ≥ +106.
- **증거:** 0xB164690C 첫경계 +7.25KB이나 **base[0:7.25KB] 디코드 False**(헤더 손상이라 plain 디코더
  실패 — header-recovery 필요, 측정 불가). 0xCA9C5000 첫경계 +28→+56KB로 buried 1장 삼켜 3→2.
  순증감 +106→**+102**(악화). 가드 불변.
- **판단:** 기각. SOS-aware는 base를 plain 기준으로 보존 못 하고(header-recovery 영역), 캐리어를
  해쳐 순증감이 낮아진다. **교훈: 손상 헤더 이미지의 "보존"은 plain 디코더로 판정 불가 —
  header-recovery가 하는 일이라 carve 경계 휴리스틱으로 흉내 내면 오히려 buried를 삼킨다. 경계는
  단순히 다음 진짜 헤더(`_next_header(손상지점)`)로 두고, 손상 첫 이미지의 복원은 recover 단계에 맡긴다.**

### 5단계 — SOF-도달 게이트로 garbage 타이트 바운드(채택)

- **가설 / 세운 이유:** 3단계 전수에서 SKIP 위양성이 손상 시 `_next_header`(멀면 10MB)로 커져 디스크
  낭비. 위양성은 SOF 도달 전 손상되고 캐리어는 SOF(APP0/DQT/SOF) 뒤 손상되므로, **SOF 미도달 시
  손상은 next_sig(다음 시그니처)로** 타이트하게 잡으면 낭비를 줄이면서 캐리어는 안 건드린다.
- **실험:** `saw_sof` 플래그 추가, 손상 시 `not saw_sof and ns<cap`이면 ns 반환. gate off/on의
  디스크 델타·커진 파일·순증감 비교(`c1_disk.py`, 822 전수 × 2).
- **예측:** gate on이 디스크 델타↓·커진파일↓, 순증감 불변(캐리어·정상 불변).
- **증거:** gate off(단순): 델타 −48MB, 커진 48(10MB 16), 순 +106. **gate on: 델타 −187MB, 커진 23
  (10MB 8), 순 +107**(하나 더). 캐리어는 saw_sof=True라 불변, 가드는 SOS 도달로 불변.
- **판단:** 채택. SOF-게이트가 디스크 낭비 139MB 절감 + 순증감 +1(garbage 타이트가 한 곳의 real
  임베딩을 방지). 최종 fix = 유효마커(mb<0xC0) + 길이검증(DHT1200·DQT600·SOF100·DRI10) + 손상 시
  `_next_header`(SOF 후) / `next_sig`(SOF 전) 경계.

### 6단계 — 전체 적용(이식→재카빙)과 plain-decode 손실 착시

- **가설 / 세운 이유:** 5단계 fix를 `extractors.py::jpeg_end`에 이식(유효마커·길이검증·SOF-게이트),
  실제 재카빙 후 baseline 대비 회귀를 본다. 샘플 sim이 놓친 전역 embedded-skip 상호작용이 있을 수 있다.
- **실험:** 재카빙 `output_c1/jpeg/`(usb.img), 원본 `output/jpeg/`와 파일 diff·사라진/축소 파일의
  plain 디코드 여부(`c1_regression.py` 계열), 이어 손실 후보의 담는 부모 파일이 header-recovery로
  복구되는지 확인(`headerfix.reconstruct`).
- **예측:** 캐리어만 변경, 정상 파일 불변.
- **증거:**
  - 재카빙 **822→999 파일(+177)**, 오류 0. 사라진 오프셋 14(디코드가능 6·garbage 8), 축소 다수.
  - plain 디코드 기준 손실 7: EXIF 썸네일 6(0x9F81634C·0x9F8203F2·0xBD03834C·0xBD0423F2·0xBD2D634C·
    0xA0ED33F2, 전부 부모 Exif APP1 안 +0x34C/+0x3F2) + base-swap 1(0xB164690C).
  - **그러나** 이 썸네일들을 담는 새 carve 부모(0x9F816000 등, 유효 46KB Exif APP1)는
    `headerfix.reconstruct`로 **320x240 u=0.00 복구** — OLD가 썸네일을 별도 CLEAN으로, NEW가 같은
    320x240을 부모 HEADER_RECOVERED로 산출(내용 보존).
  - strict-landing(6단계 정제) 시도: 전수 손실 8(0x9F816000 썸 보존하나 0xB15F* 등 새로 깨짐,
    0xCE4CF000 +1276→+0.6KB로 망가짐). 채택 안 함.
- **판단:** plain-decode 손실은 **측정 착시** — 실제 `recover.py`는 header-recovery를 돌려 부모가
  내용을 복구한다. 진짜 회귀/이득은 전수 재복구로만 확정된다(진행 중). **교훈: carve 변경의 회귀는
  plain 디코더가 아니라 실제 recover 파이프라인(header-recovery 포함)으로 판정해야 한다 — 부모가
  손상 헤더로 SKIP처럼 보여도 recover가 복구한다.** strict-landing은 whack-a-mole이라 기각.

### 7단계 — 전수 재복구·criterion A 콘텐츠 검증·크래시 수정

- **가설 / 세운 이유:** 6단계 plain-decode 손실이 착시라면, 실제 recover 파이프라인으로 전수 재복구
  시 W3 사용가능 이미지가 보존되고 순증가할 것.
- **실험:** `output_c1/jpeg/`(999) 전수 `recover.py --time-budget 0`, W3 분류와 대조. 사라진/다운그레이드
  오프셋의 콘텐츠가 부모 HF 또는 C1 분할본에 보존되는지 픽셀 상관·렌더로 확인(`c1_content_check.py`).
- **예측:** 사용가능 순증가, 사라진 것은 부모가 복구, 다운그레이드 캐리어는 분할로 대체.
- **증거:**
  - 분류 W3→C1: RECOVERED 537→611, HEADER_RECOVERED 61→85, CLEAN 142→187, FAILED 11→25,
    SKIP 71→90, ERROR 0→1. **사용가능(REC+HR+CLEAN) 740→883(+143)**.
  - 사라진 6 오프셋(EXIF 썸네일): **전부 부모가 HEADER_RECOVERED**(0x9F816000 등 → 320x240 u=0.00).
    진짜 손실 0.
  - 다운그레이드 6(HR→SKIP/ERROR): 캐리어 — 0xC8A59000 콘텐츠 상관 0.987 보존+21장, 0xCA9C5000
    0.738, 0xD0E2A000은 W3가 거의 회색(2816x2112 std=19)이고 C1은 2592x1944 std=75~80로 더 나음.
    즉 W3의 mis-framed 블록 복구를 C1이 분할한 것(정정). base-swap 1(0xB164690C 96x72 손실·주변 획득).
  - **ERROR 1(0xC8A59000)**: `parse_header`가 손상 DHT 길이로 `data[q]` 인덱스 초과(jpegdecode.py:84).
    `end=min(i+seg_len, n)` 캡으로 수정 → HEADER_RECOVERED. 회귀 테스트 3(손상 DHT 경계·SOF-게이트·
    parse 크래시) + 1 추가, 전체 65 통과.
- **판단:** criterion A·B 충족 — 사용가능 +143, 진짜 손실 0(썸네일은 부모 HF 보존, 다운그레이드는
  mis-framed 블록 정정), 크래시 수정. **교훈: 축소된 carve 조각은 `parse_header`의 미검증 세그먼트
  길이 읽기를 노출한다 — 길이는 항상 버퍼로 캡한다.** 크래시 수정 반영 위해 전수 재복구 재실행(clean).

## 기각된 가설 / 막다른 길

- **4단계 SOS-aware 손상 경계** — 순증감 +102 < 단순 +106, 캐리어 악화. base 보존은 recover 단계
  (header-recovery) 몫. **교훈: carve 경계 휴리스틱으로 손상 이미지 "보존"을 흉내 내면 buried를
  삼킨다 — 경계는 다음 진짜 헤더로 단순화하고 복원은 recover에 맡긴다.**
- **6단계 strict marker-landing**(세그먼트 점프 후 pos가 0xFF 착지 안 하면 손상) — 전수 손실 8로
  오히려 악화. 유효 46KB APP1 뒤가 우연히 FF면 통과(0xBD038000), 0xCE4CF000은 +0.6KB로 붕괴.
  **교훈: 착지 바이트 단독으로 "유효 APP1 vs 손상 APP1"을 못 가른다 — 엔트로피에 FF가 흔해 우연
  통과·오탐 양쪽이 난다.**
- **plain-decode로 carve 회귀 판정**(6단계) — 부모가 header-recovery로 복구하는데 plain 디코더는
  실패로 봐 손실을 과대 계상. **교훈: carve 변경 회귀는 실제 recover 파이프라인으로만 판정한다.**

## 사용한 방법·도구

scratchpad 일회성 스크립트(usb.img·output 대상):
- `trace_carve.py`·`c1_trace2b.py` — 디스크 jpeg_end 마커 워크·경계 추적.
- `c1_baseline.py` — 캐리어 경계·묻힌수 + 회귀 가드 저장(`c1_baseline.json`).
- `c1_cycle1.py`~`c1_cycle6.py`·`c1_disk.py` — 스크래치 jpeg_end 변형(길이·유효마커·SOF-게이트·
  SOS-aware·strict-landing) 샘플 검증·순증감·디스크델타.
- `c1_regression.py`·`c1_net.py` — 전수 경계 diff·순 디코드가능 증감.
- `c1_content_check.py` — 다운그레이드 캐리어의 W3 콘텐츠가 C1에 보존되는지 픽셀 상관·렌더.

관련 코드: `carver/extractors.py`(jpeg_end 손상 경계 3중 검증)·`carver/jpegdecode.py:68`(parse_header
길이 캡)·`carve.py:44-59`(embedded-skip)·`carver/scanner.py:4`(FFD8FF 시그니처).

## 결론

carve `jpeg_end`가 세그먼트 길이·비마커 바이트를 신뢰해 손상 첫 이미지 뒤로 점프하던 과다 카빙을,
**유효마커(mb≥0xC0)·마커별 길이 상한(DHT 1200·DQT 600·SOF 100·DRI 10)·손상 시 경계 축소
(SOF 후=다음 진짜 헤더 `_next_header` / SOF 전=`next_sig`)**로 고쳤다(`carver/extractors.py`).

- **전수(usb.img 재카빙 999 + `recover.py --time-budget 0`):** 사용가능(RECOVERED+HEADER_RECOVERED+
  CLEAN) **740→884(+144)**, ERROR 0. 파일 822→999.
- **criterion A 진짜 손실 0:** 사라진 6(EXIF 썸네일)은 부모가 HEADER_RECOVERED로 보존, 다운그레이드
  5(HR→SKIP)는 W3의 mis-framed 블록 복구를 C1이 제대로 분할한 정정(0xD0E2A000 등). base-swap 1은
  작은 이미지 대체(주변 다수 획득).
- **크래시 수정:** `parse_header` 손상 세그먼트 길이 인덱스 초과 → 버퍼 캡. 회귀 테스트 4개 추가, 전체 65 통과.
- **경계 결정은 단순화가 옳다:** SOS-aware·strict-landing 정제는 buried를 삼키거나 whack-a-mole —
  carve는 경계만 바로잡고 손상 이미지 복원은 recover(header-recovery)에 맡긴다 → [ADR 0007].
- **잔여:** base-swap 소수 손실(값싼 소형), 순수 위양성의 10MB 확대(디스크 낭비, 복구 무해). backlog
  C2(CLEAN strict 검증)·C3(SOF 길이 오염)는 별개로 남음.
