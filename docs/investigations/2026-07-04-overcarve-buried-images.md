# 조사 기록 — 과다 카빙+embedded-skip으로 연속 사진 ~38장이 한 파일에 포함돼 손실; CLEAN 6장 미개봉·SKIP 71건 중 63건 복구불가(위양성·빈조각)

- **날짜:** 2026-07-04
- **한 줄:** W3 후속 감사(CLEAN 미개봉·skip 비정상 크기·헤더 미복구 원인) 중, `jpeg_end` 과다 카빙이 연속 이미지를 한 파일로 뭉치고 내부 시그니처를 embedded로 건너뛰어(carve.py:44-56) 디코드 가능한 실제 사진 ~38장이 출력에서 손실됨을 확인. 캐리어 5개(그중 3개는 HEADER_RECOVERED 오분류). `usb.img` 보존돼 재카빙 가능.
- **결론 문서:** [ADR 0006](../adr/0006-header-recovery-structural-gates.md)(헤더 게이트)·[recover 스펙](../specs/0002-recover.md)·[backlog](../backlog.md)(과다 카빙·CLEAN sanitize 항목). 선행 [W3 조사 기록](2026-07-04-w3-header-recovery.md)

> **시점 기록(랩 노트).** W3 머지 전 사용자 3개 질문(CLEAN 미개봉·skip 비정상 크기·헤더 미복구 원인)에서 출발한 감사. 스냅샷이며 이후 코드 변경에 맞춰 갱신하지 않는다.

---

## 증상

W3 결과물(`output/jpeg_recovered_w3/`, RECOVERED 537 / HEADER_RECOVERED 61 / CLEAN 142 / FAILED 11 / SKIP 71)에 대해 사용자가 3가지를 제기:

1. **CLEAN인데 안 열림** — `clean/0x92E713CC.jpg`(3656B)·`clean/0xB164A9CC.jpg`(5184B)가 이미지 뷰어에서 열리지 않음.
2. **skip에 비정상 크기** — `skip_undecodable/`에 `0x377D5CDA`(170MB)·`0x55476735`(48MB)·`0x290897AC`(19MB) 등. 추출 오류 의심.
3. **헤더 미복구 71건의 원인** — 헤더 복구 pass가 왜 이들을 못 살렸나.

재현: `output/jpeg/`(추출 원본 822장)와 `output/jpeg_recovered_w3/report.csv`(분류). 조사 스크립트는 scratchpad(§사용한 방법).

## 조사 과정 (가설 → 예측 → 증거)

### 1단계 — CLEAN 미개봉 2장의 디코더 잣대 불일치

- **가설 / 세운 이유:** CLEAN은 `recover_file`에서 `ops==0 and before<0.02`일 때 원본 바이트를 그대로 복사한다(resync.py:370-374). 우리 디코더는 관용적이므로, 표준 디코더가 거부하는 파일을 통과시켜 CLEAN으로 복사했을 수 있다.
- **실험:** 두 파일을 PIL(`Image.open().load()`)과 자체 `jd.Decoder`로 각각 열고, 마커 구조를 스캔.
- **예측:** 자체 디코더는 성공, PIL은 실패. 헤더 어딘가에 표준 파서가 멈추는 오염이 있을 것.
- **증거:**
  - 자체 디코더: 둘 다 96x72, undec 0.000/0.002, std 77.6/68.5(완전 디코드).
  - PIL: 둘 다 `UnidentifiedImageError`(포맷 인식 단계 실패).
  - 마커: 둘 다 SOI·APP0·DQT(67) 뒤에 오염 마커 — 0x92E713CC는 `FFAA`(len 256)@0xD7·`FF01`, 0xB164A9CC는 `FF7A`/`FF7B`/`FF7C`/`FF7D` — 이후 0x259에서 정상꼴 DQT·SOF0·DHT×4·SOS가 다시 시작. 0x92E713CC는 EOI@0xE46(정상 종료), 0xB164A9CC는 EOI 없음.
- **판단:** 가설 지지. PIL은 정상 SOF(0x259) 이전의 오염 마커(`FFAA` 등)에서 "no marker found"로 중단, 우리 디코더는 그 구간을 건너뛰고 뒤의 정상 프레임을 찾는다. CLEAN이 원본을 그대로 복사하므로 오염이 산출물에 그대로 남는다.

