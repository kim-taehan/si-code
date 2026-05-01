---
issue_num: 1
issue_url: https://github.com/kim-taehan/si-code/issues/1
pr_url: https://github.com/kim-taehan/si-code/pull/2
branch: agent/issue-1
title: "sicode CLI 심플 모드 구현 (사용자 입력 에코 REPL)"
status: approved
rounds: 1
date: 2026-04-30
created_at: 2026-04-30T00:00:00+09:00
---

# 실행 기록: sicode CLI 심플 모드 구현 (사용자 입력 에코 REPL)

## TL;DR
사용자가 터미널에서 `sicode`를 입력하면 대화형 REPL이 실행되고, 심플 모드에서는 LLM 호출 없이 사용자 입력을 그대로 에코 출력하는 기능을 구현했다. 분석가가 요구사항 8개와 기술 메모를 정리한 이슈 #1을 바탕으로 개발자가 패키지 구조, 추상 인터페이스, 테스트 33개를 포함한 전체 구현을 완료했다. 리뷰어가 1라운드 만에 APPROVED를 판정했으며, PR #2가 머지되어 mergeCommit f11f76e로 반영되었다. 제안된 개선사항 6건은 모두 critical이 아니라 후속 이슈로 분리 예정이다.

## 사용자 요청
> sicode 입력 시 Claude Code처럼 동작하는 시스템 구축. 사용자가 입력한 데이터를 그대로 출력해주는 심플 모드 작성 (파이썬).

핵심 의도: 향후 LLM 연동으로 확장할 수 있는 구조를 갖추되, 첫 마일스톤으로는 입력을 에코 출력하는 대화형 REPL을 Python으로 구현한다.

## 분석가 결과 요약
분석가는 저장소에 실행 가능한 CLI 엔트리포인트가 없다는 현황을 파악하고, `sicode` 명령어가 셸에서 동작하기 위한 8개 수용기준을 도출했다. 주요 항목은 REPL 루프 시작, 환영 메시지 출력, 프롬프트 표시, 에코 출력, `exit`/`quit`/`Ctrl+C`/`Ctrl+D` 처리, 모드 분리 구조, 표준 라이브러리 전용, `pip install -e .`으로 `sicode` PATH 등록이다. 범위 외(LLM 연동, 설정 파일 등)를 명시하여 1차 구현 범위를 명확히 한정했다.

## 개발자 작업 (라운드별)

### 라운드 1
- 변경된 파일: `pyproject.toml`, `.gitignore`, `sicode/__init__.py`, `sicode/main.py`, `sicode/repl.py`, `sicode/modes/__init__.py`, `sicode/modes/base.py`, `sicode/modes/simple.py`, `tests/__init__.py`, `tests/test_simple_mode.py`, `tests/test_repl.py`, `tests/test_main.py`
- 핵심 로직: `BaseMode` 추상 인터페이스를 정의하고 `SimpleMode`가 이를 구현; `Repl` 클래스는 입력/출력 함수를 의존성 주입으로 받아 OCP·DIP 원칙을 준수하며 모드 교체 시 REPL 코드 변경 불필요. `pyproject.toml [project.scripts]`에 `sicode = "sicode.main:main"` 엔트리포인트 등록.
- 테스트 작성: 단위·통합 테스트 33개 작성 및 전부 통과.

## 리뷰 히스토리

### 라운드 1
- 판정: [APPROVED]
- 핵심 지적 (Critical): 없음.
- 제안 (Suggestion, 6건):
  1. `_select_mode`를 모드 레지스트리(`MODES: dict[str, type[BaseMode]]`)로 진화 가능 — 차기 이슈 검토.
  2. `build_welcome_message` 끝의 `\n`이 print 줄바꿈과 겹쳐 빈 줄 발생 — 의도 여부 명시 또는 제거 권장.
  3. `lambda prompt: builtins.input(prompt)` 대신 `builtins.input` 직접 참조로 단순화 가능.
  4. `tests/test_main.py`의 `builtins.print` 패치를 `output_fn` 주입으로 회피 가능.
  5. README에 `pip install -e .` 및 `sicode` 사용법 추가 권장.
  6. `EXIT_COMMANDS`를 외부 노출 의도가 없다면 `_EXIT_COMMANDS`로 비공개화 권장.

## 최종 상태
- 승인: 8개 수용기준 전부 충족, 모드 분리 구조와 DI 설계가 견고하며 테스트 커버리지(33 tests) 충분함이 검증됨. PR #2가 mergeCommit f11f76e31a3424cdd074d2c1a2938ee373a96848 으로 main에 머지 완료.

## 후속 조치
- 알려진 한계/가정: 현재 심플 모드만 존재하며 LLM 연동 없음. `_select_mode` 로직이 하드코딩되어 있어 모드가 늘어날수록 변경 필요.
- 추후 작업:
  - Suggestion 1번(모드 레지스트리) — 후속 이슈 #3(Ollama 모드) 구현 시 자연스럽게 도입 예정.
  - Suggestion 5번(README 갱신) — cleanup 이슈로 분리 가능.
  - Suggestion 2·3·4·6번 — 소규모 cleanup 이슈로 분리 가능.

## 링크
- 이슈: https://github.com/kim-taehan/si-code/issues/1
- PR: https://github.com/kim-taehan/si-code/pull/2
- 머지 커밋: f11f76e31a3424cdd074d2c1a2938ee373a96848
