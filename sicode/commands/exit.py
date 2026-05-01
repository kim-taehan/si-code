"""``/exit`` 및 ``/quit`` 슬래시 명령.

REPL 루프 종료를 요청한다. 별칭 처리는 레지스트리가 ``aliases`` 에 따라
이름과 동일한 명령으로 매핑한다.
"""

from __future__ import annotations

from sicode.commands.base import CommandResult, ReplContext, SlashCommand


class ExitCommand(SlashCommand):
    """REPL 종료 명령.

    출력은 평문 ``exit``/``quit`` 와 동일한 ``"Goodbye!"`` 로 통일한다.
    """

    name: str = "exit"
    aliases: "tuple[str, ...]" = ("quit",)
    description: str = "Exit the REPL."

    def execute(self, context: ReplContext) -> CommandResult:  # noqa: ARG002 - 컨텍스트 미사용
        return CommandResult.exit_(output="Goodbye!")


__all__ = ["ExitCommand"]
