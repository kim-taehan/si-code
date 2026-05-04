"""``!cmd`` REPL 분기 통합 테스트.

REPL 의 ``!`` 분기가:
- ``mode.handle`` 을 호출하지 않는다(LLM/멀티턴 히스토리 미오염).
- 슬래시 명령 디스패처를 호출하지 않는다.
- 출력을 한 번 ``output_fn`` 으로 흘려보낸다.
- 안내 메시지(``!`` 만 입력) 후에도 REPL 이 종료되지 않는다.
- ``build_welcome_message`` / ``/help`` 출력에 사용 안내가 포함된다.

본 테스트는 :func:`subprocess.run` 을 mock 으로 교체해 외부 명령을 실제로
실행하지 않는다.
"""

from __future__ import annotations

import subprocess
from typing import Any
from unittest import mock

from sicode.bang import executor as bang_executor
from sicode.commands import register_default_commands
from sicode.modes.conversation import Conversation
from sicode.repl import build_welcome_message, run_repl_with_inputs
from tests.conftest import EchoMode


class _FakeCompleted:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patched_run(stdout: str = "", stderr: str = "", returncode: int = 0):  # type: ignore[no-untyped-def]
    """``subprocess.run`` 을 고정 결과로 대체하는 컨텍스트 매니저."""

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompleted:
        return _FakeCompleted(stdout=stdout, stderr=stderr, returncode=returncode)

    return mock.patch.object(bang_executor.subprocess, "run", side_effect=fake_run)


class TestReplBangBranch:
    def test_bang_does_not_invoke_mode_handle(self) -> None:
        register_default_commands()
        mode = EchoMode()
        with _patched_run(stdout="files\n"):
            outputs = run_repl_with_inputs(mode, ["!ls", "hello"])
        # !ls 는 mode 로 가지 않는다. 'hello' 만 mode 에 도달.
        assert mode.calls == ["hello"]
        joined = "\n".join(outputs)
        assert "files" in joined

    def test_bang_outputs_stdout_only_for_zero_exit(self) -> None:
        mode = EchoMode()
        with _patched_run(stdout="hello-from-shell\n", stderr="", returncode=0):
            outputs = run_repl_with_inputs(mode, ["!echo hi"])
        joined = "\n".join(outputs)
        assert "hello-from-shell" in joined
        # 0 종료에서는 exit 코드 줄이 없다.
        assert "[exit code:" not in joined

    def test_bang_outputs_stderr_with_prefix_and_exit_code(self) -> None:
        mode = EchoMode()
        with _patched_run(stdout="", stderr="boom\n", returncode=1):
            outputs = run_repl_with_inputs(mode, ["!fails"])
        joined = "\n".join(outputs)
        assert "[stderr] boom" in joined
        assert "[exit code: 1]" in joined

    def test_bang_timeout_keeps_repl_alive(
        self, monkeypatch: "Any"
    ) -> None:
        # 짧은 타임아웃으로 mock 을 강제 발동.
        monkeypatch.setenv("SICODE_BANG_TIMEOUT", "2")
        mode = EchoMode()

        def fake_run(*args: Any, **kwargs: Any) -> _FakeCompleted:
            raise subprocess.TimeoutExpired(cmd="sleep 99", timeout=2)

        with mock.patch.object(bang_executor.subprocess, "run", side_effect=fake_run):
            outputs = run_repl_with_inputs(mode, ["!sleep 99", "after-timeout"])

        joined = "\n".join(outputs)
        assert "타임아웃" in joined
        # REPL 이 계속 동작했는지: 다음 입력이 mode 에 도달했는지로 확인
        assert mode.calls == ["after-timeout"]

    def test_bang_only_produces_help_message(self) -> None:
        mode = EchoMode()
        # subprocess 가 호출되지 않아야 하므로 mock 으로 fail-fast 셋업
        with mock.patch.object(
            bang_executor.subprocess,
            "run",
            side_effect=AssertionError("subprocess.run must not be called"),
        ):
            outputs = run_repl_with_inputs(mode, ["!"])
        joined = "\n".join(outputs)
        assert "실행할 명령" in joined
        assert "!ls" in joined
        # mode.handle 도 호출되지 않음
        assert mode.calls == []

    def test_bang_with_only_whitespace_produces_help_message(self) -> None:
        mode = EchoMode()
        with mock.patch.object(
            bang_executor.subprocess,
            "run",
            side_effect=AssertionError("subprocess.run must not be called"),
        ):
            outputs = run_repl_with_inputs(mode, ["!   "])
        joined = "\n".join(outputs)
        assert "실행할 명령" in joined
        assert mode.calls == []

    def test_bang_does_not_modify_conversation_history(self) -> None:
        """OllamaChat (멀티턴) 모드의 history 가 ``!`` 명령으로 변하지 않아야 한다."""
        conv = Conversation()
        conv.add_user("first")
        conv.add_assistant("answer")
        before = list(conv.messages())  # 스냅샷

        # bang 분기는 conversation 을 건드리지 않으므로, 직접 handle_bang_input 호출
        # 검증과 REPL 통합 검증 양쪽이 모두 의미가 있다. 여기서는 REPL 통합으로
        # 검증한다 — EchoMode 에는 conversation 이 없으므로 별도 가짜 모드를 사용.
        from sicode.modes.base import BaseMode

        class _ModeWithConv(BaseMode):
            name = "with-conv"

            def __init__(self, conversation: Conversation) -> None:
                self.conversation = conversation
                self.handle_calls: list[str] = []

            def handle(self, user_input: str) -> str:
                # 실제 OllamaMode 의 동작을 흉내내기 위해 conversation 에 누적
                self.conversation.add_user(user_input)
                self.conversation.add_assistant(f"echo:{user_input}")
                self.handle_calls.append(user_input)
                return f"echo:{user_input}"

        mode = _ModeWithConv(conv)

        with _patched_run(stdout="ok\n"):
            run_repl_with_inputs(mode, ["!ls", "!pwd"])

        # 두 번의 ``!`` 명령 후에도 conversation 의 messages 는 변하지 않아야 한다.
        assert conv.messages() == before
        # 또한 mode.handle 자체가 호출되지 않아야 한다.
        assert mode.handle_calls == []


class TestWelcomeAndHelpAdvertiseBang:
    def test_welcome_message_advertises_bang_prefix(self) -> None:
        mode = EchoMode()
        msg = build_welcome_message(mode)
        assert "!cmd" in msg
        assert "!ls" in msg or "!git" in msg
        # 보안 안내 문구
        assert "주의" in msg or "직접 실행" in msg

    def test_help_output_includes_bang_section(self) -> None:
        register_default_commands()
        mode = EchoMode()
        outputs = run_repl_with_inputs(mode, ["/help"])
        joined = "\n".join(outputs)
        assert "!" in joined
        # !cmd 또는 !<cmd> 형태로 표기되어야 한다
        assert "!<cmd>" in joined or "!cmd" in joined
        # 보안 주의 문구
        assert "주의" in joined or "직접 실행" in joined
