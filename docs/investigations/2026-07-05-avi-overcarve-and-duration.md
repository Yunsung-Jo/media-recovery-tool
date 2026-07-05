# 조사 기록 — AVI 손실은 JPEG 과다 카빙이 삼킴, 541시간 재생표시는 오디오 필드+인덱스 부재(카빙 무관)

- **날짜:** 2026-07-05
- **한 줄:** AVI 추출이 C1 이후 42→39로 줄고 플레이어가 541시간을 표시하는 두 현상을 조사 — 개수 감소는 JPEG 과다 카빙이 AVI를 삼킨 것(44개 중 5개 손실), 541시간은 오디오 스트림 `dwLength` 오독+`idx1` 부재로 카빙과 무관. `_next_avi` 하드 경계로 AVI 39→44 복구, 사용가능 사진 884 회귀 0.
- **결론 문서:** [ADR 0008](../adr/0008-jpeg-boundary-stops-at-avi.md) · [carve 스펙](../specs/0001-carve.md) · [보고서](../reports/2026-07-05-avi-overcarve-fix.md) · 선행 [ADR 0007](../adr/0007-carve-corrupt-header-boundary.md)

> 시점 기록(랩 노트). C1(과다 카빙 수정) 머지 직후, 사용자가 AVI 추출을 확인 요청한 데서 시작한 조사.

---

## 증상

C1(JPEG 과다 카빙 수정, main 176f69e) 재카빙 결과 `output_c1`에서 두 가지가 관찰됐다.

1. **AVI 추출 개수 감소**: `output/avi` 42개 → `output_c1/avi` 39개(−3). C1은 `jpeg_end`만 고쳤고 `avi_end`는 건드리지 않았는데 AVI 개수가 변했다.
2. **플레이어 재생시간 이상**: 추출된 AVI를 플레이어로 열면 총 재생시간이 541시간 등으로 표시.

사용자 질문: (1) AVI도 과다 카빙하는가, (2) 개수 감소 원인은 무엇이며 추출이 잘못된 것인가, (3) 541시간은 과다 카빙과 연관 있는가.

## 조사 과정 (가설 → 예측 → 증거)

### 1단계 — AVI 자체의 과다 카빙 여부

- **가설 / 세운 이유:** JPEG처럼 AVI도 세그먼트 길이를 신뢰해 뒤 파일 위로 점프하면 과다 카빙일 수 있다. C1이 JPEG 과다 카빙을 실증했으니 AVI도 의심.
- **실험:** `output_c1`의 AVI 39개 각 범위 `[offset, offset+filesize]`가 뒤따르는 다른 top-level AVI/JPEG 오프셋을 포함하는지 검사. `avi_end`(extractors.py:213) 코드 확인.
- **예측:** 만약 과다 카빙이면 일부 AVI 범위가 다음 top-level 히트를 포함할 것.
- **증거:** 내부에 다른 top-level 시그니처를 포함하는 AVI = **0/39**. `avi_end`는 RIFF 헤더의 단일 `chunk_size` 길이 필드만 읽고, 비정상 시 fallback을 `min(next_sig, offset+500MB, size)`로 캡. 세그먼트 다중 워크가 없음.
- **판단:** AVI 추출 자체는 과다 카빙하지 않는다(Q1 = 아니오). 개수 감소는 다른 원인.

### 2단계 — 사라진 AVI를 지금 덮는 파일 식별

- **가설 / 세운 이유:** AVI가 안 줄었는데 개수가 줄었다면, 어떤 파일이 그 AVI 오프셋을 "임베디드"로 덮어 carve embedded-skip(carve.py:42-60)이 건너뛴 것이다.
- **실험:** `output`(42) vs `output_c1`(39) AVI 오프셋 집합 diff. 사라진 각 오프셋을 지금 덮는 `output_c1` 파일(jpeg/avi)을 범위로 찾고, 원본 usb.img에서 RIFF 헤더 검증.
- **예측:** 사라진 AVI는 어떤 파일 범위 안에 들어갈 것. 그 파일이 AVI면 AVI 과다 카빙, JPEG면 JPEG 과다 카빙.
- **증거:** 4개 사라짐(0x026F1000·0x03112000·0x31860000·0xB1C9F000), 1개 생김(0xB167A000), 순 −3. 사라진 4개 **전부 JPEG이 덮음**:
  - 0x026F1000(RIFF valid, chunk 10.6MB) ⊂ 0x025BF73C.jpg
  - 0x03112000(13.3MB) ⊂ 0x02ECBFAA.jpg
  - 0x31860000(19.6MB) ⊂ 0x30FB55DD.jpg (end=0x319B55DD = 시작+정확히 10MB=`JPEG_MAX_FALLBACK_SIZE`)
  - 0xB1C9F000(10.3MB) ⊂ 0xB1C9D80C.jpg
