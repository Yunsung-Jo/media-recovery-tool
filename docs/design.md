# 설계

이 문서는 Media Recovery Tool의 파이프라인, 모듈 책임과 구현을 제약하는 핵심 불변조건의 지속 정본이다.
사용자 인터페이스와 세부 동작 계약은 [carve spec](specs/0001-carve.md)과
[reconstruct spec](specs/0002-recover.md), 현재 파일 산출물은 [artifacts.md](artifacts.md), 검증 범위와
한계는 [status.md](status.md)를 함께 본다.

아래 `Current`는 현재 Python 코드와 테스트에 존재하는 동작이다. `Planned`는
[전환 계획](transition-plan.md)이 승인한 목표 방향이며 아직 구현되지 않았다. 두 절 사이의 이름이나
구조가 비슷해도 Planned를 현행 동작으로 해석하지 않는다.

## Current

### 지원하는 파이프라인

현재 통합 CLI는 세 명령만 제공한다.

```text
디스크 이미지
  → media-recovery carve
  → jpeg/ · avi/ · 선택적 jpeg_thumbnails/
  → media-recovery reconstruct
  → 6개 action 디렉터리 · report.csv
  → 선택적 media-recovery enhance
  → 썸네일 참조 보정 트리 · report_thumbref.csv
```

- `carve`는 파일 시스템 구조가 아니라 byte signature와 JPEG·AVI 구조를 사용해 범위를 추출한다.
- `reconstruct`는 3컴포넌트 baseline JPEG를 직접 디코드하고 byte 편집·bit resync·header 재구성·MCU
  placement를 적용한다.
- `enhance`는 카빙 원본의 Exif thumbnail을 참조 오라클로 사용해 복구본의 잔여 순환 밀림과 색 캐스트
  밴드를 선택적으로 보정한다.
- AVI는 경계를 계산해 추출할 뿐 stream, index 또는 재생 구조를 복구하지 않는다.

`render` 명령과 N-best 후보를 실제 생성·선택하는 pipeline은 현재 없다. T-0003의 case/run persistence와
T-0004의 forensic domain·record·NPZ 기반은 내부 API로 존재하지만 이 세 CLI 흐름에는 아직 연결되지
않았다.

### 모듈 책임

| 현재 모듈 | 책임 |
|---|---|
| [`cli/`](../src/media_recovery/cli/) | 통합 CLI 등록, 인자 검증, 파일 순회·worker 관리, CSV와 사용자 요약 출력 |
| [`domain/objects.py`](../src/media_recovery/domain/objects.py) | scanner의 파일 종류·offset과 탐지 근거 `source`·`confidence`·`scan_start`를 담는 불변 `FileHit` |
| [`domain/forensics.py`](../src/media_recovery/domain/forensics.py) | object/hypothesis/candidate/result, 명시적 좌표·provenance, segment/edit/placement와 coefficient manifest의 불변 model·검증 |
| [`discovery/scanner.py`](../src/media_recovery/discovery/scanner.py) | exact signature와 JFIF/Exif·AVI 구조 anchor 탐색, 후보 전체를 모은 뒤 경계 검증 |
| [`discovery/materializer.py`](../src/media_recovery/discovery/materializer.py) | hit 중복 제거·정렬·중첩 분류, 경계 호출, 청크 단위 저장과 오류 집계 |
| [`formats/boundaries.py`](../src/media_recovery/formats/boundaries.py) | JPEG marker/entropy와 RIFF/OpenDML 구조를 이용한 exclusive 끝 계산 |
| [`formats/jpeg/baseline_decoder.py`](../src/media_recovery/formats/jpeg/baseline_decoder.py) | 임의 bit 위치와 DC 상태에서 재개 가능한 3컴포넌트 baseline JPEG decoder |
| [`reconstruction/engine.py`](../src/media_recovery/reconstruction/engine.py) | byte 편집, bit resync, decode segment, MCU placement, 렌더·분류·저장 |
| [`reconstruction/header_hypotheses.py`](../src/media_recovery/reconstruction/header_hypotheses.py) | DHT/DQT/SOF/SOS 후보 재구성과 구조 gate |
| [`enhancement/thumbnail_guided.py`](../src/media_recovery/enhancement/thumbnail_guided.py) | thumbnail 정합, 행별 밀림·색 보정 추정과 self-check |
| [`artifacts/`](../src/media_recovery/artifacts/) | source hash case, run lifecycle·seal, strict JSONL, forensic record와 결정적 coefficient NPZ reader/writer |

