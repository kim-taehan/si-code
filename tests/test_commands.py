"""슬래시 명령 시스템 단위 테스트.

검증 범위:
    - :class:`SlashCommand` / :class:`CommandResult` 계약.
    - :class:`SlashCommandRegistry` 등록·조회·중복방지·정렬·리셋.
    - 디스패처(``dispatch_command``) 의 분기/미상 명령 처리.
    - ``/exit``, ``/quit``, ``/help`` 기본 명령 동작.
    - 새 명령 등록만으로 REPL 무수정 동작(확장성).
"""

from __future__ import annotations

from typing import List

import pytest

from sicode.commands import (
    CommandResult,
    ExitCommand,
    HelpCommand,
    ReplContext,
    SlashCommand,
    SlashCommandRegistry,
    default_registry,
    dispatch_command,
    register,
    register_default_commands,
    reset,
)
from sicode.commands.base import CommandAction
from sicode.commands.registry import parse_slash_input, temporary_registry
from sicode.repl import run_repl_with_inputs
from tests.conftest import EchoMode


# ---------------------------------------------------------------------------
# CommandResult / Enum
# ---------------------------------------------------------------------------
class TestCommandResult:
    def test_action_enum_has_continue_and_exit(self) -> None:
        assert CommandAction.CONTINUE != CommandAction.EXIT
        assert {a.name for a in CommandAction} == {"CONTINUE", "EXIT"}

    def test_cont_and_exit_helpers(self) -> None:
        cont = CommandResult.cont("ok")
        ex = CommandResult.exit_("bye")
        assert cont.action is CommandAction.CONTINUE and cont.output == "ok"
        assert ex.action is CommandAction.EXIT and ex.output == "bye"

    def test_default_output_is_empty_string(self) -> None:
        assert CommandResult.cont().output == ""
        assert CommandResult.exit_().output == ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class _DummyCommand(SlashCommand):
    """확장성 테스트용 더미 명령."""

    name = "dummy"
    aliases = ("dm",)
    description = "Dummy test command."

    def __init__(self) -> None:
        self.calls: List[ReplContext] = []

    def execute(self, context: ReplContext) -> CommandResult:
        self.calls.append(context)
        return CommandResult.cont("dummy ran")


class TestSlashCommandRegistry:
    def test_register_and_get_by_name_and_alias(self) -> None:
        reg = SlashCommandRegistry()
        cmd = ExitCommand()
        reg.register(cmd)
        assert reg.get("exit") is cmd
        assert reg.get("quit") is cmd
        assert reg.get("nope") is None

    def test_duplicate_registration_raises(self) -> None:
        reg = SlashCommandRegistry()
        reg.register(ExitCommand())
        with pytest.raises(ValueError):
            reg.register(ExitCommand())  # 같은 name/aliases 재등록

    def test_register_rejects_empty_name(self) -> None:
        class _Empty(SlashCommand):
            name = ""
            description = "empty"

            def execute(self, context: ReplContext) -> CommandResult:
                return CommandResult.cont()

        reg = SlashCommandRegistry()
        with pytest.raises(ValueError):
            reg.register(_Empty())

    def test_commands_returns_alphabetical_order(self) -> None:
        reg = SlashCommandRegistry()
        reg.register(HelpCommand(registry=reg))
        reg.register(ExitCommand())
        reg.register(_DummyCommand())
        names = [c.name for c in reg.commands()]
        assert names == sorted(names)
        assert names == ["dummy", "exit", "help"]

    def test_reset_clears_all(self) -> None:
        reg = SlashCommandRegistry()
        reg.register(ExitCommand())
        reg.reset()
        assert reg.get("exit") is None
        assert reg.commands() == []

    def test_unregister_removes_aliases_too(self) -> None:
        reg = SlashCommandRegistry()
        reg.register(ExitCommand())
        reg.unregister("exit")
        assert reg.get("exit") is None
        assert reg.get("quit") is None
        with pytest.raises(KeyError):
            reg.unregister("exit")

    def test_module_level_register_uses_default_registry(self) -> None:
        register(ExitCommand())
        assert default_registry.get("exit") is not None
        reset()
        assert default_registry.get("exit") is None

    def test_temporary_registry_restores_state(self) -> None:
        register(ExitCommand())
        with temporary_registry() as reg:
            assert reg.get("exit") is None  # 비어있어야 한다
            reg.register(_DummyCommand())
            assert reg.get("dummy") is not None
        # 복구 후 원상태
        assert default_registry.get("exit") is not None
        assert default_registry.get("dummy") is None


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
class TestParseSlashInput:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("/exit", "exit"),
            ("  /Exit ", "exit"),
            ("/HELP", "help"),
            ("/foo bar", "foo"),
            ("hello", None),
            ("", None),
            ("/", ""),
        ],
    )
    def test_parses_correctly(self, line: str, expected) -> None:
        assert parse_slash_input(line) == expected


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
class TestDispatchCommand:
    def test_dispatches_registered_command(self) -> None:
        reg = SlashCommandRegistry()
        cmd = ExitCommand()
        reg.register(cmd)
        result = dispatch_command("/exit", registry=reg)
        assert result.action is CommandAction.EXIT
        assert result.output == "Goodbye!"

    def test_dispatches_alias(self) -> None:
        reg = SlashCommandRegistry()
        reg.register(ExitCommand())
        result = dispatch_command("/quit", registry=reg)
        assert result.action is CommandAction.EXIT

    def test_unknown_command_returns_helpful_message(self) -> None:
        reg = SlashCommandRegistry()
        result = dispatch_command("/foo", registry=reg)
        assert result.action is CommandAction.CONTINUE
        assert result.output == (
            "Unknown command: /foo. Type /help for available commands."
        )

    def test_only_slash_returns_unknown(self) -> None:
        reg = SlashCommandRegistry()
        result = dispatch_command("/", registry=reg)
        assert result.action is CommandAction.CONTINUE
        assert "Type /help" in result.output

    def test_passes_context_to_command(self) -> None:
        reg = SlashCommandRegistry()
        dummy = _DummyCommand()
        reg.register(dummy)
        result = dispatch_command("/dummy", registry=reg)
        assert result.action is CommandAction.CONTINUE
        assert dummy.calls and dummy.calls[0].registry is reg

    def test_case_insensitive_token(self) -> None:
        reg = SlashCommandRegistry()
        reg.register(ExitCommand())
        result = dispatch_command("/EXIT", registry=reg)
        assert result.action is CommandAction.EXIT


