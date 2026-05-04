---
issue_num: 18
issue_url: https://github.com/kim-taehan/si-code/issues/18
pr_url: https://github.com/kim-taehan/si-code/pull/21
branch: agent/issue-18
title: "! 셸 prefix로 REPL에서 직접 셸 명령 실행"
status: approved
rounds: 1
date: 2026-05-04
created_at: 2026-05-04T00:00:00+09:00
---

# 실행 기록: ! 셸 prefix로 REPL에서 직접 셸 명령 실행

## TL;DR

REPL 입력 앞에 `!`를 붙이면 해당 입력을 셸 명령으로 직접 실행하는 기능을 구현했다. `sicode/bang/` 패키지를 신설하고 `repl.py`에 2줄 분기만 추가(REPL 무수정에 가까운 OCP)하며, `Conversation` 히스토리를 오염시키지 않도록 통합 검증했다. 환경변수 `SICODE_BANG_TIMEOUT`으로 타임아웃 조정 가능하며, 보안 안내 문구를 환영 메시지·`/help` 출력에 포함했다. 42개 신규 테스트를 포함한 389개 전체 pass로 라운드 1에서 즉시 승인·머지되었다.

## 사용자 요청

REPL 내에서 `!ls`, `!git status` 등 셸 명령을 직접 입력해 실행 결과를 확인할 수 있도록 해 달라는 요청. **핵심 의도**: 별도 터미널 전환 없이 REPL 세션 안에서 파일 시스템 탐색·빌드 명령 등을 즉시 실행.

## 분석가 결과 요약

`!` prefix 인식, `subprocess` 기반 실행 래퍼, 타임아웃 처리, stderr 구분 출력, `Conversation` 히스토리 미오염, 보안 경고 문구 포함, `SICODE_BANG_TIMEOUT` 환경변수 지원이 수용기준으로 정의되었다.

## 개발자 작업 (라운드별)

### 라운드 1

- 변경된 파일: `sicode/bang/__init__.py`, `sicode/bang/executor.py`(BangResult/BangTimeout dataclass + 실행 래퍼 + 출력 포맷터 + runner 주입 진입점), `sicode/repl.py`(is_slash_command 직전 `!` 분기 2줄), 환영 메시지 및 `/help` 출력 업데이트, `main.py` 진입점 업데이트
- 핵심 로직: `subprocess.run(shell=True, capture_output=True, text=True, stdin=DEVNULL, timeout=N, cwd=Path.cwd())`로 현재 작업 디렉토리 기준 실행; `[stderr]` 접두사·`[exit code: N]`·타임아웃 메시지로 출력 포맷 구분; `BangResult`/`BangTimeout` dataclass 분리로 LSP 안전; `Conversation` history 미오염 통합 검증
- 테스트: 신규 42개 테스트, 전체 389 pass(347+42, 회귀 0)

## 리뷰 히스토리

### 라운드 1

- 판정: [APPROVED]
- 핵심 지적: 9개 수용기준 모두 충족, SRP/DIP 명확, 보안 안내 완비, Critical 없음. Suggestion 2건(후속 cleanup 후보):
  1. 타입 힌트 범위 확대 검토
  2. conftest 이동 검토

## 최종 상태

- 승인: 9개 수용기준 전부 충족, 389개 테스트 pass(회귀 0), 머지 커밋 (2026-05-04 머지)

## 후속 조치

- 알려진 한계/가정: `shell=True` 사용으로 셸 인젝션 위험이 있어 사용자에게 보안 안내 문구 제공; 신뢰할 수 없는 입력 자동 실행 시 주의 필요
- 추후 작업: 리뷰어 Suggestion 2건(타입 힌트 폭, conftest 이동) cleanup
