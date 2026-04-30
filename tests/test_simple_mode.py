"""SimpleMode 단위 테스트."""

from __future__ import annotations

import pytest

from sicode.modes.base import BaseMode
from sicode.modes.simple import SimpleMode


class TestSimpleMode:
    def test_handle_returns_input_as_is(self) -> None:
        mode = SimpleMode()
        assert mode.handle("hello world") == "hello world"

    def test_handle_returns_empty_for_empty_input(self) -> None:
        mode = SimpleMode()
        assert mode.handle("") == ""

    def test_handle_preserves_whitespace_and_unicode(self) -> None:
        mode = SimpleMode()
        assert mode.handle("  안녕  ") == "  안녕  "

    def test_simple_mode_is_a_basemode(self) -> None:
        # LSP: SimpleMode는 BaseMode 자리에 그대로 대입 가능해야 한다.
        assert isinstance(SimpleMode(), BaseMode)

    def test_name_attribute(self) -> None:
        assert SimpleMode().name == "simple"


class TestBaseModeContract:
    def test_basemode_cannot_be_instantiated_directly(self) -> None:
        with pytest.raises(TypeError):
            BaseMode()  # type: ignore[abstract]

    def test_subclass_must_implement_handle(self) -> None:
        class IncompleteMode(BaseMode):
            pass

        with pytest.raises(TypeError):
            IncompleteMode()  # type: ignore[abstract]