- **판단:** AVI가 아니라 **JPEG이 AVI를 삼켰다.** 0x31860000은 정확히 10MB fallback으로 절단된 JPEG이 삼킴. 왜 C1 후에 새로 삼켰는지 확인 필요.

### 3단계 — 전수 범위 정량화 + C1 인과

- **가설 / 세운 이유:** JPEG이 AVI를 삼키는 게 C1 회귀인지 기존 문제인지 확인해야 한다. 2단계에서 3개는 C1 후 사라졌고 1개는 생겼으니 C1이 상태를 바꿨다.
- **실험:** usb.img에서 RIFF+AVI 시그니처 전수 스캔. OLD·NEW 각각에서 각 AVI 시그니처를 extracted / embedded_in_jpeg / embedded_in_avi로 분류. 덮는 JPEG의 OLD vs NEW 크기 대조.
- **예측:** C1 회귀라면 embedded_in_jpeg 개수가 OLD→NEW로 늘 것.
- **증거:** usb.img RIFF+AVI = **총 44개**. OLD: extracted 42, embedded_in_jpeg 2. NEW: extracted 39, embedded_in_jpeg 5, embedded_in_avi 0(AVI가 AVI를 삼키는 경우 없음, 1단계 재확인). 덮는 JPEG 크기 변화:
  - 0x30FB55DD: OLD 4.18MB(end 0x313B31BF, AVI 앞에서 끝) → NEW 10MB(AVI 삼킴)
  - 0x025BF73C: OLD 507KB(AVI 앞) → NEW 8.58MB(삼킴)
  - 0x02ECBFAA: OLD embedded(자신도 미추출) → NEW 4.6MB(삼킴)
- **판단:** JPEG-swallows-AVI는 OLD 2건(사전 존재) → NEW 5건. C1이 **1건 고치고(0xB167A000: embedded→extracted) 4건 새로 유발.** C1이 어떤 JPEG의 경계를 바꾸거나(0x30FB55DD 4.18→10MB) 이전에 임베디드였던 JPEG을 top-level로 노출(0x02ECBFAA)해, 그 JPEG이 뒤 AVI를 삼키게 됐다.

### 4단계 — 근본 원인: JPEG 경계 함수가 AVI 시그니처를 미인식

- **가설 / 세운 이유:** JPEG이 AVI 위로 확장한다면, `jpeg_end`의 경계 계산이 AVI(RIFF) 시그니처를 정지점으로 안 본다는 뜻. C1이 도입한 `_corrupt_boundary`와 기존 SOS `upper`를 코드로 확인.
- **실험:** extractors.py의 `_next_header`(:43), SOS 경로 `upper`(:156), `_corrupt_boundary`(:88) 정독. 0x30FB55DD·0x025BF73C가 어느 경로로 AVI를 삼키는지 대조.
- **예측:** 경계 함수들이 JPEG 헤더(FF D8 FF E0–EF)와 +10MB만 캡으로 쓰고 RIFF는 무시할 것.
- **증거:** 확정. `_next_header`는 FF D8 FF E0–EF만 탐색. SOS `upper=min(_next_header, offset+10MB, size)`, `_corrupt_boundary`도 동일 캡. 셋 다 RIFF 미인식. 0x30FB55DD는 SOS 후 진짜 EOI를 못 찾아 10MB fallback(뒤 AVI 무시). 0x025BF73C는 `_next_header`가 AVI 내부 MJPEG 프레임(FF D8 FF E0–EF, 0x02DEEC90)을 다음 헤더로 잡아 거기서 끝(AVI 시작 삼킴).
- **판단:** JPEG 전진 경계가 AVI 시그니처를 하드 정지점으로 취급해야 한다. AVI는 JPEG 안에 정상적으로 존재할 수 없으므로(RIFF+AVI 12바이트 구조) 안전한 경계.