### 2단계 — CLEAN 미개봉은 6장, 두 실패 유형

- **가설 / 세운 이유:** 1단계 원인(관용 디코더가 표준 미개봉 파일을 통과)이 맞다면, CLEAN 142장 전수에 유사 사례가 더 있을 것.
- **실험:** `clean/` 142장 전수를 PIL로 열어 실패분 색출, 각 실패의 자체 디코더 결과·마커·EOI·PIL 관용 모드(`LOAD_TRUNCATED_IMAGES`) 대조(`clean_audit.py`·`clean6.py`).
- **예측:** 소수(<10)가 PIL 실패, 전부 자체 디코더로는 열림.
- **증거:** 6/142가 PIL 실패. 두 유형:
  - **A. 헤더 오염 마커(2)** — 0x92E713CC·0xB164A9CC. `UnidentifiedImageError`. PIL 관용 모드로도 실패(인식 단계라 load 이전).
  - **B. EOI 뒤 쓰레기 꼬리(4)** — 0xC87D0000(8192B)·0xC8B66000(8192B)·0xC8B8B000(28672B)·0xC97AA000(8192B). 헤더 정상, `broken data stream`(strict). EOI는 있으나 뒤에 703~3255B 쓰레기(gap: 2830/3255/703/2945). **PIL 관용 모드로는 열림**(50x50·320x239). 자체 디코더 undec ≤0.014.
- **판단:** 가설 지지. 6장 모두 원본 바이트가 표준 미개봉. B는 스캔이 완결에 근접(관용 모드 개봉)하나 뒤 쓰레기가 strict 파서를 막고, A는 정상 프레임 앞 오염이 인식을 막는다.

### 3단계 — 트림·접합으론 불충분, 재인코딩이 보편 정규화

- **가설 / 세운 이유:** 2단계에서 B는 "EOI 뒤 쓰레기"로 보였으므로 EOI에서 자르면 열릴 것, A는 오염 마커 구간을 들어내면 열릴 것으로 예상됨.
- **실험:** B는 마지막 FFD9에서 트림, A는 SOI+APP0(20B) 뒤에 두 번째 DQT부터 접합. 각각 PIL 재시도. 별도로 6장 전부 자체 디코드→PIL로 재인코딩 후 PIL 재시도(`fix_test.py`).
- **예측:** 트림·접합으로 PIL 개봉.
- **증거:**
  - B 트림: 4장 전부 여전히 `broken data stream`(스캔 자체가 strict 기준 미완결 — ~1 MCU 부족).
  - A 접합: `UnidentifiedImageError`→`broken data stream`으로 오류만 이동(스캔 미완결 잔존).
  - 재인코딩: 6장 전부 PIL-OK(96x72·50x50·320x239 정상).
- **판단:** 트림·접합 기각(스캔이 관용 디코더 전용). 재인코딩이 6장 모두를 정규화하는 유일 보편 해법. → CLEAN 저장 전 strict 개봉 검증 후 실패 시 재인코딩(backlog).

### 4단계 — skip 비정상 크기는 `jpeg_end` fallback 산물

- **가설 / 세운 이유:** 170MB JPEG은 불가능하다. carve가 진짜 EOI를 못 찾을 때의 fallback이 과대 경계를 잡았을 것.
- **실험:** 지목 5개 파일의 head 16B·마커·내부 FFD8/FFD9 수를 스캔하고, `carve.py`·`carver/extractors.py::jpeg_end`의 경계 로직을 확인.
- **예측:** 파일 시작이 `FFD8`+오염 APPn(위양성 SOI), 진짜 EOI 부재로 fallback 경계까지 긁힘.
- **증거:**
  - 0x377D5CDA head=`FFD8 FFE6`+오염, 0x55476735=`FFD8 FFF8`, 0x290897AC=`FFD8 FFE3`, 0x5827B9A5=`FFD8 FFFE`(COM) — 전부 위양성 SOI.
  - 0xCE4CF000·0x5827B9A5는 정확히 10MB(0xA00000) = `JPEG_MAX_FALLBACK_SIZE`(extractors.py:7). 162/46/18MB는 그 초과 = `next_sig_offset` fallback(extractors.py:143-144, 상한 미적용).
  - 0xCE4CF000만 head=`FFD8 FFE1`+`Exif..MM.*`(진짜 EXIF).
