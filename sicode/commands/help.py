"""``/help`` 슬래시 명령.

등록된 모든 슬래시 명령(이름·별칭·설명)을 이름 알파벳 오름차순으로 출력한다.
``!cmd`` (셸 prefix) 사용법도 함께 안내한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sicode.commands.base import CommandResult, ReplContext, SlashCommand

if TYPE_CHECKING:  # pragma: no cover - 타입 힌트 전용
    from sicode.commands.registry import SlashCommandRegistry


#: ``/help`` 출력 말미에 추가되는 ``!cmd`` 안내 블록.
#:
#: 슬래시 명령 디스패처와 bang 분기는 서로 별개의 입력 prefix 이지만, ``/help``
#: 가 사용자가 가장 먼저 찾는 도움말이므로 함께 안내한다(이슈 #18).
BANG_HELP_LINES: "tuple[str, ...]" = (
    "",
    "Shell prefix:",
    "  !<cmd> - 셸에서 명령을 직접 실행 (예: !ls, !git status, !cat file.txt).",
    "  주의: 시스템에 직접 실행됩니다. 위험한 명령(rm 등)에 유의하세요.",
    "  타임아웃 기본 60초 — SICODE_BANG_TIMEOUT 환경 변수로 재정의.",
)


class HelpCommand(SlashCommand):
    """등록된 명령 목록을 출력한다.

    레지스트리는 생성자 주입 또는 :class:`ReplContext` 로 전달받는다 (DIP).
    생성자 주입이 우선되며, 둘 다 없으면 안내만 출력한다.
    """

    name: str = "help"
    aliases: "tuple[str, ...]" = ()
    description: str = "List all available slash commands."

    def __init__(self, registry: Optional["SlashCommandRegistry"] = None) -> None:
        self._registry = registry

    def execute(self, context: ReplContext) -> CommandResult:
        registry = self._registry or context.registry
        if registry is None:
            return CommandResult.cont(
                output="\n".join(("No commands registered.",) + BANG_HELP_LINES)
            )

        commands = registry.commands()
        if not commands:
            return CommandResult.cont(
                output="\n".join(("No commands registered.",) + BANG_HELP_LINES)
            )

        lines = ["Available commands:"]
        for cmd in commands:
            alias_text = (
                f" (aliases: {', '.join('/' + a for a in cmd.aliases)})"
                if cmd.aliases
                else ""
            )
            lines.append(f"  /{cmd.name}{alias_text} - {cmd.description}")
        lines.extend(BANG_HELP_LINES)
        return CommandResult.cont(output="\n".join(lines))


__all__ = ["BANG_HELP_LINES", "HelpCommand"]
