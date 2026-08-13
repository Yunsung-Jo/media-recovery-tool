---
id: T-0001
title: 프로젝트 정체성과 Python 패키지 구조 전환
status: completed
type: refactor
depends_on: []
---

# T-0001. 프로젝트 정체성과 Python 패키지 구조 전환

## 시작 전 필독

1. 저장소 루트의 `AGENTS.md`
2. [`transition-plan.md`](../../../transition-plan.md)
3. 현재 구조를 설명하는 [`architecture.md`](../../../architecture.md)
4. 현재 CLI 계약인 [`0001-carve.md`](../../../specs/0001-carve.md)와
   [`0002-recover.md`](../../../specs/0002-recover.md)

`transition-plan.md`는 목표 상태이고 `architecture.md`와 specs는 아직 현재 구현을 설명한다. 둘을 섞어서
이미 구현된 것으로 간주하지 않는다.

## 문제

- `rawcarve`는 현재 카빙, JPEG 구조 복구와 선택적 후처리를 모두 포함하는 프로젝트 범위를 충분히
  표현하지 못한다.
- 저장소 루트 실행 스크립트와 `carver` 패키지는 설치 가능한 단일 CLI와 명시적인 프로젝트 namespace를
  제공하지 않는다.
- 향후 domain, discovery, format, reconstruction, artifact와 enhancement 책임을 분리할 기반이 필요하다.

## 목표

- 프로젝트 표시 이름을 `Media Recovery Tool`로 바꾼다.
- Python 배포 이름은 `media-recovery-tool`, import package는 `media_recovery`로 정한다.
- 표준 `src/media_recovery` layout과 `pyproject.toml`을 도입한다.
- `media-recovery` 단일 CLI 아래에 현재 기능을 subcommand로 이전한다.
- 현재 알고리즘과 출력 의미를 바꾸지 않고 파일과 import 경로만 목표 namespace로 옮긴다.

## 확정한 CLI 이름

T-0001에서는 현재 입력·출력 계약을 그대로 유지하면서 다음 진입점으로 옮긴다.

```text
media-recovery carve ...
media-recovery reconstruct ...
media-recovery enhance ...
```

- `recover` alias는 만들지 않는다.
- 기존 루트 스크립트 wrapper는 남기지 않는다.
- `enhance`는 T-0001 시점에 현재 thumbnail 기반 기능 하나를 제공한다. case/run 기반 `--method` 계약과
  preview/enhancement artifact 분리는 T-0010 범위다.
- 각 subcommand의 인자, 기본값, 출력 분류와 파일 내용은 이름 변경을 제외하고 현재 CLI와 같아야 한다.

## 범위

- `pyproject.toml`과 `media-recovery` console script
- `src/media_recovery/` package
- `__main__.py`와 통합 CLI dispatch
- 현재 루트 CLI 세 개의 subcommand 이전
- 기존 `carver` 모듈의 일대일 package 이전
- 코드와 테스트의 import 경로 변경
- 패키지 설치와 CLI smoke test
- 루트 README의 프로젝트 이름, 설치법과 실행 예시 갱신
- 현재 구조 문서에서 패키지 이전에 직접 영향받는 경로 갱신
- 이전 뒤 기존 루트 CLI와 `carver/` 제거

### 패키징 계약

- build backend는 초기 복잡도를 낮추기 위해 `setuptools`를 사용한다.
- `requires-python`은 현재 지원 범위와 같은 `>=3.10`으로 둔다.
- 배포 package의 초기 version은 `0.1.0`으로 시작하며 artifact schema·engine·policy version과 별개다.
- 저장소에 LICENSE가 없으므로 사용자 결정 없이 임의의 license metadata를 선언하지 않는다.
- runtime dependency(`tqdm`, `numpy>=1.24`, `Pillow>=10.0`, `numba>=0.60`)와 `dev` optional
  dependency(`pytest`)의 정본은 `pyproject.toml`이다. 패키지 이전만으로 현재 최소 버전을 올리거나 새 상한을
  추가하지 않는다.
