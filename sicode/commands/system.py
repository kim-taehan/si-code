"""``/system <text>`` 슬래시 명령: 대화의 system 메시지 설정.

요구사항(이슈 #11):
    - ``/system <text>`` 입력 시 ``<text>`` 를 :class:`Conversation` 의 system
      메시지로 교체하고 "시스템 메시지를 설정했습니다." 를 출력한다.
    - 인자가 비어 있으면 "사용법: /system <메시지 내용>" 을 출력하고 히스토리는
      변경하지 않는다.
    - REPL 루프 본문은 수정하지 않는다 (OCP).

설계 메모:
    - DIP: ``conversation`` 속성을 가진 모드 객체에만 의존한다. 모드 구체 클래스
      미참조.
    - SRP: 인자 파싱은 디스패처가 :attr:`ReplContext.argument` 로 전달하므로,
      본 명령은 "검증 + 호출 + 안내" 만 담당.
"""

from __future__ import annotations

from sicode.commands.base import CommandResult, ReplContext, SlashCommand
from sicode.commands.clear import _resolve_conversation


#: 정상 처리 메시지.
SUCCESS_MESSAGE: str = "시스템 메시지를 설정했습니다."

#: 인자 없이 호출됐을 때 안내 메시지.
USAGE_MESSAGE: str = "사용법: /system <메시지 내용>"

#: 모드가 대화 히스토리를 보유하지 않을 때 안내 메시지.
NO_CONVERSATION_MESSAGE: str = (
    "현재 모드는 대화 히스토리를 사용하지 않아 시스템 메시지를 설정할 수 없습니다."
)


class SystemCommand(SlashCommand):
    """대화의 system 메시지를 설정/교체하는 슬래시 명령."""

    name: str = "system"
    aliases: "tuple[str, ...]" = ()
    description: str = "Set or replace the conversation system message."

    def execute(self, context: ReplContext) -> CommandResult:
        argument = context.argument.strip()
        if not argument:
            return CommandResult.cont(USAGE_MESSAGE)

        conversation = _resolve_conversation(context)
        if conversation is None:
            return CommandResult.cont(NO_CONVERSATION_MESSAGE)

        conversation.set_system(argument)
        return CommandResult.cont(SUCCESS_MESSAGE)


__all__ = [
    "NO_CONVERSATION_MESSAGE",
    "SUCCESS_MESSAGE",
    "SystemCommand",
    "USAGE_MESSAGE",
]
