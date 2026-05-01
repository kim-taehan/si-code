"""``/clear`` 슬래시 명령: 대화 히스토리 초기화.

요구사항(이슈 #11):
    - 실행 시 :class:`OllamaMode` 의 :class:`Conversation` 히스토리를 초기화하고
      "대화 히스토리를 초기화했습니다." 메시지를 출력한다.
    - REPL 루프(``repl.py``) 본문은 수정하지 않는다 (OCP).

설계 메모:
    - DIP: 본 명령은 모드 구체 클래스 대신 ``conversation`` 속성(:class:`Conversation`)을
      가진 객체에만 의존한다. 단위 테스트는 가짜 모드(``DummyMode``) 를 주입한다.
    - SRP: "히스토리 초기화 + 안내 출력" 한 가지 책임.
    - 모드가 :class:`Conversation` 인터페이스를 노출하지 않는 경우(예: legacy
      single-turn 콜러블) 안전하게 안내 문구로 폴백한다.
"""

from __future__ import annotations

from sicode.commands.base import CommandResult, ReplContext, SlashCommand
from sicode.modes.conversation import Conversation


#: 정상 처리 시 사용자에게 보여줄 메시지.
SUCCESS_MESSAGE: str = "대화 히스토리를 초기화했습니다."

#: 모드가 대화 히스토리를 보유하지 않을 때(legacy 단일 턴 등) 보여줄 메시지.
NO_CONVERSATION_MESSAGE: str = (
    "현재 모드는 대화 히스토리를 사용하지 않습니다."
)


def _resolve_conversation(context: ReplContext) -> "Conversation | None":
    """컨텍스트의 mode 에서 :class:`Conversation` 핸들을 안전하게 꺼낸다.

    DIP: 모드의 구체 클래스(``OllamaMode``)에 의존하지 않고 ``conversation``
    속성의 존재만 확인한다. 다른 모드가 같은 속성 이름으로 본인의 Conversation
    을 노출하면 별도 분기 없이 동작한다 (OCP).
    """
    mode = context.mode
    if mode is None:
        return None
    conversation = getattr(mode, "conversation", None)
    if isinstance(conversation, Conversation):
        return conversation
    return None


class ClearCommand(SlashCommand):
    """대화 히스토리를 초기화하는 슬래시 명령."""

    name: str = "clear"
    aliases: "tuple[str, ...]" = ()
    description: str = "Clear the conversation history."

    def execute(self, context: ReplContext) -> CommandResult:
        conversation = _resolve_conversation(context)
        if conversation is None:
            return CommandResult.cont(NO_CONVERSATION_MESSAGE)
        conversation.clear()
        return CommandResult.cont(SUCCESS_MESSAGE)


__all__ = ["ClearCommand", "NO_CONVERSATION_MESSAGE", "SUCCESS_MESSAGE"]