- 설치 명령은 `python -m pip install -e .`와 개발용 `python -m pip install -e ".[dev]"`로 한다.
- `requirements.txt`와 `requirements-dev.txt`를 별도 수동 정본으로 유지하지 않는다. T-0001에서 제거하고
  README·AGENTS의 설치 명령을 함께 갱신한다.
- package discovery는 `src` layout만 대상으로 하며 저장소 루트가 우연히 import path가 되어 테스트를
  통과하지 않게 한다.

## 비범위

- 카빙, JPEG 경계, entropy, header, resync, placement와 thumbnail 알고리즘 변경
- 함수 내부 리팩터링 또는 성능 최적화
- `resync.py` 책임 분리
- case/run, JSONL, NPZ 또는 forensic artifact 구현
- 기존 출력 디렉터리와 `report.csv` 계약 변경
- 기존 action 이름 변경
- N-best, beam search 또는 새 confidence/evidence 평가
- thumbnail의 reconstruction 사용
- preview와 enhancement의 실제 artifact 분리
- 기존 ADR/spec 전체 이관 또는 삭제
- `usb.img` 전수 카빙·복구

## 일대일 이전 원칙

T-0001은 모듈을 새 책임 영역에 배치하되 한 기존 파일을 여러 구현 파일로 분해하지 않는다. 구체적인
파일명은 충돌 없이 더 명확하게 정할 수 있지만 다음 대응을 기본으로 한다.

| 현재 | T-0001 목표 |
|---|---|
| `carve.py` | `media_recovery/cli/carve.py` |
| `recover.py` | `media_recovery/cli/reconstruct.py` |
| `thumbref.py` | `media_recovery/cli/enhance.py` |
| `carver/models.py` | `media_recovery/domain/objects.py` |
| `carver/scanner.py` | `media_recovery/discovery/scanner.py` |
| `carver/carving.py` | `media_recovery/discovery/materializer.py` |
| `carver/extractors.py` | `media_recovery/formats/boundaries.py` |
| `carver/jpegdecode.py` | `media_recovery/formats/jpeg/baseline_decoder.py` |
| `carver/headerfix.py` | `media_recovery/reconstruction/header_hypotheses.py` |
| `carver/resync.py` | `media_recovery/reconstruction/engine.py` |
| `carver/thumbref.py` | `media_recovery/enhancement/thumbnail_guided.py` |

`formats/boundaries.py`에 JPEG와 AVI가 함께 남는 것과 `reconstruction/engine.py`가 큰 것은 이 Task에서
허용하는 의도적인 중간 상태다. 책임 분리는 T-0005에서 동작 동일성 검증과 함께 수행한다.

## 유지할 불변조건

- 현재 회귀 테스트의 의미와 통과 결과를 유지한다.
- 같은 입력과 옵션에서 출력 분류, 파일명, `report.csv` 필드와 복구 알고리즘 결과를 의도적으로 바꾸지
  않는다.
- 원본 `*.img`와 외부 legacy 결과를 수정·삭제하지 않는다.
- 개인 사진 내용은 문서나 응답에 묘사하지 않는다.
- package 이전과 무관한 발견을 구현하지 않는다.
- 새 compatibility wrapper나 `carver` alias를 만들지 않는다.
- 패키지 import는 설치된 `media_recovery` namespace를 통과해야 한다.

## 작업 계획

1. `git status`와 저장소 경로가 `media-recovery-tool`로 변경됐는지 확인한다.
2. 기존 `.venv`의 `sys.prefix`, Python과 pip 실행 경로를 확인한다. 이전 저장소 절대 경로가 남거나 실행이
   실패하면 새 저장소 경로에서 `.venv`를 재생성하고 현재 requirements를 설치한다.
3. 243개 테스트 baseline을 확인하고 Python·Pillow·NumPy·Numba 등 비교 결과에 영향을 줄 수 있는 실행환경
   버전을 기록한다. 패키지 이전 전후 비교는 같은 환경을 사용한다.