- **판단:** 가설 지지. skip 비정상 크기는 carve fallback 2종(10MB 상한 / 다음 시그니처까지). 대부분 위양성 SOI가 진짜 EOI를 못 만나 발생.

### 5단계 — skip 71건 구조 분류: 63건 위양성/빈조각, 8건만 진짜 SOF 보유

- **가설 / 세운 이유:** DEC-OK가 0(전부 디코더 구성 실패)이라 SKIP됐다. 진짜 이미지와 위양성을 가르려면 precision=8·comps=3·sane 해상도의 SOF 존재 여부가 지표.
- **실험:** 71건 전수에 대해 SOI 직후 마커·SOF(FFC0/FFC2/FFC1) 해상도·DHT/SOS 유무·자체 디코더 구성 여부를 집계(`skip_audit.py`), 이어 길이 오염 무시하고 FFC0에서 직접 precision·dims를 읽어 sane 여부 판정(`skip_final.py`).
- **예측:** 다수가 SOF 해상도 비정상(랜덤 바이트) = 위양성.
- **증거:**
  - DEC-OK=0/71.
  - **위양성 62**(SOF 위치가 랜덤바이트 = 65510x62975 등 비정상 해상도), 합계 311MB.
  - **빈/초소형 1**(0x92EC578C, 64B).
  - **진짜 SOF 보유 8** — 그중 sane+구조 완비: 0xCA067000(2448x1832)·0xCE4CF000(160x120)·0xC8039000(320x240)·0xCA9AA000(240x240)·0xB15F1BCC(96x72)·0xB164F7CC(96x74)·0xBD2E03F2(160x1128).
- **판단:** SKIP 71 = 63 사실상 복구 불가(위양성·빈조각, SKIP이 정답) + 8 진짜 SOF. 선행 W3 보고서의 "68건 재료 없음"을 63으로 정정. 8건의 미복구 원인은 6단계.

### 6단계 — 진짜 SOF 8건의 헤더 복구 실패 4유형

- **가설 / 세운 이유:** 8건은 재료가 있는데 `headerfix.reconstruct`가 None을 반환한다. 어느 게이트/스캐너에서 막히는지 계측 필요.
- **실험:** 각 파일에 대해 sos/sof/dqt/dht 후보 수, own_ok, variant 수, build_decoder 성공 수, probe≥floor 통과 수, 통과분의 fit·소비율·undec를 계측(`trace_recon.py`·`deep2.py`).
- **예측:** 후보 스캐너가 재료를 못 찾거나(후보 0) probe가 바닥 미달일 것.
- **증거:** 4유형 —
  - **SOF후보=0(3)** — 0xCA067000·0xB15F1BCC·0xBD2E03F2. `sof_candidates`는 `00 11 08`(len=0x0011·precision=8) 패턴. 0xCA067000 진짜 SOF0@0x12A = `FF C0 08 11 08 07 28 09 90` — **길이 상위바이트가 0x08**(정상 0x00)이라 패턴 불일치. 프레임 필드(H=0x0728=1832·W=0x0990=2448)는 그 자리에 정상. 1MB 밖 매치는 전부 내부 썸네일 SOF.
  - **DQT후보=0(1)** — 0xC8039000(comps=9 파싱). `dqt_candidates`가 유효 양자화표를 못 뽑음.
  - **probe<floor(2)** — 0xB164F7CC(160 빌드, 전부 run=0)·0xCA9AA000(own_ok, run=2/floor=30). scan_start 정렬 소실.
  - **probe 통과·하위 게이트 탈락(1)** — 0xCE4CF000. 후보 4개가 run 54~85≥floor 30, **완전 디코드**(done=150/150, u_plain=0.000). 그러나 fit 소비율 cf=0.003~0.006 ≪ FIT_CONSUME_LO=0.25로 전원 탈락.
