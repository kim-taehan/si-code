"""SymbolCompleter / setup_readline_completer 단위 테스트(이슈 #20).

본 테스트는 실제 ``readline`` 모듈에 의존하지 않고 :class:`SymbolCompleter`
콜백을 직접 호출해 후보 산출 로직을 검증한다. ``setup_readline_completer`` 는
별도 테스트에서 readline 가짜 객체를 ``sys.modules`` 에 주입해 흐름만 확인한다.

설계 메모:
    - 테스트는 작고 의도가 명확한 fake resolver/registry 객체에만 의존한다 (DIP).
    - 통합 테스트는 ``test_main.py`` 통합 스위트가 이미 main 흐름을 커버하므로,
      본 모듈은 단위 수준의 콜백 시그니처/경계 케이스에 집중한다 (SRP).
"""

from __future__ import annotations

import sys
import types
from typing import List, Optional

import pytest

from sicode.symbols.completer import (
    COMPLETER_DELIMS,
    MAX_SYMBOL_CANDIDATES,
    SymbolCompleter,
    setup_readline_completer,
)
from sicode.symbols.indexer import SymbolRecord


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _record(name: str, kind: str = "class") -> SymbolRecord:
    """경로/라인은 의미 없는 더미를 채우고 이름만 의미 있는 SymbolRecord 생성."""
    return SymbolRecord(
        name=name,
        kind=kind,
        rel_path="dummy.py",
        start_line=1,
        end_line=1,
        source=f"class {name}:\n    pass\n",
    )


class FakeResolver:
    """``all_records()`` 만 노출하는 최소 리졸버 더블 (DIP/ISP)."""

    def __init__(self, records: List[SymbolRecord]) -> None:
        self._records = records

    def all_records(self) -> List[SymbolRecord]:
        return list(self._records)


class FakeCommand:
    """``name`` 속성만 있는 슬래시 명령 더블."""

    def __init__(self, name: str) -> None:
        self.name = name


class FakeRegistry:
    """``commands()`` 만 노출하는 슬래시 레지스트리 더블."""

    def __init__(self, names: List[str]) -> None:
        self._cmds = [FakeCommand(n) for n in names]

    def commands(self) -> List[FakeCommand]:
        return list(self._cmds)


# ---------------------------------------------------------------------------
# Symbol prefix 자동완성
# ---------------------------------------------------------------------------


class TestSymbolCompletionByPrefix:
    def test_returns_at_prefixed_match_for_state_zero(self) -> None:
        # Given: 인덱스에 ConversationMode / ConvergenceTool / Other 가 있다
        resolver = FakeResolver(
            [
                _record("ConversationMode"),
                _record("ConvergenceTool"),
                _record("Other"),
            ]
        )
        completer = SymbolCompleter(resolver)

        first = completer("@Conv", 0)
        assert first is not None
        assert first.startswith("@")
        # ConvergenceTool 이 알파벳 순으로 ConversationMode 보다 앞.
        assert first == "@ConvergenceTool"

    def test_returns_none_when_state_exceeds_candidates(self) -> None:
        resolver = FakeResolver([_record("ConversationMode")])
        completer = SymbolCompleter(resolver)

        assert completer("@Conv", 0) == "@ConversationMode"
        # 후보가 1건이므로 state=1 이면 None.
        assert completer("@Conv", 1) is None

    def test_returns_none_for_state_zero_when_no_match(self) -> None:
        resolver = FakeResolver([_record("Foo"), _record("Bar")])
        completer = SymbolCompleter(resolver)
        assert completer("@Zzz", 0) is None

    def test_results_are_sorted_alphabetically(self) -> None:
        resolver = FakeResolver(
            [
                _record("Zeta"),
                _record("Alpha"),
                _record("Mu"),
            ]
        )
        completer = SymbolCompleter(resolver)
        # 접두사 ``@`` 만 입력 → 모든 심볼이 후보.
        results: List[str] = []
        idx = 0
        while True:
            value = completer("@", idx)
            if value is None:
                break
            results.append(value)
            idx += 1
        assert results == ["@Alpha", "@Mu", "@Zeta"]

    def test_prefix_match_is_case_sensitive(self) -> None:
        # 이슈 본문 정책: prefix 일치(대소문자 구분).
        resolver = FakeResolver([_record("ConversationMode")])
        completer = SymbolCompleter(resolver)
        # 소문자로 시작하는 prefix 는 매치되지 않는다.
        assert completer("@conv", 0) is None

    def test_duplicate_names_are_collapsed(self) -> None:
        # 같은 이름이 두 파일에 존재해도 후보 한 건만.
        rec_a = SymbolRecord(
            name="DupName",
            kind="class",
            rel_path="a.py",
            start_line=1,
            end_line=2,
            source="",
        )
        rec_b = SymbolRecord(
            name="DupName",
            kind="class",
            rel_path="b.py",
            start_line=1,
            end_line=2,
            source="",
        )
        resolver = FakeResolver([rec_a, rec_b])
        completer = SymbolCompleter(resolver)

        assert completer("@Dup", 0) == "@DupName"
        assert completer("@Dup", 1) is None

    def test_caps_candidates_at_twenty(self) -> None:
        # 25개의 같은 prefix 심볼이 있어도 20건만 노출.
        records = [_record(f"Foo{i:02d}") for i in range(25)]
        resolver = FakeResolver(records)
        completer = SymbolCompleter(resolver)

        results: List[str] = []
        idx = 0
        while True:
            value = completer("@Foo", idx)
            if value is None:
                break
            results.append(value)
            idx += 1
        assert len(results) == MAX_SYMBOL_CANDIDATES
        # 정렬 안정성: 첫 후보는 알파벳상 가장 작은 Foo00.
        assert results[0] == "@Foo00"
        assert results[-1] == "@Foo19"


