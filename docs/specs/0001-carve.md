# carve.py

- **날짜:** 2026-07-05
- **상태:** Accepted

---

## 개요

손상된 디스크 이미지(`.img` 등)에서 JPEG와 AVI 파일을 추출하는 도구.
파일 시스템 메타데이터 없이 시그니처 기반으로 파일 경계를 탐지하고, 해당 바이트 범위를 그대로 저장한다.
ddrescue 등으로 복구한 이미지처럼 디렉토리 구조가 유실된 경우에 사용한다.

## 인터페이스

| 이름 | 설명 | 타입 | 기본값 |
|------|------|------|--------|
| `image` | 디스크 이미지 파일 경로 | positional | — |
| `-o, --output` | 출력 디렉토리 | str | `./output` |
| `--max-avi-size` | AVI 최대 크기 (MB) | int | `500` |
| `--save-thumbnails` | AVI 내 임베디드 JPEG를 `jpeg_thumbnails/`에 저장 | flag | 생략 시 스킵 |

## 출력

출력 디렉토리 구조:

```
<output>/
  jpeg/               # 추출된 JPEG 파일
  avi/                # 추출된 AVI 파일
  jpeg_thumbnails/    # --save-thumbnails 사용 시에만 생성
  errors.log          # 추출 중 예외가 발생한 항목 기록
```

파일명은 이미지 내 발견 오프셋을 그대로 사용한다: `0x{오프셋:08X}.jpg` / `0x{오프셋:08X}.avi`.

## 파이프라인

1. **mmap 로드** (`carve.py`) — 이미지 파일을 읽기 전용 메모리 맵으로 연다.
2. **시그니처 탐색** (`carver/scanner.py`) — `FF D8 FF`(JPEG)와 `RIFF...AVI `(AVI) 시그니처를 전체 이미지에서 탐색해 오프셋 순으로 정렬된 `FileHit` 목록을 반환한다.
3. **임베디드 판별** (`carve.py`) — 이미 추출된 범위 안에 속하는 오프셋은 임베디드 파일(AVI 내 JPEG 썸네일 등)로 간주한다.
4. **끝 오프셋 계산** (`carver/extractors.py`) — JPEG는 세그먼트 파싱으로 EOI를 찾는다. 단 SOS 이후 첫 `FF D9`를 바로 EOI로 채택하지 않고, **직후 4 KB가 엔트로피 연속인지 검사**해 손상으로 생긴 가짜 EOI를 건너뛴다(엣지 케이스 표 참조). 탐색 상한은 엔트로피 시작 이후 다음 JPEG 헤더(`FF D8 FF E0`–`EF`)로, 헤더 내 EXIF 썸네일이 아닌 실제 다음 파일 경계다. 헤더 마커 워크는 세그먼트 길이를 신뢰해 전진하므로, **손상된 길이나 비마커 바이트를 만나면 뒤따르는 이미지 위로 점프해 과다 카빙된다** — 이를 막기 위해 워크 중 (a) 유효 마커(`mb ≥ 0xC0`)인지, (b) 마커별 길이 상한(DHT 1200·DQT 600·SOF 100·DRI 10바이트) 이내인지 검증하고, 위반 시 손상으로 보아 경계를 축소한다(엣지 케이스 표·[ADR 0007](../adr/0007-carve-corrupt-header-boundary.md)). AVI는 RIFF chunk_size 헤더를 읽는다. 정상 파싱이 불가능하면 다음 시그니처 오프셋 또는 크기 상한으로 fallback한다.
5. **파일 저장** (`carve.py`) — 계산된 바이트 범위를 출력 디렉토리에 기록한다. 예외 발생 시 `errors.log`에 남기고 다음 항목으로 넘어간다.

## 사용하는 모듈

- `carver/scanner.py` — 전체 이미지 시그니처 탐색
- `carver/extractors.py` — JPEG/AVI 파일 경계(끝 오프셋) 계산
- `carver/models.py` — `FileHit` 데이터 클래스

## 의존하는 포맷 / 스펙

- [JPEG 마커 구조](../reference/jpeg-markers.md)
- [AVI RIFF 청크 구조](../reference/avi-riff.md)

## 엣지 케이스

| 상황 | 동작 |
|------|------|
| AVI 내 JPEG 썸네일 (임베디드) | `--save-thumbnails` 있으면 `jpeg_thumbnails/`에 저장, 없으면 스킵. `jpeg/`에는 저장하지 않는다. |
| JPEG 엔트로피 중 가짜 `FF D9` (손상으로 stuffing 깨짐) | EOI 직후 4 KB의 "`FF` 다음 `00`/RST" 비율이 0.3 이상이면 엔트로피 연속(가짜)으로 보고 다음 `FF D9` 후보로 전진한다. 첫 `FF D9`에서 잘려 누락됐던 데이터를 되살린다. 근거: [ADR 0002](../adr/0002-carve-eoi-validation.md). |
| JPEG에 EOI 없음 (truncated) | 다음 JPEG 헤더 또는 10 MB 상한까지 추출. SOS를 못 찾으면 다음 시그니처 오프셋으로 fallback. 파일명 옆에 `[불완전, fallback 사용]` 표시. |
| 헤더 세그먼트 길이 손상 / 비마커 바이트 (`FF 00` 등) | 마커 워크가 손상 길이·비마커(`mb < 0xC0`)를 만나면 손상으로 보고 경계를 축소한다: **SOF를 이미 지났으면**(=유효 이미지) 다음 진짜 헤더로, **SOF 도달 전이면**(=위양성 의심) 다음 시그니처로. 손상 첫 이미지 뒤에 연속된 진짜 이미지들이 별도 히트로 추출된다(과다 카빙 방지). 근거: [ADR 0007](../adr/0007-carve-corrupt-header-boundary.md). |
| AVI RIFF chunk_size가 0이거나 max_avi_size 초과 | 다음 시그니처 오프셋 또는 max_avi_size 상한으로 fallback. `[fallback 사용]` 표시. |
| 이미지 파일 없음 | 오류 메시지 출력 후 exit code 1로 종료. |
