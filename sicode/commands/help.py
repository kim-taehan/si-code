"""``/help`` 슬래시 명령.

등록된 모든 슬래시 명령(이름·별칭·설명)을 이름 알파벳 오름차순으로 출력한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sicode.commands.base import CommandResult, ReplContext, SlashCommand

if TYPE_CHECKING:  # pragma: no cover - 타입 힌트 전용
    from sicode.commands.registry import SlashCommandRegistry


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
            return CommandResult.cont(output="No commands registered.")

        commands = registry.commands()
        if not commands:
            return CommandResult.cont(output="No commands registered.")

        lines = ["Available commands:"]
        for cmd in commands:
            alias_text = (
                f" (aliases: {', '.join('/' + a for a in cmd.aliases)})"
                if cmd.aliases
                else ""
            )
            lines.append(f"  /{cmd.name}{alias_text} - {cmd.description}")
        return CommandResult.cont(output="\n".join(lines))


__all__ = ["HelpCommand"]