4. 개인 자료가 아닌 작은 합성 fixture에서 carve/reconstruct/enhance의 파일 집합, byte hash, 분류와
   정규화한 보고서를 이전 전 snapshot으로 저장한다.
5. `pyproject.toml`, `src/media_recovery`와 CLI entry point를 추가한다.
6. 기존 구현 파일을 위 일대일 원칙으로 이동하고 import만 갱신한다.
7. 테스트를 새 import로 갱신하고 CLI smoke test를 추가한다.
8. 기존 루트 CLI, `carver/`와 수동 requirements 정본을 제거한다.
9. README, AGENTS와 현재 구조 문서의 실제 경로·명령을 갱신한다.
10. 가까운 테스트, 이전 전 snapshot 비교와 전체 테스트를 실행한다.
11. 코드·테스트·현행 문서에서 의도하지 않은 `carver` import와 구 CLI 경로가 남지 않았는지 검색한다.
12. 결과와 생략한 검증을 이 Task에 기록하고 지속 문서를 갱신한다.

## 검증

필수:

```powershell
.venv\Scripts\python.exe -m pytest
```

추가 검증:

- editable install 또는 프로젝트가 정한 로컬 설치 방법 성공
- `media-recovery --help`
- 세 subcommand의 `--help`
- 최소 합성 입력을 사용한 CLI smoke test
- Windows spawn 환경에서 작은 합성 fixture로 `reconstruct -j 2`, `enhance -j 2` 실행
- package 이전 전후 합성 fixture의 파일 집합, carved/reconstructed/enhanced byte SHA-256, 결과 분류와
  보고서의 안정 필드 비교
- `report.csv`와 `report_thumbref.csv`는 CSV로 읽어 field 목록과 record 수를 확인하고 `filename`으로
  정렬해 비교한다. 병렬 worker 완료 순서는 비교하지 않으며 `recover_sec`·`secs` 같은 실행시간 필드는
  동일성 비교에서 제외한다. 그 밖의 안정 필드는 정확히 같아야 한다.
- `rg`로 코드·테스트 내 `from carver`, `import carver`가 0인지 확인
- 루트 `carve.py`, `recover.py`, `thumbref.py`와 `carver/`가 제거됐는지 확인
- `requirements*.txt`가 제거되고 README·AGENTS 설치법이 `pyproject.toml`과 일치하는지 확인
- README의 명령을 실제 환경에서 실행할 수 있는지 확인

3.5GB `usb.img` 전수 처리는 패키지 이전의 필수 검증이 아니다. 실행하지 않았다면 결과에 명시한다.

## 완료 조건

- 전체 테스트가 최소 현재 기준선 243개 이상 통과하고 기존 테스트의 설명 없는 삭제·skip·xfail이 없다.
- 새 CLI와 세 subcommand smoke test가 통과한다.
- Windows `-j 2` 병렬 smoke test가 통과한다.
- 이전 전후 합성 fixture의 파일 집합·byte SHA-256·분류와 정규화한 보고서 안정 필드가 의도한 CLI 이름
  차이 외에는 같다. 실행시간과 병렬 완료 순서는 동일성 조건이 아니다.
- 코드와 테스트의 `carver` import가 0이다.
- 기존 루트 CLI와 `carver/`가 제거됐다.
- `pyproject.toml`이 dependency 정본이고 수동 `requirements*.txt` 중복 정본이 없다.
- `recover` alias와 기존 CLI compatibility wrapper가 없다.
- README의 프로젝트 이름, 설치와 실행 예시가 실제 새 CLI와 일치한다.
- 복구 알고리즘, 결과 분류와 출력 계약에 의도적인 변경이 없다.
- 이 Task의 `결과`에 실제 파일 이동, 테스트 수, 생략한 검증과 발견한 한계를 기록했다.

## 결과

