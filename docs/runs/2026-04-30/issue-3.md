---
issue_num: 3
issue_url: https://github.com/kim-taehan/si-code/issues/3
pr_url: https://github.com/kim-taehan/si-code/pull/4
branch: agent/issue-3
title: "Ollama 로컬 LLM 연결 모드 추가"
status: approved
rounds: 1
date: 2026-04-30
created_at: 2026-04-30T00:00:00+09:00
---

# 실행 기록: Ollama 로컬 LLM 연결 모드 추가

## TL;DR
PR #2에서 확립된 OCP 구조(`BaseMode` + `_select_mode`) 위에 로컬 Ollama LLM과 대화할 수 있는 `OllamaMode`를 새 모드로 추가했다. 외부 API 키 없이 표준 라이브러리(`urllib.request`)만으로 HTTP 통신을 구현했으며, 보안상 호스트 URL은 환경 변수(`SICODE_OLLAMA_HOST`)로만 지정 가능하다. 개발자가 SRP·DIP·OCP 원칙을 준수하여 `OllamaClient`와 `OllamaMode`를 분리 설계하고 신규 테스트 39개(기존 33개 회귀 없음, 총 72개 통과)를 작성했으며, 리뷰어가 1라운드 만에 APPROVED를 판정했다. PR #4는 사용자 머지 대기 중이다.

## 사용자 요청
> 로컬에 있는 ollama 연결해서 답변하는 내용을 추가 해줘. (현재 진행 중인 sicode CLI 심플 모드 구현에 이어, LLM 연결을 준비하기 위한 후속 단계.)

핵심 의도: 기존 REPL 구조를 유지하면서 로컬 Ollama 서버와 통신하는 모드를 추가하여, 외부 API 키 없이 로컬 LLM으로 대화형 응답을 제공한다.

## 분석가 결과 요약
분석가는 PR #2의 `BaseMode` 상속 구조를 재사용하는 방향으로 설계를 정리했다. 핵심 요구사항은 표준 라이브러리 HTTP(`urllib.request`)만 사용, `OllamaClient`를 별도 클래스로 분리(mock 가능 구조), `POST /api/generate` 비스트리밍 호출, CLI `--mode ollama --model <모델명>` 지원, 환경 변수 `SICODE_OLLAMA_HOST`/`SICODE_OLLAMA_MODEL` 지원, CLI `--model` 우선 순위가 환경 변수보다 높음, 호스트 URL은 환경 변수 전용(SSRF 방어를 위해 CLI 미노출), 연결 거부·타임아웃·HTTP 에러 시 메시지 출력 후 REPL 유지로 정리되었다. 수용기준 8개를 명시하고 스트리밍·`/api/chat`·`ollama pull`·CLI 호스트 직접 지정은 범위 외로 제외했다.

## 개발자 작업 (라운드별)

### 라운드 1
- 변경된 파일:
  - 수정: `sicode/main.py` (모드 레지스트리 `MODES` + `argparse` 확장), `sicode/modes/__init__.py`
  - 신규: `sicode/modes/ollama.py` (`OllamaClientProtocol`, `OllamaClient`, `OllamaError`, `OllamaMode`), `tests/modes/__init__.py`, `tests/modes/test_ollama.py` (28 케이스), `tests/test_main_select_mode.py` (11 케이스)
- 핵심 로직: `OllamaClient`가 HTTP 통신과 에러 정규화를 담당하고 `OllamaMode`는 `BaseMode` 어댑터 역할로 사용자 메시지 포맷팅만 수행(SRP·DIP). `main.py`의 `_select_mode`를 `MODES` 딕셔너리 레지스트리 + `argparse`로 교체하여 새 모드 추가 시 코드 수정 불필요(OCP). 모델 우선순위는 CLI `--model` > `SICODE_OLLAMA_MODEL` > `llama3`. 연결 거부·타임아웃·4xx·5xx는 `OllamaError`로 정규화한 뒤 `[ollama] ...` 메시지 출력 후 REPL 유지.
- 테스트 작성: 신규 39개 작성(기존 33개 회귀 없음), 총 72개 전부 통과. 실 Ollama 서버 수동 스모크 테스트에서 모델 미존재 시 에러 메시지 출력 + REPL 유지 라이브 검증 완료.

## 리뷰 히스토리

### 라운드 1
- 판정: [APPROVED]
- 핵심 지적 (Critical): 없음.
- 제안 (Suggestion, 6건):
  1. `urllib.error.HTTPError`/`URLError` except 순서 의도를 한 줄 주석으로 명시 권장.
  2. `host` 정규화 시 스킴 검증(`http`/`https`) 한 줄 추가 권장.
  3. 빈 응답(`{"response":""}`) 시 placeholder(`[ollama] (empty response)`) 출력 검토 — 후속 PR 권장.
  4. HTTP 에러 본문 truncate(첫 256/512바이트) 적용 — 가벼운 방어.
  5. `lambda prompt: builtins.input(prompt)` 스타일 의견 — 동작은 정확.
  6. 누락 케이스(IPv6 host, 3xx redirect, 빈 Content-Length, 매우 긴 응답)는 후속 이슈로 트래킹 권장.
- 칭찬: `MODES` 레지스트리 + 팩토리 패턴(OCP 모범), `OllamaClientProtocol` 최소화(ISP), `URLError.reason` 타입 분기 견고함, `OllamaError`만 잡고 비-`OllamaError`는 전파하는 의도 명시, CLI `--host` 미노출을 회귀 테스트로 잠가둔 보안 의식.

## 최종 상태
- 승인: 수용기준 8개 전부 충족, SOLID 원칙 준수 및 보안(SSRF 방어) 설계 검증됨. 테스트 커버리지(72 tests) 충분. PR #4 APPROVED, 사용자 머지 대기 중.

## 후속 조치
- 알려진 한계/가정: 비스트리밍(`POST /api/generate`)만 지원. 스트리밍·`/api/chat`·`ollama pull` 미구현. CLI 호스트 직접 지정 불가(환경 변수 전용).
- 추후 작업:
  - Suggestion 1·2(except 순서 주석 / 스킴 검증) — 가벼운 cleanup 이슈로 분리.
  - Suggestion 3(빈 응답 placeholder) — 후속 PR로 처리.
  - Suggestion 6(IPv6 host, 3xx redirect, 빈 Content-Length, 매우 긴 응답 케이스) — 별도 후속 이슈 권장.

## 링크
- 이슈: https://github.com/kim-taehan/si-code/issues/3
- PR: https://github.com/kim-taehan/si-code/pull/4
