# 0007. carve 과다 카빙을 헤더 마커·길이 검증으로 방지하고, 손상 첫 이미지 복원은 recover에 위임

- **날짜:** 2026-07-05
- **상태:** Accepted

---

## 배경

carve `jpeg_end`(`carver/extractors.py`)의 헤더 마커 워크는 세그먼트 길이 필드를 신뢰해
`pos += 2 + seg_len`으로 전진한다. 디스크에 **여러 사진이 연속으로 배치**되어 있고 앞 사진의
헤더가 손상되면, 워크가 뒤따르는 진짜 이미지들을 세그먼트 본문(또는 엔트로피)으로 오해하고
그 위로 점프해 멀리 있는 EOI/10 MB/다음 시그니처까지 경계를 잡는다. 그 범위 내부의 `FF D8`
히트는 carve가 embedded로 건너뛰므로(`carve.py:44-59`), 묻힌 진짜 이미지들이 출력에서 사라진다.

관찰된 손상 유형(전수 감사, [조사 기록 2026-07-04](../investigations/2026-07-04-overcarve-buried-images.md)):
- 손상된 **길이 필드**: 0xC8A59000의 DHT `len=32799`(정상 ~181), 0xCA067000의 SOF `len=2065`.
- 첫 이미지의 불완전 헤더 뒤 **엔트로피 진입**: `FF 00`(스터핑)·`FF 41`·`FF B0` 등 `0xC0` 미만
  바이트를 길이 세그먼트로 오해.

캐리어 5개(0xC8A59000·0xCA067000·0xCA9C5000·0xCE4CF000·0x92EC750C)만으로도 디코드 가능한
디코드 가능한 실제 사진 62장(2448x1836·480x640 등)이 묻혀 손실됐고, 1 MB 미만 소형 캐리어까지 포함하면
더 많다. carve 단계 문제이며 recover로는 복구 불가(입력 파일 자체가 여러 이미지의 덩어리).

## 결정

`jpeg_end`의 헤더 마커 워크에 **3중 검증**을 추가하고, 위반 시 `_corrupt_boundary`로 경계를 축소한다.

1. **유효 마커** — 길이 세그먼트 브랜치에서 `mb < 0xC0`이면 손상. 유효 JPEG 마커는 전부 `0xC0`
   이상(SOF `C0`–`CF`·DQT `DB`·DHT `C4`·APPn `E0`–`EF`·COM `FE`)이고, 이미 별도 처리되는
   `0x01`(TEM)·`D0`–`D7`(RST)를 지난 뒤 `0xC0` 미만이 나오면 엔트로피(`FF 00`) 또는 쓰레기다.
   정상 헤더는 SOS 전에 `mb < 0xC0`을 담지 않으므로 정상 파일 불변(회귀 안전).

2. **마커별 길이 상한** — DHT 1200·DQT 600·SOF 100·DRI 10바이트 초과면 손상. 각 값은 **합법
   최댓값**에서 왔다: DHT 4테이블 = 2+4×(1+16+256)=1094(상한 1200), DQT 4테이블 16bit =
   2+4×(1+128)=518(상한 600), SOF 2+6+3·Nf(3컴포넌트 17, ≤30컴포넌트 98 → 상한 100 — 이 코퍼스는
   3컴포넌트 YCbCr), DRI=4(상한 10). **APPn·COM은 상한 없음**(≤65535 유효 — 실제 46 KB Exif가
   존재: 가드 0x1704A000 APP1=46628바이트).

3. **손상 시 경계**(`_corrupt_boundary`) — **SOF를 이미 지났으면**(=유효 이미지) 다음 진짜 헤더
   (`_next_header`, `FF D8 FF E0`–`EF`)로, **SOF 도달 전이면**(=위양성 의심) 다음 시그니처
   (`next_sig`)로 축소한다. 상한은 `offset + 10 MB`. 이로써 손상 첫 이미지 뒤 연속된 진짜
   이미지들이 별도 히트로 추출된다.

**손상 첫 이미지 자체의 복원은 carve가 하지 않는다** — 경계만 바로잡아 별도 파일로 분리하고,
손상 헤더 복구는 recover의 header-recovery([ADR 0006](0006-header-recovery-structural-gates.md))에
맡긴다. carve 단계에서 그 조각을 plain 디코드로 판정·보존하려 하면 오히려 뒤 이미지를 삼킨다(대안 참조).

부수적으로, 축소된 조각이 `parse_header`의 미검증 세그먼트 길이 읽기(`data[q]` 인덱스 초과)를
노출해 `end = min(i + seg_len, n)` 버퍼 캡으로 수정했다(`carver/jpegdecode.py`).

## 대안

