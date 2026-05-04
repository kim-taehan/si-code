"""SymbolResolver 의 파일 매칭(이슈 #24) 단위 테스트.

기존 :mod:`tests.symbols.test_resolver` 는 심볼 매칭 동작에 집중한다. 본 모듈은
이슈 #24 에서 추가된 ``find_files`` / ``all_files`` / 통합 캐시 무효화 동작을
검증한다.
"""

from __future__ import annotations

from typing import List

from sicode.symbols.indexer import FileRecord, IndexedRecord, SymbolRecord
from sicode.symbols.resolver import SymbolResolver


class _MixedIndexer:
    """심볼/파일 레코드를 함께 돌려주는 in-memory 인덱서."""

    def __init__(self, records: List[IndexedRecord]) -> None:
        self._records = list(records)
        self.build_calls: int = 0

    def build(self) -> List[IndexedRecord]:
        self.build_calls += 1
        return list(self._records)


def _file(rel_path: str, *, name: str = "", **flags: object) -> FileRecord:
    return FileRecord(
        name=name or rel_path.rsplit("/", 1)[-1],
        rel_path=rel_path,
        kind="file",
        start_line=1,
        snippet="",
        size_bytes=int(flags.get("size_bytes", 0) or 0),
        is_binary=bool(flags.get("is_binary", False)),
        is_oversize=bool(flags.get("is_oversize", False)),
    )


def _symbol(name: str, *, rel_path: str = "m.py") -> SymbolRecord:
    return SymbolRecord(
        name=name,
        kind="class",
        rel_path=rel_path,
        start_line=1,
        end_line=2,
        source=f"class {name}:\n    pass\n",
    )


class TestFindFilesExactMatch:
    def test_exact_rel_path_match(self) -> None:
        resolver = SymbolResolver(
            _MixedIndexer([_file("sicode/repl.py"), _file("sicode/main.py")])
        )
        result = [r.rel_path for r in resolver.find_files("sicode/repl.py")]
        assert result == ["sicode/repl.py"]

    def test_exact_name_match(self) -> None:
        resolver = SymbolResolver(
            _MixedIndexer([_file("README.md"), _file("docs/README.md")])
        )
        result = sorted(r.rel_path for r in resolver.find_files("README.md"))
        assert result == ["README.md", "docs/README.md"]

    def test_extension_omission_returns_completion(self) -> None:
        # ``@README`` 입력 → ``README.md`` 매칭.
        resolver = SymbolResolver(_MixedIndexer([_file("README.md")]))
        result = [r.rel_path for r in resolver.find_files("README")]
        assert result == ["README.md"]

    def test_no_match_returns_empty(self) -> None:
        resolver = SymbolResolver(_MixedIndexer([_file("README.md")]))
        assert resolver.find_files("does_not_exist") == []

    def test_empty_token_returns_empty(self) -> None:
        resolver = SymbolResolver(_MixedIndexer([_file("README.md")]))
        assert resolver.find_files("") == []


class TestFindFilesPrefix:
    def test_segment_prefix_in_path(self) -> None:
        # ``@sicode/r`` → ``sicode/repl.py``
        resolver = SymbolResolver(
            _MixedIndexer(
                [_file("sicode/repl.py"), _file("sicode/main.py")]
            )
        )
        result = sorted(r.rel_path for r in resolver.find_files("sicode/r"))
        assert result == ["sicode/repl.py"]

    def test_name_prefix_in_root_files(self) -> None:
        resolver = SymbolResolver(
            _MixedIndexer([_file("README.md"), _file("ROADMAP.md")])
        )
        result = sorted(r.rel_path for r in resolver.find_files("RO"))
        assert result == ["ROADMAP.md"]

    def test_case_insensitive_prefix(self) -> None:
        resolver = SymbolResolver(_MixedIndexer([_file("README.md")]))
        result = [r.rel_path for r in resolver.find_files("readme.m")]
        # 정확 매치 / 확장자 보정 모두 실패 → 대소문자 무시 prefix.
        assert result == ["README.md"]


class TestAllRecordsAndCaching:
    def test_all_records_includes_files_and_symbols(self) -> None:
        records: List[IndexedRecord] = [
            _symbol("Foo"),
            _file("README.md"),
        ]
        resolver = SymbolResolver(_MixedIndexer(records))
        result = resolver.all_records()
        assert len(result) == 2
        assert any(isinstance(r, SymbolRecord) for r in result)
        assert any(isinstance(r, FileRecord) for r in result)

    def test_all_files_filters_to_file_records(self) -> None:
        records: List[IndexedRecord] = [
            _symbol("Foo"),
            _file("README.md"),
            _file("main.py"),
        ]
        resolver = SymbolResolver(_MixedIndexer(records))
        files = resolver.all_files()
        assert sorted(f.name for f in files) == ["README.md", "main.py"]

    def test_all_symbols_filters_to_symbol_records(self) -> None:
        records: List[IndexedRecord] = [
            _symbol("Foo"),
            _file("README.md"),
        ]
        resolver = SymbolResolver(_MixedIndexer(records))
        symbols = resolver.all_symbols()
        assert [s.name for s in symbols] == ["Foo"]

    def test_invalidate_clears_both_indexes(self) -> None:
        indexer = _MixedIndexer([_symbol("Foo"), _file("README.md")])
        resolver = SymbolResolver(indexer)
        resolver.find("Foo")
        resolver.find_files("README")
        assert indexer.build_calls == 1

        resolver.invalidate()
        resolver.find("Foo")
        # 무효화 후 build 가 다시 1회 호출.
        assert indexer.build_calls == 2
        # 이후 find_files 는 같은 캐시를 공유.
        resolver.find_files("README")
        assert indexer.build_calls == 2

    def test_find_does_not_match_file_records(self) -> None:
        # ``find`` 는 심볼 결과만 반환해야 한다(이슈 #24 호환 정책).
        resolver = SymbolResolver(
            _MixedIndexer([_file("Foo.py", name="Foo.py")])
        )
        # ``Foo.py`` 라는 파일이 있어도 심볼 ``Foo`` 매칭은 없으므로 빈 결과.
        assert resolver.find("Foo") == []