# ---------------------------------------------------------------------------
# /help command
# ---------------------------------------------------------------------------
class TestHelpCommand:
    def test_lists_commands_alphabetically(self) -> None:
        reg = SlashCommandRegistry()
        reg.register(HelpCommand(registry=reg))
        reg.register(ExitCommand())
        result = dispatch_command("/help", registry=reg)
        assert result.action is CommandAction.CONTINUE
        # 본문에 등록 명령들이 알파벳 오름차순으로 등장해야 한다.
        idx_exit = result.output.index("/exit")
        idx_help = result.output.index("/help")
        assert idx_exit < idx_help

    def test_includes_aliases_and_description(self) -> None:
        reg = SlashCommandRegistry()
        reg.register(ExitCommand())
        reg.register(HelpCommand(registry=reg))
        result = dispatch_command("/help", registry=reg)
        assert "/quit" in result.output  # 별칭
        assert "Exit the REPL." in result.output

    def test_help_with_empty_registry_is_safe(self) -> None:
        # 컨텍스트로 레지스트리를 넘기는 경로를 검증한다.
        cmd = HelpCommand()
        result = cmd.execute(ReplContext(registry=SlashCommandRegistry()))
        assert result.action is CommandAction.CONTINUE
        assert "No commands" in result.output


# ---------------------------------------------------------------------------
# REPL integration
# ---------------------------------------------------------------------------
class TestReplSlashIntegration:
    def test_slash_exit_terminates_repl(self) -> None:
        register_default_commands()
        mode = EchoMode()
        outputs = run_repl_with_inputs(mode, ["/exit"])
        assert mode.calls == []  # mode.handle 미호출
        assert any("Goodbye" in line for line in outputs)

    def test_slash_quit_terminates_repl(self) -> None:
        register_default_commands()
        mode = EchoMode()
        outputs = run_repl_with_inputs(mode, ["/quit"])
        assert mode.calls == []
        assert any("Goodbye" in line for line in outputs)

    def test_slash_help_lists_commands_and_keeps_repl_alive(self) -> None:
        register_default_commands()
        mode = EchoMode()
        outputs = run_repl_with_inputs(mode, ["/help", "hello"])
        # /help 는 mode.handle 호출하지 않음
        assert mode.calls == ["hello"]
        # 출력 어딘가에 등록 명령이 모두 등장해야 함
        joined = "\n".join(outputs)
        assert "/exit" in joined
        assert "/help" in joined
        assert "/quit" in joined

    def test_unknown_slash_command_shows_message_and_continues(self) -> None:
        register_default_commands()
        mode = EchoMode()
        outputs = run_repl_with_inputs(mode, ["/foo", "still alive"])
        assert mode.calls == ["still alive"]
        joined = "\n".join(outputs)
        assert "Unknown command: /foo" in joined
        assert "still alive" in outputs

    def test_plain_exit_still_works_no_regression(self) -> None:
        register_default_commands()
        mode = EchoMode()
        outputs = run_repl_with_inputs(mode, ["exit"])
        assert mode.calls == []
        assert any("Goodbye" in line for line in outputs)

    def test_plain_quit_still_works_no_regression(self) -> None:
        register_default_commands()
        mode = EchoMode()
        outputs = run_repl_with_inputs(mode, ["QUIT"])
        assert mode.calls == []
        assert any("Goodbye" in line for line in outputs)

    def test_welcome_message_mentions_help(self) -> None:
        register_default_commands()
        outputs = run_repl_with_inputs(EchoMode(), ["/exit"])
        assert any("/help" in line for line in outputs)

    def test_dummy_command_extension_without_repl_changes(self) -> None:
        """확장성: 새 명령을 등록만 하면 REPL 코드 수정 없이 동작한다 (OCP)."""

        class _Greet(SlashCommand):
            name = "greet"
            aliases = ()
            description = "Greet the user."

            def __init__(self) -> None:
                self.executed = 0

            def execute(self, context: ReplContext) -> CommandResult:
                self.executed += 1
                return CommandResult.cont("hello there")

        cmd = _Greet()
        register(cmd)
        mode = EchoMode()
        outputs = run_repl_with_inputs(mode, ["/greet", "/exit"])
        assert cmd.executed == 1
        assert mode.calls == []  # mode.handle 미호출
        assert "hello there" in outputs

    def test_slash_commands_do_not_invoke_mode_handle(self) -> None:
        """/exit · /quit · /help 모두 mode.handle 을 호출하지 않아야 한다."""
        register_default_commands()
        mode = EchoMode()
        run_repl_with_inputs(mode, ["/help", "/quit"])
        assert mode.calls == []
