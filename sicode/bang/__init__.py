"""``!cmd`` prefix 셸 실행 패키지.

REPL 입력이 ``!`` 로 시작하면 슬래시 명령/모드 전달과 무관하게 본 모듈의
:func:`handle_bang_input` 으로 분기된다. 본 패키지는 다음 책임만 갖는다 (SRP):

- ``!`` 뒤 문자열을 추출하고, 빈 입력을 안내 문구로 막는다.
- :func:`run_shell` 로 서브프로세스를 동기 실행하고 stdout/stderr/exit code 를
  REPL 출력 형식에 맞춰 한 줄씩 모아 돌려준다.
- 멀티턴 히스토리 (``mode.handle``) 와 완전히 격리한다.

설계 메모:
    - SRP: 셸 실행 로직(:mod:`sicode.bang.executor`) 과 REPL 표시 로직
      (:func:`format_bang_output` / :func:`handle_bang_input`) 을 분리한다.
    - DIP: 테스트는 ``runner`` 콜러블을 :func:`handle_bang_input` 에 주입해
      서브프로세스 호출 없이 결과 객체를 합성한다.
    - OCP: ``!`` 외 prefix 를 추가할 가능성은 본 이슈 범위 외 — 인터페이스
      추상화 (``PrefixHandler`` 등) 는 하지 않고 단일 진입점만 제공한다.
"""

from __future__ import annotations

from sicode.bang.executor import (
    BANG_PREFIX,
    DEFAULT_TIMEOUT_SECONDS,
    TIMEOUT_ENV_VAR,
    BangResult,
    BangTimeout,
    format_bang_output,
    handle_bang_input,
    is_bang_input,
    resolve_timeout_seconds,
    run_shell,
)


__all__ = [
    "BANG_PREFIX",
    "DEFAULT_TIMEOUT_SECONDS",
    "TIMEOUT_ENV_VAR",
    "BangResult",
    "BangTimeout",
    "format_bang_output",
    "handle_bang_input",
    "is_bang_input",
    "resolve_timeout_seconds",
    "run_shell",
]