2026-08-13에 완료했다.

- 저장소 경로와 `.venv`의 `sys.executable`·`sys.prefix`·pip 경로가 모두
  `C:\Users\Yunsung\Desktop\media-recovery-tool` 아래를 가리킴을 확인했다.
- 같은 환경(Python 3.12.13, Pillow 12.3.0, NumPy 2.4.6, Numba 0.66.0, pytest 9.1.1,
  tqdm 4.68.4)에서 이전 전 전체 테스트 `243 passed`를 확인했다.
- `pyproject.toml`을 배포·dependency 정본으로 추가하고 `media-recovery-tool` 0.1.0,
  `requires-python >=3.10`, setuptools build backend, `media-recovery` console script와 `dev` extra를
  정의했다. `python -m pip install -e .`와 `python -m pip install -e ".[dev]"`가 모두 성공했다.
- 기존 루트 CLI 세 개와 `carver` 구현을 계획표대로 `src/media_recovery` 아래로 일대일 이전했다.
  통합 CLI는 `carve`, `reconstruct`, `enhance`만 제공하며 `recover` alias와 compatibility wrapper는 없다.
- 기존 루트 `carve.py`, `recover.py`, `thumbref.py`, `carver/`, `requirements.txt`,
  `requirements-dev.txt`를 제거했다. 코드와 테스트의 `from carver`·`import carver` 검색 결과는 0이고
  설치 환경에서도 `carver` spec은 없다.
- `README.md`, `AGENTS.md`, 현재 architecture/spec/format/status 문서의 설치법·명령·현재 경로를
  새 package와 CLI에 맞췄다. 과거 실행을 식별하는 legacy 명령은 역사 기록으로만 유지했다.
- 통합 CLI smoke 5개를 추가했고 최종 전체 테스트는 `248 passed in 18.25s`였다. 기존 테스트 삭제,
  skip 또는 xfail은 없었다.

### 합성 fixture 동등성

개인 자료가 아닌 고정 합성 fixture에 640×480 baseline JPEG, Exif 내부 JPEG와 최소 AVI를 넣었다.
이전 CLI와 설치된 새 CLI가 각각 JPEG 1개, AVI 1개, 내부 JPEG 2개를 같은 이름으로 카빙했고,
reconstruct는 `CLEAN`, enhance는 `skip_reg`로 분류했다.

- 비교 대상: 전체 파일 집합 9개, JPEG/AVI 산출물 6개 SHA-256, `report.csv`와
  `report_thumbref.csv`의 field 목록·record 수·`filename` 정렬 행
- 제외 필드: `recover_sec`, `secs`
- 이전/이후 정규화 snapshot SHA-256:
  `BA0D846972DB205C57A1250D596A1D40481125CD0A40EF71389295D2F7506C18`
- Windows spawn: `media-recovery reconstruct --time-budget 0 -j 2`와
  `media-recovery enhance -j 2` 성공

`media-recovery --help`와 세 subcommand의 `--help`, `python -m media_recovery --help`가 모두
exit code 0이었다. `media-recovery recover --help`는 의도대로 invalid choice와 exit code 2를 반환했다.

3.5GB `usb.img` 전수 처리는 Task 비범위이므로 실행하지 않았다. 원본 `*.img`와 외부 legacy 결과는
수정·삭제하지 않았고, 구현 완료 결과를 commit과 push 전에 먼저 보고했다.

## 지속 문서 반영

- 루트 `README.md`: 이름, 설치와 CLI
- `docs/architecture.md`: T-0002 전까지 실제 새 코드 구조를 반영
- `docs/current-state.md`: 완료한 패키지 전환과 테스트 기준선
- `AGENTS.md`: 전환 완료 뒤 현재 구조와 명령으로 정리

ADR/spec의 전면 이관은 T-0002에서 수행한다.

## 후속 작업

T-0002부터의 순서는 [`transition-plan.md`](../../../transition-plan.md)의 Task 로드맵을 따른다.
