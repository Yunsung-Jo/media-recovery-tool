# 조사 기록 목록

분석·디버깅 과정의 **시점 기록(랩 노트, 스냅샷)** — 다음 작업이 같은 길을 다시 헤매지 않게 한다.
작성은 [write-investigation](../../.claude/skills/write-investigation/SKILL.md), 문서 경계·갱신 규칙은
[문서 역할 경계](../../.claude/skills/shared/doc-roles.md).

| 날짜 | 제목 | 요약 |
|------|------|------|
| 2026-07-05 | [AVI 손실은 JPEG 과다카빙, 541시간은 오디오 필드+인덱스 부재](2026-07-05-avi-overcarve-and-duration.md) | AVI 42→39 원인은 JPEG 경계(_next_header·SOS upper·_corrupt_boundary)가 AVI(RIFF) 시그니처 미인식 → 44개 중 5개 삼킴(OLD 2→NEW 5, C1이 4 유발·1 해소). `_next_avi` 하드 경계로 AVI 39→44·사용가능 884 회귀 0·JPEG −21(AVI 내부 MJPEG 프레임 정상 재분류, ADR 0008). 541시간=오디오 dwLength(샘플수) 오독+idx1 부재(38/39), carve=OK·경계 직후 제로패딩 → 카빙 무관(C5). 막다른 길: plain 개수 회귀판정·next_sig 일괄(EXIF 썸 절단)·541h를 과다카빙으로 추정 |
| 2026-07-05 | [과다 카빙 수정: 마커·길이 검증으로 경계 축소](2026-07-05-carve-overcarve-fix.md) | `jpeg_end`에 유효마커(mb≥0xC0)·마커별 길이 상한(DHT 1200·DQT 600·SOF 100·DRI 10)·손상 시 경계(SOF후=next_header/SOF전=next_sig) 추가(ADR 0007). 재카빙 822→999, 재복구 사용가능 740→884(+144)·ERROR 0, 진짜 손실 0(썸네일은 부모 HF 보존·다운그레이드는 mis-framed 블록 정정). parse_header 길이 캡 크래시 수정. 막다른 길: SOS-aware(+102<+106)·strict-landing(손실 8)·plain-decode 회귀판정(착시) |
| 2026-07-04 | [과다 카빙+embedded-skip으로 사진 ~38장 손실](2026-07-04-overcarve-buried-images.md) | W3 후속 감사(CLEAN 미개봉·skip 비정상 크기·헤더 미복구). `jpeg_end` 과다 경계+carve embedded-skip(carve.py:44-56)으로 연속 이미지가 한 파일에 포함돼 디코드가능 62장(사진 38·썸 24) 손실(별도 추출 0), 캐리어 3개 HEADER_RECOVERED 오분류. CLEAN 미개봉 6장=관용 디코더 통과(재인코딩만 정규화), SKIP 71=63 복구불가+8 진짜 SOF. 막다른 길: EOI 트림·오염 접합·probe run 최댓값·거대=전부 위양성 |
| 2026-07-04 | [W3: 헤더 복구 pass — 구조 게이트로 진위 판정](2026-07-04-w3-header-recovery.md) | DHT 이식(Annex-K 전역 단일)·DQT 스무딩·SOF/SOS 재구성을 관용 스캔+템플릿으로 후보화, 구조 게이트(probe 바닥·소비율 0.25~1.1·엔진 undec<0.95·own-우월)로 채택(ADR 0006). 전수 SKIP 126→71·FAILED 15→11, 헤더 복구 61건, 유지 파일 회귀 0. 기각: 렌더 통계·썸 픽셀 상관·코퍼스 prior·레이트 게이트 — 전부 밀림·캐스트 하 위양성/위음성. DQT 오진으로 계수 경계 문제 정정 |
| 2026-07-03 | [W2: 수락 임계 잔여 비례화, 물리 채점은 필터 부적합](2026-07-03-w2-scaled-accept-dc-scoring.md) | 임계 잠금을 창 비례 max(30, 0.35W)+데이터-끝 완주 규칙으로 해소(전수 FAILED 66→15·전환 51·파일별 회귀 0, ADR 0005). 계획된 DC 물리 채점은 수락 필터로 기각 — 정당 수락 후보 52건 중 31건이 불변량 위반(리셋 오프셋·잔존 드리프트). 유효 용법은 같은 비트 DC 동률 선택(→W5)·진단. 한계 후보는 직접 렌더로 판정(3건 전부 진짜 콘텐츠) |
| 2026-07-02 | [blocky MCU·밀림·색 캐스트 구조와 경계 재검](2026-07-02-blocky-shift-dc-offset.md) | blocky는 27.9bits/MCU 저엔트로피 구간(clean run 정렬 판별력 0, 비중 0.3~0.5%). 지배 결함은 밀림 밴드+색 캐스트(2029세그/286파일) — 색 캐스트는 디코드 후 세그먼트별 상수 오프셋으로 보정 가능(DC 선형성). 경계 err3은 드리프트 정탐(plain e0 40건 전원 물리 초과 DC — 선행 결론 3 수정), 본질은 연속 손상 간격<450. DC 물리 범위 채점 제안 |
| 2026-07-02 | [잔존 회색·SKIP의 도구 측 원인 3종](2026-07-02-recovery-tool-blindspots.md) | 822건 전수 재질의 → SKIP 122 전부 헤더 손상(DHT 소실 66, 다수파=Annex-K 이식으로 복원 실증), 소형 이미지 수락 임계 잠금 196건(비례 임계로 4/5 복구 실증), 계수 경계 quant 미보정(→ 결론 3은 blocky 조사에서 수정됨). "데이터 한계" 결론의 과대적용 정정. 발견만 기록(코드 변경 없음) |
| 2026-07-01 | [잔존 회색은 carve 정상·데이터 한계](2026-07-01-resync-skew-underconsumption.md) | zero 복구 후 잔존 회색 진단 → carve 정상(재추출+0B)·output 신버전, 데이터 소진+데이터 특성. skew 채점 재동기 실패(모두 악화) — skew는 재동기 기준 못 됨(rate·skew 3번째 확인). 복구 불가 한계 |
| 2026-07-01 | [resync DC=0 리셋으로 hole 잔존 회색 복구](2026-07-01-resync-dc-reset.md) | resync-skip의 DC 캐리(resync.py `_resync_skip`)를 DC 리셋 후보로 확장. 전체 리셋(zero)이 회색 최대 감소(0.571→0.077 등), 무조건 적용의 gray 회귀는 무채색 착시(undecoded로 확인). 복구본 undec 0.100→0.092(회색 잔존 케이스는 대폭). resync-limit 대체 |
| 2026-06-29 | [recover 성능 병목 프로파일링](2026-06-29-recover-perf-profiling.md) | recover 1시간+의 주원인은 _best_edit의 np.insert 전체복사(시간 68%)·CPU 8%↔0%는 대역폭 포화. 무손실 제거+_recv_extend 일괄추출 → 실전 8.2배(80분→9.7분). 막다른 길(njit 융합·90초 budget 비교·GPU·비트버퍼 기각) |
| 2026-06-29 | [DC 리셋과 비트 과소비 한계](2026-06-29-resync-limit.md) | carve 정상 추출본의 절반 회색 원인 → resync의 DC 캐리·비트 과소비 두 한계 확인(레버② 과제). DC=0 리셋 시 gray 0.461→0.274이나 색 캐스트 |
| 2026-06-29 | [회색 주원인은 carve 가짜 EOI](2026-06-29-carve-eoi-discovery.md) | 회색 잔존을 resync로 더 복구하려다 carve 가짜 EOI 조기종료(추출 누락)임을 확인. 막다른 길(깊은탐색·rate강화·통째로추출·임계튜닝 기각) → stuffing 휴리스틱(ADR0002) |
| 2026-06-03 | [JPEG 회색·깨짐의 디싱크 복구](2026-06-03-jpeg-gray-desync-recovery.md) | 회색/깨짐 원인을 바이트 단위로 규명 → bit-level 디코더 + 바이트 오라클/resync 복구에 도달하기까지 |
