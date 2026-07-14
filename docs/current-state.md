# 현재 상태

- **코드 테스트:** 2026-07-13, 167개 통과
- **역사적 full-recover 기준선:** 2026-07-05, `usb.img` 재카빙 후 `recover.py --time-budget 0`
- **역사적 기준 산출물:** `output_c2/` 계열(출력 디렉터리는 Git 비추적)
- **현행 카빙 감사:** 2026-07-13, `usb.img` 읽기 전용 dry-run(출력 저장·full recover 미실행)

## 안정적으로 동작하는 범위

- 디스크 이미지에서 JPEG·AVI 시그니처 검색과 추출
- 손상 JPEG의 바이트 편집·비트 재동기 복구
- DHT/DQT/SOF/SOS 손상 헤더의 구조 기반 복구
- 가짜 EOI, 손상 세그먼트 길이, 뒤따르는 AVI 때문에 발생하던 과다 카빙 방지
- 복구 결과 6종 분류와 `report.csv` 지표 출력

## 역사적 전체 복구 기준선

| 항목 | 값 | 비고 |
|------|---:|------|
| JPEG | 978 | AVI 내부 MJPEG 프레임 제외 후 |
| AVI | 44 | 알려진 AVI 손실 0 |
| 사용 가능한 사진 | 884 | `RECOVERED + HEADER_RECOVERED + CLEAN` |
| 처리 오류 | 0 | 전체 파이프라인 |

`report.csv`의 action 분포는 `CLEAN 187`, `RECOVERED 611`, `HEADER_RECOVERED 86`, `FAILED 25`,
`SKIP_UNDECODABLE 69`, `ERROR 0`이다. 합계는 JPEG 978개와 일치한다.

이 수치는 `output_c2/`의 이전 파이프라인 전체 카빙 결과와 recover `report.csv` 대조에서 확정한 역사
기준선이다. 현행 코드의 usable 수치가 아니며 이번 작업에서는 새 출력과 full recover를 만들지 않았다.
다시 비교할 때는 같은 `usb.img`와 `--time-budget 0`을 사용하고 원자료에서 재계산한다.

## 현행 `usb.img` 읽기 전용 감사

입력 크기는 3,517,120,512바이트다. raw `FF D8 FF` 1,830개를 구조 게이트로 줄이고 손상 앵커 후보를
더한 결과, 시작 후보는 1,671개였다. 별도의 4 KiB 정렬 DQT/SOF/DHT/SOS 구조 census에서 일관된 JPEG
구조 680개를 찾았고 모두 현행 hit에 포함됐다.

| 항목 | 값 | 비고 |
|---|---:|---|
| exact JPEG 시작 | 1,625 | 구조 후보와 내장 JPEG 포함 |
| damaged JPEG 시작 | 2 | JFIF/Exif 앵커·후속 구조·EOI 검증 |
| AVI 시작 | 44 | 모두 exact; 손상 RIFF/form 추가 0 |
| dry top-level JPEG | 970 | 파일 쓰기 없이 경계와 중첩 분류만 실행 |
| dry top-level AVI | 44 | 기존 AVI 44개와 경계 변경 0 |
| Exif 내부 JPEG | 556 | `--save-thumbnails` 없이 분류만 집계 |
| 처리 오류 | 0 | dry boundary 실행 |

이전 `output_c2`의 JPEG 오프셋과 비교하면 36개가 빠지고 28개가 추가돼 `978 - 36 + 28 = 970`이다.

- 제거 36개: 비임베디드 `SKIP_UNDECODABLE` 위양성 26개, Exif 내부 미리보기 재분류 10개다.
- 추가 28개: 무효 DQT/DHT 등 header segment가 덮던 exact JFIF 시작 25개, 기존 과다 범위에서 분리된
  exact JPEG 1개(`0xC15DE6CB`, 76,919바이트), 손상 시작 2개다.
- header 의미 분할 후보 25개는 경계 계산상 EOI 완결 17개, Pillow load 성공 16개, verify-only 3개,
  실패 6개였다. 관련 부모 12개는 모두 strict 연속 header walk에 실패했고(DQT 9, DHT 3), tolerant
  decoder로 열리는 부모 6개 중 5개는 내부 자식의 디코드 바이트와 같았다. 부모 조각도 모두 별도 보존했으며
  출력 범위 사이 8,745바이트의 gap에는 exact JPEG 시작이 없었다. 25개 모두 SOI 직전 12바이트가 같은
  record envelope 형식이고 `usb.img` 전체에 같은 형식의 exact JPEG hit이 281개 있다. 이 후보들은 Exif/AVI
  내부가 아니고 FAT JPG 엔트리와 직접 매핑되지 않아, 구조·디코드 근거를 가진 보수적 raw 분리로 취급한다.

FAT32는 런타임 탐지에 쓰지 않고 독립 감사에만 사용했다. strict archive JPG 엔트리 677개 중 명확한
149개 일치로 4,096바이트 cluster와 data start `0x011FF000`을 역검증했다. 손상 시작
`0xC82F0000`과 `0xC8ABD000`은 모두 4 KiB 정렬이고 `scan_start=offset+0x26F`이며, 각각 FAT 선언 크기
21,938바이트·19,676바이트가 EOI exclusive 크기와 정확히 일치했다. 그러나 현행 `recover.py` 자동 판정은
둘 다 `SKIP_UNDECODABLE`이므로 “사용 가능한 사진 2개 복구”로 해석하지 않는다.

