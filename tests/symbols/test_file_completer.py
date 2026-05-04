"""SymbolCompleter 의 파일 후보 합산(이슈 #24) 단위 테스트."""

from __future__ import annotations

from typing import List

from sicode.symbols.completer import (
    MAX_CANDIDATES,
    MAX_FILE_CANDIDATES,
    MAX_SYMBOL_CANDIDATES,
    SymbolCompleter,
)
from sicode.symbols.indexer import FileRecord, IndexedRecord, SymbolRecord


def _file(rel_path: str) -> FileRecord:
    name = rel_path.rsplit("/", 1)[-1]
    return FileRecord(
        name=name,
        rel_path=rel_path,
        kind="file",
        start_line=1,
        snippet="",
        size_bytes=0,
        is_binary=False,
        is_oversize=False,
    )


def _symbol(name: str) -> SymbolRecord:
    return SymbolRecord(
        name=name,
        kind="class",
        rel_path="dummy.py",
        start_line=1,
        end_line=1,
        source=f"class {name}:\n    pass\n",
    )


class FakeResolver:
    def __init__(self, records: List[IndexedRecord]) -> None:
        self._records = records

    def all_records(self) -> List[IndexedRecord]:
        return list(self._records)


class TestFileCompletion:
    def test_root_file_prefix_returns_at_path(self) -> None:
        # 이슈 #24 수용 기준: ``@README`` 탭 → ``@README.md``
        resolver = FakeResolver([_file("README.md"), _file("docs/extra.md")])
        completer = SymbolCompleter(resolver)
        assert completer("@README", 0) == "@README.md"
        assert completer("@README", 1) is None

    def test_segment_prefix_in_subdirectory(self) -> None:
        # ``@sicode/r`` → ``@sicode/repl.py``
        resolver = FakeResolver(
            [_file("sicode/repl.py"), _file("sicode/main.py")]
        )
        completer = SymbolCompleter(resolver)
        assert completer("@sicode/r", 0) == "@sicode/repl.py"
        assert completer("@sicode/r", 1) is None

    def test_no_file_match_returns_none(self) -> None:
        resolver = FakeResolver([_file("README.md")])
        completer = SymbolCompleter(resolver)
        assert completer("@unknown_xyz", 0) is None


class TestFileAndSymbolMerge:
    def test_results_are_alphabetically_sorted_after_merge(self) -> None:
        resolver = FakeResolver(
            [
                _symbol("Beta"),
                _file("alpha.txt"),
                _file("zeta.md"),
            ]
        )
        completer = SymbolCompleter(resolver)
        # 빈 prefix → 모든 후보. ``@`` 기호 정렬로 알파벳 순.
        results: List[str] = []
        idx = 0
        while True:
            value = completer("@", idx)
            if value is None:
                break
            results.append(value)
            idx += 1
        # ``@Beta`` 는 대문자라 ASCII 순으로 ``@a-`` 보다 앞.
        assert results == sorted(results)
        assert "@Beta" in results
        assert "@alpha.txt" in results
        assert "@zeta.md" in results

    def test_total_results_capped_at_max_candidates(self) -> None:
        # 심볼 25 + 파일 25 → 합산 후 상한 :data:`MAX_CANDIDATES`.
        records: List[IndexedRecord] = [
            _symbol(f"Sym{i:02d}") for i in range(25)
        ]
        records.extend(_file(f"file{i:02d}.txt") for i in range(25))
        resolver = FakeResolver(records)
        completer = SymbolCompleter(resolver)

        results: List[str] = []
        idx = 0
        while True:
            value = completer("@", idx)
            if value is None:
                break
            results.append(value)
            idx += 1
        assert len(results) == MAX_CANDIDATES

    def test_caps_respected_for_each_kind_individually(self) -> None:
        # 파일만 있을 때 :data:`MAX_FILE_CANDIDATES` 까지만 노출되어야 한다.
        records: List[IndexedRecord] = [
            _file(f"file{i:03d}.txt") for i in range(50)
        ]
        resolver = FakeResolver(records)
        completer = SymbolCompleter(resolver)

        results: List[str] = []
        idx = 0
        while True:
            value = completer("@file", idx)
            if value is None:
                break
            results.append(value)
            idx += 1
        # 파일 후보 상한은 MAX_FILE_CANDIDATES, 합산 상한은 MAX_CANDIDATES.
        assert len(results) == min(MAX_FILE_CANDIDATES, MAX_CANDIDATES)


class TestFileAndSymbolDoNotInterfereByDefault:
    def test_symbol_only_completion_unchanged(self) -> None:
        resolver = FakeResolver([_symbol("ConvergenceTool"), _symbol("Other")])
        completer = SymbolCompleter(resolver)
        assert completer("@Conv", 0) == "@ConvergenceTool"
        assert completer("@Conv", 1) is None

    def test_file_only_completion_works_without_symbols(self) -> None:
        resolver = FakeResolver([_file("README.md")])
        completer = SymbolCompleter(resolver)
        assert completer("@README", 0) == "@README.md"

    def test_max_candidate_constants_are_consistent(self) -> None:
        # 합산 상한이 개별 상한 합보다 같거나 작다(설계 정책).
        assert MAX_CANDIDATES <= MAX_SYMBOL_CANDIDATES + MAX_FILE_CANDIDATES
