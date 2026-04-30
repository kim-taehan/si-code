"""대화형 REPL 루프.

REPL은 :class:`sicode.modes.base.BaseMode` 추상에만 의존하며, I/O 함수(``input_fn``,
``output_fn``)를 주입받을 수 있어 테스트가 용이하다 (DIP, 의존성 주입).
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from sicode import __version__
from sicode.modes.base import BaseMode


#: REPL 종료 명령어. 대소문자 무시 비교는 호출부에서 처리한다.
EXIT_COMMANDS: frozenset[str] = frozenset({"exit", "quit"})

#: 사용자에게 표시되는 프롬프트 문자열.
DEFAULT_PROMPT: str = "sicode> "


def build_welcome_message(mode: BaseMode, version: str = __version__) -> str:
    """환영 메시지 문자열을 생성한다.

    Args:
        mode: 현재 활성화된 모드.
        version: 표시할 sicode 버전.

    Returns:
        여러 줄로 된 환영 메시지 문자열.
    """
    return (
        f"sicode v{version} ({mode.name} mode)\n"
        "Type 'exit' or 'quit' to leave. Press Ctrl+C / Ctrl+D to abort.\n"
    )


def is_exit_command(user_input: str) -> bool:
    """입력이 종료 명령어인지 판정한다 (앞뒤 공백 무시, 대소문자 무시)."""
    return user_input.strip().lower() in EXIT_COMMANDS


def run_repl(
    mode: BaseMode,
    *,
    prompt: str = DEFAULT_PROMPT,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    """REPL 루프를 실행한다.

    설계 메모:
        - 모드는 :class:`BaseMode` 추상으로 받아 구체 구현을 모른다 (DIP).
        - ``input_fn`` / ``output_fn`` 을 주입 가능하게 해서 단위 테스트에서
          실제 표준 입출력을 사용하지 않고 검증할 수 있다.

    Args:
        mode: 입력 처리를 위임할 모드 객체.
        prompt: 사용자에게 표시할 프롬프트.
        input_fn: 한 줄 입력을 받을 함수. 기본값은 내장 ``input``.
        output_fn: 한 줄 출력을 수행할 함수. 기본값은 내장 ``print``.

    Returns:
        프로세스 종료 코드. 정상 종료는 0.
    """
    output_fn(build_welcome_message(mode))

    while True:
        try:
            user_input = input_fn(prompt)
        except EOFError:
            # Ctrl+D: 트레이스백 없이 깔끔하게 종료
            output_fn("")
            output_fn("Goodbye!")
            return 0
        except KeyboardInterrupt:
            # Ctrl+C: 트레이스백 없이 깔끔하게 종료
            output_fn("")
            output_fn("Interrupted. Goodbye!")
            return 0

        if is_exit_command(user_input):
            output_fn("Goodbye!")
            return 0

        # 빈 문자열이면 아무 출력 없이 다음 프롬프트로 진행
        if user_input == "":
            continue

        response = mode.handle(user_input)
        # 모드가 빈 문자열을 반환하면 출력을 생략한다.
        if response != "":
            output_fn(response)


def run_repl_with_inputs(
    mode: BaseMode,
    inputs: Iterable[str],
    *,
    prompt: str = DEFAULT_PROMPT,
    output_fn: Optional[Callable[[str], None]] = None,
) -> list[str]:
    """테스트 편의 함수: 주어진 입력 시퀀스로 REPL을 한 번 실행한다.

    내부적으로 ``inputs`` iterator 의 ``StopIteration`` 을 ``EOFError`` 로 변환해
    REPL의 EOF 처리 경로를 자연스럽게 활용한다.

    Args:
        mode: 사용할 모드.
        inputs: 한 줄씩 공급할 입력 시퀀스.
        prompt: 프롬프트(테스트용으로 무시 가능).
        output_fn: 출력 함수. ``None`` 이면 결과를 리스트에 모은다.

    Returns:
        ``output_fn`` 이 ``None`` 이었을 경우 출력된 라인들의 리스트.
        ``output_fn`` 을 직접 전달했다면 빈 리스트를 반환한다.
    """
    iterator = iter(inputs)
    captured: list[str] = []

    def _input(_prompt: str) -> str:
        try:
            return next(iterator)
        except StopIteration as exc:  # 입력 소진 -> EOF 처리 경로로 위임
            raise EOFError from exc

    actual_output = output_fn or (lambda line: captured.append(line))

    run_repl(mode, prompt=prompt, input_fn=_input, output_fn=actual_output)
    return captured