### 5단계 — 541시간 재생표시는 카빙 무관(오디오 필드+인덱스 부재)

- **가설 / 세운 이유:** 541시간이 과다 카빙(파일이 너무 큼)에서 오는지, 헤더 필드에서 오는지 분리해야 한다. AVI 재생시간은 파일 크기가 아니라 헤더 `avih`/`strh` 필드에서 계산된다.
- **실험:** 39개 AVI 헤더 파싱 — `avih`(usPerFrame·totalFrames), 스트림별 `strh`(scale·rate·length), `idx1` 유무. 추출 크기 vs `8+chunk_size`(carve OK/OVER/UNDER). 경계 직후 16바이트를 usb.img에서 확인.
- **예측:** 과다 카빙이면 carve=OVER가 나오거나 헤더 재생시간이 정상인데 파일만 큼. 카빙 무관이면 carve=OK이고 재생시간 필드가 원본 특성.
- **증거:**
  - 39/39 **carve=OK**(추출 크기 == 선언 크기). OVER·UNDER 0.
  - 영상 스트림 정상: 640×480 15fps, 정상 dur 최대 ~10분. `avih.totalFrames×usPerFrame`도 전부 sane.
  - 2스트림(영상+오디오). 오디오 `auds`: scale=1, rate=8000, **length=오디오 샘플 수**(예 1,451,577). 정상 dur=length/rate=181초(3분). `length/scale`(=length)를 초로 오독하면 **403시간**. 파일별 274~1401시간 대역 → 541시간은 그중 한 파일.
  - **38/39 `idx1`/`indx` 인덱스 없음.** 경계 직후 바이트는 전부 `00…` 제로 패딩 — idx1을 자른 흔적 0개.
- **판단:** 541시간은 카빙과 무관(Q3). 원본이 인덱스 없이 녹화한 대시캠류 AVI를 그대로 추출한 것이고, 인덱스 부재로 플레이어가 오디오 `dwLength`를 오독한다. 영상 데이터는 온전(경계 정확). → 백로그 C5(별도 AVI 리페어).

### 6단계 — `_next_avi` 하드 경계 수정 + 전수 검증

- **가설 / 세운 이유:** 4단계 근본 원인대로, JPEG 경계에 다음 AVI 시그니처를 하드 정지점으로 추가하면 5개 AVI 전부 복구되고 JPEG 회귀는 없을 것(AVI는 JPEG 안에 정상 존재 불가).
- **실험:** `_next_avi(data, start, hi)` 추가 — [start,hi) 첫 RIFF…AVI 반환, 없으면 hi. SOS `upper`와 `_corrupt_boundary`에 삽입. 단위 검증(5개 덮개 JPEG의 `jpeg_end`가 AVI에서 정지), 재카빙 `output_c2`, `recover --time-budget 0` 전수 재복구, c1 대비 파일별 action 대조.
- **예측:** 재카빙 AVI 39→44, 사라진 21 JPEG은 전부 복구된 AVI 내부 MJPEG 프레임(c1 SKIP), 사용가능 사진 884 유지(회귀 0).
- **증거:**
  - 단위: 5개 덮개 JPEG 전부 해당 AVI 오프셋에서 정지(0x025BF73C→0x026F1000 등).
  - 재카빙(42초): **AVI 39→44**(+5, 5개 carve=OK), JPEG 999→978(−21), ERROR 0. c1\c2 AVI 손실 0.
  - 사라진 21 JPEG **전부 복구된 5개 AVI 범위 내부**(MJPEG 프레임), c1에서 전부 SKIP_UNDECODABLE(usable 0).
  - 덮개 JPEG 중 usable 2개 디코드 불변: 0xB1C9D80C c1(175KB)·c2(6KB) 둘 다 PIL 96×96 RGB(진짜 이미지 6KB, c1은 뒤에 169KB AVI 붙어 있던 것); 0x92EDB60C CLEAN 유지.
  - 전수 재복구: c2 clean 187·HR 86·REC 611·FAILED 25·SKIP 69(−21)·ERROR 0. 978 공통 파일 **action 변화 0종**, usable→non-usable 0건. **사용가능 사진 884→884(Δ0).**
- **판단:** 세 기준 충족. AVI 39→44 복구, 실사진 회귀 0, 구조 온전. 수정 채택.

## 기각된 가설 / 막다른 길

