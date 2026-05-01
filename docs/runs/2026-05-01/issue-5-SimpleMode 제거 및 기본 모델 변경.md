---
issue_num: 5
issue_url: https://github.com/kim-taehan/si-code/issues/5
pr_url: https://github.com/kim-taehan/si-code/pull/6
branch: agent/issue-5
title: "SimpleMode 제거 및 기본 모델을 llama3.1:8b로 변경"
status: approved
rounds: 1
date: 2026-05-01
created_at: 2026-05-01T00:00:00+09:00
---

# 실행 기록: SimpleMode 제거 및 기본 모델을 llama3.1:8b로 변경

## TL;DR

테스트용 에코 응답만 반환하던 `SimpleMode`를 코드베이스에서 완전 제거하고, 기본 모드를 ollama, 기본 모델을 `llama3.1:8b`로 전환했다. 환영 메시지에 Ollama 서버 실행 안내 문구를 추가하고, 기존 `SimpleMode` 의존 테스트는 `conftest.py`의 `EchoMode` 픽스처로 교체해 REPL 핵심 시나리오를 지속 검증했다. `python -m sicode` 진입점을 위해 `sicode/__main__.py`도 추가됐다. 1라운드 리뷰에서 Critical 없이 바로 APPROVED, PR #6으로 머지됐다.

## 사용자 요청

> simple 모드는 없애주고 지금 ollama list 확인해서 그버전으로 변경해줘.

핵심 의도: 개발용 더미 모드를 제거하고, 사용자 로컬 환경에 실재하는 Ollama 모델(`llama3.1:8b`)을 기본값으로 설정해 첫 실행 즉시 정상 동작하도록 한다.

## 분석가 결과 요약

`SimpleMode`는 에코 응답만 반환하는 테스트용 모드로, 사용자 환경의 실제 Ollama 모델(`llama3.1:8b`)로 교체가 필요했다. 기존 `llama3` 기본값은 사용자 환경에 존재하지 않아 첫 실행 오류 원인이기도 했다. 분석가는 총 8개 수용 기준을 도출했다: SimpleMode 완전 제거, MODES 레지스트리에서 simple 항목 삭제, `--mode` 기본값 ollama, `--model` 기본값 `llama3.1:8b`, 환영 메시지에 Ollama 서버 안내 추가, 기존 SimpleMode 의존 테스트의 EchoMode(BaseMode 인라인 픽스처) 교체, pyproject.toml description 갱신.

## 개발자 작업 (라운드별)

### 라운드 1

변경된 파일:
- 삭제: `sicode/modes/simple.py`, `tests/test_simple_mode.py`
- 신규: `sicode/__main__.py` (`python -m sicode` 진입점), `tests/conftest.py` (`EchoMode` 픽스처 정의)
- 수정: `sicode/__init__.py`, `sicode/main.py` (`--mode` 기본값 ollama, `--model` 기본값 `llama3.1:8b`), `sicode/repl.py` (환영 메시지 Ollama 안내 추가), `sicode/modes/__init__.py` (MODES 레지스트리에서 simple 제거), `sicode/modes/ollama.py`, `pyproject.toml` (description 갱신), `tests/test_main.py`, `tests/test_main_select_mode.py`, `tests/test_repl.py`, `tests/modes/test_ollama.py`

핵심 로직:
- MODES 레지스트리에서 `"simple"` 키 삭제 → `--mode simple` 입력 시 argparse `"invalid choice"` 오류 반환
- `EchoMode`(BaseMode 구현체)를 `conftest.py`에 정의해 REPL 핵심 시나리오(에코, exit, EOF, KeyboardInterrupt, 빈 입력) 지속 검증

테스트: 71 tests pass. 수용 기준 라이브 검증 완료(`--help`, `--mode simple` 오류, `SimpleMode` ImportError, 환영 메시지 출력 확인).

## 리뷰 히스토리

### 라운드 1

판정: [APPROVED]

핵심 지적(Suggestion, 필수 아님):
1. 환영 메시지의 `http://localhost:11434` 하드코딩 — `SICODE_OLLAMA_HOST` 환경 변수를 덮어쓴 경우 안내 문구가 부정확해질 수 있음.
2. `build_welcome_message`가 Ollama 안내를 직접 포함해 SRP 누수 경향 — 향후 `BaseMode`에 `welcome_notice()` 훅 도입 검토 권장.
3. `tests/test_main_select_mode.py`에서 `mode._client` 비공개 속성을 직접 단언 — `client` 프로퍼티 도입으로 화이트박스 결합 완화 권장.

칭찬 사항: `TestSimpleModeFullyRemoved`(패키지 export + 모듈 양쪽 회귀 테스트), argparse stderr 직접 캡처, `DEFAULT_MODEL` 단언 이중화, `sicode/__main__.py` 표준 형태, `_select_mode` MODES 디스패치(OCP 준수).

## 최종 상태

승인 완료. PR #6 머지 (머지 커밋: `f538ec8`). SimpleMode가 코드베이스에서 완전 제거됐고, 기본 모드 ollama·기본 모델 `llama3.1:8b` 동작이 테스트로 검증됐다.

## 후속 조치

알려진 한계/가정:
- 환영 메시지의 Ollama 호스트 주소가 `http://localhost:11434`로 하드코딩되어 있어, 커스텀 호스트 환경에서 안내 문구가 부정확할 수 있다.
- `mode._client` 비공개 속성 직접 참조 테스트는 내부 구현 변경 시 취약해질 수 있다.

추후 작업:
- Suggestion 1·2: 환영 메시지 모드별 안내 개선 → 별도 cleanup 이슈 후보
- Suggestion 3: `client` 프로퍼티 도입 → 소규모 후속 이슈로 분리 가능

## 링크

- 이슈: https://github.com/kim-taehan/si-code/issues/5
- PR: https://github.com/kim-taehan/si-code/pull/6
- 머지 커밋: `f538ec8`