# ---------------------------------------------------------------------------
# Slash command 자동완성
# ---------------------------------------------------------------------------


class TestSlashCompletion:
    def test_slash_prefix_returns_command_name(self) -> None:
        registry = FakeRegistry(["clear", "exit", "help"])
        completer = SymbolCompleter(FakeResolver([]), registry=registry)

        assert completer("/cl", 0) == "/clear"
        assert completer("/cl", 1) is None

    def test_slash_only_lists_all_commands_alphabetically(self) -> None:
        registry = FakeRegistry(["help", "exit", "clear"])
        completer = SymbolCompleter(FakeResolver([]), registry=registry)

        results: List[str] = []
        idx = 0
        while True:
            value = completer("/", idx)
            if value is None:
                break
            results.append(value)
            idx += 1
        assert results == ["/clear", "/exit", "/help"]

    def test_slash_no_match_returns_none(self) -> None:
        registry = FakeRegistry(["clear", "exit"])
        completer = SymbolCompleter(FakeResolver([]), registry=registry)
        assert completer("/zzz", 0) is None

    def test_slash_without_registry_returns_none(self) -> None:
        # 레지스트리 미주입 시 슬래시 자동완성은 비활성.
        completer = SymbolCompleter(FakeResolver([]))
        assert completer("/cl", 0) is None


# ---------------------------------------------------------------------------
# 일반 텍스트 / 캐시 동작
# ---------------------------------------------------------------------------


class TestCallableContract:
    def test_plain_text_returns_none(self) -> None:
        # ``@`` 도 ``/`` 도 아닌 일반 텍스트는 후보 0건.
        resolver = FakeResolver([_record("Foo")])
        completer = SymbolCompleter(resolver)
        assert completer("hello", 0) is None

    def test_state_indices_are_iterated_until_none(self) -> None:
        resolver = FakeResolver([_record("Foo"), _record("Bar")])
        completer = SymbolCompleter(resolver)

        # ``@`` → 모든 후보 노출.
        assert completer("@", 0) == "@Bar"
        assert completer("@", 1) == "@Foo"
        assert completer("@", 2) is None

    def test_changing_text_recomputes_candidates(self) -> None:
        resolver = FakeResolver([_record("Foo"), _record("Bar")])
        completer = SymbolCompleter(resolver)

        # state=0 호출이 캐시를 갱신해야 한다.
        assert completer("@F", 0) == "@Foo"
        assert completer("@B", 0) == "@Bar"
        # 새 text 의 첫 호출은 새 후보 리스트로 재계산되어야 함.
        assert completer("@B", 1) is None

    def test_negative_state_returns_none(self) -> None:
        resolver = FakeResolver([_record("Foo")])
        completer = SymbolCompleter(resolver)
        # 비정상 state 입력은 None 으로 흡수한다.
        completer("@F", 0)
        assert completer("@F", -1) is None