- **AVI 개수 감소를 plain 추출 개수(999→978)로 판정.** −21을 손실로 보면 회귀로 오판. 실제로는 전부 복구된 AVI 내부 MJPEG 프레임(c1에서 top-level로 잘못 추출된 조각)이 정상적으로 embedded로 전환된 것. **교훈: carve 회귀는 plain 개수가 아니라 recover 파이프라인의 파일별 action으로 판정한다(C1 교훈 재확인).**
- **JPEG 경계 캡을 `next_sig`(다음 히트)로 전면 교체.** `next_sig`는 현재 JPEG 내부 EXIF 썸네일일 수 있어(FF D8 FF도 히트) JPEG을 자기 썸네일에서 절단한다. 그래서 `_next_avi`만 별도로 — RIFF+AVI 12바이트 구조는 JPEG 내부에 정상 등장하지 않아 saw_sof여도 안전한 하드 경계. **교훈: 다음 파일 경계로 임의의 다음 히트를 쓰면 내부 임베디드(EXIF 썸)와 충돌한다. 절대 안전한 시그니처(AVI)만 하드 경계로.**
- **541시간을 과다 카빙 증상으로 추정.** carve=OK(크기 일치)·경계 직후 제로 패딩(idx1 미절단)로 반증. 원인은 원본의 인덱스 부재+오디오 샘플 수 오독. **교훈: 재생시간 이상은 파일 경계가 아니라 헤더 시간축 필드에서 먼저 확인 — AVI 재생시간은 파일 크기와 무관하다.**
- **AVI 리페어(idx1 재생성)를 이번 카빙 작업에 포함.** 541시간은 카빙과 독립적이라 경계 수정으로 안 고쳐진다(별도 후처리 기능). 스코프 분리 → 백로그 C5.

## 사용한 방법·도구

scratchpad 스크립트(전부 usb.img·output·output_c1·output_c2 대상):
- `avi_probe.py`·`avi_probe2.py`: 사라진 AVI 덮개 파일·RIFF 검증·AVI 과다카빙 검사
- `avi_scope.py`: usb.img RIFF+AVI 전수 스캔·OLD/NEW 분류
- `avi_dur.py`·`avi_dur_old.py`·`avi_streams.py`·`avi_541.py`: AVI 헤더 재생시간 필드·스트림·인덱스 파싱, 541시간 재현
- `avi_idx.py`: 경계 직후 바이트로 idx1 절단 여부 확인
- `verify_fix.py`: 5개 덮개 JPEG의 `jpeg_end` 단위 검증
- `jpeg_diff.py`·`c1_frames2.py`·`covering.py`·`decode_check.py`·`action_diff.py`: c1 vs c2 JPEG diff·프레임 분류·덮개 디코드·전수 action 대조
- 재카빙 `python carve.py usb.img -o output_c2`, 재복구 `python recover.py output_c2/jpeg -o output_c2/jpeg_recovered --time-budget 0 -j 0`

## 결론

1. **AVI 추출 자체는 과다 카빙하지 않는다**(0/39, `avi_end`는 단일 RIFF 길이 필드+next_sig 캡).
2. **AVI 개수 감소(42→39)의 원인은 JPEG 과다 카빙이 AVI를 삼킴** — usb.img 44개 AVI 중 5개가 JPEG 범위에 임베디드(OLD 2건→NEW 5건). C1이 JPEG 경계를 바꿔 4건 새로 유발·1건 해소. 근본 원인은 `_next_header`·SOS `upper`·`_corrupt_boundary`가 AVI(RIFF) 시그니처를 경계로 인식하지 않은 것.
3. **수정**: `_next_avi` 하드 경계 추가 → AVI 39→44 복구, JPEG 978(−21은 AVI 내부 MJPEG 프레임의 정상 embedded 전환), 사용가능 사진 884→884(회귀 0), ERROR 0, 테스트 65→69. 결정은 [ADR 0008](../adr/0008-jpeg-boundary-stops-at-avi.md), 동작은 [carve 스펙](../specs/0001-carve.md).
4. **541시간 재생표시는 카빙과 무관** — 원본 AVI의 `idx1` 인덱스 부재(38/39)+오디오 스트림 `dwLength`(샘플 수) 오독. 영상 데이터·경계는 온전. 별도 AVI 리페어는 백로그 C5.
