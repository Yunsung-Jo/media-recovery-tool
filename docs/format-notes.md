# 구현에 필요한 포맷 메모

일반 JPEG·AVI 해설이 아니라 이 프로젝트의 경계 판정과 복구 로직에서 실수하기 쉬운 사실만 남긴다.
표준 근거는 [ITU-T T.81](https://www.itu.int/rec/T-REC-T.81)과
[Microsoft AVI RIFF 문서](https://learn.microsoft.com/en-us/windows/win32/directshow/avi-riff-file-reference)다.
코퍼스 관찰과 프로젝트별 휴리스틱은 표준 요구사항이 아니며, 선택 근거는 관련 ADR이 정본이다.

## JPEG 마커와 경계

- JPEG 시작은 `FF D8`, 종료는 `FF D9`다. 카빙 시 오탐을 줄이기 위해 시작 시그니처는 `FF D8 FF`를
  사용한다.
- SOI(`D8`), EOI(`D9`), RST0~RST7(`D0`~`D7`), TEM(`01`)에는 길이 필드가 없다. 다른 일반 세그먼트의
  16비트 big-endian 길이는 길이 필드 자체 2바이트를 포함한다.
- SOS 이후 엔트로피에서 `FF 00`은 데이터 바이트 `FF`, `FF D0`~`FF D7`은 restart marker,
  `FF FF`는 fill이다. 이들을 일반 마커나 파일 경계로 취급하지 않는다.
- 손상으로 엔트로피 안에 가짜 `FF D9`가 생길 수 있다. `carver/extractors.py`는 EOI 직후에도 엔트로피가
  이어지는지 확인해 조기 종료를 피한다([ADR 0002](adr/0002-carve-eoi-validation.md)).
- EXIF APP1에는 독립된 썸네일 JPEG가 들어갈 수 있다. 따라서 메인 SOS 이전의 `FF D8 FF`를 다음 파일
  경계로 사용하면 안 된다.
- 손상 세그먼트 길이는 뒤 파일이나 AVI 위로 점프할 수 있다. 마커 유효성·세그먼트별 길이 상한과 다음
  JPEG/AVI 시그니처를 함께 경계로 사용한다([ADR 0007](adr/0007-carve-corrupt-header-boundary.md),
  [ADR 0008](adr/0008-jpeg-boundary-stops-at-avi.md)).

## 엔트로피와 재동기

- baseline JPEG의 DC는 이전 블록 DC와의 차분으로 저장된다. restart marker가 없으면 예측값이 스캔 전체에
  누적되므로 한 번의 비트 디싱크가 뒤 블록 전체에 영향을 준다.
- restart marker에서는 DC 예측값이 0으로 리셋되고 스트림이 바이트 경계로 정렬된다. 이 코퍼스의 다수
  JPEG에는 restart marker가 없다.
- Annex-K 전형 Huffman 테이블은 코드 공간이 조밀하다. 잘못 정렬된 비트열도 무효 코드 없이 오래 디코드될
  수 있으므로 “끝까지 디코드됨”만으로 온전성을 판정하지 않는다.
- 8비트 블록의 dequant DC는 이론상 약 `[-1024, +1016]` 범위이며 양자화 반올림 여유가 있다. 범위 초과는
  디싱크 진단에는 유용하지만, 복구 후보의 수락 필터로 사용하면 정당 후보를 제거한다([ADR 0005](adr/0005-scaled-accept-threshold.md)).
- resync 후보는 clean run, 잔여 비례 임계, 버퍼 끝 완주, masking 거부를 함께 사용한다. 자세한 계약은
  [recover spec](specs/0002-recover.md)이 정본이다.

## 헤더 복구의 코퍼스 사실

- 코퍼스의 지배적 DHT는 ITU-T T.81 Annex-K 전형 4테이블과 일치하며 `carver/headerfix.py`의
  `DONOR_HUFF`에 고정돼 있다. 이는 보편적인 JPEG 요구사항이 아니라 이 코퍼스에서 검증한 복구 후보이다.
- DHT와 달리 DQT는 품질 설정별로 달라 파일 간 전역 공유 자원으로 취급하지 않는다.
- 도너 DHT, DQT 스무딩, SOF/SOS 재구성은 단독으로 신뢰하지 않는다. probe 바닥·소비율·엔진 결과·자체
  헤더 우월 조건을 통과할 때만 채택한다([ADR 0006](adr/0006-header-recovery-structural-gates.md)).

## AVI RIFF 경계

- AVI는 `RIFF` 4바이트, little-endian `fileSizeMinus8` 4바이트, `AVI ` 4바이트로 시작한다.
- 전체 파일 크기는 `8 + fileSizeMinus8`이다. 값이 0이거나 상한을 넘으면 다음 시그니처 또는 설정된 최대
  크기로 제한한다.
- `RIFF`만으로 AVI로 판정하지 않고 12바이트의 `RIFF....AVI ` 구조를 확인한다.
- 이 프로젝트는 12바이트 `RIFF....AVI ` 구조를 JPEG 내부의 정상 데이터가 아닌 컨테이너 경계로
  취급한다. 따라서 JPEG 경계 계산이 다음 AVI를 넘지 않게 한다
  ([ADR 0008](adr/0008-jpeg-boundary-stops-at-avi.md)).
- `idx1` 부재는 카빙 경계 오류와 별개다. 영상 데이터가 온전해도 일부 플레이어가 재생시간을 잘못 표시할
  수 있으며, 필요하면 `movi` 청크를 스캔해 인덱스를 재구성한다.
