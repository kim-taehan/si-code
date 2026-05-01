"""``/clear`` 와 ``/system`` 슬래시 명령 단위/통합 테스트.

검증 범위:
    - ``parse_slash_command`` 가 토큰과 인자를 올바르게 분해한다.
    - 디스패처가 :attr:`ReplContext.argument` 와 :attr:`ReplContext.mode` 를 채운다.
    - ``/clear`` 가 :class:`Conversation` 을 초기화한다.
    - ``/system <text>`` 가 system 메시지를 설정한다(빈 인자는 사용법 안내).
    - REPL 통합: 슬래시 입력이 dispatcher 로 전달되어 모드 상태를 변경하고,
      이후 멀티턴 요청에서 system 메시지가 실제로 전송된다.
"""

from __future__ import annotations

import json
from typing import Any, Callable, List, Optional

import pytest

from sicode.commands import (
    ClearCommand,
    SystemCommand,
    register_default_commands,
)
from sicode.commands.base import CommandAction, ReplContext
from sicode.commands.clear import (
    NO_CONVERSATION_MESSAGE as CLEAR_NO_CONV,
    SUCCESS_MESSAGE as CLEAR_SUCCESS,
)
from sicode.commands.registry import (
    SlashCommandRegistry,
    dispatch_command,
    parse_slash_command,
)
from sicode.commands.system import (
    NO_CONVERSATION_MESSAGE as SYS_NO_CONV,
    SUCCESS_MESSAGE as SYS_SUCCESS,
    USAGE_MESSAGE as SYS_USAGE,
)
from sicode.modes.base import BaseMode
from sicode.modes.conversation import Conversation
from sicode.modes.ollama import OllamaMode
from sicode.modes.ollama_chat import OllamaChatClient
from sicode.repl import run_repl_with_inputs


# ---------------------------------------------------------------------------
# parse_slash_command
# ---------------------------------------------------------------------------


class TestParseSlashCommand:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("/clear", ("clear", "")),
            ("  /Clear  ", ("clear", "")),
            ("/system Hello world", ("system", "Hello world")),
            ("/SYSTEM   Hello   world  ", ("system", "Hello   world")),
            ("/system", ("system", "")),
            ("/", ("", "")),
            ("hello", None),
            ("", None),
        ],
    )
    def test_parses_token_and_argument(self, line: str, expected) -> None:
        assert parse_slash_command(line) == expected


# ---------------------------------------------------------------------------
# Dispatcher → context 통합
# ---------------------------------------------------------------------------


class _RecordContextCommand:
    """디스패처가 ``argument`` / ``mode`` 를 컨텍스트에 채워주는지 검증용."""

    name = "recordctx"
    aliases: tuple = ()
    description = "records context"

    def __init__(self) -> None:
        self.last_ctx: Optional[ReplContext] = None

    def execute(self, context: ReplContext):
        from sicode.commands.base import CommandResult

        self.last_ctx = context
        return CommandResult.cont("recorded")


class TestDispatcherPropagatesArgumentAndMode:
    def test_argument_passed_through_context(self) -> None:
        reg = SlashCommandRegistry()
        cmd = _RecordContextCommand()
        reg.register(cmd)  # type: ignore[arg-type]
        dispatch_command("/recordctx hello world", registry=reg)
        assert cmd.last_ctx is not None
        assert cmd.last_ctx.argument == "hello world"

    def test_mode_passed_through_context(self) -> None:
        class _Mode(BaseMode):
            name = "x"

            def handle(self, user_input: str) -> str:
                return user_input

        reg = SlashCommandRegistry()
        cmd = _RecordContextCommand()
        reg.register(cmd)  # type: ignore[arg-type]
        m = _Mode()
        dispatch_command("/recordctx", registry=reg, mode=m)
        assert cmd.last_ctx is not None
        assert cmd.last_ctx.mode is m


# ---------------------------------------------------------------------------
# /clear command (unit)
# ---------------------------------------------------------------------------


class _StubModeWithConversation(BaseMode):
    """``conversation`` 속성을 노출하는 가벼운 모드 더블."""

    name = "stub"

    def __init__(self) -> None:
        self.conversation = Conversation()

    def handle(self, user_input: str) -> str:  # pragma: no cover - 미사용
        return user_input


class TestClearCommandUnit:
    def test_clears_conversation_and_returns_success(self) -> None:
        mode = _StubModeWithConversation()
        mode.conversation.set_system("you are helpful")
        mode.conversation.add_user("hi")
        mode.conversation.add_assistant("hello")

        result = ClearCommand().execute(ReplContext(mode=mode))
        assert result.action is CommandAction.CONTINUE
        assert result.output == CLEAR_SUCCESS
        assert mode.conversation.messages() == []

    def test_no_conversation_when_mode_missing_attr(self) -> None:
        class _Plain(BaseMode):
            name = "p"

            def handle(self, user_input: str) -> str:  # pragma: no cover
                return user_input

        result = ClearCommand().execute(ReplContext(mode=_Plain()))
        assert result.action is CommandAction.CONTINUE
        assert result.output == CLEAR_NO_CONV

    def test_no_conversation_when_mode_none(self) -> None:
        result = ClearCommand().execute(ReplContext(mode=None))
        assert result.output == CLEAR_NO_CONV

    def test_clear_does_not_set_action_to_exit(self) -> None:
        mode = _StubModeWithConversation()
        result = ClearCommand().execute(ReplContext(mode=mode))
        assert result.action is CommandAction.CONTINUE


# ---------------------------------------------------------------------------
# /system command (unit)
# ---------------------------------------------------------------------------


