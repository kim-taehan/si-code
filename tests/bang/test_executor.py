"""``sicode.bang.executor`` 단위 테스트.

테스트는 :func:`subprocess.run` 을 mock 으로 교체해 외부 프로세스를 실제로 띄우지
않는다. 또한 :func:`handle_bang_input` 은 ``runner`` 콜러블 주입을 받으므로
서브프로세스 호출 자체를 우회한 합성 테스트도 함께 둔다 (DIP).

검증 항목 (이슈 #18 수용 기준 매핑):
    - ``run_shell`` 정상 실행 / 종료 코드 / stdout 캡처
    - stderr 출력
    - 비-0 종료 코드의 ``[exit code: N]`` 표시
    - 타임아웃 메시지
    - ``shell=True``, ``stdin=DEVNULL``, ``capture_output``, ``text=True``,
      ``cwd=Path.cwd()`` 인자 전달
    - 빈 ``!`` 입력 안내
    - 환경 변수 ``SICODE_BANG_TIMEOUT`` 해석
    - 출력 형식 합성 (stdout/stderr 동시, 0 종료시 exit 라인 없음)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from sicode.bang import executor as bang_executor
from sicode.bang.executor import (
    DEFAULT_TIMEOUT_SECONDS,
    TIMEOUT_ENV_VAR,
    BangResult,
    BangTimeout,
    format_bang_output,
    handle_bang_input,
    is_bang_input,
    resolve_timeout_seconds,
    run_shell,
)


# ---------------------------------------------------------------------------- helpers


class _FakeCompleted:
    """``subprocess.run`` 반환을 흉내내는 단순 객체."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# ---------------------------------------------------------------------------- is_bang_input


class TestIsBangInput:
    def test_starts_with_bang(self) -> None:
        assert is_bang_input("!ls") is True

    def test_with_leading_whitespace(self) -> None:
        assert is_bang_input("   !pwd") is True

    def test_plain_text_is_not_bang(self) -> None:
        assert is_bang_input("hello") is False

    def test_slash_is_not_bang(self) -> None:
        assert is_bang_input("/help") is False

    def test_empty_string_is_not_bang(self) -> None:
        assert is_bang_input("") is False

    def test_bang_alone_is_bang(self) -> None:
        # 빈 명령 안내 분기는 handle_bang_input 이 처리한다.
        assert is_bang_input("!") is True


# ---------------------------------------------------------------------------- resolve_timeout_seconds


class TestResolveTimeoutSeconds:
    def test_unset_returns_default(self) -> None:
        env: dict[str, str] = {}
        assert resolve_timeout_seconds(env) == DEFAULT_TIMEOUT_SECONDS  # type: ignore[arg-type]

    def test_valid_int_string(self) -> None:
        env = {TIMEOUT_ENV_VAR: "5"}
        assert resolve_timeout_seconds(env) == 5  # type: ignore[arg-type]

    def test_whitespace_padded_int(self) -> None:
        env = {TIMEOUT_ENV_VAR: "  10 "}
        assert resolve_timeout_seconds(env) == 10  # type: ignore[arg-type]

    def test_empty_string_falls_back_to_default(self) -> None:
        env = {TIMEOUT_ENV_VAR: ""}
        assert resolve_timeout_seconds(env) == DEFAULT_TIMEOUT_SECONDS  # type: ignore[arg-type]

    def test_invalid_string_falls_back_to_default(self) -> None:
        env = {TIMEOUT_ENV_VAR: "not-a-number"}
        assert resolve_timeout_seconds(env) == DEFAULT_TIMEOUT_SECONDS  # type: ignore[arg-type]

    def test_zero_falls_back_to_default(self) -> None:
        env = {TIMEOUT_ENV_VAR: "0"}
        assert resolve_timeout_seconds(env) == DEFAULT_TIMEOUT_SECONDS  # type: ignore[arg-type]

    def test_negative_falls_back_to_default(self) -> None:
        env = {TIMEOUT_ENV_VAR: "-7"}
        assert resolve_timeout_seconds(env) == DEFAULT_TIMEOUT_SECONDS  # type: ignore[arg-type]

    def test_default_uses_os_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TIMEOUT_ENV_VAR, "11")
        assert resolve_timeout_seconds() == 11

    def test_default_argument_override(self) -> None:
        env: dict[str, str] = {}
        assert resolve_timeout_seconds(env, default=99) == 99  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- run_shell