CLI·저장·중첩 분류는 별도 모듈이지만 JPEG와 AVI boundary는 현재 한 파일에 있다. 두 경계가 다음 외부
후보와 AVI 내부 MJPEG 여부를 함께 알아야 하기 때문이다. 지원 형식이 늘어나 이 상호 참조가 커질 때
형식별 모듈과 공통 boundary registry로 나누는 것이 분리 조건이다. 지금 나누면 순환 의존이나 얇은
forwarding 모듈만 늘어날 수 있어 현재 배치를 유지한다. `reconstruction/engine.py`가 탐색, 배치, 평가,
분류와 저장을 함께 맡는 것도 현재의 의도적인 중간 상태이며 그 책임 분리는 T-0005 범위다.

### Carve 데이터 흐름과 불변조건

```text
read-only disk mmap
  → scanner.find_all_hits
  → offset 순 FileHit
  → materializer.process
  → boundaries.jpeg_end / boundaries.avi_end
  → 임시 파일 완성 후 os.replace
```

현재 카빙 설계는 다음 원칙을 유지한다.

1. 첫 `SOI`부터 첫 `EOI`까지를 무조건 JPEG 경계로 확정하지 않는다. entropy 손상으로 생긴 가짜 EOI는
   뒤 stuffing과 외부 구조를 함께 보고 건너뛴다([ADR 0002](adr/0002-carve-eoi-validation.md)).
2. 유효한 APP/COM·table payload 안 signature는 외부 객체가 아니다. Exif APP1의 JPEG만 thumbnail로
   분류하고 AVI의 `movi` 안 JPEG는 MJPEG frame으로 유지한다.
3. 무효 marker·길이·DQT/DHT 의미가 뒤 객체를 덮으면 선언 길이를 신뢰하지 않는다. 앞의 손상 조각과 뒤의
   구조 후보를 모두 보존한다([ADR 0007](adr/0007-carve-corrupt-header-boundary.md)).
4. JPEG와 AVI는 서로의 외부 경계를 제한한다. AVI의 RIFF size는 `hdrl`·`avih`·`movi`와 연속 AVIX 구조가
   맞을 때만 신뢰한다([ADR 0008](adr/0008-jpeg-boundary-stops-at-avi.md),
   [ADR 0010](adr/0010-avi-structure-and-opendml-boundary.md)).
5. 손상 시작은 fuzzy signature 하나가 아니라 JFIF/Exif 또는 RIFF/form anchor와 제한된 후속 구조를 함께
   검증한다. FAT metadata는 독립 감사에는 쓸 수 있지만 런타임 후보 생성에는 쓰지 않는다
   ([ADR 0009](adr/0009-structural-damaged-starts.md)).
6. 디스크 이미지는 읽기 전용 mmap으로 열고, 출력은 8 MiB 청크로 같은 디렉터리의 임시 파일에 쓴 뒤
   교체한다. 이 계약은 일반 쓰기 예외의 부분 최종 파일 노출을 막지만 fsync 전원 장애 내구성을 보장하지
   않는다.

객관적인 marker와 RIFF 사실은 [format-notes.md](format-notes.md), 현재 세부 경계 규칙은
[carve spec](specs/0001-carve.md)이 정본이다.

### Reconstruct 데이터 흐름과 불변조건

```text
입력 *.jpg
  → baseline Decoder 구성
  → 실패 시 header hypothesis
  → byte substitution/deletion/insertion 또는 bit resync
  → 선택된 decode의 MCU placement
  → JPEG 렌더와 action 분류
  → report.csv
```

현재 복구 설계는 다음 불변조건을 유지한다.

