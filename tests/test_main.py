"""CLI 엔트리포인트(``sicode.main:main``) 통합 테스트."""

from __future__ import annotations

from typing import Iterator, List

import pytest

import sicode.main as main_module
from sicode.modes.simple import SimpleMode


def _patch_input(monkeypatch: pytest.MonkeyPatch, inputs: List[str]) -> List[str]:
    iterator: Iterator[str] = iter(inputs)
    captured: List[str] = []

    def _fake_input(_prompt: str = "") -> str:
        return next(iterator)

    monkeypatch.setattr("builtins.input", _fake_input)
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: captured.append(" ".join(str(a) for a in args)))
    return captured


class TestMainEntryPoint:
    def test_main_returns_zero_on_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _patch_input(monkeypatch, ["hello", "exit"])
        exit_code = main_module.main([])
        assert exit_code == 0
        assert any("hello" in line for line in captured)

    def test_main_handles_eof(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 입력이 소진되면 next() 가 StopIteration 을 던지므로 EOFError 로 변환해 준다.
        called = {"count": 0}

        def _fake_input(_prompt: str = "") -> str:
            called["count"] += 1
            raise EOFError

        monkeypatch.setattr("builtins.input", _fake_input)
        monkeypatch.setattr("builtins.print", lambda *a, **k: None)
        assert main_module.main([]) == 0
        assert called["count"] == 1

    def test_main_handles_keyboard_interrupt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fake_input(_prompt: str = "") -> str:
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", _fake_input)
        monkeypatch.setattr("builtins.print", lambda *a, **k: None)
        assert main_module.main([]) == 0

    def test_select_mode_returns_simple_mode(self) -> None:
        # 현재는 항상 SimpleMode 를 선택해야 한다.
        mode = main_module._select_mode([])
        assert isinstance(mode, SimpleMode)
