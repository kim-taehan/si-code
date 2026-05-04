"""파일 토큰(@파일명) 통합 흐름 테스트(이슈 #24).

본 모듈은 :class:`FileIndexer` + :class:`SymbolResolver` + :class:`SymbolExpander`
가 실제 디스크에서 함께 동작할 때 이슈 #24 의 수용 기준을 만족하는지 확인한다.
"""

from __future__ import annotations

from pathlib import Path

from sicode.commands import ClearCommand, register_default_commands
from sicode.commands.base import ReplContext
from sicode.commands.clear import SUCCESS_MESSAGE as CLEAR_SUCCESS
from sicode.modes.ollama import OllamaMode
from sicode.modes.ollama_chat import OllamaChatClient
from sicode.symbols import (
    CompositeIndexer,
    FileIndexer,
    SymbolExpander,
    SymbolIndexer,
    SymbolResolver,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestEndToEndFileExpansion:
    def test_readme_token_attaches_file_body(self, tmp_path: Path) -> None:
        _write(tmp_path / "README.md", "# Title\n\nbody\n")

        composite = CompositeIndexer(
            [SymbolIndexer(tmp_path), FileIndexer(tmp_path)]
        )
        resolver = SymbolResolver(composite)
        expander = SymbolExpander(resolver, file_root=tmp_path)

        result = expander.expand("@README 설명해줘")
        assert "# Title" in result
        assert "body" in result
        assert "Referenced file:" in result

    def test_subpath_token_attaches_file_body(self, tmp_path: Path) -> None:
        _write(tmp_path / "sicode" / "repl.py", "def repl():\n    return\n")

        composite = CompositeIndexer(
            [SymbolIndexer(tmp_path), FileIndexer(tmp_path)]
        )
        resolver = SymbolResolver(composite)
        expander = SymbolExpander(resolver, file_root=tmp_path)

        result = expander.expand("@sicode/repl.py")
        assert "def repl():" in result
        assert "Referenced file: `@sicode/repl.py`" in result

    def test_secret_file_is_not_attached_or_indexed(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path / ".env", "SECRET=should-not-leak\n")

        composite = CompositeIndexer(
            [SymbolIndexer(tmp_path), FileIndexer(tmp_path)]
        )
        resolver = SymbolResolver(composite)
        expander = SymbolExpander(resolver, file_root=tmp_path)

        # 자동완성 후보에 노출되지 않는다.
        files = resolver.all_files()
        assert all(f.name != ".env" for f in files)
        # 직접 검색해도 인덱스에 없다.
        assert resolver.find_files(".env") == []

        # expand 시에도 본문이 첨부되지 않는다(어떤 토큰 형태든).
        result = expander.expand("@.env")
        assert "should-not-leak" not in result

    def test_unknown_token_passes_through_with_note(
        self, tmp_path: Path
    ) -> None:
        composite = CompositeIndexer(
            [SymbolIndexer(tmp_path), FileIndexer(tmp_path)]
        )
        resolver = SymbolResolver(composite)
        expander = SymbolExpander(resolver, file_root=tmp_path)

        result = expander.expand("@unknown_xyz 무엇?")
        assert result.startswith("@unknown_xyz 무엇?")
        assert "no definition found" in result

    def test_oversized_file_attaches_metadata_only(
        self, tmp_path: Path
    ) -> None:
        big = tmp_path / "huge.txt"
        # 1 MB + 약간 — FileIndexer 가 oversize 마킹.
        big.write_text("x" * (1024 * 1024 + 100), encoding="utf-8")

        composite = CompositeIndexer(
            [SymbolIndexer(tmp_path), FileIndexer(tmp_path)]
        )
        resolver = SymbolResolver(composite)
        expander = SymbolExpander(resolver, file_root=tmp_path)

        result = expander.expand("@huge.txt")
        assert "Referenced file: `@huge.txt`" in result
        assert "exceeds 1 MB" in result
        # 본문 1 MB 가 그대로 첨부되면 안 된다.
        assert "x" * 1024 not in result


class TestClearInvalidatesFileIndex:
    def test_clear_clears_file_records_on_resolver(
        self, tmp_path: Path
    ) -> None:
        # 1) 초기 인덱싱: README.md 만 존재.
        readme = tmp_path / "README.md"
        readme.write_text("# v1\n", encoding="utf-8")

        composite = CompositeIndexer(
            [SymbolIndexer(tmp_path), FileIndexer(tmp_path)]
        )
        resolver = SymbolResolver(composite)
        # lazy 인덱싱 트리거.
        first_files = sorted(f.name for f in resolver.all_files())
        assert first_files == ["README.md"]

        # 2) 디스크에 새 파일 추가 후 invalidate 전에는 보이지 않는다.
        (tmp_path / "NEW.md").write_text("# new\n", encoding="utf-8")
        cached_files = sorted(f.name for f in resolver.all_files())
        assert "NEW.md" not in cached_files

        # 3) /clear 가 호출하는 invalidate() 후 다시 조회하면 NEW.md 가 인덱스에
        #    잡힌다(file index 도 함께 무효화됨).
        resolver.invalidate()
        rebuilt = sorted(f.name for f in resolver.all_files())
        assert "NEW.md" in rebuilt

    def test_clear_command_invalidates_resolver_with_files(
        self, tmp_path: Path
    ) -> None:
        register_default_commands()

        composite = CompositeIndexer(
            [SymbolIndexer(tmp_path), FileIndexer(tmp_path)]
        )
        resolver = SymbolResolver(composite)
        resolver.all_records()  # lazy 캐시 채움

        mode = OllamaMode(
            client=OllamaChatClient(),
            symbol_resolver=resolver,
        )
        result = ClearCommand().execute(ReplContext(mode=mode))
        assert result.output == CLEAR_SUCCESS
        # invalidate 후 캐시는 비어 있어야 한다(다음 조회 시 재인덱싱).
        # 외부 동작 확인: ``_cache`` 가 None 이라는 내부 상태 대신, 다음
        # ``all_files()`` 호출이 디스크를 다시 본다는 사실로 검증.
        # 새 파일을 추가해도 invalidate 가 작동했는지 확인한다.
        (tmp_path / "afterclear.md").write_text("after\n", encoding="utf-8")
        assert any(
            f.name == "afterclear.md" for f in resolver.all_files()
        )