1. baseline JPEG를 직접 bit 단위로 디코드해 손상 지점과 재개 bit 위치를 제어한다. libjpeg의 오류 은폐를
   source 복구로 간주하지 않는다([ADR 0001](adr/0001-resync-recovery.md)).
2. decode segment는 작은 MCU에서 큰 MCU로 진행하며 새 segment는 이전 segment보다 뒤 source bit에서
   시작한다. 이미 확정한 앞 source를 다시 사용해 반복 콘텐츠를 만들지 않는다.
3. 복구 근거가 없는 영역은 RGB 128의 회색으로 남긴다. 현재 회색은 표시와 저장 방식이며 source가
   존재했다는 주장이 아니다.
4. resync는 직전 DC carry와 전체 0 reset을 함께 평가한다. 0 reset은 관측값이 아니라 재개를 위한 추론이고
   색 offset을 만들 수 있으며, 원래 지점 24 bit 안의 masking 재개는 거부한다
   ([ADR 0004](adr/0004-resync-dc-reset-recovery.md)).
5. header 후보는 렌더가 그럴듯해 보이는지가 아니라 시작 clean run, entropy 소비율, 복구 범위와 자체
   header 우월 조건 같은 구조 신호로 선택한다([ADR 0006](adr/0006-header-recovery-structural-gates.md)).
6. placement는 수락한 편집·resync의 실패 MCU를 절단 근거로 사용한다. `phase_cuts=[]`이면 픽셀 상관만으로
   정상 이미지를 이동시키지 않는다. owner는 역순·복제되지 않고 출력 크기를 유지하며, 삽입·유실은
   `ceil(전체 MCU·0.05)` 안으로 제한한다. 소형 이미지에도 한 MCU 행 예외를 두지 않는다
   ([ADR 0011](adr/0011-resync-segment-mcu-alignment.md)).
7. 1차 band 배치 뒤 모든 MCU 행 경계를 1·2·3·4 pixel strip으로 검사한다. 상단 행 위상 0에서 누적한
   절대 위상 후보를 같은 1차 render와 owner map에서 매번 다시 배치하고 최대 5회 연쇄를 탐색한다. 손실·
   owner 조건과 독립 잔차 감사를 통과한 최선 상태만 반환한다.
8. 전역 행 위상 후보가 안전하게 채택되면 같은 경계를 국소 단계에서 다시 해석하지 않는다. 전역 해가
   없을 때만 절단점 주변 구조적 국소 보정과 보수적 행 stitch로 후퇴한다.

임계와 action 판정, CSV 필드의 세부 계약은 [reconstruct spec](specs/0002-recover.md)이 정본이다.

### Case/run persistence 데이터 흐름과 불변조건

T-0003에서 추가한 내부 persistence는 기존 command output과 격리되어 다음 흐름을 제공한다.

```text
source 절대 경로
  → 전체 SHA-256·크기로 case 등록
  → stage·completed parent·version·environment·options로 run 생성
  → source hash 재검증 후 created → running
  → coordinator가 canonical JSONL을 atomic replace
  → completed run.json + completed.json seal
```

1. 기본 work root는 호출 현재 디렉터리의 `./work`이고 재정의할 수 있다. source는 기본적으로 복사하지
   않으며 `/work/` 전체는 Git 비추적이다.
2. case ID는 source SHA-256 앞 20 hex에 안정적이다. 기존 prefix가 있으면 전체 hash를 비교해 다른
   source를 거부하고 기존 metadata를 덮어쓰지 않는다.
3. discovery는 부모가 없고 후속 stage는 같은 case에서 stage가 맞으며 completion seal을 통과한 부모만
   참조한다.
4. 새 run 시작과 interrupted/error resume는 source를 다시 hash한다. resume는 tool·engine·policy·artifact
   schema version, environment와 canonical options가 같으며 caller가 stage 지원을 명시할 때만 허용한다.
   이전 attempt는 보존한다.
5. completed run은 API에서 쓰기와 resume를 거부한다. reader는 completion marker와 `run.json`뿐 아니라
   봉인한 전체 파일 집합·크기·SHA-256도 대조해 외부 변경이나 추가를 탐지한다.
