---
issue_num: 14
issue_url: https://github.com/kim-taehan/si-code/issues/14
pr_url: https://github.com/kim-taehan/si-code/pull/15
branch: agent/issue-14
title: "JSON 모델 레지스트리 + /model//models 슬래시 명령으로 모델 전환 (Tab 키 처리는 후속 분리)"
status: approved
rounds: 1
date: 2026-05-03
created_at: 2026-05-03T00:00:00+09:00
---

# 실행 기록: JSON 모델 레지스트리 + /model//models 슬래시 명령으로 모델 전환

## TL;DR

`~/.config/sicode/models.json`에 모델 목록을 선언하고 `/model`·`/models` 슬래시 명령으로 런타임 모델 전환을 구현했다. 모델 변경 시 `Conversation` 인스턴스를 재사용해 히스토리를 보존하며, `OllamaMode`가 `client_factory`에 의존(DIP)하는 설계로 REPL 무수정(OCP)을 유지했다. 신규 82개 테스트를 포함한 297개 전체 pass하며 라운드 1에서 즉시 승인·머지되었다.

## 사용자 요청

JSON 파일로 모델 목록을 관리하고 REPL 실행 중 명령어로 모델을 전환할 수 있게 해 달라는 요청. **핵심 의도**: 코드 수정 없이 모델을 선언적으로 등록하고 대화 히스토리를 유지하면서 모델 전환.

## 분석가 결과 요약

JSON 레지스트리 파일 구조, `/model <이름>` 전환 명령, `/models` 목록 조회 명령, 전환 시 히스토리 보존, 기존 단위 테스트 monkeypatch 호환성이 수용기준으로 정의되었다. Tab 키 자동완성은 후속 이슈로 분리되었다.

## 개발자 작업 (라운드별)

### 라운드 1

- 변경된 파일: `~/.config/sicode/models.json` 모델 목록 파일, `/model`·`/models` 슬래시 명령 모듈, `OllamaMode` client_factory 의존성 주입 수정, `_select_mode_with_registry` 분리, `main.py` 진입점 업데이트
- 핵심 로직: 모델 변경 시 `OllamaMode._client`를 새 `OllamaChatClient`로 교체하되 `Conversation` 인스턴스 재사용 → 히스토리 보존; `OllamaMode`가 `client_factory: Callable[[str], OllamaChatClient]`에 의존(DIP); `_select_mode_with_registry` 분리로 기존 단위 테스트 monkeypatch 호환 유지
- 테스트: 신규 82개 테스트, 전체 297 pass(215+82, 회귀 0)

## 리뷰 히스토리

### 라운드 1

- 판정: [APPROVED]
- 핵심 지적: 9개 수용기준 모두 충족, Critical 없음. Suggestion 8건(후속 cleanup 후보):
  1. `_select_mode is _ORIGINAL_SELECT_MODE` 영리한 분기 방식
  2. `models.py`의 `model.py` 비공개 함수 import
  3. 환경변수 빈 문자열 처리 등

## 최종 상태

- 승인: 9개 수용기준 전부 충족, 297개 테스트 pass(회귀 0), 머지 커밋 b471e86

## 후속 조치

- 알려진 한계/가정: Tab 키 자동완성은 이번 범위에서 제외(후속 이슈로 분리 예정)
- 추후 작업: 리뷰어 Suggestion 8건(`_select_mode` 분기 정리, `model.py` 비공개 함수 import 정리, 환경변수 빈 문자열 처리) cleanup
