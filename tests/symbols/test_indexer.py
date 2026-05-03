"""SymbolIndexer 단위 테스트(이슈 #17)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sicode.symbols.indexer import (
    DEFAULT_MAX_FILE_BYTES,
    SymbolIndexer,
    SymbolRecord,
    iter_python_files,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestSymbolIndexerBasic:
    def test_indexes_class_and_function_definitions(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "module.py",
            "class Foo:\n"
            "    pass\n"
            "\n"
            "def bar():\n"
            "    return 1\n",
        )
        records = SymbolIndexer(tmp_path).build()
        names = sorted(r.name for r in records)
        assert names == ["Foo", "bar"]

        kinds = {r.name: r.kind for r in records}
        assert kinds["Foo"] == "class"
        assert kinds["bar"] == "function"

    def test_async_functions_are_indexed_as_function(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.py", "async def aio():\n    return 1\n")
        records = SymbolIndexer(tmp_path).build()
        assert len(records) == 1
        assert records[0].name == "aio"
        assert records[0].kind == "function"

    def test_records_relative_path_in_posix_style(self, tmp_path: Path) -> None:
        _write(tmp_path / "pkg" / "sub" / "m.py", "class A:\n    pass\n")
        records = SymbolIndexer(tmp_path).build()
        assert len(records) == 1
        assert records[0].rel_path == "pkg/sub/m.py"

    def test_records_start_and_end_lineno(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "m.py",
            "x = 1\n"  # line 1
            "class C:\n"  # line 2
            "    def m(self):\n"  # line 3
            "        return 0\n",  # line 4
        )
        records = SymbolIndexer(tmp_path).build()
        klass = next(r for r in records if r.name == "C")
        assert klass.start_line == 2
        assert klass.end_line == 4

    def test_record_source_contains_definition_text(self, tmp_path: Path) -> None:
        _write(tmp_path / "m.py", "class Hello:\n    value = 42\n")
        records = SymbolIndexer(tmp_path).build()
        assert any(
            "class Hello:" in r.source and "value = 42" in r.source
            for r in records
        )

    def test_skips_files_over_max_bytes(self, tmp_path: Path) -> None:
        _write(tmp_path / "huge.py", "class Big:\n    pass\n")
        records = SymbolIndexer(tmp_path, max_file_bytes=1).build()
        assert records == []

    def test_one_megabyte_threshold_constant(self) -> None:
        assert DEFAULT_MAX_FILE_BYTES == 1024 * 1024

    def test_skips_invalid_python(self, tmp_path: Path) -> None:
        _write(tmp_path / "bad.py", "def broken(:\n")
        _write(tmp_path / "ok.py", "class Ok:\n    pass\n")
        records = SymbolIndexer(tmp_path).build()
        assert [r.name for r in records] == ["Ok"]

    def test_only_python_files_are_indexed(self, tmp_path: Path) -> None:
        _write(tmp_path / "module.py", "class A:\n    pass\n")
        _write(tmp_path / "notes.txt", "class B:\n    pass\n")
        records = SymbolIndexer(tmp_path).build()
        names = [r.name for r in records]
        assert names == ["A"]


class TestSymbolIndexerIgnorePatterns:
    def test_default_ignores_dot_env(self, tmp_path: Path) -> None:
        _write(tmp_path / ".env", "class Secret:\n    pass\n")
        _write(tmp_path / "ok.py", "class Ok:\n    pass\n")
        records = SymbolIndexer(tmp_path).build()
        names = sorted(r.name for r in records)
        assert "Secret" not in names
        assert "Ok" in names

    def test_default_ignores_pem_and_keystore(self, tmp_path: Path) -> None:
        # ``.pem`` 등은 파이썬 파일이 아니라 어차피 인덱싱 대상 아니지만,
        # 디렉토리 이름 차원에서 ``.git`` 등이 무시되는지 확인한다.
        _write(tmp_path / ".git" / "tracked.py", "class GitInternal:\n    pass\n")
        _write(tmp_path / "ok.py", "class Ok:\n    pass\n")
        records = SymbolIndexer(tmp_path).build()
        names = [r.name for r in records]
        assert "GitInternal" not in names
        assert "Ok" in names

    def test_default_ignores_credentials_named_python_file(
        self, tmp_path: Path
    ) -> None:
        # ``credentials*`` fnmatch 패턴이 파일 이름에 적용되어 ``credentials.py``
        # 처럼 시크릿 의도의 파일이 인덱싱되지 않는지 확인한다.
        _write(tmp_path / "credentials.py", "class Cred:\n    pass\n")
        records = SymbolIndexer(tmp_path).build()
        assert [r.name for r in records] == []

    def test_default_ignores_id_rsa_directory(self, tmp_path: Path) -> None:
        _write(tmp_path / "id_rsa_legacy" / "leak.py", "class Leak:\n    pass\n")
        _write(tmp_path / "ok.py", "class Ok:\n    pass\n")
        records = SymbolIndexer(tmp_path).build()
        names = [r.name for r in records]
        assert "Leak" not in names
        assert "Ok" in names

    def test_default_ignores_pycache_dir(self, tmp_path: Path) -> None:
        _write(tmp_path / "__pycache__" / "cached.py", "class Cached:\n    pass\n")
        _write(tmp_path / "ok.py", "class Ok:\n    pass\n")
        records = SymbolIndexer(tmp_path).build()
        assert [r.name for r in records] == ["Ok"]

    def test_custom_ignore_matcher_is_used(self, tmp_path: Path) -> None:
        _write(tmp_path / "skipme.py", "class Skip:\n    pass\n")
        _write(tmp_path / "keep.py", "class Keep:\n    pass\n")

        def _ignore(name: str) -> bool:
            return name == "skipme.py"

        records = SymbolIndexer(tmp_path, ignore_matcher=_ignore).build()
        assert [r.name for r in records] == ["Keep"]


class TestIterPythonFiles:
    def test_yields_only_python_files_passing_filters(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path / "a.py", "x = 1\n")
        _write(tmp_path / "b.txt", "ignore me")
        _write(tmp_path / ".env", "x = 1\n")
        files = sorted(p.name for p in iter_python_files(tmp_path))
        assert files == ["a.py"]