6. JSON은 strict UTF-8이고 JSONL은 record별 LF다. non-finite number와 중복 key를 거부하며 coordinator가
   전순서를 갖는 stable key와 canonical byte로 정렬한 뒤 임시 파일을 원자 교체한다.
7. dirty run은 diff hash만 기록할 수 없고 실제 patch byte를 `provenance/dirty.patch`로 함께 보존해야 한다.

schema·ID·completion marker의 정확한 on-disk 계약은 [artifacts.md](artifacts.md)가 정본이다. 이 기반은
현재 `carve`, `reconstruct`, `enhance`의 인자·output·CSV를 바꾸지 않는다.

### Forensic domain·NPZ 데이터 흐름과 불변조건

T-0004의 내부 API는 기존 engine과 격리된 다음 흐름을 제공한다.

```text
disk offset 기반 ObjectRecord
  → hypothesis·observed SourceSpan
  → CandidateRecord의 decoded segment·inferred edit/placement
  → object/candidate별 quantized coefficient·validity NPZ + embedded manifest
  → ResultRecord의 직교 상태
  → canonical JSONL
  → 기존 completed run seal
```

1. object ID는 media type과 64-bit disk absolute start offset에 안정적이다. candidate ID는 object 안의
   `0..999` ordinal이고, run 사이 비교 key는 구조적 canonical input의 SHA-256 fingerprint다.
2. 한 domain value에는 하나의 provenance만 둔다. source span은 observed, decode segment와 canonical
   coefficient는 decoded, virtual edit와 placement는 inferred다. generated는 source-backed validity에
   들어가지 않는다.
3. observed source의 정본은 같은 길이의 수정되지 않은 disk/object raw byte half-open range다. raw entropy,
   destuffed, virtual work bit를 구분하고 불연속 source는 여러 span으로 보존한다. virtual edit만 pre-edit와
   work range를 연결하며 placement는 source block을 raster 위치에 연결할 뿐 source 좌표를 바꾸지 않는다.
   한 record의 disk/object raw 및 존재하는 raw entropy/destuffed span끼리는 같은 좌표를 중복 소유할 수
   없다. record reader/writer는 disk span이 case source 크기 안에 있는지 확인한다. object parent는 같은
   `objects.jsonl`에서 해소되는 비순환 graph이고 입력 깊이에 무관한 반복형 검증을 사용한다. candidate의
   첫 source anchor는 object ID offset과 같으며 candidate/result object ID는 부모 discovery record 하나에
   정확히 해소돼야 한다.
4. canonical coefficient는 DQT 적용 전 quantized `int32` DCT 값이다. Y/Cb/Cr 각각 coefficient 단위
   source-backed mask와 그 0/partial/complete block 상태를 갖는다. source span ref가 없는 coefficient는
   decode 가능해 보여도 source-backed가 될 수 없다.
5. component별 raster owner는 source block index, `-1` gap, `-2` unresolved overlap만 쓴다. placement
   record와 owner array는 같은 mapping이어야 하며 하나의 source block을 복제 배치하지 않는다.
6. NPZ는 고정 18개 array와 dtype/byte order를 사용한다. writer는 fixed ZIP/NPY metadata와 이름순 member로
   byte 결정성을 보장하고 같은 디렉터리 staging 뒤 원자 교체한다. reader는 manifest hash·크기·array
   metadata와 의미를 대조하고 pickle/object dtype, 비정규·drive-qualified·run 밖 path와 symlink를
   거부하고 각 NPY member의 format version도 `2.0`으로 확인한다. colon/NTFS alternate data stream 또는
   control character가 있는 path와 한 record
   file에서 서로 다른 owner가 filesystem-normalized 동일 NPZ path를 공유하는 경우도 거부한다. object
   owner는 discovery, candidate owner는 reconstruction run에서만 읽고 쓴다.
   `DecodeSegment`의 source span/edit ID는 생성 시 tuple snapshot으로 고정해 fingerprint 입력이 사후 변경되지
   않게 한다.
