"""SymbolExpander 의 파일 첨부(이슈 #24) 단위 테스트."""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from sicode.symbols.expand import (
    DEFAULT_MAX_FILE_LINES,
    SymbolExpander,
)
from sicode.symbols.indexer import FileRecord, IndexedRecord, SymbolRecord
from sicode.symbols.resolver import SymbolResolver


class _MixedIndexer:
    def __init__(self, records: List[IndexedRecord]) -> None:
        self._records = list(records)

    def build(self) -> List[IndexedRecord]:
        return list(self._records)


def _file(
    rel_path: str,
    *,
    size_bytes: int = 0,
    is_binary: bool = False,
    is_oversize: bool = False,
) -> FileRecord:
    return FileRecord(
        name=rel_path.rsplit("/", 1)[-1],
        rel_path=rel_path,
        kind="file",
        start_line=1,
        snippet="",
        size_bytes=size_bytes,
        is_binary=is_binary,
        is_oversize=is_oversize,
    )


def _symbol(name: str) -> SymbolRecord:
    return SymbolRecord(
        name=name,
        kind="class",
        rel_path="m.py",
        start_line=1,
        end_line=2,
        source=f"class {name}:\n    pass\n",
    )


class TestFileBodyAttachment:
    def test_attaches_file_body_within_limit(self, tmp_path: Path) -> None:
        target = tmp_path / "main.py"
        target.write_text("print('hello')\n", encoding="utf-8")

        resolver = SymbolResolver(_MixedIndexer([_file("main.py")]))
        expander = SymbolExpander(resolver, file_root=tmp_path)
        result = expander.expand("@main.py 설명")

        assert "Referenced file: `@main.py`" in result
        assert "(main.py)" in result
        assert "print('hello')" in result
        assert "[truncated]" not in result

    def test_truncates_file_body_at_two_hundred_lines(
        self, tmp_path: Path
    ) -> None:
        # 250 라인 파일 → 200 라인까지 첨부되고 [truncated] 안내.
        lines = [f"line_{i}" for i in range(250)]
        target = tmp_path / "big.txt"
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")

        resolver = SymbolResolver(_MixedIndexer([_file("big.txt")]))
        expander = SymbolExpander(resolver, file_root=tmp_path)
        result = expander.expand("@big.txt")

        # 200번째 라인까지는 포함되어야 한다.
        assert "line_199" in result
        # 201번째 라인부터는 잘려서 빠진다.
        assert "line_200" not in result
        assert "[truncated]" in result

    def test_default_max_file_lines_is_two_hundred(self) -> None:
        assert DEFAULT_MAX_FILE_LINES == 200

    def test_short_file_does_not_get_truncated_label(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "tiny.txt"
        target.write_text("hi\n", encoding="utf-8")

        resolver = SymbolResolver(_MixedIndexer([_file("tiny.txt")]))
        expander = SymbolExpander(resolver, file_root=tmp_path)
        result = expander.expand("@tiny.txt")
        assert "[truncated]" not in result

    def test_extension_omission_attaches_full_file(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "README.md"
        target.write_text("# hello\n", encoding="utf-8")

        resolver = SymbolResolver(_MixedIndexer([_file("README.md")]))
        expander = SymbolExpander(resolver, file_root=tmp_path)
        result = expander.expand("@README 설명")
        assert "Referenced file: `@README`" in result
        assert "# hello" in result


class TestOversizedAndBinaryFiles:
    def test_oversized_attaches_metadata_only(self, tmp_path: Path) -> None:
        # 인덱서가 oversize 로 마킹한 파일은 본문 대신 메타데이터만.
        record = _file("huge.bin", size_bytes=2 * 1024 * 1024, is_oversize=True)
        resolver = SymbolResolver(_MixedIndexer([record]))
        expander = SymbolExpander(resolver, file_root=tmp_path)
        result = expander.expand("@huge.bin")

        assert "Referenced file: `@huge.bin`" in result
        assert "exceeds 1 MB" in result
        # 본문 펜스(```)가 첨부되지 않는다.
        assert "```" not in result

    def test_binary_attaches_metadata_only(self, tmp_path: Path) -> None:
        record = _file("blob.bin", size_bytes=128, is_binary=True)
        resolver = SymbolResolver(_MixedIndexer([record]))
        expander = SymbolExpander(resolver, file_root=tmp_path)
        result = expander.expand("@blob.bin")

        assert "binary — content omitted" in result
        assert "```" not in result


class TestSecretFilesAreNotAttached:
    def test_secret_pattern_not_indexed_so_not_attached(
        self, tmp_path: Path
    ) -> None:
        # FileIndexer 가 ``DEFAULT_IGNORE_PATTERNS`` 로 ``.env`` 를 차단하므로
        # 어떤 형태로 토큰이 들어오든 본문이 첨부되면 안 된다.
        from sicode.symbols.indexer import FileIndexer

        (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

        composite_records: List[IndexedRecord] = list(
            FileIndexer(tmp_path).build()
        )
        resolver = SymbolResolver(_MixedIndexer(composite_records))
        expander = SymbolExpander(resolver, file_root=tmp_path)
        # 시크릿은 인덱스에 없으므로 ``find_files`` 결과가 비어 있다.
        assert resolver.find_files(".env") == []

        # 토큰 패턴은 ``@`` 다음에 영문/숫자/언더스코어로 시작해야 하므로
        # ``@.env`` 는 토큰으로 잡히지 않아 입력이 그대로 보존된다.
        # 어느 쪽이든 본문 ``SECRET=1`` 이 새지 않는 것이 핵심.
        result = expander.expand("@.env")
        assert "SECRET=1" not in result


class TestNoMatchPolicy:
    def test_unknown_token_keeps_input_and_appends_note(
        self, tmp_path: Path
    ) -> None:
        resolver = SymbolResolver(_MixedIndexer([]))
        expander = SymbolExpander(resolver, file_root=tmp_path)
        result = expander.expand("@unknown_xyz 무엇?")

        # 원본 입력이 유지된다.
        assert result.startswith("@unknown_xyz 무엇?")
        # 안내 문구가 붙는다(예외 없이).
        assert "no definition found" in result

    def test_file_match_takes_priority_over_symbol_match(
        self, tmp_path: Path
    ) -> None:
        # 같은 토큰 ``Foo`` 가 파일 ``Foo`` (확장자 없는) 와 심볼 ``Foo`` 양쪽
        # 인덱스에 있을 때, 파일이 우선 첨부된다.
        target = tmp_path / "Foo"
        target.write_text("file body\n", encoding="utf-8")
        resolver = SymbolResolver(
            _MixedIndexer([_symbol("Foo"), _file("Foo")])
        )
        expander = SymbolExpander(resolver, file_root=tmp_path)
        result = expander.expand("@Foo")

        assert "Referenced file: `@Foo`" in result
        assert "file body" in result
        # 심볼 헤더 라인이 등장하지 않아야 한다(파일 우선 정책).
        assert "Referenced symbol: `@Foo`" not in result


class TestPathTraversalSafety:
    def test_does_not_read_outside_file_root(self, tmp_path: Path) -> None:
        outer = tmp_path / "outside"
        outer.mkdir()
        (outer / "secret.txt").write_text("LEAK\n", encoding="utf-8")

        inner = tmp_path / "inner"
        inner.mkdir()

        # 인덱서가 부정확한 ``rel_path`` 를 만들었다고 가정해도, expander 는
        # ``file_root`` 바깥의 본문을 읽으면 안 된다.
        record = _file("../outside/secret.txt")
        resolver = SymbolResolver(_MixedIndexer([record]))
        expander = SymbolExpander(resolver, file_root=inner)
        result = expander.expand("@../outside/secret.txt")

        # 본문 LEAK 이 새지 않아야 한다.
        assert "LEAK" not in result


class TestExpanderConfigValidation:
    def test_max_file_lines_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            SymbolExpander(
                SymbolResolver(_MixedIndexer([])),
                max_file_lines=0,
            )

    def test_max_file_bytes_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            SymbolExpander(
                SymbolResolver(_MixedIndexer([])),
                max_file_bytes=0,
            )
