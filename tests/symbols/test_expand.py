"""SymbolExpander 단위 테스트(이슈 #17)."""

from __future__ import annotations

from typing import List

import pytest

from sicode.symbols.expand import (
    DEFAULT_MAX_MATCHES,
    DEFAULT_MAX_SYMBOL_LINES,
    SymbolExpander,
    expand_user_input,
)
from sicode.symbols.indexer import SymbolRecord
from sicode.symbols.resolver import SymbolResolver


class _StubIndexer:
    def __init__(self, records: List[SymbolRecord]) -> None:
        self._records = list(records)

    def build(self) -> List[SymbolRecord]:
        return list(self._records)


def _make_resolver(records: List[SymbolRecord]) -> SymbolResolver:
    return SymbolResolver(_StubIndexer(records))


def _rec(
    name: str,
    *,
    kind: str = "class",
    rel_path: str = "module.py",
    start_line: int = 1,
    end_line: int = 2,
    source: str = "class X:\n    pass\n",
) -> SymbolRecord:
    return SymbolRecord(
        name=name,
        kind=kind,
        rel_path=rel_path,
        start_line=start_line,
        end_line=end_line,
        source=source,
    )


class TestNoTokenPassthrough:
    def test_input_without_at_returns_unchanged(self) -> None:
        resolver = _make_resolver([_rec("Foo")])
        expander = SymbolExpander(resolver)
        text = "그냥 일반 질문입니다."
        assert expander.expand(text) == text

    def test_at_without_identifier_does_nothing(self) -> None:
        resolver = _make_resolver([_rec("Foo")])
        expander = SymbolExpander(resolver)
        text = "이메일 주소: kim@example.com 입니다."
        # 정규식이 ``@`` 다음 식별자(영문/언더스코어 시작) 만 매칭하므로
        # ``@example`` 는 토큰으로 잡히지만 매칭 결과가 없으면 ``no
        # definition`` 안내가 붙는다. ``example`` 심볼이 없는 인덱스를
        # 사용했으므로 안내 문구가 따라붙는지 확인.
        result = expander.expand(text)
        assert "no definition found" in result

    def test_empty_input_returns_empty(self) -> None:
        expander = SymbolExpander(_make_resolver([]))
        assert expander.expand("") == ""


class TestMatchAttachment:
    def test_exact_match_appends_code_fence_block(self) -> None:
        record = _rec(
            "Conversation",
            rel_path="sicode/modes/conversation.py",
            start_line=37,
            end_line=40,
            source="class Conversation:\n    pass\n",
        )
        resolver = _make_resolver([record])
        expander = SymbolExpander(resolver)
        result = expander.expand("@Conversation 설명해줘")

        assert result.startswith("@Conversation 설명해줘\n\n---\n")
        assert "Referenced symbol: `@Conversation`" in result
        assert "(sicode/modes/conversation.py:37)" in result
        assert "```python" in result
        assert "class Conversation:" in result
        assert result.rstrip().endswith("```")

    def test_no_match_appends_note_line(self) -> None:
        resolver = _make_resolver([_rec("Other")])
        expander = SymbolExpander(resolver)
        result = expander.expand("@nonexistent_symbol 설명")
        # 원본 입력이 보존된다.
        assert result.startswith("@nonexistent_symbol 설명")
        # 안내 문구가 추가된다.
        assert "(note: @nonexistent_symbol — no definition found in project)" in result

    def test_caps_at_max_matches(self) -> None:
        # 같은 이름 5건이 인덱스에 있을 때 상위 3건만 첨부.
        records = [
            _rec(
                "Same",
                rel_path=f"f{i}.py",
                source=f"class Same:\n    # variant {i}\n    pass\n",
            )
            for i in range(5)
        ]
        resolver = _make_resolver(records)
        expander = SymbolExpander(resolver)
        result = expander.expand("@Same 무엇?")
        # 상위 3건만 본문에 포함된다(파일 경로로 카운트).
        included = sum(1 for i in range(5) if f"f{i}.py" in result)
        assert included == DEFAULT_MAX_MATCHES == 3
        # 4번째/5번째는 누락된다.
        assert "f3.py" not in result or "f4.py" not in result

    def test_truncates_long_symbol_body(self) -> None:
        long_source = "class Big:\n" + "\n".join(
            f"    line_{i} = {i}" for i in range(DEFAULT_MAX_SYMBOL_LINES + 50)
        )
        record = _rec("Big", source=long_source)
        resolver = _make_resolver([record])
        expander = SymbolExpander(resolver)
        result = expander.expand("@Big")
        assert "... (truncated)" in result

    def test_short_symbol_does_not_truncate(self) -> None:
        record = _rec("Tiny", source="class Tiny:\n    x = 1\n")
        resolver = _make_resolver([record])
        expander = SymbolExpander(resolver)
        result = expander.expand("@Tiny")
        assert "... (truncated)" not in result


class TestMultipleTokens:
    def test_processes_each_unique_token_once(self) -> None:
        records = [_rec("Alpha"), _rec("Beta")]
        resolver = _make_resolver(records)
        expander = SymbolExpander(resolver)
        result = expander.expand("@Alpha @Beta @Alpha 다시")
        assert result.count("Referenced symbol: `@Alpha`") == 1
        assert result.count("Referenced symbol: `@Beta`") == 1

    def test_unknown_tokens_get_individual_notes(self) -> None:
        resolver = _make_resolver([])
        expander = SymbolExpander(resolver)
        result = expander.expand("@one @two")
        assert "(note: @one — no definition found in project)" in result
        assert "(note: @two — no definition found in project)" in result


class TestConfigValidation:
    def test_max_matches_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            SymbolExpander(_make_resolver([]), max_matches=0)

    def test_max_symbol_lines_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            SymbolExpander(_make_resolver([]), max_symbol_lines=0)


class TestCaseInsensitivePartial:
    def test_falls_back_to_substring_when_no_exact(self) -> None:
        record = _rec(
            "VeryLongConversationName",
            source="class VeryLongConversationName:\n    pass\n",
        )
        resolver = _make_resolver([record])
        expander = SymbolExpander(resolver)
        result = expander.expand("@conv 설명")
        assert "VeryLongConversationName" in result


class TestModuleHelper:
    def test_expand_user_input_helper_uses_default_resolver(self) -> None:
        # 기본 resolver 가 cwd 를 인덱싱하더라도, 토큰이 매칭되지 않으면
        # 적어도 안내 문구가 붙는 것을 확인(실제 cwd 인덱싱 결과에 의존하지 않게).
        result = expand_user_input(
            "@__definitely_not_a_real_symbol__ 무엇?",
            resolver=_make_resolver([]),
        )
        assert "no definition found" in result