7. result는 execution/support/decode/selection/header/artifact를 직교 field로 둔다. 배포 schema와 Python
   validator의 상태 조합 허용 집합은 전수 조합 test로 같게 유지하며 `interventions`는 중복 없는 enum
   집합이다.
8. schema, engine, policy version은 별도 field다. schema version은 선행 0 없는 `major.minor` 십진 표기를
   사용하며 같은 major reader, unknown required feature 거부와 완료 run 불변성은 T-0003 규칙을 그대로
   사용한다.

배포 schema가 검증하는 field·enum·shape와 외부 파일·cross-record 관계 때문에 Python reader가 추가로
검증하는 경계는 [artifacts.md](artifacts.md)가 정본이다. 이 기반은 model과 persistence 계약이며 현재
engine 결과를 실제로 변환하거나 N-best를 생성하지 않는다.

### Enhance 데이터 흐름과 경계

현재 `enhance`는 reconstruction과 분리된 선택적 후처리다. Exif thumbnail은 생성 pixel source가 아니라
정합·수락을 위한 참조 오라클이고, 출력 pixel은 복구본에서 온다. 전역 scale·세로 offset 정합, 행별 FFT
순환 상관, MCU 단위 shift, YCbCr 색 보정과 회차별 self-check를 거치며 근거가 없거나 개선되지 않으면
입력을 그대로 보존한다([ADR 0012](adr/0012-thumbnail-reference-correction.md)).

이는 현재 픽셀 기반 보정 동작이다. thumbnail이 source 구조를 입증하거나 header/resync 후보를 선택한다고
주장하지 않는다. AI enhancement도 현재 구현되어 있지 않다.

### 현재 지원 경계

- 카빙 입력은 일반 파일 형태의 디스크 이미지이며 파일 시스템 metadata를 런타임에서 읽지 않는다.
- exact와 제한된 손상 JPEG·AVI 시작을 찾지만 비연속 cluster에 저장된 조각을 자동 연결하지 않는다.
- reconstruction은 8-bit, 3-component baseline JPEG를 대상으로 한다. progressive와 비 3컴포넌트 JPEG는
  구조 복구 대상이 아니다.
- AVI는 객체 경계와 연속 OpenDML form을 추출할 수 있지만 stream·index를 수리하지 않는다.
- CLI 출력은 현재 디렉터리 트리와 CSV 중심이다. 별도 case/run·forensic model/NPZ 기반은 있지만 현재
  engine 결과를 source span, virtual edit, coefficient·validity와 후보 record로 쓰는 연결은 없다.

### 현재 비용과 I/O 경계

- scanner hit 수를 `H`, 동일 type/offset을 합친 수를 `U`라 하면 중복 제거는 O(H), 정렬은
  O(U log U), hit 메모리는 O(U)다.
- 고정 signature scan은 입력 크기 `N`에 대해 O(N)이지만 후보별 bounded JPEG 구조 검증과 AVI RIFF walk가
  추가된다. 손상 AVI 후보가 매우 많은 적대적 입력에서는 후보 수와 image 크기의 곱까지 커질 수 있다.
- JPEG boundary walk는 후보별 `--max-jpeg-size` 안에서 선형이며 scanner가 만든 JPEG·AVI offset index에서
  다음 외부 경계를 이진 탐색한다.
- materializer는 성공한 마지막 최상위 범위 하나를 active range로 유지해 일반 포함 판정을 O(1)로 한다.
- 출력 writer의 추가 Python heap buffer는 최대 8 MiB지만 mmap resident set과 OS page cache는 이 상한에
  포함되지 않는다.

## Planned — 아직 구현되지 않음

이 절은 현재 package나 CLI의 동작 계약이 아니다. 구체적인 구현 순서와 범위는 각 활성 Task가 정하며,
schema·enum·기본값은 해당 Task에서 테스트와 함께 확정하기 전까지 계획값이다.

### 목표 파이프라인

```text
disk의 observed byte와 절대 offset
  → 복수 object boundary와 소속 관계
  → header hypothesis
  → entropy decode와 resync 후보
  → DecodeSegment와 component validity
  → MCU placement, gap와 overlap
  → evidence 기반 N-best 평가와 필요 시 재탐색
  → forensic artifact
  → preview render
  → 선택적 enhancement
```

