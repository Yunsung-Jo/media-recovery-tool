---
id: T-0005
title: 기존 복구 엔진의 동작 보존 책임 분리
status: completed
type: refactor
depends_on: [T-0004]
---

# T-0005. 기존 복구 엔진의 동작 보존 책임 분리

## 문제

`src/media_recovery/reconstruction/engine.py` 3,329줄이 품질 지표, entropy decode와 byte edit·resync,
segment/MCU placement, header/정상 경로의 single-best 선택, action 판정, JPEG encoding과 legacy 경로 저장을
함께 담당한다. 계산 결과가 출력 경로에 쓰는 코드 안에서만 완성되므로 T-0006이 현재 single-best 결과를
forensic artifact로 변환하려면 선택·판정·저장 코드를 다시 분해해야 한다.

현행 T-0003/T-0004 case/run과 forensic domain·NPZ 기반은 이 엔진과 독립되어 있다. 이번 Task에서 이를
연결하거나 schema를 바꾸지 않고, 같은 입력·옵션의 현행 single-best 결과와 사용자 동작을 그대로 유지한
채 책임 경계만 바꾼다.

## 구현 전 조사 결과

- `engine.py`의 call graph는 placement helper 군(기존 62~2,929행), entropy helper와 `recover()`
  (2,930~3,190행), `recover_file()`의 header/정상 경로 선택·action·저장(3,201~3,329행)으로 응집된다.
- module global은 entropy의 `DC_BOUND`, `AC_BOUND`, `_ZZ`와 placement의 `_STRUCTURAL_ROW_LEVELS`뿐이다.
  Numba 함수 `_gradient_phase_scores`는 placement helper만 호출하고, entropy Numba 의존은 JPEG decoder의
  `decode_probe`·`decode_range` 호출이다.
- `_decode_traj()`는 전달받은 decoder의 `buf`, `nbits`, `cy`, `cb`, `cr`를 현재 작업 stream과 coefficient
  grid로 바꾼다. normal/header 후보는 각자 decoder instance를 사용하며 선택 전 render는 placement를
  적용하지 않는다.
- 파일 I/O는 기존 `recover_file()`의 입력 `read_bytes`, action별 `mkdir`·`write_bytes`, Pillow JPEG
  encoding에만 있다. entropy와 placement 계산 helper는 출력 경로에 접근하지 않는다.
- action 우선순위는 decoder 구성 실패의 header/skip, opening probe가 유발한 자체-header 우월 비교,
  선택 경로 placement 1회, normal 경로의 clean → failed → recovered 순이다.
- `header_hypotheses`는 순환 회피용 지연 import로 engine의 `_probe`, `gray_fraction`,
  `undecoded_fraction`을 호출한다. 이를 새 실제 소유 모듈로 바꾸면 지연 순환이 필요 없다.
- 기존 reconstruction test는 placement private helper를 `engine`에서 직접 호출·monkeypatch한다. 책임 이동
  뒤에는 façade wrapper를 남기지 않고 실제 placement 소유 모듈을 대상으로 갱신한다.

## 목표

- 현행 single-best reconstruction의 action, RGB, segment, `phase_cuts`, 안정 stats/info, output 상대 경로와
  byte를 보존한다.
- entropy repair, placement, header/정상 single-best 선택, legacy materialization, 공개 façade의 책임을
  응집된 모듈로 분리한다.
- 출력 디렉터리를 모르는 계산 함수가 immutable `SingleBestResult`를 반환하고 legacy writer가 이를 소비하게
  해 T-0006이 복구 알고리즘이나 writer를 다시 분해하지 않고 결과를 변환할 수 있게 한다.
- `media_recovery.reconstruction.engine`의 `recover_file`, `recover`, `recover_bytes`, `gray_fraction`,
  `undecoded_fraction` import 경로와 signature·반환 구조를 유지한다.

## 확정 모듈 경계