exact RIFF 시작은 45개였고 그중 `AVI ` form은 44개였다. AVI 44개는 모두 `hdrl`/`avih` 구조를 다시
확인했으며 RIFF 또는 form type 한쪽의 1~2바이트 손상 모델에서 추가 AVI는 없었다. 이는 RIFF와
form/구조가 함께 손상되거나 덮어써진 AVI, 단편화 AVI까지 포함한 누락 0 증명이 아니다.

기본 10 MiB JPEG 하드 상한에 닿은 exact 후보는 10개였다. 알려진 다음 외부 경계는 모두 상한 밖이고,
FAT JPG 선언 크기의 최대값은 5,936,612바이트였으므로 확인된 FAT 파일 누락 근거는 없었다. 다만 FAT에
매핑되지 않거나 단편화된 큰 파일은 옵션을 올려 별도 검증해야 한다.

### 성능

동일 프로세스에서 출력 쓰기를 막고 scanner와 boundary를 두 번 실행했다. OS cache와 장치 상태 영향을
받는 참고값이며 파일 저장 시간은 포함하지 않는다.

| 단계 | 이전 구현 | 현행 반복 측정 |
|---|---:|---:|
| 시작 후보 스캔 | 약 3.0초 | 7.524~8.790초 |
| 경계·중첩 분류 | 약 39~41초 | 0.621~0.648초 |
| 합계 | 약 42~44초 | 8.172~9.411초 |

구조 기반 손상 후보 검증으로 scanner 비용은 늘었지만, 정렬 인덱스·단일 활성 범위·segment-aware 선형
entropy walk를 사용해 전체 시간은 줄었다. 밀집 `FF 00`과 반복 inter-scan 합성 입력도 크기 2배에 실행
시간이 대략 2배가 되는 선형 거동을 확인했다.

## 알려진 한계와 다음 작업

1. **재동기 세그먼트 밀림 보정** — 건너뛴 MCU 수를 몰라 세그먼트별 수평 오프셋이 누적된다. 경계 행
   연속성과 EOI 앵커를 이용한 상대 위치 추정이 다음 우선순위다.
2. **색 캐스트 보정** — DC=0 재동기 세그먼트에 상수 색 오프셋이 남는다. 밀림 보정 후 경계 연속성으로
   세그먼트·컴포넌트별 오프셋을 추정한다.
3. **단편화 탐색** — 데이터 소진 파일이 `usb.img`의 다른 위치에 이어지는지 찾는 연구성 과제다. 원본 전체
   탐색 비용이 크고 후보 진위 신호가 아직 확립되지 않았다.
4. **CLEAN 출력 정규화** — 관용 디코더는 통과하지만 표준 디코더가 열지 못하는 원본을 저장할 수 있다.
   strict 개봉 실패 시 재인코딩본 저장을 검토한다.
5. **SOF 길이 필드 손상 허용** — `sof_candidates`의 고정 `00 11 08` 패턴이 길이 상위 바이트 손상을
   놓친다. 주변 마커와 정상 치수 검증을 결합한 후보화가 필요하다.
6. **AVI 인덱스 복구** — 일부 원본에 `idx1`이 없어 플레이어가 오디오 `dwLength`를 재생시간으로 오독한다.
   카빙 문제가 아니며 `movi` 스캔 기반 후처리 과제다.

## 재시도하지 않을 접근

- **DC 물리 범위를 resync 후보 수락 필터로 사용하지 않는다.** 정당 후보도 리셋 오프셋과 잔존 드리프트로
  범위를 자주 위반했다. 같은 비트위치 후보의 보조 비교에는 재검토할 수 있다([ADR 0005](adr/0005-scaled-accept-threshold.md)).
- **렌더 통계·픽셀 상관·코퍼스 prior로 헤더 후보를 기각하지 않는다.** 밀림과 색 캐스트가 있는 복구본에서
  판별력이 없었다. 구조 신호만 채택 게이트로 사용한다([ADR 0006](adr/0006-header-recovery-structural-gates.md)).
- **다음 JPEG 시그니처를 무조건 파일 경계로 사용하지 않는다.** EXIF 헤더 안에 썸네일 JPEG가 존재할 수
  있다. 엔트로피 시작 이후의 진짜 헤더만 경계 후보로 사용한다([ADR 0007](adr/0007-carve-corrupt-header-boundary.md)).
- **plain 디코드 개수만으로 carve 회귀를 판정하지 않는다.** 헤더 복구 보존과 AVI 내부 MJPEG 재분류를
  포함한 전체 recover 파이프라인으로 비교한다.
- **비트 소비량 또는 skew만으로 재동기 위치를 채점하지 않는다.** 짧게 우연히 맞은 잘못된 정렬을 선택해
  정상 가드까지 악화시켰다. clean run과 구조 신호를 사용한다.
- **DHT 도너를 DQT 계열별로 고르지 않는다.** 이 코퍼스의 지배적 DHT는 Annex-K 전형 테이블 하나이며
  DQT는 공유 자원이 아니다([ADR 0006](adr/0006-header-recovery-structural-gates.md)).

## 빠른 시작

```powershell
.venv\Scripts\python.exe -m pytest
```

POSIX 환경에서는 `.venv/bin/python -m pytest`를 사용한다.

코드 진입점과 모듈 책임은 [architecture.md](architecture.md), 현재 동작 계약은
[carve spec](specs/0001-carve.md)과 [recover spec](specs/0002-recover.md)을 본다. 전체 `usb.img` 처리는
비용이 크므로 사용자와 범위를 합의한 뒤 실행한다.