장기 설계는 관측값(`observed`), source bit에서 디코드한 값(`decoded`), 구조·배치 가정(`inferred`),
표시·보정으로 만든 값(`generated`)을 분리한다. disk byte, object raw byte, destuff 전후 bit, virtual edit
작업 bit와 MCU/block 위치 사이의 변환을 추적하고, placement가 source 좌표를 바꾸지 않게 한다.

### 계획된 책임 분리

| Planned 영역 | 목표 | 최초 담당 Task |
|---|---|---|
| reconstruction 책임 분리 | 현행 single-best 동작을 보존한 engine 분해 | T-0005 |
| forensic artifact 출력 | 현행 결과와 근거를 재감사 가능한 record로 저장 | T-0006 |
| boundary/header N-best | 복수 경계·header 후보 유지 | T-0007 |
| entropy beam과 validity | 복수 resync, block/component validity | T-0008 |
| 반복 placement·평가 | gap·overlap·evidence 기반 후보 재평가 | T-0009 |
| `render`와 enhancement 분리 | artifact에서 preview를 재생성하고 생성값을 분리 | T-0010 |

`work/`, case와 stage run, strict JSON/JSONL은 T-0003, forensic domain·record와 coefficient NPZ는
T-0004에서 구현했다. 위 Planned 책임은 이 기반에 기존 engine을 연결하고 실제 값을 채우는 후속 범위다.

`rendering` 같은 목표 이름이 문서에 나타나더라도 현재 package에 해당 구현이 있다는 뜻은 아니다. Current
artifact와 상태 모델은 [artifacts.md](artifacts.md), 남은 검증 계획은 [evaluation.md](evaluation.md)를
본다.

## 결정 근거의 이관 상태

ADR은 현재 계약 정본이 아니라 결정 당시 맥락을 보존하는 역사 자료로 모두 유지한다. 지속할 설계 원칙은
다음처럼 이 문서와 평가 문서에 흡수했다.

| ADR | 지속 문서에 남긴 핵심 |
|---|---|
| [0001](adr/0001-resync-recovery.md) | 직접 baseline decoder, bit resync와 가짜 source 생성 금지 |
| [0002](adr/0002-carve-eoi-validation.md) | 첫 EOI를 확정 경계로 쓰지 않는 원칙; marker 사실은 format notes |
| [0003](adr/0003-recover-perf-optimization.md) | 출력 동일성을 지키는 성능 검증은 evaluation |
| [0004](adr/0004-resync-dc-reset-recovery.md) | DC reset은 추론이며 색 offset을 만들 수 있음 |
| [0005](adr/0005-scaled-accept-threshold.md) | 짧은 파일·꼬리에서 고정 임계가 실패한 사실은 evaluation |
| [0006](adr/0006-header-recovery-structural-gates.md) | header hypothesis 재료와 구조 gate; 평가 원칙은 evaluation |
| [0007](adr/0007-carve-corrupt-header-boundary.md) | 무효 segment 길이가 뒤 객체를 덮지 못하게 함 |
| [0008](adr/0008-jpeg-boundary-stops-at-avi.md) | JPEG와 AVI 사이 object boundary 조정 |
| [0009](adr/0009-structural-damaged-starts.md) | 손상 시작은 anchor와 후속 구조를 함께 검증 |
| [0010](adr/0010-avi-structure-and-opendml-boundary.md) | RIFF 구조·OpenDML boundary; 포맷 사실은 format notes |
| [0011](adr/0011-resync-segment-mcu-alignment.md) | gap, owner 단조성, 원본 크기와 placement 손실 제한 |
| [0012](adr/0012-thumbnail-reference-correction.md) | thumbnail oracle, 보수적 gate와 rollback; 평가 원칙은 evaluation |

세부 임계, 과거 실험 수치와 결정 당시 대안은 각 ADR에 남긴다. 새 설계 결정은 새 ADR을 만들지 않고
해당 Task와 이 지속 문서를 갱신한다.
