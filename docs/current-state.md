# 현재 상태

- **코드 테스트:** 2026-07-13, 69개 통과
- **전체 데이터 기준선:** 2026-07-05, `usb.img` 재카빙 후 `recover.py --time-budget 0`
- **기준 산출물:** `output_c2/` 계열(출력 디렉터리는 Git 비추적)

## 안정적으로 동작하는 범위

- 디스크 이미지에서 JPEG·AVI 시그니처 검색과 추출
- 손상 JPEG의 바이트 편집·비트 재동기 복구
- DHT/DQT/SOF/SOS 손상 헤더의 구조 기반 복구
- 가짜 EOI, 손상 세그먼트 길이, 뒤따르는 AVI 때문에 발생하던 과다 카빙 방지
- 복구 결과 6종 분류와 `report.csv` 지표 출력

## 전체 데이터 기준선

| 항목 | 값 | 비고 |
|------|---:|------|
| JPEG | 978 | AVI 내부 MJPEG 프레임 제외 후 |
| AVI | 44 | 알려진 AVI 손실 0 |
| 사용 가능한 사진 | 884 | `RECOVERED + HEADER_RECOVERED + CLEAN` |
| 처리 오류 | 0 | 전체 파이프라인 |

`report.csv`의 action 분포는 `CLEAN 187`, `RECOVERED 611`, `HEADER_RECOVERED 86`, `FAILED 25`,
`SKIP_UNDECODABLE 69`, `ERROR 0`이다. 합계는 JPEG 978개와 일치한다.

수치는 `output_c2/`의 전체 카빙 결과와 recover `report.csv` 대조에서 확정됐다. 다시 비교할 때는 같은
`usb.img`와 `--time-budget 0`을 사용하고 원자료에서 재계산한다. 경계 결정은
[ADR 0007](adr/0007-carve-corrupt-header-boundary.md)과 [ADR 0008](adr/0008-jpeg-boundary-stops-at-avi.md)에 남아 있다.

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