- **판단:** 0xCA067000은 수동으로 진짜 dims(2448x1832)+올바른 DQT를 주면 opening_probe run=900(윈도 최대). 즉 재료는 충분, `sof_candidates` 패턴이 길이 오염을 허용 안 해 놓친다. 0xCE4CF000은 다음 단계에서 원인 추적.

### 7단계 — 0xCE4CF000 소비율 탈락의 원인은 과다 카빙 꼬리

- **가설 / 세운 이유:** 6단계에서 0xCE4CF000은 완전 디코드인데 cf가 비정상적으로 작다(0.003). `_consumed_fraction` 분모는 scan_start→그 뒤 첫 FFD9. 파일이 10MB로 과다 카빙됐으니(4단계) 뒤쪽 쓰레기의 가짜 FFD9가 분모를 부풀렸을 것.
- **실험:** 통과 후보별 eb(소비 비트)·cf·scan_start를 나열하고, 후보들의 run 순위를 확인(`deep2.py`), 대표 후보 렌더(`ce4cf_candidates.py`).
- **예측:** 진짜 스캔은 수 KB에서 끝나는데 분모가 ~MB → cf 급락. scan_start별로 진짜/쓰레기 후보가 섞이고 쓰레기가 run 상위.
- **증거:** eb=29446비트(~3.7KB 소비), cf=0.003 → 분모 ~1.2MB(첫 FFD9가 scan_start에서 1.2MB 하류). scan_start=0x4E3 후보 run=71이 진짜(상단 정렬 디코드), scan_start=0x309B 후보 run=85(더 높음)는 하단 색 캐스트·전역 노이즈 결함. 즉 쓰레기 scan_start가 진짜보다 run 상위.
- **판단:** 가설 지지. 과다 카빙이 (1) 가짜 FFD9로 소비율 분모를 부풀려 진짜 후보를 게이트에서 탈락시키고, (2) 고엔트로피 꼬리가 쓰레기 scan_start도 완주시켜 run 순위를 오염. **과다 카빙이 헤더 복구를 직접 방해한다.** → 거대 파일 내부에 진짜 이미지가 있는지 확인 필요(8단계).

### 8단계 — 거대 파일 내부의 묻힌 진짜 JPEG

- **가설 / 세운 이유:** 7단계에서 과다 카빙 꼬리가 고엔트로피(=이미지 데이터)로 보였다. 위양성이 다음 시그니처까지 긁는다면(4단계), 그 사이의 진짜 JPEG도 추출 범위에 포함돼 embedded로 건너뛰어졌을 것(carve.py:44-56).
- **실험:** 거대 파일 5개 내부에서 오프셋>0의 `FFD8 FFE0`(JFIF)·`FFD8 FFE1`(Exif) 서명을 찾아 각각 `jpeg_end`로 경계 추정 후 자체 디코더로 디코드, undec<0.5·std>20을 "진짜 후보"로 집계(`buried.py`).
- **예측:** 위양성(0x377D5CDA 등)은 내부 진짜 서명 0, 진짜 EXIF 시작(0xCE4CF000)은 내부에 다수.
- **증거:**
  - 순수 위양성 4개(0x377D5CDA 162MB·0x55476735 46MB·0x290897AC 18MB·0x5827B9A5 10MB): 내부 진짜 서명 **0개**.
  - 0xCE4CF000(10MB): 내부 Exif 8개 — 2592x1944×6(undec 0.93~0.98, 상단 수 행만 정렬·나머지 undec) + 480x640@0x975000(undec 0.00).
- **판단:** 가설 지지. 위양성 거대 파일은 통째로 비이미지(수정해도 복구 대상 없음, 디스크 절약만). 0xCE4CF000류(진짜 이미지 영역 과다 카빙)는 내부에 디코드 가능한 진짜 JPEG을 포함한다. → 코퍼스 전수로 규모 확인(9단계).

### 9단계 — 코퍼스 전수: 캐리어 5개에 62장 묻힘, 전부 손실(별도 추출 0)

