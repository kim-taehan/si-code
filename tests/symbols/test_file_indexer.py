"""FileIndexer / CompositeIndexer 단위 테스트(이슈 #24)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sicode.symbols.indexer import (
    CompositeIndexer,
    DEFAULT_MAX_FILE_BYTES,
    FileIndexer,
    FileRecord,
    SymbolIndexer,
    SymbolRecord,
    make_default_composite_indexer,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class TestFileIndexerBasic:
    def test_collects_top_level_text_files(self, tmp_path: Path) -> None:
        _write(tmp_path / "README.md", "# hello\n")
        _write(tmp_path / "main.py", "x = 1\n")
        records = FileIndexer(tmp_path).build()
        names = sorted(r.name for r in records)
        assert names == ["README.md", "main.py"]
        for record in records:
            assert isinstance(record, FileRecord)
            assert record.kind == "file"
            assert record.start_line == 1
            # 인덱싱 시점에는 본문을 읽지 않는다(저용량 인덱스).
            assert record.snippet == ""

    def test_records_relative_path_in_posix_style(self, tmp_path: Path) -> None:
        _write(tmp_path / "pkg" / "sub" / "m.py", "x = 1\n")
        records = FileIndexer(tmp_path).build()
        rels = sorted(r.rel_path for r in records)
        assert rels == ["pkg/sub/m.py"]

    def test_collects_non_python_files(self, tmp_path: Path) -> None:
        _write(tmp_path / "notes.txt", "memo\n")
        _write(tmp_path / "data.json", "{}\n")
        records = FileIndexer(tmp_path).build()
        names = sorted(r.name for r in records)
        assert names == ["data.json", "notes.txt"]

    def test_marks_oversized_files(self, tmp_path: Path) -> None:
        big = tmp_path / "big.txt"
        big.write_text("x" * 1024, encoding="utf-8")
        records = FileIndexer(tmp_path, max_file_bytes=100).build()
        # 인덱스에는 포함되지만 메타데이터로만.
        assert len(records) == 1
        assert records[0].is_oversize is True
        assert records[0].size_bytes == 1024

    def test_marks_binary_files(self, tmp_path: Path) -> None:
        # NULL 바이트가 들어간 바이너리 파일.
        _write_bytes(tmp_path / "blob.bin", b"abc\x00def")
        _write(tmp_path / "ok.txt", "ascii\n")
        records = sorted(FileIndexer(tmp_path).build(), key=lambda r: r.name)
        names = [r.name for r in records]
        assert names == ["blob.bin", "ok.txt"]
        binary = next(r for r in records if r.name == "blob.bin")
        text = next(r for r in records if r.name == "ok.txt")
        assert binary.is_binary is True
        assert text.is_binary is False

    def test_default_max_file_bytes_is_one_megabyte(self) -> None:
        assert DEFAULT_MAX_FILE_BYTES == 1024 * 1024


class TestFileIndexerIgnorePatterns:
    def test_default_ignores_dot_env(self, tmp_path: Path) -> None:
        _write(tmp_path / ".env", "SECRET=1\n")
        _write(tmp_path / "ok.py", "x = 1\n")
        records = FileIndexer(tmp_path).build()
        names = [r.name for r in records]
        assert ".env" not in names
        assert "ok.py" in names

    def test_default_ignores_pem(self, tmp_path: Path) -> None:
        _write(tmp_path / "key.pem", "-----BEGIN-----\n")
        _write(tmp_path / "ok.py", "x = 1\n")
        records = FileIndexer(tmp_path).build()
        names = [r.name for r in records]
        assert "key.pem" not in names
        assert "ok.py" in names

    def test_default_ignores_git_directory(self, tmp_path: Path) -> None:
        _write(tmp_path / ".git" / "HEAD", "ref: refs/heads/main\n")
        _write(tmp_path / "ok.py", "x = 1\n")
        records = FileIndexer(tmp_path).build()
        names = [r.name for r in records]
        assert "HEAD" not in names
        assert "ok.py" in names

    def test_default_ignores_pycache(self, tmp_path: Path) -> None:
        _write(tmp_path / "__pycache__" / "cached.pyc", "")
        _write(tmp_path / "ok.py", "x = 1\n")
        records = FileIndexer(tmp_path).build()
        names = [r.name for r in records]
        assert "cached.pyc" not in names
        assert "ok.py" in names

    def test_default_ignores_credentials(self, tmp_path: Path) -> None:
        _write(tmp_path / "credentials.json", "{}\n")
        _write(tmp_path / "ok.py", "x = 1\n")
        records = FileIndexer(tmp_path).build()
        names = [r.name for r in records]
        assert "credentials.json" not in names
        assert "ok.py" in names

    def test_custom_ignore_matcher_overrides_default(self, tmp_path: Path) -> None:
        _write(tmp_path / "skipme.txt", "x")
        _write(tmp_path / "keep.txt", "x")

        def _ignore(name: str) -> bool:
            return name == "skipme.txt"

        records = FileIndexer(tmp_path, ignore_matcher=_ignore).build()
        names = sorted(r.name for r in records)
        assert names == ["keep.txt"]


class TestFileIndexerSymlinkSafety:
    def test_skips_symlinks_when_walking(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("leak\n", encoding="utf-8")
        link = tmp_path / "inside_link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):  # pragma: no cover
            pytest.skip("symlink unsupported on this platform")

        # 인덱싱 루트는 ``tmp_path`` 다. ``inside_link`` 는 symlink 이므로 walk 가
        # 따라가지 않아 ``secret.txt`` 가 결과에 포함되지 않아야 한다.
        # (단, ``outside`` 자체는 ``tmp_path`` 의 자식이므로 walk 됨 — 그 사실이
        # 본 테스트 설계의 약점인데, 여기선 ``outside`` 도 같은 트리이므로
        # ``secret.txt`` 가 포함되는 게 정상이다. 이 케이스에서는 link 자체가
        # 결과에 포함되지 않는 것만 검증하면 충분.)
        records = FileIndexer(tmp_path).build()
        rels = [r.rel_path for r in records]
        # 링크 디렉토리 자체는 결과 트리에 등장하지 않는다.
        assert not any(rel.startswith("inside_link/") for rel in rels)


class TestCompositeIndexer:
    def test_merges_two_indexers_in_order(self, tmp_path: Path) -> None:
        _write(tmp_path / "m.py", "class Foo:\n    pass\n")
        _write(tmp_path / "README.md", "# hi\n")

        composite = CompositeIndexer(
            [SymbolIndexer(tmp_path), FileIndexer(tmp_path)]
        )
        records = composite.build()

        symbol_names = [
            r.name for r in records if isinstance(r, SymbolRecord)
        ]
        file_names = sorted(
            r.name for r in records if isinstance(r, FileRecord)
        )
        assert symbol_names == ["Foo"]
        assert file_names == ["README.md", "m.py"]

    def test_make_default_composite_uses_both_indexers(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path / "m.py", "class Foo:\n    pass\n")
        _write(tmp_path / "README.md", "# hi\n")

        composite = make_default_composite_indexer(tmp_path)
        records = composite.build()

        types = {
            "symbol": sum(1 for r in records if isinstance(r, SymbolRecord)),
            "file": sum(1 for r in records if isinstance(r, FileRecord)),
        }
        assert types["symbol"] >= 1
        assert types["file"] >= 2  # README.md + m.py

    def test_empty_indexer_list_returns_empty(self) -> None:
        composite = CompositeIndexer([])
        assert composite.build() == []