class TestRunShell:
    def test_invokes_subprocess_with_required_kwargs(self) -> None:
        captured: dict[str, Any] = {}

        def fake_run(cmd: str, **kwargs: Any) -> _FakeCompleted:
            captured["cmd"] = cmd
            captured.update(kwargs)
            return _FakeCompleted(stdout="ok\n", stderr="", returncode=0)

        with mock.patch.object(bang_executor.subprocess, "run", side_effect=fake_run):
            result = run_shell("echo ok", timeout=42)

        assert isinstance(result, BangResult)
        assert result.stdout == "ok\n"
        assert result.stderr == ""
        assert result.returncode == 0
        assert captured["cmd"] == "echo ok"
        assert captured["shell"] is True
        assert captured["capture_output"] is True
        assert captured["text"] is True
        assert captured["stdin"] is subprocess.DEVNULL
        assert captured["timeout"] == 42
        # cwd 는 현재 작업 디렉토리 문자열이어야 한다.
        assert captured["cwd"] == str(Path.cwd())

    def test_returns_bang_timeout_on_timeout_expired(self) -> None:
        def fake_run(*args: Any, **kwargs: Any) -> _FakeCompleted:
            raise subprocess.TimeoutExpired(cmd="sleep 99", timeout=1)

        with mock.patch.object(bang_executor.subprocess, "run", side_effect=fake_run):
            result = run_shell("sleep 99", timeout=1)

        assert isinstance(result, BangTimeout)
        assert result.timeout == 1

    def test_passes_through_nonzero_returncode(self) -> None:
        def fake_run(*args: Any, **kwargs: Any) -> _FakeCompleted:
            return _FakeCompleted(stdout="", stderr="bad\n", returncode=2)

        with mock.patch.object(bang_executor.subprocess, "run", side_effect=fake_run):
            result = run_shell("false", timeout=10)

        assert isinstance(result, BangResult)
        assert result.returncode == 2
        assert result.stderr == "bad\n"

    def test_real_echo_smoke(self) -> None:
        """실제 ``subprocess.run`` 으로 ``echo`` 한 번. 외부 모델/네트워크 의존 없음."""
        # macOS/Linux 환경 가정. echo 는 POSIX 표준이고 stdin 미요구.
        result = run_shell("echo hello-bang", timeout=5)
        assert isinstance(result, BangResult)
        assert result.returncode == 0
        assert "hello-bang" in result.stdout

    def test_real_stdin_devnull(self) -> None:
        """``stdin=DEVNULL`` 로 봉쇄되었음을 실제 명령으로 검증한다.

        ``cat`` 은 표준 입력 파일이 EOF 면 즉시 종료하고 빈 출력을 반환한다 —
        만약 stdin 이 봉쇄되지 않았다면 본 테스트가 행(hang) 걸린다.
        """
        result = run_shell("cat", timeout=5)
        assert isinstance(result, BangResult)
        assert result.returncode == 0
        assert result.stdout == ""


# ---------------------------------------------------------------------------- format_bang_output


class TestFormatBangOutput:
    def test_stdout_only_zero_exit_no_exit_line(self) -> None:
        out = format_bang_output(BangResult(stdout="hello\n", stderr="", returncode=0))
        assert out == "hello"

    def test_nonzero_exit_appends_exit_code_line(self) -> None:
        out = format_bang_output(BangResult(stdout="x\n", stderr="", returncode=3))
        assert out.endswith("[exit code: 3]")
        assert out.startswith("x")

    def test_stderr_lines_are_prefixed(self) -> None:
        out = format_bang_output(
            BangResult(stdout="", stderr="boom\nbang\n", returncode=1)
        )
        # 각 라인 앞에 [stderr] 접두사
        assert "[stderr] boom" in out
        assert "[stderr] bang" in out
        assert "[exit code: 1]" in out

    def test_stdout_and_stderr_both_present(self) -> None:
        out = format_bang_output(
            BangResult(stdout="ok\n", stderr="warn\n", returncode=0)
        )
        # 0 종료에서는 exit 라인 없음
        assert "[exit code:" not in out
        assert "ok" in out
        assert "[stderr] warn" in out

    def test_empty_result_produces_empty_string(self) -> None:
        out = format_bang_output(BangResult(stdout="", stderr="", returncode=0))
        assert out == ""

    def test_timeout_message(self) -> None:
        out = format_bang_output(BangTimeout(timeout=7))
        assert "타임아웃" in out
        assert "7s" in out
        assert "REPL" in out


# ---------------------------------------------------------------------------- handle_bang_input


class TestHandleBangInput:
    def test_empty_after_bang_returns_help_message(self) -> None:
        out = handle_bang_input("!")
        assert "!ls" in out
        assert "실행할 명령" in out

    def test_whitespace_only_after_bang_returns_help_message(self) -> None:
        out = handle_bang_input("!   ")
        assert "실행할 명령" in out

    def test_uses_injected_runner(self) -> None:
        calls: list[tuple[str, int]] = []

        def fake_runner(cmd: str, timeout: int) -> BangResult:
            calls.append((cmd, timeout))
            return BangResult(stdout="injected\n", stderr="", returncode=0)

        out = handle_bang_input("!echo hi", runner=fake_runner, timeout=8)
        assert calls == [("echo hi", 8)]
        assert "injected" in out

    def test_strips_leading_whitespace_before_bang(self) -> None:
        captured: list[str] = []

        def fake_runner(cmd: str, timeout: int) -> BangResult:
            captured.append(cmd)
            return BangResult(stdout="", stderr="", returncode=0)

        handle_bang_input("   !pwd", runner=fake_runner, timeout=5)
        assert captured == ["pwd"]

    def test_resolves_timeout_from_env_when_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(TIMEOUT_ENV_VAR, "13")
        seen: list[int] = []

        def fake_runner(cmd: str, timeout: int) -> BangResult:
            seen.append(timeout)
            return BangResult(stdout="", stderr="", returncode=0)

        handle_bang_input("!ls", runner=fake_runner)
        assert seen == [13]

    def test_propagates_timeout_message(self) -> None:
        def fake_runner(cmd: str, timeout: int) -> BangTimeout:
            return BangTimeout(timeout=timeout)

        out = handle_bang_input("!sleep 99", runner=fake_runner, timeout=4)
        assert "타임아웃" in out
        assert "4s" in out

    def test_nonzero_exit_renders_exit_code(self) -> None:
        def fake_runner(cmd: str, timeout: int) -> BangResult:
            return BangResult(stdout="", stderr="oops\n", returncode=2)

        out = handle_bang_input("!false", runner=fake_runner, timeout=5)
        assert "[stderr] oops" in out
        assert "[exit code: 2]" in out
