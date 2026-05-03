"""SymbolResolver 단위 테스트(이슈 #17)."""

from __future__ import annotations

from typing import List

from sicode.symbols.indexer import SymbolRecord
from sicode.symbols.resolver import SymbolResolver


class _StubIndexer:
    """``build`` 호출 횟수와 결과를 직접 제어하는 테스트 더블."""

    def __init__(self, records: List[SymbolRecord]) -> None:
        self._records = list(records)
        self.build_calls: int = 0

    def build(self) -> List[SymbolRecord]:
        self.build_calls += 1
        return list(self._records)


def _rec(name: str, *, kind: str = "class") -> SymbolRecord:
    return SymbolRecord(
        name=name,
        kind=kind,
        rel_path="m.py",
        start_line=1,
        end_line=2,
        source=f"class {name}:\n    pass\n",
    )


class TestExactMatchPriority:
    def test_returns_exact_match_only_when_present(self) -> None:
        indexer = _StubIndexer([_rec("Foo"), _rec("FooBar"), _rec("foo")])
        resolver = SymbolResolver(indexer)
        result = [r.name for r in resolver.find("Foo")]
        # 정확 일치 ``Foo`` 1건만 반환되고 ``FooBar`` / ``foo`` 는 포함되지 않는다.
        assert result == ["Foo"]

    def test_case_insensitive_substring_when_no_exact(self) -> None:
        indexer = _StubIndexer([_rec("LongFooName"), _rec("Bar")])
        resolver = SymbolResolver(indexer)
        result = [r.name for r in resolver.find("foo")]
        assert result == ["LongFooName"]

    def test_no_match_returns_empty_list(self) -> None:
        indexer = _StubIndexer([_rec("Foo")])
        resolver = SymbolResolver(indexer)
        assert resolver.find("nope") == []

    def test_empty_token_returns_empty(self) -> None:
        indexer = _StubIndexer([_rec("Foo")])
        resolver = SymbolResolver(indexer)
        assert resolver.find("") == []


class TestCaching:
    def test_first_find_triggers_build(self) -> None:
        indexer = _StubIndexer([_rec("Foo")])
        resolver = SymbolResolver(indexer)
        assert indexer.build_calls == 0
        resolver.find("Foo")
        assert indexer.build_calls == 1

    def test_second_find_uses_cache(self) -> None:
        indexer = _StubIndexer([_rec("Foo")])
        resolver = SymbolResolver(indexer)
        resolver.find("Foo")
        resolver.find("Foo")
        resolver.find("anything-else")
        assert indexer.build_calls == 1

    def test_invalidate_forces_rebuild_on_next_find(self) -> None:
        indexer = _StubIndexer([_rec("Foo")])
        resolver = SymbolResolver(indexer)
        resolver.find("Foo")
        resolver.invalidate()
        resolver.find("Foo")
        assert indexer.build_calls == 2

    def test_invalidate_without_prior_use_is_safe(self) -> None:
        resolver = SymbolResolver(_StubIndexer([]))
        # 한 번도 인덱싱 안 한 상태에서 invalidate 가 예외를 내지 않아야 한다.
        resolver.invalidate()
        assert resolver.find("anything") == []

    def test_all_records_returns_full_index(self) -> None:
        records = [_rec("A"), _rec("B"), _rec("C")]
        indexer = _StubIndexer(records)
        resolver = SymbolResolver(indexer)
        result = sorted(r.name for r in resolver.all_records())
        assert result == ["A", "B", "C"]


class TestMultipleMatchesAreReturnedAll:
    """초과 제한은 expander 가 적용한다 — resolver 는 모든 매칭을 반환한다."""

    def test_returns_all_exact_matches(self) -> None:
        # 같은 이름이 여러 파일에 존재하는 상황 모사.
        records = [
            SymbolRecord(
                name="Same",
                kind="class",
                rel_path=f"f{i}.py",
                start_line=1,
                end_line=2,
                source="class Same:\n    pass\n",
            )
            for i in range(5)
        ]
        indexer = _StubIndexer(records)
        resolver = SymbolResolver(indexer)
        assert len(resolver.find("Same")) == 5
