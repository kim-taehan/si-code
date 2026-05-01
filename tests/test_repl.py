"""REPL 루프 단위 테스트.

표준 입출력을 직접 사용하지 않고 ``input_fn`` / ``output_fn`` 을 주입해 결정론적으로
검증한다 (DIP 덕분에 가능).
"""

from __future__ import annotations

from typing import Iterator, List

import pytest

from sicode.modes.base import BaseMode
from sicode.repl import (
    DEFAULT_PROMPT,
    build_welcome_message,
    is_exit_command,
    run_repl,
    run_repl_with_inputs,
)
from tests.conftest import EchoMode


class _RecordingMode(BaseMode):
    """handle 호출 인자를 기록해 주는 테스트 더블."""

    name = "recording"

    def __init__(self, response: str = "") -> None:
        self.calls: List[str] = []
        self._response = response

    def handle(self, user_input: str) -> str:
        self.calls.append(user_input)
        return self._response or user_input


def _make_input_fn(inputs: Iterator[str]):
    def _fn(_prompt: str) -> str:
        return next(inputs)

    return _fn


class TestExitCommandHelper:
    @pytest.mark.parametrize("text", ["exit", "quit", "EXIT", "Quit", "  exit  "])
    def test_recognizes_exit_variants(self, text: str) -> None:
        assert is_exit_command(text) is True

    @pytest.mark.parametrize("text", ["", "exits", "qu it", "hello", " e "])
    def test_rejects_non_exit_text(self, text: str) -> None:
        assert is_exit_command(text) is False


class TestWelcomeMessage:
    def test_contains_version_and_mode_name(self) -> None:
        msg = build_welcome_message(EchoMode(), version="9.9.9")
        assert "9.9.9" in msg
        assert "echo" in msg

    def test_contains_ollama_server_notice(self) -> None:
        # 이슈 #5 수용 기준: 환영 메시지에 Ollama 서버 필요 안내가 포함되어야 한다.
        msg = build_welcome_message(EchoMode(), version="9.9.9")
        assert "Ollama" in msg
        assert "실행" in msg


class TestRunRepl:
    def test_echoes_user_input(self) -> None:
        outputs = run_repl_with_inputs(EchoMode(), ["hello world"])
        # outputs[0] 은 환영 메시지. 그 이후에 에코된 라인이 있어야 한다.
        assert "hello world" in outputs

    def test_exits_on_exit_command(self) -> None:
        mode = _RecordingMode()
        outputs = run_repl_with_inputs(mode, ["exit", "should-not-be-handled"])
        assert mode.calls == []  # exit 이후의 입력은 handle 되면 안 됨
        assert any("Goodbye" in line for line in outputs)

    def test_exits_on_quit_command_case_insensitive(self) -> None:
        mode = _RecordingMode()
        outputs = run_repl_with_inputs(mode, ["QUIT"])
        assert mode.calls == []
        assert any("Goodbye" in line for line in outputs)

    def test_blank_input_does_not_call_mode_and_continues(self) -> None:
        mode = _RecordingMode()
        outputs = run_repl_with_inputs(mode, ["", "", "hello"])
        # 두 번의 빈 입력은 mode.handle 이 호출되지 않아야 한다.
        assert mode.calls == ["hello"]
        assert "hello" in outputs

    def test_handles_eof_gracefully(self) -> None:
        # run_repl_with_inputs 는 입력이 소진되면 EOFError 로 위임한다.
        outputs = run_repl_with_inputs(EchoMode(), [])
        assert any("Goodbye" in line for line in outputs)

    def test_handles_keyboard_interrupt_gracefully(self) -> None:
        captured: List[str] = []

        def _input_fn(_prompt: str) -> str:
            raise KeyboardInterrupt

        exit_code = run_repl(
            EchoMode(),
            input_fn=_input_fn,
            output_fn=captured.append,
        )
        assert exit_code == 0
        assert any("Interrupted" in line for line in captured)

    def test_returns_zero_on_normal_exit(self) -> None:
        captured: List[str] = []
        inputs = iter(["hello", "exit"])

        exit_code = run_repl(
            EchoMode(),
            input_fn=_make_input_fn(inputs),
            output_fn=captured.append,
        )
        assert exit_code == 0

    def test_uses_provided_prompt(self) -> None:
        recorded_prompts: List[str] = []
        inputs = iter(["exit"])

        def _input_fn(prompt: str) -> str:
            recorded_prompts.append(prompt)
            return next(inputs)

        run_repl(
            EchoMode(),
            prompt=">> ",
            input_fn=_input_fn,
            output_fn=lambda _line: None,
        )
        assert recorded_prompts == [">> "]

    def test_default_prompt_value(self) -> None:
        assert DEFAULT_PROMPT == "sicode> "

    def test_custom_mode_via_dip(self) -> None:
        # 새로운 모드를 추가해도 REPL 코드 변경 없이 동작한다 (OCP).
        class UpperMode(BaseMode):
            name = "upper"

            def handle(self, user_input: str) -> str:
                return user_input.upper()

        outputs = run_repl_with_inputs(UpperMode(), ["hello"])
        assert "HELLO" in outputs

    def test_mode_returning_empty_string_suppresses_output(self) -> None:
        class SilentMode(BaseMode):
            name = "silent"

            def handle(self, user_input: str) -> str:
                return ""

        captured: List[str] = []
        inputs = iter(["please be silent", "exit"])
        run_repl(
            SilentMode(),
            input_fn=_make_input_fn(inputs),
            output_fn=captured.append,
        )
        # 빈 응답은 출력에 포함되지 않아야 한다.
        assert "please be silent" not in captured