# ---------------------------------------------------------------------------
# setup_readline_completer 동작
# ---------------------------------------------------------------------------


class _FakeReadline:
    """``readline`` 모듈 인터페이스를 흉내내는 더블."""

    def __init__(self, *, fail: bool = False) -> None:
        self.completer: Optional[object] = None
        self.delims: Optional[str] = None
        self.bind_args: List[str] = []
        self._fail = fail

    def set_completer(self, completer: object) -> None:
        if self._fail:
            raise RuntimeError("readline init failed")
        self.completer = completer

    def set_completer_delims(self, delims: str) -> None:
        self.delims = delims

    def parse_and_bind(self, command: str) -> None:
        self.bind_args.append(command)


class TestSetupReadline:
    def test_returns_false_when_readline_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # readline 임포트가 ImportError 를 던지도록 만든다.
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def _failing_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "readline":
                raise ImportError("readline not available in this environment")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", _failing_import)

        completer = SymbolCompleter(FakeResolver([]))
        # 예외가 밖으로 새지 않아야 한다.
        ok = setup_readline_completer(completer)
        assert ok is False

    def test_registers_completer_when_readline_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeReadline()
        monkeypatch.setitem(sys.modules, "readline", fake)  # type: ignore[arg-type]

        completer = SymbolCompleter(FakeResolver([]))
        ok = setup_readline_completer(completer)

        assert ok is True
        assert fake.completer is completer
        assert fake.delims == COMPLETER_DELIMS
        assert "tab: complete" in fake.bind_args

    def test_returns_false_when_initialization_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeReadline(fail=True)
        monkeypatch.setitem(sys.modules, "readline", fake)  # type: ignore[arg-type]

        completer = SymbolCompleter(FakeResolver([]))
        # readline 호출 도중 예외가 발생해도 외부로 던지지 않는다.
        ok = setup_readline_completer(completer)
        assert ok is False

    def test_completer_delims_does_not_strip_at_or_slash(self) -> None:
        # ``@`` 와 ``/`` 는 토큰 본체 — delims 는 공백/탭/개행만 허용.
        assert "@" not in COMPLETER_DELIMS
        assert "/" not in COMPLETER_DELIMS
        assert " " in COMPLETER_DELIMS
        assert "\t" in COMPLETER_DELIMS
        assert "\n" in COMPLETER_DELIMS


# ---------------------------------------------------------------------------
# 통합: SymbolResolver 와 함께 (캐시 일관성)
# ---------------------------------------------------------------------------


class TestWithRealSymbolResolver:
    """실제 :class:`SymbolResolver` + in-memory 인덱서로 통합 동작 확인."""

    def test_completer_uses_resolver_cache_via_all_records(self) -> None:
        from sicode.symbols.resolver import SymbolResolver

        class StaticIndexer:
            """``build()`` 가 고정 레코드 리스트를 돌려주는 in-memory 인덱서."""

            def __init__(self, records: List[SymbolRecord]) -> None:
                self._records = records
                self.build_calls = 0

            def build(self) -> List[SymbolRecord]:
                self.build_calls += 1
                return list(self._records)

        records = [_record("Alpha"), _record("Beta"), _record("Gamma")]
        indexer = StaticIndexer(records)
        resolver = SymbolResolver(indexer=indexer)
        completer = SymbolCompleter(resolver)

        # 첫 호출이 lazy 인덱싱을 트리거.
        assert completer("@A", 0) == "@Alpha"
        # 두 번째 호출은 캐시 히트 — build() 가 추가 호출되지 않아야 한다.
        assert completer("@B", 0) == "@Beta"
        assert indexer.build_calls == 1

        # invalidate 후 다음 호출은 재인덱싱.
        resolver.invalidate()
        assert completer("@G", 0) == "@Gamma"
        assert indexer.build_calls == 2