| 모듈 | 책임 |
|---|---|
| `metrics.py` | normal/header 양쪽이 공유하는 순수 RGB gray·undecoded 지표. 선택과 I/O를 import하지 않는다. |
| `placement.py` | segment/MCU owner mapping, band·전역·구조적 국소·legacy row 보정, 손실·안전 평가. decoder를 duck-typed 입력으로만 소비한다. |
| `entropy.py` | probe, byte edit, resync, segment trajectory와 반복 복구 loop. JPEG decoder를 mutation하는 유일한 reconstruction 모듈이며 공개 `recover()` 의미를 위해 마지막 placement를 선택적으로 호출한다. |
| `single_best.py` | header와 normal 경로 실행·비교, 선택 경로 placement 1회, action 판정, output-neutral immutable `SingleBestResult`. |
| `legacy_output.py` | result의 JPEG encoding 또는 원본 byte 보존, 여섯 legacy action 중 파일 action의 directory routing과 파일명. |
| `engine.py` | 기존 공개 함수의 호환 façade. path 입력을 읽고 계산과 legacy materialization을 조합하되 알고리즘 helper를 소유하지 않는다. |

의존 방향은 `engine → single_best → {header_hypotheses, entropy, placement, metrics}`와
`engine → legacy_output → single_best`, `entropy → {baseline_decoder, placement}`,
`header_hypotheses → {baseline_decoder, entropy, metrics}`다. `placement`와 `metrics`는 상위 orchestration·
writer를 import하지 않으며 JPEG format decoder도 reconstruction을 import하지 않는다.

## 범위

- 위 모듈 경계로 기존 구현을 이동하고 call site·test seam을 실제 소유 모듈에 맞춘다.
- `SingleBestResult`에 action, source byte snapshot, read-only RGB snapshot, immutable info와 segment snapshot을
  둔다. caller의 mutable array·mapping·segment DC alias가 결과를 바꾸지 못하게 한다.
- output-neutral single-best 계산 API와 별도 legacy materializer를 추가한다.
- 현행 합성 fixture를 이용해 일곱 action/경로와 normalized snapshot·output hash characterization을 먼저
  고정한다.
- package/wheel에 새 모듈이 포함되고 Windows spawn에서 import 가능한지 검증한다.
- 실제 Current module 책임과 검증 결과를 지속 문서에 반영한다.

## 비범위

- 복구율·화질·성능 개선, threshold·탐색 순서·window·후보 순위·구조 gate·placement algorithm 변경
- 새 resync/header/boundary/placement 후보, N-best·beam search·반복 재평가
- T-0004 domain/schema/NPZ, case/run lifecycle·completion seal 변경 또는 기존 CLI 연결
- forensic object/result/candidate/NPZ 실제 출력과 T-0006 mapping 확정
- CLI 인자·기본값·preset, legacy directory/CSV/console 형식 변경
- carve, enhance, preview/render, package/schema version 변경
- 원본 `*.img`, 외부 legacy output·실험 자료 접근 또는 전수 처리
- ADR/spec 삭제, commit·merge·push·stash·브랜치 삭제

## 유지할 불변조건

- `--time-budget 0`에서 action, output 파일 집합·상대 경로·SHA-256, `recover()` RGB shape/dtype/byte,
  segment 순서·값, `phase_cuts`, `recover_sec` 제외 stats/info와 CSV 안정 필드가 같다.
- `CLEAN`, `FAILED`, `SKIP_UNDECODABLE`, `ERROR`는 원본 byte를 보존하고 `RECOVERED`,
  `HEADER_RECOVERED`는 같은 quality·이름·위치·Pillow 설정으로 재인코딩한다.
- 여섯 action 판정 조건과 우선순위, byte edit/resync 순서·임계·DC carry/zero reset·masking 거부·operation
  상한·deadline 위치와 중단 의미를 바꾸지 않는다.
- segment MCU/source bit 단조성, frontier/hole, placement owner 유일·단조, 크기·gap·overlap·5% 손실·
  top-anchor와 전역→구조적 국소→legacy 순서를 보존한다. `phase_cuts=[]`는 모든 공간 단계를 우회한다.
- decoder 구성 실패, opening probe header 시도, 보정 전 자체-header 우월 비교와 `undec_after` 0.01 조건,
  선택 경로 placement 정확히 1회를 보존한다.
- positive time budget의 wall-clock 동일성은 요구하지 않지만 deadline 확인 위치와 의미를 의도적으로
  이동하지 않는다.
- 새 dependency graph에는 cycle이 없고 CLI worker와 함수·dataclass는 Windows spawn에서 import 가능하다.

## 작업 계획

1. 합성 fixture characterization과 공개 signature, output-neutral result/writer 경계 test를 현행 구현에
   먼저 추가하고 통과시켜 baseline snapshot을 기록한다.
