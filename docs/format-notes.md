# 구현에 필요한 포맷 메모

일반 JPEG·AVI 해설이 아니라 이 프로젝트의 경계 판정과 복구 로직에서 실수하기 쉬운 사실만 남긴다.
표준 근거는 [ITU-T T.81](https://www.itu.int/rec/T-REC-T.81)과
[Microsoft AVI RIFF 문서](https://learn.microsoft.com/en-us/windows/win32/directshow/avi-riff-file-reference)다.
코퍼스 관찰과 프로젝트별 휴리스틱은 표준 요구사항이 아니다. 현재 설계와 계약은
[design.md](design.md)와 관련 spec이 정본이고, 선택 당시의 배경·대안은 관련 ADR에 보존한다.

## JPEG 마커와 경계

- JPEG 시작은 `FF D8`, 종료는 `FF D9`다. 카빙 시 오탐을 줄이기 위해 시작 시그니처는 `FF D8 FF`를
  사용한다. APPn은 필수가 아니며 첫 길이형 marker는 DQT·DHT·SOF·SOS 등일 수 있다. marker prefix의
  연속 `FF` fill도 합법이다.
- SOI(`D8`), EOI(`D9`), RST0~RST7(`D0`~`D7`), TEM(`01`)에는 길이 필드가 없다. 다른 일반 세그먼트의
  16비트 big-endian 길이는 길이 필드 자체 2바이트를 포함한다.
- DQT의 8비트·16비트 양자화 계수는 0일 수 없다. 선언 길이가 맞더라도 0 계수를 포함한 DQT가 뒤의
  `FF D8`을 payload로 덮으면 그 길이를 경계 근거로 신뢰하지 않는다.
- SOS 이후 엔트로피에서 `FF 00`은 데이터 바이트 `FF`, `FF D0`~`FF D7`은 restart marker,
  `FF FF`는 fill이다. 이들을 일반 마커나 파일 경계로 취급하지 않는다.
- 손상으로 엔트로피 안에 가짜 `FF D9`가 생길 수 있다. `media_recovery/formats/boundaries.py`는 EOI 직후에도 엔트로피가
  이어지는지 확인해 조기 종료를 피한다. stuffing 비율은 FF 표본이 충분할 때만 강한 반증이며, 희소한
  `FF 00`이 섞인 zero padding을 엔트로피로 단정하면 실제 EOI를 놓친다
  ([ADR 0002](adr/0002-carve-eoi-validation.md)).
- Progressive뿐 아니라 sequential JPEG도 컴포넌트를 여러 scan으로 나눌 수 있다. scan 사이에는 새 SOS
  외에 DHT·DQT·DAC·DNL·DRI·APPn·COM이 올 수 있고, 그 길이형 payload의 `FF D8`/`FF D9`는 경계가 아니다.
- EXIF APP1에는 독립된 미리보기 JPEG가 들어갈 수 있다. 다른 APP/COM·테이블 payload에도 우연한 파일
  시그니처가 있을 수 있으므로, 유효한 pre-SOS 세그먼트 안 hit은 외부 파일로 분리하지 않는다.
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

- 코퍼스의 지배적 DHT는 ITU-T T.81 Annex-K 전형 4테이블과 일치하며
  `media_recovery/reconstruction/header_hypotheses.py`의
  `DONOR_HUFF`에 고정돼 있다. 이는 보편적인 JPEG 요구사항이 아니라 이 코퍼스에서 검증한 복구 후보이다.
- DHT와 달리 DQT는 품질 설정별로 달라 파일 간 전역 공유 자원으로 취급하지 않는다.
- 도너 DHT, DQT 스무딩, SOF/SOS 재구성은 단독으로 신뢰하지 않는다. probe 바닥·소비율·엔진 결과·자체
  헤더 우월 조건을 통과할 때만 채택한다([ADR 0006](adr/0006-header-recovery-structural-gates.md)).

## AVI RIFF 경계

- AVI는 `RIFF` 4바이트, little-endian `fileSizeMinus8` 4바이트, `AVI ` 4바이트로 시작한다.
- 전체 파일 크기는 `8 + fileSizeMinus8`이다. 값이 16 미만·상한 초과·이미지 밖이거나 선언 범위의
  top-level 구조 walk가 실패하거나 뒤 외부 후보를 가로지르면 size를 버리고 chunk walk와 다음 경계로
  제한한다. RIFF chunk payload는 WORD 정렬되며 홀수 `ckSize` 뒤 padding 1바이트는 `ckSize`에 포함되지
  않지만 부모 RIFF 범위에는 포함된다.
- 정확 시작 스캔은 `RIFF`와 `AVI ` form type 12바이트를 후보로 잡는다. 선언 size를 실제 끝으로 신뢰하거나
  RIFF/form 한쪽의 1~2바이트 손상에서 시작을 추론할 때는 `LIST/hdrl` 안 `avih`와 뒤 `LIST/movi` 구조를
  추가로 요구한다.
- JPEG entropy 밖의 구조 검증 AVI 시작은 JPEG의 외부 경계다. 다만 pre-SOS/interscan 길이형 segment
  payload 안의 `RIFF...AVI `는 metadata일 수 있으므로 segment를 먼저 파싱해 내부 후보를 건너뛴다
  ([ADR 0008](adr/0008-jpeg-boundary-stops-at-avi.md)).
- [OpenDML AVI File Format Extensions 1.02](https://web.archive.org/web/20191226055430/http://www.morgan-multimedia.com/download/odmlff2.pdf)는
  첫 `RIFF...AVI ` 뒤에 하나 이상의 `RIFF...AVIX`를 둘 수 있다. AVIX는 `LIST/movi`와 완전한 chunk walk를
  확인한 경우만 같은 출력에 붙이고 `ix##` standard-index를 허용한다. Microsoft의 AVI RIFF 문서는
  OpenDML 확장 자체를 설명하지 않으므로 두 근거를 구분한다.
- `movi` 또는 `rec ` 안의 `NNdc`/`NNdb` payload에는 MJPEG JPEG가 정상적으로 들어간다. 이 SOI는 AVI
  경계도 별도 사진도 아니다.
- `idx1` 부재는 카빙 경계 오류와 별개다. 영상 데이터가 온전해도 일부 플레이어가 재생시간을 잘못 표시할
  수 있으며, 필요하면 `movi` 청크를 스캔해 인덱스를 재구성한다.