class TestSystemCommandUnit:
    def test_sets_system_message_on_conversation(self) -> None:
        mode = _StubModeWithConversation()
        ctx = ReplContext(mode=mode, argument="You are helpful.")
        result = SystemCommand().execute(ctx)
        assert result.action is CommandAction.CONTINUE
        assert result.output == SYS_SUCCESS
        assert mode.conversation.system_message == "You are helpful."

    def test_replaces_existing_system_message(self) -> None:
        mode = _StubModeWithConversation()
        mode.conversation.set_system("v1")
        ctx = ReplContext(mode=mode, argument="v2")
        SystemCommand().execute(ctx)
        assert mode.conversation.system_message == "v2"

    def test_empty_argument_shows_usage_and_does_not_change_history(self) -> None:
        mode = _StubModeWithConversation()
        mode.conversation.set_system("untouched")
        ctx = ReplContext(mode=mode, argument="")
        result = SystemCommand().execute(ctx)
        assert result.output == SYS_USAGE
        assert mode.conversation.system_message == "untouched"

    def test_whitespace_only_argument_shows_usage(self) -> None:
        mode = _StubModeWithConversation()
        ctx = ReplContext(mode=mode, argument="   ")
        result = SystemCommand().execute(ctx)
        assert result.output == SYS_USAGE
        assert mode.conversation.system_message is None

    def test_no_conversation_returns_safe_message(self) -> None:
        class _Plain(BaseMode):
            name = "p"

            def handle(self, user_input: str) -> str:  # pragma: no cover
                return user_input

        ctx = ReplContext(mode=_Plain(), argument="hi")
        result = SystemCommand().execute(ctx)
        assert result.output == SYS_NO_CONV


# ---------------------------------------------------------------------------
# REPL 통합
# ---------------------------------------------------------------------------


def _make_chat_opener(payload_seq: List[dict], captured: Optional[List[dict]] = None) -> Callable[..., Any]:
    """``/api/chat`` 응답을 순서대로 돌려주는 가짜 ``urlopen``."""
    import io

    iterator = iter(payload_seq)

    class _Resp:
        def __init__(self, data: bytes) -> None:
            self._buf = io.BytesIO(data)

        def read(self) -> bytes:
            return self._buf.read()

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *exc: Any) -> None:
            self._buf.close()

    def _opener(req: Any, timeout: float) -> _Resp:
        if captured is not None:
            captured.append({"url": req.full_url, "body": req.data})
        return _Resp(json.dumps(next(iterator)).encode("utf-8"))

    return _opener


class TestReplIntegrationClearAndSystem:
    def test_slash_clear_resets_conversation_after_turn(self) -> None:
        register_default_commands()
        opener = _make_chat_opener(
            [
                {"message": {"role": "assistant", "content": "first reply"}},
            ]
        )
        mode = OllamaMode(client=OllamaChatClient(url_opener=opener))
        outputs = run_repl_with_inputs(
            mode, ["hello", "/clear", "/exit"]
        )
        # /clear 가 안내를 출력하고 history 가 비워졌는지.
        assert any(CLEAR_SUCCESS in line for line in outputs)
        assert mode.conversation.messages() == []

    def test_slash_system_then_chat_includes_system_message(self) -> None:
        register_default_commands()
        captured: List[dict] = []
        opener = _make_chat_opener(
            [{"message": {"role": "assistant", "content": "ok"}}],
            captured=captured,
        )
        mode = OllamaMode(client=OllamaChatClient(url_opener=opener))

        outputs = run_repl_with_inputs(
            mode, ["/system You are helpful.", "ask now", "/exit"]
        )
        assert any(SYS_SUCCESS in line for line in outputs)
        # 첫 채팅 호출의 messages 배열 첫 항목이 system 으로 시작해야 한다.
        assert len(captured) == 1
        body = json.loads(captured[0]["body"].decode("utf-8"))
        assert body["messages"][0] == {
            "role": "system",
            "content": "You are helpful.",
        }
        assert body["messages"][1] == {"role": "user", "content": "ask now"}

    def test_slash_system_with_no_argument_shows_usage(self) -> None:
        register_default_commands()
        opener = _make_chat_opener([])  # 호출되지 않아야 함
        mode = OllamaMode(client=OllamaChatClient(url_opener=opener))

        outputs = run_repl_with_inputs(mode, ["/system", "/exit"])
        assert any(SYS_USAGE in line for line in outputs)
        assert mode.conversation.system_message is None

    def test_slash_clear_then_new_request_starts_fresh(self) -> None:
        register_default_commands()
        captured: List[dict] = []
        opener = _make_chat_opener(
            [
                {"message": {"role": "assistant", "content": "r1"}},
                {"message": {"role": "assistant", "content": "r2"}},
            ],
            captured=captured,
        )
        mode = OllamaMode(client=OllamaChatClient(url_opener=opener))
        run_repl_with_inputs(
            mode, ["first", "/clear", "second", "/exit"]
        )
        # 두 번째 호출의 messages 배열에는 첫 턴이 포함되지 않아야 한다.
        body2 = json.loads(captured[1]["body"].decode("utf-8"))
        assert body2["messages"] == [{"role": "user", "content": "second"}]

    def test_help_lists_clear_and_system(self) -> None:
        register_default_commands()
        opener = _make_chat_opener([])
        mode = OllamaMode(client=OllamaChatClient(url_opener=opener))
        outputs = run_repl_with_inputs(mode, ["/help", "/exit"])
        joined = "\n".join(outputs)
        assert "/clear" in joined
        assert "/system" in joined