2. placement와 entropy 구현을 call graph 단위로 이동하고 private helper test seam을 새 소유 모듈로 바꾼다.
3. immutable single-best result와 output-neutral 선택 함수를 추가하고 legacy writer를 분리한다.
4. 얇은 engine façade에서 기존 공개 API를 재노출하고 `recover_file()` 조합 계약을 유지한다.
5. 근접·artifact·전체·spawn·wheel·import/cycle·link·diff 검증 뒤 지속 문서와 Task 결과를 갱신한다.

## 검증

- 리팩터링 전/후 characterization normalized snapshot과 output SHA-256
- `tests/test_resync.py`, 새 responsibility/characterization test와 CLI test
- `tests/test_artifacts.py tests/test_forensic_artifacts.py`
- `.venv\Scripts\python.exe -m pytest`
- 합성 CLI `--time-budget 0 -j 1`과 Windows spawn `-j 2`
- wheel build, 새 module과 배포 schema 8개 포함 확인
- 공개 signature/import smoke, module import cycle 검사, Markdown local link 검사
- `git diff --check`, `git status --short --branch`

## 결과

### 구현

- 3,329줄 `engine.py`를 35줄 공개 façade로 바꾸고 실제 call graph에 따라 `metrics.py` 34줄,
  `entropy.py` 283줄, `placement.py` 2,876줄, `single_best.py`와 `legacy_output.py`로 책임을 이동했다.
  private forwarding wrapper는 남기지 않았고 placement test의 직접 호출·monkeypatch seam도 실제 소유
  모듈로 옮겼다.
- `header_hypotheses`는 더 이상 engine 지연 import로 `_probe`·RGB 지표를 찾지 않고 entropy·metrics를
  직접 사용한다. 확정한 dependency graph의 cycle은 자동 test와 import smoke에서 0이다.
- `reconstruct_single_best(data, ...)`는 output path를 받지 않고 `SingleBestResult`를 반환한다. result는
  source byte, write-protected RGB, deep-frozen 표준 `Mapping` 호환 info와 `(MCU, bit, DC tuple)` segment를
  snapshot하며 원래 bytearray·NumPy array·mapping/list/DC mutation과 alias되지 않는다. 원본과 pickle
  round-trip 배열 모두 immutable byte buffer를 기반으로 해 write flag를 다시 켤 수 없다. 이미 frozen인
  wrapper와 `SegmentSnapshot`도 다시 정규화하고 unsupported mutable/custom info 값은 거부한다.
- `legacy_output.materialize_result()`만 action directory, `.jpg` 이름, Pillow quality encoding과 원본 byte
  보존을 담당한다. `engine.recover_file()`은 input read → output-neutral 계산 → legacy materialization만
  조합하고 기존 tuple 반환을 유지한다. CLI worker 예외도 `ERROR` `SingleBestResult`를 만든 뒤 같은 writer를
  사용하며 원본 읽기·저장 실패를 삼키는 기존 예외 격리는 유지한다.
- header 구조 gate와 자체 header 우월 비교는 보정 전 RGB를 계속 사용한다. decoder 구성 실패와 opening
  probe 경로 모두 선택된 header result에 placement를 정확히 한 번만 적용한다.

### 동등성 결과

리팩터링 전에 추가한 합성 fixture snapshot은 뒤에도 byte 단위로 같았다. `recover_sec`만 제외하고 action,
상대 경로, info/stats, `recover()` RGB shape·dtype·byte, segment와 `phase_cuts`, 원본 보존 여부를 포함한다.

- normalized snapshot:
  `d16d2092beb65ba6aa723d6c9d9d54d37ed839f4ef42cd66c467c1e9738bdf95`
- clean output: `a669293446bea46e216eb47e4aea3aec10610cea80d7b71af6271d79bf4d1e17`
- entropy recovered output: `04c62696b63dced8e698ea5eb952217d105ec875095db8a67824e600ff2b5e8c`
- spatial-only recovered output: `db8d02d8010a00fbb3d7680bc38063601aa4fda896cb30164e9d3c3cb07a6f75`
- header recovered output: `a47fb760e9290a93046735aeef868c7d3bb9897d40cf4c578a3e3df6789de3ad`
- failed original: `ddc6d7ab521638ee6d6bbd4cde6df50bfa45363c00dcd7dc174e9c00cdcd2964`
- skip original: `cee221e76076336aa6e05563034223cbb11991bf6dd36a945aa05ef12f3ab2dd`
- worker error original: `3462e4ef716a063b75ad9c94c4797c13cf328b2780e1ecbcf36baa624198e854`