| 대안 | 기각 이유 |
|------|----------|
| **손상 안 건드리고 유지** (기존 동작) | 캐리어 5개에서 디코드 가능한 실제 사진 62장 손실. 전수로 사용가능 이미지가 740에 정체. |
| **SOS-aware 경계** (손상 시 현재 이미지 SOS를 찾아 그 스캔 뒤 헤더로) | base(손상 첫 이미지) 보존을 노렸으나 순증감 +102 < 단순 +106, 0xCA9C5000은 buried 1장을 삼켜 3→2. 손상 헤더 이미지의 "보존"은 plain 디코더로 판정 불가(header-recovery 영역)라 흉내 내면 buried 손실. |
| **strict marker-landing** (세그먼트 점프 후 `pos`가 `0xFF` 착지 안 하면 손상) | 전수 손실 8로 악화. 유효 46 KB APP1 뒤가 우연히 `FF`면 통과(0xBD038000 미해결), 0xCE4CF000은 +1276→+0.6 KB로 붕괴. 착지 바이트로 "유효 APP1 vs 손상 APP1"을 못 가른다(엔트로피에 `FF` 흔함). |
| **모든 손상을 `next_sig`로 축소** | EXIF 썸네일 파일 붕괴 — 정상 이미지의 썸네일이 유효 APP1 안 `next_sig`라 거기서 잘림. 0xCE4CF000도 +0.6 KB로 손실. |
| **길이 검증만**(유효마커 검사 없이) | 0x92EC750C(15장)·0xCA9C5000(부분)의 `FF 00` 엔트로피 방황을 못 잡아 과다 카빙 잔존. |
| **de-bury 후처리**(carve 후 조각 안 임베디드 이미지를 디코드로 분리) | carve 경계는 그대로 두고 복구만 하는 방식. root 수정(경계 자체)이 더 깨끗하고 재카빙으로 전 파이프라인 베이스라인이 정합. root fix가 커버하므로 불필요. |

## 결과

**실제 영향**
- `jpeg_end`에 `_seg_sane_max`·`_corrupt_boundary` 추가, 마커 워크에 유효마커·길이 검증·`saw_sof`
  추적. `parse_header` 길이 캡. 회귀 테스트 4개(손상 DHT 경계·SOF-게이트·`FF00` 헤더·parse 크래시).
- usb.img 재카빙 822→999 파일, `recover.py --time-budget 0` 재복구로 **사용가능(RECOVERED+
  HEADER_RECOVERED+CLEAN) 740→884(+144)**, ERROR 0.
- **criterion A 진짜 손실 0**: 사라진 6(EXIF 썸네일)은 부모가 HEADER_RECOVERED로 보존, 다운그레이드
  5(HR→SKIP)는 W3의 mis-framed 블록 복구를 C1이 제대로 분할한 정정. 상세: [조사 기록 2026-07-05](../investigations/2026-07-05-carve-overcarve-fix.md).

**감수한 트레이드오프**
- 순수 위양성(진짜 이미지 아닌 `FF D8` 쓰레기)이 SOF 마커를 우연히 담으면 손상 시 10 MB까지
  확대돼 디스크를 낭비한다(복구엔 무해 — SKIP 유지). base-swap 소수 손실(손상 첫 이미지가 더 깨끗한
  묻힌 이미지로 대체 — 값싼 소형).
- carve는 손상 첫 이미지를 보존하지 않는다. 그 복원은 recover에 의존한다.

**향후 고려사항**
- **carve 변경의 회귀는 plain 디코더가 아니라 실제 recover 파이프라인(header-recovery 포함)으로만
  판정한다** — 부모가 손상 헤더로 SKIP처럼 보여도 recover가 복구하므로 plain-decode는 손실을 과대
  계상한다(이 결정 도출 중 실제로 오판).
- SOF 길이 상한 100은 3컴포넌트 코퍼스 전제. 다른 카메라(>30컴포넌트)엔 재검토.
- 위양성 디스크 낭비·base-swap은 여지로 남김(현재 무해). de-bury는 root fix가 커버해 불필요.

## 관련 항목

- [ADR 0002](0002-carve-eoi-validation.md) — 같은 모듈의 가짜 EOI 검증(상보적).
- [ADR 0006](0006-header-recovery-structural-gates.md) — 손상 첫 이미지 복원을 맡는 recover 헤더 복구.
- [carve 스펙](../specs/0001-carve.md)·[조사 기록 2026-07-05](../investigations/2026-07-05-carve-overcarve-fix.md)·[과다 카빙 발견 조사 2026-07-04](../investigations/2026-07-04-overcarve-buried-images.md).
- 포맷 지식: [JPEG 마커 구조](../reference/jpeg-markers.md).
