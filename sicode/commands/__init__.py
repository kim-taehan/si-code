"""슬래시 명령(`/exit`, `/help` 등) 패키지.

REPL 루프에 명령 추가를 위한 분기 코드를 직접 작성하지 않고
:class:`SlashCommand` 추상을 구현한 뒤 :func:`registry.register` 로
명시적으로 등록만 하면 동작하는 구조를 제공한다 (OCP, DIP).

사용 예::

    from sicode.commands import register_default_commands

    register_default_commands()
"""

from __future__ import annotations

from sicode.commands.base import CommandResult, ReplContext, SlashCommand
from sicode.commands.exit import ExitCommand
from sicode.commands.help import HelpCommand
from sicode.commands.registry import (
    SlashCommandRegistry,
    default_registry,
    dispatch_command,
    register,
    reset,
)


def register_default_commands(registry: SlashCommandRegistry | None = None) -> None:
    """기본 슬래시 명령(``/exit``, ``/quit``, ``/help``, ``/init``) 을 등록한다.

    임포트 부수효과로 자동 등록하지 않으며, 본 함수의 명시적 호출이 등록 시점이다.

    Args:
        registry: 등록 대상 레지스트리. ``None`` 이면 모듈 전역
            :data:`default_registry` 에 등록한다.
    """
    # ``or`` 폴백을 쓰면 ``__len__`` 이 0인 빈 레지스트리가 falsy 로 평가되어
    # 의도와 달리 ``default_registry`` 로 가버린다. 명시적 ``is None`` 검사로 회피.
    target = default_registry if registry is None else registry
    # ``InitCommand`` 는 ``sicode.commands.base`` 에 의존하므로, 본 패키지 임포트
    # 시점에 같이 임포트하면 순환 참조가 발생한다. 등록 시점에 지연 임포트한다.
    from sicode.init.command import InitCommand

    target.register(ExitCommand())
    target.register(HelpCommand(registry=target))
    target.register(InitCommand())


__all__ = [
    "CommandResult",
    "ExitCommand",
    "HelpCommand",
    "ReplContext",
    "SlashCommand",
    "SlashCommandRegistry",
    "default_registry",
    "dispatch_command",
    "register",
    "register_default_commands",
    "reset",
]