full 값은 [`t0005-engine-baseline.json`](../../../../tests/fixtures/reconstruction/t0005-engine-baseline.json)에
고정했다. 실제 CLI 합성 3파일은 `--time-budget 0 -j 1`과 Windows spawn `-j 2`에서 `recover_sec`와 worker
완료 순서만 정규화했을 때 기준 HEAD와 현재 실행 모두 snapshot SHA-256
`becf2619a73034728d9c096ba68ac54189d26f1e194987c85e79d566867db89c`로 같았다. 이 snapshot은 report
field/record·output hash·action/header 안정 콘솔 요약을 포함한다. placement
요약까지 포함하도록 확장했으며, 여섯 action·worker error·placement·header를 함께 고정한 별도 CLI summary
snapshot은 `ba4c1e89ad619c1a3c83f73c389ef79daab6198fc47575e5ece90f298e1d0cf8`이다.

### 검증 결과

- 리팩터링 전 characterization:
  `.venv\Scripts\python.exe -m pytest tests\test_reconstruction_characterization.py -q` →
  `2 passed in 1.41s`
- 최종 reconstruction 근접:
  `.venv\Scripts\python.exe -m pytest tests\test_resync.py tests\test_recover.py tests\test_cli.py tests\test_reconstruction_boundaries.py tests\test_reconstruction_characterization.py -q`
  → `105 passed in 9.30s`
- T-0003/T-0004 artifact:
  `.venv\Scripts\python.exe -m pytest tests\test_artifacts.py tests\test_forensic_artifacts.py -q` →
  `98 passed in 9.53s`
- 전체: `.venv\Scripts\python.exe -m pytest` → Python 3.12.13, `363 passed in 26.90s`
- wheel: 격리 `pip wheel . --no-deps` 성공, reconstruction module 8개와 배포 schema 8개 포함 확인;
  최종 검증 wheel SHA-256 `d7fe2f959bc8b353ed1c62e53c3bc8cad8546b8baeb1ed9b4148dfd6c99c661c`
- import: 공개 5개 함수 signature, 새 module import, immutable result pickle, `compileall` 성공;
  engine에 placement private wrapper가 없고 static dependency cycle test 통과
- 이동 무결성: HEAD의 기존 engine과 새 metrics·placement·entropy의 공통 함수/class 81개를 AST로 비교해
  module qualification만 정규화한 구현 불일치 0
- 문서: Markdown 32개, local link 224개, broken 0
- Git: `git diff --check` 오류 0; 원본 image·외부 legacy 자료 접근 0

첫 격리 wheel 시도는 sandbox network가 `setuptools>=68` 다운로드를 막아 실패했다. 승인된 network에서 같은
격리 build를 재실행해 성공했고 임시 wheel directory는 확인 뒤 안전하게 삭제했다.

### 변경 파일 묶음

- 구현: `reconstruction/engine.py`, `metrics.py`, `entropy.py`, `placement.py`, `single_best.py`,
  `legacy_output.py`, `header_hypotheses.py`
- 검증: `test_resync.py`, `test_reconstruction_boundaries.py`, `test_reconstruction_characterization.py`,
  합성 baseline JSON
- 문서: `AGENTS.md`, `docs/design.md`, `docs/status.md`, `docs/evaluation.md`,
  `docs/specs/0002-recover.md`, `docs/transition-plan.md`, Task index와 이 Task

`docs/artifacts.md`, README, T-0004 domain/schema/NPZ, package/schema version과 CLI 계약은 변경하지 않았다.
실제 `*.img` 전수 처리와 외부 legacy 자료 검증도 비범위대로 생략했다.

## 지속 문서 반영

실제 Current dependency flow를 `docs/design.md`, module ownership만 `docs/specs/0002-recover.md`, 테스트 수치와
검증 경계를 `docs/status.md`·`docs/evaluation.md`, T-0005 완료 경계를 `docs/transition-plan.md`와
`AGENTS.md`에 반영했다. artifact 계약과 CLI 사용법이 바뀌지 않아 `docs/artifacts.md`와 README는 수정하지
않았다.

## 후속 작업

- T-0006: `SingleBestResult`와 decoder/segment 계산 결과를 현행 forensic record·NPZ에 매핑하고 stage run을
  실제 출력한다.
- T-0007 이후: header/boundary N-best, entropy beam과 component validity, 반복 placement/evidence 평가를
  별도 Task에서 구현한다.