- **가설 / 세운 이유:** 8단계가 0xCE4CF000만의 문제인지, 과다 카빙 일반의 패턴인지 불명. 1MB+ 카빙 파일 전수 스캔 필요.
- **실험:** `output/jpeg/`의 1MB 이상 파일 전수에서 내부 JFIF/Exif 서명을 디코드(undec<0.5·std>20 집계, `buried_sweep.py`). 각 묻힌 이미지의 절대 오프셋(=캐리어오프셋+내부오프셋)에 별도 카빙본이 있는지(±64B), 캐리어의 report 분류, 소형(≤160²=썸네일급) 여부를 대조(`verify_lost.py`).
- **예측:** 소수 캐리어에 집중. 묻힌 이미지 다수는 별도 추출 안 됨(embedded-skip) → 손실.
- **증거:**
  - 캐리어 5개, 묻힌 디코드가능 62장. **별도 추출(dup)=0**(전부 손실). `jpeg_thumbnails/` 미생성(--save-thumbnails 미사용)이라 embedded는 완전 소거.
  - 파일별(사진=비썸네일 / 썸=≤160²):
    - 0x92EC750C(1.2MB, **HEADER_RECOVERED**): 사진 0 / 썸 17(96x72).
    - 0xC8A59000(1.1MB, **HEADER_RECOVERED**): 사진 22(324x243×19 undec 0.00, 407x253, 1836x1377 undec 0.25, 459x345) / 썸 0.
    - 0xCA067000(8.1MB, SKIP): 사진 12(2448x1836 undec 0.00, 1377x1836 undec 0.18, 480x640×2, 306x230×2, 408x306, 345x459×2, 240x320×2, 320x240) / 썸 7.
    - 0xCA9C5000(7.8MB, **HEADER_RECOVERED**): 사진 3(2592x1944 undec 0.00·수평 색밴드 결함, 324x243, 406x304) / 썸 0.
    - 0xCE4CF000(10MB, SKIP): 사진 1(480x640 undec 0.00) / 썸 0.
  - 합계 사진 38 + 썸 24. 렌더 결함 패턴(내용 묘사 없이): 다수 undec 0.00 완전 디코드, 0xCA9C5000@0x4E3000는 undec 0.00이나 수평 색밴드 손상, 0xCA067000@0x624000(2448x1836)은 하단 ~55% 시안 색 캐스트, 일부 undec 0.18~0.34 부분.
- **판단:** 가설 지지. 과다 카빙+embedded-skip이 사진 ~38장을 손실. 캐리어 3개가 HEADER_RECOVERED 오분류 — 헤더 복구가 다중 이미지 덩어리를 한 장으로 채택해 나머지를 묻었다(W3 헤더 복구본 61 중 이 오분류 포함). `usb.img`(3.5GB) 보존돼 재카빙 가능.

## 기각된 가설 / 막다른 길

- **B군(EOI 뒤 쓰레기) → EOI 트림이면 열린다:** 4장 전부 트림 후에도 `broken data stream`. 스캔 자체가 strict 기준 ~1 MCU 미완결(관용 디코더·PIL 관용 모드 전용). **교훈: "EOI 뒤 쓰레기"로 보여도 꼬리 제거만으론 부족 — 관용 디코더만 여는 파일은 스캔이 strict 미완결일 수 있고, 재인코딩이 유일한 보편 정규화다.**
- **A군(헤더 오염 마커) → 오염 구간 접합이면 열린다:** `UnidentifiedImageError`가 `broken data stream`으로 이동만. **교훈: 헤더 오염 구간 excision은 오류 종류만 바꾸고 스캔 미완결을 못 고친다. strict 개봉을 목표로 하면 재인코딩으로 통일.**
- **0xCE4CF000 헤더 복구 = probe run 최댓값 후보 채택이면 된다:** 쓰레기 scan_start(0x309B) run=85 > 진짜(0x4E3) run=71. run 상위가 쓰레기. **교훈: 과다 카빙 꼬리가 있으면 probe run 단독으로 scan_start를 못 고른다 — 고엔트로피 꼬리는 잘못된 정렬도 완주시킨다.**
- **거대 skip은 전부 위양성 쓰레기다(수정=디스크 절약뿐):** 위양성 4개는 맞으나 0xCE4CF000·0xCA067000 등은 내부에 진짜 사진. **교훈: 과다 카빙 파일을 "위양성"으로 일괄 처리하지 말 것 — 진짜 이미지 영역이 과다 카빙되면 내부에 손실된 사진이 있다. head 서명(FFD8 FFE1 Exif vs FFD8+오염 마커)으로 갈린다.**
- **SKIP 71 = 재료 없음(선행 W3 보고서):** 실제 63(위양성+빈조각) + 8(진짜 SOF). **교훈: DEC-OK=0을 "재료 없음"과 동일시하지 말 것 — 길이 오염으로 파서가 실패해도 FFC0에서 dims는 직접 읽힌다.**

