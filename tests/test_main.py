"""CLI 엔트리포인트(``sicode.main:main``) 통합 테스트.

실제 Ollama 서버 호출을 피하기 위해 ``main()`` 테스트들은 ``_select_mode`` 가
가벼운 :class:`EchoMode` 를 반환하도록 monkeypatch 한다 (DIP 활용).
"""

from __future__ import annotations

from typing import Iterator, List

import pytest

import sicode.main as main_module
from sicode.modes.ollama import OllamaMode
from tests.conftest import EchoMode


def _patch_input(monkeypatch: pytest.MonkeyPatch, inputs: List[str]) -> List[str]:
    iterator: Iterator[str] = iter(inputs)
    captured: List[str] = []

    def _fake_input(_prompt: str = "") -> str:
        return next(iterator)

    monkeypatch.setattr("builtins.input", _fake_input)
    monkeypatch.setattr(
        "builtins.print",
        lambda *args, **kwargs: captured.append(" ".join(str(a) for a in args)),
    )
    return captured


def _patch_select_mode_to_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_select_mode`` 가 EchoMode 를 반환하게 만들어 네트워크 호출을 차단한다."""
    monkeypatch.setattr(main_module, "_select_mode", lambda _argv: EchoMode())


class TestMainEntryPoint:
    def test_main_returns_zero_on_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_select_mode_to_echo(monkeypatch)
        captured = _patch_input(monkeypatch, ["hello", "exit"])
        exit_code = main_module.main([])
        assert exit_code == 0
        assert any("hello" in line for line in captured)

    def test_main_handles_eof(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_select_mode_to_echo(monkeypatch)
        called = {"count": 0}

        def _fake_input(_prompt: str = "") -> str:
            called["count"] += 1
            raise EOFError

        monkeypatch.setattr("builtins.input", _fake_input)
        monkeypatch.setattr("builtins.print", lambda *a, **k: None)
        assert main_module.main([]) == 0
        assert called["count"] == 1

    def test_main_handles_keyboard_interrupt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_select_mode_to_echo(monkeypatch)

        def _fake_input(_prompt: str = "") -> str:
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", _fake_input)
        monkeypatch.setattr("builtins.print", lambda *a, **k: None)
        assert main_module.main([]) == 0


class TestSelectModeDefaultsToOllama:
    def test_select_mode_returns_ollama_mode_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``simple`` 모드는 제거되었고 기본 모드는 ``ollama`` 가 된다.
        monkeypatch.delenv(main_module.ENV_OLLAMA_HOST, raising=False)
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)
        mode = main_module._select_mode([])
        assert isinstance(mode, OllamaMode)

    def test_ollama_is_the_only_registered_mode(self) -> None:
        # 레지스트리는 ``ollama`` 모드만 가지며 ``simple`` 은 등록되어 있지 않아야 한다.
        assert "simple" not in main_module.MODES
        assert list(main_module.MODES.keys()) == ["ollama"]
