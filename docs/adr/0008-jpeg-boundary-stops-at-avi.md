# 0008. JPEG carve 경계는 다음 AVI(RIFF) 시그니처에서도 정지한다

- **날짜:** 2026-07-05
- **상태:** Accepted

---

## 배경

[ADR 0007](0007-carve-corrupt-header-boundary.md)은 손상 헤더 JPEG의 과다 카빙을 막기 위해 `jpeg_end`
(`carver/extractors.py`)의 전진 경계를 **다음 진짜 JPEG 헤더**(`_next_header`, `FF D8 FF E0`–`EF`)
또는 `next_sig`로 축소했다. 그러나 경계를 앞으로 미는 세 경로 — `_next_header`(:43), SOS 엔트로피
상한 `upper`(:156), `_corrupt_boundary`(:88) — 는 **JPEG 헤더와 `offset+10 MB` 캡만** 경계로 인식하고
**AVI(RIFF) 시그니처는 무시**한다.

그 결과, AVI 바로 앞에 위치한 절단·손상 JPEG이 경계를 앞으로 확장할 때 뒤따르는 AVI를 건너뛴다.
`_next_header`가 AVI 내부 MJPEG 프레임(`FF D8 FF E0`–`EF`)을 "다음 헤더"로 잡거나(0x025BF73C가
0x02DEEC90에서 끝나며 AVI 0x026F1000 시작을 삼킴), SOS가 진짜 EOI를 못 찾아 10 MB fallback으로
확장한다(0x30FB55DD가 정확히 +10 MB까지 가며 AVI 0x31860000 삼킴). 삼켜진 AVI 오프셋은 carve가
embedded로 건너뛰어(`carve.py:42-60`) 출력에서 사라진다.

전수 감사([조사 기록 2026-07-05](../investigations/2026-07-05-avi-overcarve-and-duration.md)):
usb.img의 진짜 AVI 44개 중 **5개가 JPEG 범위에 임베디드**(RIFF 헤더 유효, 10–20 MB 대시캠 영상).
이 중 4개는 ADR 0007(C1)이 JPEG 경계를 바꾸며 새로 유발했고, 1개(0x92F48000)는 그 전부터 손실 상태였다.
carve 단계 문제이며 recover로는 복구 불가(AVI가 JPEG 파일 안에 묻힘).

**AVI 추출(`avi_end`) 자체는 과다 카빙하지 않는다** — 단일 RIFF `chunk_size` 길이 필드를 읽고 fallback을
`next_sig`로 캡하므로, 세그먼트 다중 워크가 없어 뒤 파일 위로 점프하지 않는다(0/39 검증). 문제는 전적으로
JPEG 경계가 AVI를 경계로 안 본 것이다.

## 결정

JPEG 전진 경계에 **다음 AVI 시그니처를 하드 정지점으로 추가**한다. `_next_avi(data, start, hi)` 헬퍼
(`[start, hi)` 사이 첫 `RIFF … AVI ` 오프셋, 없으면 `hi`)를 SOS `upper`와 `_corrupt_boundary` 두
경로의 캡 계산에 삽입한다:

```
upper = _next_avi(data, pos, min(_next_header(...), offset+10MB, size))   # SOS 경로
cap   = _next_avi(data, corrupt_pos, min(_next_header(...), offset+10MB, size))  # 손상 경계
```

**AVI 시그니처를 절대 경계로 쓰는 근거**: `RIFF` + 4바이트 크기 + `AVI ` 12바이트 구조는 JPEG의 헤더나
엔트로피에 정상적으로 나타나지 않는다(우연 매칭 확률 무시 가능, 스캐너의 AVI 판별 기준과 동일). 따라서
**SOF를 이미 지난 유효 이미지(`saw_sof=True`)여도 안전한 하드 경계**다 — 이 점이 `next_sig`와 다르다
(`next_sig`는 현재 JPEG 내부 EXIF 썸네일일 수 있어 saw_sof일 때 쓰면 자기 썸네일에서 절단된다. 그래서
ADR 0007은 saw_sof일 때 `_next_header`만 썼다). 탐색은 `hi`(이미 계산된 캡)로 제한해 전 이미지 스캔을
피한다.

