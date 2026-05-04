"""``!cmd`` prefix 셸 실행기.

REPL 의 ``!`` 분기에서 호출되는 작은 함수들의 모음. 책임 경계:

- :func:`is_bang_input`: 입력이 bang 분기 대상인지 단순 판정 (왼쪽 공백 trim 후
  ``!`` 시작 여부). REPL 에서 슬래시 명령 분기와 동등한 위치에 배치한다.
- :func:`resolve_timeout_seconds`: 환경 변수 ``SICODE_BANG_TIMEOUT`` 을 정수로
  해석하고, 잘못된 값/0 이하 값은 기본값 60 으로 떨어진다.
- :func:`run_shell`: ``subprocess.run`` 래퍼. ``shell=True`` / ``stdin=DEVNULL``
  / ``capture_output`` / ``text=True`` / ``cwd=Path.cwd()`` 를 강제한다.
- :func:`format_bang_output`: 결과 객체를 REPL stdout/stderr 표시 규약에 따라
  단일 문자열(빈 줄 포함 가능) 로 변환한다.
- :func:`handle_bang_input`: 위 함수들을 묶는 진입점. ``runner`` 를 주입할 수
  있어 테스트가 :mod:`subprocess` mock 없이도 가능하다 (DIP).

설계 메모:
    - SRP: 한 함수 = 한 책임. ``run_shell`` 은 IO 만, ``format_bang_output`` 은
      문자열 가공만 담당한다.
    - DIP: 테스트가 ``runner`` 를 주입해 서브프로세스 실제 실행을 우회한다.
    - 보안: 본 모듈은 명령 필터링/허용 목록을 도입하지 않는다 — 사용자는
      자신의 셸과 동일한 권한으로 명령을 실행한다는 것을 환영 메시지/``/help``
      가 사전 안내한다.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


#: REPL 입력의 bang 분기 prefix.
BANG_PREFIX: str = "!"

#: ``SICODE_BANG_TIMEOUT`` 미설정/오류 시 적용되는 기본 타임아웃(초).
DEFAULT_TIMEOUT_SECONDS: int = 60

#: 사용자 재정의용 환경 변수 이름.
TIMEOUT_ENV_VAR: str = "SICODE_BANG_TIMEOUT"


@dataclass(frozen=True)
class BangResult:
    """:func:`run_shell` 반환 객체.

    Attributes:
        stdout: 명령의 표준 출력(개행 포함될 수 있음).
        stderr: 명령의 표준 에러.
        returncode: 종료 코드.
        timed_out: ``False`` 고정. 타임아웃은 :class:`BangTimeout` 으로 분리한다.
    """

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False


@dataclass(frozen=True)
class BangTimeout:
    """타임아웃이 발생했을 때 :func:`run_shell` 이 반환하는 표시용 객체.

    REPL 표시 단계에서 :class:`BangResult` 와 분기 처리하기 위해 별도 타입으로
    분리했다(LSP 측면에서 같은 인터페이스를 강요하지 않기 위함).
    """

    timeout: int


#: ``run_shell`` 시그니처와 호환되는 콜러블 (테스트 주입용).
ShellRunner = Callable[..., "BangResult | BangTimeout"]


# ---------------------------------------------------------------------------- helpers


def is_bang_input(user_input: str) -> bool:
    """입력이 bang 분기 대상인지 판정한다.

    슬래시 분기와 동일하게 왼쪽 공백을 무시한다. 본 함수는 입력 본문에 어떤
    유효성 검사도 하지 않는다 — 비어 있는 ``!`` 도 ``True`` 를 반환하고,
    호출자(:func:`handle_bang_input`) 가 안내 문구를 출력한다.
    """
    return user_input.lstrip().startswith(BANG_PREFIX)


def resolve_timeout_seconds(
    env: Optional["os._Environ[str]"] = None,
    *,
    default: int = DEFAULT_TIMEOUT_SECONDS,
) -> int:
    """``SICODE_BANG_TIMEOUT`` 을 정수로 해석한다.

    Args:
        env: 환경 변수 매핑. ``None`` 이면 :data:`os.environ` 사용.
        default: 미설정/파싱 실패/비양수 시 적용할 기본값.

    Returns:
        양의 정수. 잘못된 값은 ``default`` 로 떨어진다.
    """
    source = os.environ if env is None else env
    raw = source.get(TIMEOUT_ENV_VAR)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def _strip_bang_prefix(user_input: str) -> str:
    """입력에서 ``!`` 와 그 앞의 공백을 제거한 명령 본문을 돌려준다.

    호출자는 이 결과를 ``str.strip()`` 으로 후처리해 빈 명령 여부를 판정한다.
    """
    after_lstrip = user_input.lstrip()
    if not after_lstrip.startswith(BANG_PREFIX):
        return after_lstrip
    return after_lstrip[len(BANG_PREFIX):]


# ---------------------------------------------------------------------------- runner


def run_shell(
    cmd: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> "BangResult | BangTimeout":
    """주어진 셸 명령을 실행하고 결과를 돌려준다.

    ``shell=True`` 로 파이프/리다이렉션/glob 을 그대로 지원하며, ``stdin`` 은
    :data:`subprocess.DEVNULL` 로 봉쇄해 인터랙티브 입력을 막는다.

    Args:
        cmd: ``!`` 를 제거한 명령 문자열. 빈 문자열은 호출자가 사전 차단한다.
        timeout: 타임아웃 초. ``subprocess.TimeoutExpired`` 발생 시
            :class:`BangTimeout` 객체를 돌려준다.

    Returns:
        성공/실패와 관계 없이 정상 완료시 :class:`BangResult`,
        타임아웃 시 :class:`BangTimeout`.
    """
    try:
        completed = subprocess.run(  # noqa: S602 - 의도적 shell=True (이슈 #18 합의)
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            cwd=str(Path.cwd()),
        )
    except subprocess.TimeoutExpired:
        return BangTimeout(timeout=timeout)
    return BangResult(
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        returncode=int(completed.returncode),
    )


# ---------------------------------------------------------------------------- formatter


def _prefix_lines(text: str, prefix: str) -> str:
    """``text`` 의 각 라인에 ``prefix`` 를 붙인다.

    빈 문자열은 빈 문자열 그대로 돌려준다. 마지막 개행은 보존하지 않는다 — 호출
    측에서 join 시 제어한다.
    """
    if text == "":
        return ""
    # 마지막 개행이 있으면 splitlines 가 무시하므로 별도로 보존
    trailing_nl = text.endswith("\n")
    body = "\n".join(prefix + line for line in text.splitlines())
    return body + ("\n" if trailing_nl else "")


def format_bang_output(result: "BangResult | BangTimeout") -> str:
    """실행 결과를 REPL output_fn 한 번 호출 분량의 문자열로 변환한다.

    표시 규약 (이슈 #18):
        - stdout 은 그대로 출력한다.
        - stderr 는 각 줄 앞에 ``[stderr] `` 를 붙인다.
        - returncode 가 0 이 아니면 ``[exit code: N]`` 한 줄을 마지막에 추가한다.
        - 모두 비어 있으면 빈 문자열을 돌려준다 (REPL 측에서 출력 생략).
        - 타임아웃은 ``[bang] 명령이 타임아웃되었습니다 (Ns). REPL을 계속합니다.``
          한 줄로 변환된다.

    Args:
        result: :func:`run_shell` 반환 객체.

    Returns:
        합성된 출력 문자열. 줄 사이 구분은 ``\\n`` 으로 합친다.
    """
    if isinstance(result, BangTimeout):
        return (
            f"[bang] 명령이 타임아웃되었습니다 ({result.timeout}s). "
            "REPL을 계속합니다."
        )

    parts = []
    stdout = result.stdout.rstrip("\n")
    if stdout != "":
        parts.append(stdout)
    stderr_formatted = _prefix_lines(result.stderr.rstrip("\n"), "[stderr] ")
    if stderr_formatted != "":
        parts.append(stderr_formatted)
    if result.returncode != 0:
        parts.append(f"[exit code: {result.returncode}]")
    return "\n".join(parts)


# ---------------------------------------------------------------------------- entrypoint


def handle_bang_input(
    user_input: str,
    *,
    runner: Optional[ShellRunner] = None,
    timeout: Optional[int] = None,
) -> str:
    """REPL 의 ``!`` 분기 진입점.

    Args:
        user_input: 사용자가 입력한 한 줄 (앞뒤 공백, ``!`` 포함).
        runner: ``run_shell`` 호환 콜러블. ``None`` 이면 :func:`run_shell` 사용.
            테스트에서 서브프로세스 실제 실행을 우회하기 위해 주입한다 (DIP).
        timeout: 사용할 타임아웃(초). ``None`` 이면 :func:`resolve_timeout_seconds`
            로 환경 변수에서 해석한다. 0 이하/None-경계 동작은 그 함수가 흡수한다.

    Returns:
        REPL ``output_fn`` 한 번 호출에 적합한 문자열.
        ``!`` 뒤가 비어 있으면 사용 안내 문자열을 돌려준다.
    """
    body = _strip_bang_prefix(user_input).strip()
    if body == "":
        return "[bang] 실행할 명령을 입력하세요. 예시: !ls"

    actual_runner = runner or run_shell
    actual_timeout = (
        timeout if timeout is not None and timeout > 0 else resolve_timeout_seconds()
    )
    result = actual_runner(body, actual_timeout)
    return format_bang_output(result)


__all__ = [
    "BANG_PREFIX",
    "DEFAULT_TIMEOUT_SECONDS",
    "TIMEOUT_ENV_VAR",
    "BangResult",
    "BangTimeout",
    "ShellRunner",
    "format_bang_output",
    "handle_bang_input",
    "is_bang_input",
    "resolve_timeout_seconds",
    "run_shell",
]