## 사용한 방법·도구

scratchpad(`.../add512c1-.../scratchpad/`) 일회성 스크립트, 전부 `output/jpeg/`·`output/jpeg_recovered_w3/` 대상:

- `clean_audit.py` — CLEAN 142 전수 PIL 개봉 감사(6장 실패 색출).
- `clean6.py`·`fix_test.py` — 6장 마커·EOI·PIL 관용 모드·트림/접합/재인코딩 검증.
- `skip_audit.py`·`skip_final.py` — skip 71 구조 분류(SOF dims·DHT/SOS·sane 판정).
- `trace_recon.py`·`deep2.py` — `headerfix.reconstruct` 게이트 계측(후보 수·probe·소비율).
- `buried.py`·`buried_sweep.py`·`verify_lost.py` — 거대/1MB+ 파일 내부 묻힌 JPEG 스캔·손실 검증(절대 오프셋 대조).
- 렌더 육안(`render_candidates.py`·`ce4cf_candidates.py`·`buried_montage.py`): 판정은 지표(undec)·결함 패턴(색 캐스트·색밴드)으로만.

관련 코드: `carve.py:44-56`(embedded-skip)·`carver/extractors.py:7,57-145`(jpeg_end fallback)·`carver/scanner.py:4`(FFD8FF 3바이트 시그니처)·`carver/headerfix.py:99-111`(sof_candidates)·`carver/resync.py:370-374`(CLEAN 분기).

## 결론

W3 후속 감사가 3개 질문에 답하고 그보다 큰 결함을 노출했다:

1. **CLEAN 미개봉 6장** — 관용 디코더가 표준 미개봉 파일을 통과시켜 CLEAN 원본 복사(2 헤더 오염 마커, 4 EOI 뒤 쓰레기+스캔 미완결). 트림·접합 불충분, 재인코딩이 유일 정규화. → CLEAN 저장 전 strict 개봉 검증 후 실패 시 재인코딩(backlog).
2. **skip 비정상 크기** — `jpeg_end`의 fallback 2종(10MB 상한 / 다음 시그니처). 대부분 위양성 SOI가 진짜 EOI를 못 만남.
3. **헤더 미복구 71건** — 63 위양성/빈조각(SKIP이 정답) + 8 진짜 SOF. 8건 실패 4유형 중 최대 가치는 0xCA067000(2448x1832, 길이 상위바이트 오염으로 `sof_candidates` 패턴 미스, 수동 dims로 run=900).
4. **[최대 발견] 과다 카빙이 사진 ~38장 손실** — `jpeg_end` 과다 경계 + carve embedded-skip(carve.py:44-56)이 연속 이미지를 한 파일로 뭉치고 내부 시그니처를 건너뜀. 캐리어 5개(0xC8A59000·0xCA067000·0xCA9C5000·0xCE4CF000·0x92EC750C), 묻힌 디코드가능 62장(사진 38·썸 24), 별도 추출 0(전부 손실). 캐리어 3개가 HEADER_RECOVERED 오분류. `usb.img` 보존.

과다 카빙은 W3 헤더 복구 확장(~3-5건)보다 큰 레버(~38장)이며 성격이 carve 단계 문제다. 수정 후보(승인 대기): (a) carve 경계 수정+재카빙, (b) 기존 캐리어 de-bury(재카빙 없이), (c) `sof_candidates` 길이 오염 허용(헤더 복구 확장), (d) CLEAN strict 개봉 검증+재인코딩. backlog에 등재.
