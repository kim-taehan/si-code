---
issue_num: 17
issue_url: https://github.com/kim-taehan/si-code/issues/17
pr_url: https://github.com/kim-taehan/si-code/pull/19
branch: agent/issue-17
title: "@symbol 자동 확장으로 REPL 입력에 정의 코드 첨부"
status: approved
rounds: 1
date: 2026-05-04
created_at: 2026-05-04T00:00:00+09:00
---

# 실행 기록: @symbol 자동 확장으로 REPL 입력에 정의 코드 첨부

## TL;DR

REPL 입력 시 `@심볼명` 패턴을 인식해 해당 심볼의 정의 코드를 자동으로 입력에 첨부하는 기능을 구현했다. `sicode/symbols/` 패키지(indexer/resolver/expander)를 신설하고 `OllamaMode`에 `input_preprocessor` 옵셔널 주입으로 REPL 본문은 무수정(OCP) 유지했다. `/clear` 명령 시 심볼 캐시가 정확히 무효화되며, 50개 신규 테스트를 포함한 347개 전체 pass로 라운드 1에서 즉시 승인·머지되었다.

## 사용자 요청

REPL에서 `@함수명` 또는 `@클래스명`을 입력하면 해당 심볼의 정의 코드가 자동으로 프롬프트에 첨부되도록 해 달라는 요청. **핵심 의도**: 별도 복사·붙여넣기 없이 대화 중 심볼을 참조해 LLM이 실제 구현 코드를 컨텍스트로 받을 수 있게 함.

## 분석가 결과 요약

`@token` 패턴 인식·인덱싱·확장 로직의 SRP 분리, `OllamaMode`와 REPL 본문 무수정 조건(OCP), `/clear` 호출 시 심볼 캐시 무효화, 시크릿 파일(`.env`, `credentials`) 확장 차단이 수용기준으로 정의되었다.

## 개발자 작업 (라운드별)

### 라운드 1

- 변경된 파일: `sicode/symbols/__init__.py`, `sicode/symbols/indexer.py`, `sicode/symbols/resolver.py`, `sicode/symbols/expand.py`, `OllamaMode` 수정(`input_preprocessor` 옵셔널 주입), `_handle_chat`/`_handle_legacy` 1라인 통합, `ClearCommand` duck-typing 추가, `main.py` 진입점 업데이트
- 핵심 로직: `main.py`가 `SymbolResolver`/`SymbolExpander`를 한 번 생성해 모드와 expander에 공유; `ClearCommand`가 `mode.symbol_resolver.invalidate()` duck-typing 호출로 `/clear` 무효화가 다음 `@token` 입력의 lazy 인덱싱에 정확 반영; 시크릿 파일 확장 차단 내장
- 테스트: 신규 50개 테스트, 전체 347 pass(297+50, 회귀 0)

## 리뷰 히스토리

### 라운드 1

- 판정: [APPROVED]
- 핵심 지적: 9개 수용기준 모두 충족, SOLID 분리(SRP/OCP/DIP) 및 시크릿 차단 모두 만족, Critical 없음

## 최종 상태

- 승인: 9개 수용기준 전부 충족, 347개 테스트 pass(회귀 0), 머지 커밋 e1db7fc (UTC 2026-05-03 23:53)

## 후속 조치

- 알려진 한계/가정: 심볼 인덱싱은 lazy 방식으로 첫 `@token` 입력 시 수행; 대규모 코드베이스에서 초기 인덱싱 지연 가능성 있음
- 추후 작업: PR #22(`@` 자동완성) 머지 후 Tab 키 자동완성과의 통합 검토
