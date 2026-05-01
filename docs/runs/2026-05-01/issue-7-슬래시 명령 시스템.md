---
issue_num: 7
issue_url: https://github.com/kim-taehan/si-code/issues/7
pr_url: https://github.com/kim-taehan/si-code/pull/8
branch: agent/issue-7
title: "슬래시 명령 시스템 도입 (/exit, /quit, /help) 및 확장 가능한 명령 레지스트리 구현"
status: approved
rounds: 1
date: 2026-05-01
created_at: 2026-05-01T00:00:00+09:00
---

# 실행 기록: 슬래시 명령 시스템 도입 (/exit, /quit, /help) 및 확장 가능한 명령 레지스트리 구현

## TL;DR

REPL에 `/exit`, `/quit`, `/help` 슬래시 명령과 이를 뒷받침하는 확장 가능한 명령 레지스트리를 도입했다. `sicode/commands/` 패키지에 `SlashCommand` ABC, `CommandResult`, `SlashCommandRegistry`, `dispatch_command`를 구현해 새 명령을 `register()` 한 줄로 추가할 수 있는 OCP 구조를 달성했다. 기존 평문 `exit`/`quit` 동작에 회귀 없이 107 tests pass. 1라운드 리뷰에서 Critical 없이 APPROVED, PR #8로 머지됐다.

## 사용자 요청

> '/exit' 종료 명령어는 이런 형태도 지정하고 싶어 /help 로 설명해주는 기능도 추가해보자 이런 기능은 계속 추가될꺼라 구조를 잘 잡아줘.

핵심 의도: `/exit` 같은 슬래시 명령과 `/help` 안내 기능을 추가하되, 앞으로 명령이 계속 늘어날 것을 고려해 루프 수정 없이 명령을 등록·실행할 수 있는 확장 가능한 구조를 설계한다.

## 분석가 결과 요약

기존 REPL은 종료를 평문 `exit`/`quit`만 처리했고, 명령 추가 시마다 `run_repl` 직접 수정이 필요한 구조였다. 분석가는 `sicode/commands/` 패키지(base/registry/exit/help) 신설, `SlashCommand` ABC, `CommandResult`(CONTINUE/EXIT) 도입, 명시적 `register()`만 허용, REPL 루프의 `/` 시작 입력은 `dispatch_command`에 위임, `/help` 알파벳 정렬, 미등록 명령 친화적 안내, 평문 `exit`/`quit` 회귀 없음, 환영 메시지 `/help` 안내, 표준 라이브러리만 사용 등 수용 기준 8개를 도출했다.

## 개발자 작업 (라운드별)

### 라운드 1

변경된 파일:
- 신규: `sicode/commands/__init__.py`, `sicode/commands/base.py` (`SlashCommand` ABC, `CommandResult`, `CommandAction`), `sicode/commands/registry.py` (`SlashCommandRegistry`, `dispatch_command`, `temporary_registry()`, `reset()`), `sicode/commands/exit.py` (`/exit`, `/quit` 별칭), `sicode/commands/help.py` (`/help`, 알파벳 정렬), `tests/test_commands.py`
- 수정: `sicode/repl.py` (`/` 분기 추가, 환영 메시지에 `/help` 안내, `registry` 주입), `sicode/main.py` (`register_default_commands()` 호출), `tests/conftest.py` (레지스트리 격리 autouse 픽스처 추가)

핵심 로직:
- REPL 루프에서 입력이 `/`로 시작하면 `dispatch_command(token, context)` 위임 → `CommandResult`가 CONTINUE면 계속, EXIT이면 종료
- `SlashCommandRegistry`는 중복 등록 방지, 알파벳 정렬, `reset()`/`temporary_registry()` 제공 → 테스트 격리 지원
- `main.py`에서 `register_default_commands()` 명시적 호출(임포트 부수효과 자동 등록 의식적 거부)

테스트: 107 tests pass (기존 71 회귀 없음 + 신규 36). `conftest.py` autouse 픽스처로 전역 레지스트리 매 테스트 격리.

## 리뷰 히스토리

### 라운드 1

판정: [APPROVED]

핵심 지적(Suggestion, 필수 아님):
1. `temporary_registry()`가 `default_registry._by_token`/`_primary_names` private 속성 직접 접근 → `snapshot()`/`restore()` 또는 `clone()` 공개 API 도입 권장.
2. `parse_slash_input`이 첫 토큰 추출 후 `lower()` 적용 — 향후 인자 파싱 호환을 위해 `(token, args)` 튜플 진화 경로를 docstring에 명시 권장.
3. `HelpCommand`가 생성자 주입과 `context.registry` 이중 경로로 동작 — DIP상 단일 경로(`ReplContext`)로 통일이 더 명확.

보안 메모: `dispatch_command`가 미등록 토큰을 그대로 출력 메시지에 echo (`Unknown command: /{token}`) — ANSI escape 등 제어문자 입력 시 터미널 흐름 가능. 추후 `repr()`/화이트리스트 강화 권장(낮은 우선순위).

칭찬 사항: `dispatch_command`의 엣지케이스(`/` 단독, 미등록, 빈 문자열) 결정론적 처리. `CommandResult.cont`/`exit_` 헬퍼 + frozen dataclass 가독성. `register_default_commands(registry=None)` 시그니처로 테스트 유연성 확보. 임포트 부수효과 자동 등록 의식적 거부.

## 최종 상태

승인 완료. PR #8 머지 (머지 커밋: `063ffc7`). `sicode/commands/` 패키지가 안정적으로 동작하며, 새 슬래시 명령은 `register()` 한 줄로 추가 가능한 구조가 검증됐다. 기존 평문 `exit`/`quit` 동작 회귀 없음.

## 후속 조치

알려진 한계/가정:
- `temporary_registry()`가 내부 private 속성에 의존해 레지스트리 구현 변경 시 깨질 수 있다.
- 미등록 슬래시 명령 입력 시 토큰이 그대로 출력되어 제어문자 인젝션 위험이 낮은 우선순위로 존재한다.
- 평문 `exit`/`quit`과 슬래시 시스템이 별도 분기로 남아 있다.

추후 작업:
- Suggestion 1·2·5: 레지스트리 캡슐화(`snapshot()`/`restore()`) + `parse_slash_input` 진화 경로 docstring + `unregister` 별칭 정책 명시 → cleanup 이슈로 묶기 가능
- 평문 `exit`/`quit`을 슬래시 디스패처로 통합하는 리팩터링 → 별도 이슈로 분리 권장
- 보안: 미등록 토큰 출력 시 `repr()` 적용 → 낮은 우선순위 후속 작업

## 링크

- 이슈: https://github.com/kim-taehan/si-code/issues/7
- PR: https://github.com/kim-taehan/si-code/pull/8
- 머지 커밋: `063ffc7`