손상·절단 JPEG 자체의 복원은 여전히 carve가 하지 않는다(ADR 0007 원칙 유지) — 경계만 AVI 시작에서
바로잡아 AVI를 별도 히트로 분리하고, 삼켰던 JPEG은 제 크기로 축소된다.

## 대안

| 대안 | 기각 이유 |
|------|----------|
| **그대로 둠**(ADR 0007 상태) | usb.img 44개 AVI 중 5개(10–20 MB 영상) 손실 유지. 사용자가 직접 확인 요청한 회귀. |
| **경계 캡을 `next_sig`(다음 히트)로 전면 교체** | `next_sig`는 현재 JPEG 내부 EXIF 썸네일일 수 있어(썸네일도 `FF D8 FF` 히트) JPEG을 자기 썸네일에서 절단한다 — ADR 0007이 이미 기각한 이유. AVI만 별도 하드 경계로 두면 썸네일 충돌 없이 AVI만 잡는다. |
| **AVI 리페어(idx1 재생성)로 541시간 표시 수정을 함께** | 541시간 재생표시는 원본 AVI의 `idx1` 인덱스 부재+오디오 `dwLength`(샘플 수) 오독이 원인으로 **카빙과 독립**(carve=OK, 경계 직후 제로 패딩=idx1 미절단). 경계 수정으로 안 고쳐지는 별도 후처리라 스코프 분리(백로그 C5). |
| **JPEG뿐 아니라 모든 파일 타입 시그니처를 하드 경계로 일반화** | 현재 코퍼스에 JPEG·AVI 두 타입뿐이라 불필요한 일반화. AVI만 안전 시그니처로 확립됨(RIFF+AVI 구조). 타입 추가 시 재검토. |

## 결과

**실제 영향**
- `_next_avi` 추가, SOS `upper`·`_corrupt_boundary`에 삽입. 회귀 테스트 4개(`_next_avi` 단위·SOS 오버슛
  AVI 정지·손상 DHT AVI 정지·정상 JPEG 뒤 AVI 무절단), 전체 65→69 통과.
- usb.img 재카빙 `output_c1`(AVI 39)→`output_c2`(AVI 44), `recover.py --time-budget 0` 재복구.
- **AVI 39→44**(+5, 5개 carve=OK, AVI 손실 0). JPEG 999→978(−21). ERROR 0.
- **criterion A 회귀 0**: 사용가능 사진(RECOVERED+HEADER_RECOVERED+CLEAN) **884→884**. 공통 978 JPEG의
  recover action 변화 0종. 사라진 21 JPEG은 전부 복구된 AVI 내부 MJPEG 프레임으로, c1에서 모두
  SKIP_UNDECODABLE(usable 0)였다가 정상 embedded로 전환된 것.

**감수한 트레이드오프**
- 없음(실질). 삼켰던 5개 덮개 JPEG은 제 크기로 축소되나 recover 분류 불변(usable 2개는 디코드 동일 —
  0xB1C9D80C는 c1·c2 모두 96×96, c1은 뒤에 169 KB AVI가 붙어 있던 것).
- 541시간 재생표시는 이 결정으로 고쳐지지 않는다(의도 — 별개 문제).

**향후 고려사항**
- carve 회귀 판정은 plain 개수가 아니라 recover 파이프라인의 파일별 action으로(ADR 0007 원칙 재확인 —
  plain 개수 999→978만 보면 손실로 오판하나 전부 AVI 내부 프레임).
- 파일 타입이 늘면(WAV·MP4 등) 각 시그니처의 "JPEG 내부 정상 등장 불가" 안전성을 확인 후 하드 경계에 추가.
- 541시간(오디오 필드+인덱스 부재)은 별도 AVI 리페어로 — 백로그 C5.

## 관련 항목

- [ADR 0007](0007-carve-corrupt-header-boundary.md) — 이 결정이 확장하는 손상 헤더 경계 결정(같은 `jpeg_end`).
- [carve 스펙](../specs/0001-carve.md) · [조사 기록 2026-07-05](../investigations/2026-07-05-avi-overcarve-and-duration.md) · [보고서](../reports/2026-07-05-avi-overcarve-fix.md).
- 포맷 지식: [AVI RIFF 청크 구조](../reference/avi-riff.md).
