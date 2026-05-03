"""@심볼 자동 확장의 OllamaMode / ClearCommand 통합 테스트(이슈 #17)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, List

import pytest

from sicode.commands import ClearCommand, register_default_commands
from sicode.commands.base import ReplContext
from sicode.commands.clear import SUCCESS_MESSAGE as CLEAR_SUCCESS
from sicode.modes.ollama import OllamaMode
from sicode.modes.ollama_chat import OllamaChatClient
from sicode.repl import run_repl_with_inputs
from sicode.symbols import SymbolExpander, SymbolResolver
from sicode.symbols.indexer import SymbolRecord


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubIndexer:
    def __init__(self, records: List[SymbolRecord]) -> None:
        self._records = list(records)
        self.build_calls: int = 0

    def build(self) -> List[SymbolRecord]:
        self.build_calls += 1
        return list(self._records)


def _make_chat_opener(
    payload_seq: List[dict], captured: List[dict]
) -> Callable[..., Any]:
    """``/api/chat`` 응답을 순서대로 돌려주는 가짜 ``urlopen``."""
    import io

    iterator = iter(payload_seq)

    class _Resp:
        def __init__(self, data: bytes) -> None:
            self._buf = io.BytesIO(data)

        def read(self) -> bytes:
            return self._buf.read()

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *exc: Any) -> None:
            self._buf.close()

    def _opener(req: Any, timeout: float) -> _Resp:
        captured.append({"url": req.full_url, "body": req.data})
        return _Resp(json.dumps(next(iterator)).encode("utf-8"))

    return _opener


def _rec(name: str, *, source: str) -> SymbolRecord:
    return SymbolRecord(
        name=name,
        kind="class",
        rel_path=f"{name.lower()}.py",
        start_line=1,
        end_line=2,
        source=source,
    )


# ---------------------------------------------------------------------------
# OllamaMode 통합
# ---------------------------------------------------------------------------


class TestOllamaModeWithSymbolExpansion:
    def test_user_input_with_known_symbol_is_expanded_before_send(self) -> None:
        register_default_commands()
        captured: List[dict] = []
        opener = _make_chat_opener(
            [{"message": {"role": "assistant", "content": "ok"}}],
            captured,
        )
        resolver = SymbolResolver(
            _StubIndexer([_rec("Foo", source="class Foo:\n    pass\n")])
        )
        expander = SymbolExpander(resolver)
        mode = OllamaMode(
            client=OllamaChatClient(url_opener=opener),
            input_preprocessor=expander.expand,
            symbol_resolver=resolver,
        )

        run_repl_with_inputs(mode, ["@Foo 설명", "/exit"])

        assert len(captured) == 1
        body = json.loads(captured[0]["body"].decode("utf-8"))
        # 원본 입력이 보존되고 첨부 블록이 추가되어 전송된다.
        user_msg = body["messages"][-1]
        assert user_msg["role"] == "user"
        assert user_msg["content"].startswith("@Foo 설명")
        assert "Referenced symbol: `@Foo`" in user_msg["content"]
        assert "class Foo:" in user_msg["content"]

    def test_user_input_without_at_is_unchanged(self) -> None:
        register_default_commands()
        captured: List[dict] = []
        opener = _make_chat_opener(
            [{"message": {"role": "assistant", "content": "ok"}}],
            captured,
        )
        resolver = SymbolResolver(_StubIndexer([_rec("Foo", source="class Foo:\n    pass\n")]))
        expander = SymbolExpander(resolver)
        mode = OllamaMode(
            client=OllamaChatClient(url_opener=opener),
            input_preprocessor=expander.expand,
            symbol_resolver=resolver,
        )

        run_repl_with_inputs(mode, ["그냥 인사", "/exit"])

        body = json.loads(captured[0]["body"].decode("utf-8"))
        user_msg = body["messages"][-1]
        # ``@`` 가 없으므로 어떠한 첨부도 없다.
        assert user_msg["content"] == "그냥 인사"

    def test_unknown_symbol_appends_note(self) -> None:
        register_default_commands()
        captured: List[dict] = []
        opener = _make_chat_opener(
            [{"message": {"role": "assistant", "content": "ok"}}],
            captured,
        )
        resolver = SymbolResolver(_StubIndexer([]))  # 인덱스 비어있음
        expander = SymbolExpander(resolver)
        mode = OllamaMode(
            client=OllamaChatClient(url_opener=opener),
            input_preprocessor=expander.expand,
            symbol_resolver=resolver,
        )

        run_repl_with_inputs(mode, ["@nonexistent_symbol 무엇?", "/exit"])

        body = json.loads(captured[0]["body"].decode("utf-8"))
        user_msg = body["messages"][-1]
        assert user_msg["content"].startswith("@nonexistent_symbol 무엇?")
        assert "(note: @nonexistent_symbol — no definition found in project)" in user_msg["content"]

    def test_legacy_mode_without_preprocessor_is_unchanged(self) -> None:
        # input_preprocessor 없이 만든 OllamaMode 는 기존 297 테스트 환경 그대로.
        captured: List[dict] = []
        opener = _make_chat_opener(
            [{"message": {"role": "assistant", "content": "ok"}}],
            captured,
        )
        mode = OllamaMode(client=OllamaChatClient(url_opener=opener))
        register_default_commands()
        run_repl_with_inputs(mode, ["@Foo plain", "/exit"])
        body = json.loads(captured[0]["body"].decode("utf-8"))
        # 프리프로세서가 없으므로 ``@Foo`` 가 변환되지 않고 그대로 전송된다.
        assert body["messages"][-1]["content"] == "@Foo plain"

    def test_preprocessor_exception_falls_back_to_original(self) -> None:
        register_default_commands()
        captured: List[dict] = []
        opener = _make_chat_opener(
            [{"message": {"role": "assistant", "content": "ok"}}],
            captured,
        )

        def _broken(_: str) -> str:
            raise RuntimeError("boom")

        mode = OllamaMode(
            client=OllamaChatClient(url_opener=opener),
            input_preprocessor=_broken,
        )
        run_repl_with_inputs(mode, ["@Foo 설명", "/exit"])
        body = json.loads(captured[0]["body"].decode("utf-8"))
        assert body["messages"][-1]["content"] == "@Foo 설명"


# ---------------------------------------------------------------------------
# /clear 가 심볼 캐시도 무효화
# ---------------------------------------------------------------------------


class TestClearInvalidatesSymbolCache:
    def test_clear_calls_invalidate_on_resolver(self) -> None:
        register_default_commands()
        captured: List[dict] = []
        opener = _make_chat_opener(
            [
                {"message": {"role": "assistant", "content": "r1"}},
                {"message": {"role": "assistant", "content": "r2"}},
            ],
            captured,
        )
        indexer = _StubIndexer([_rec("Foo", source="class Foo:\n    pass\n")])
        resolver = SymbolResolver(indexer)
        expander = SymbolExpander(resolver)
        mode = OllamaMode(
            client=OllamaChatClient(url_opener=opener),
            input_preprocessor=expander.expand,
            symbol_resolver=resolver,
        )

        # 첫 번째 ``@Foo`` 입력 시 인덱싱이 1회 발생.
        run_repl_with_inputs(mode, ["@Foo 첫 번째", "/clear", "@Foo 두 번째", "/exit"])

        # 두 번째 ``@Foo`` 입력 시 ``/clear`` 의 invalidate 가 작동했다면
        # 인덱서가 다시 호출되어 build_calls 가 2 가 되어야 한다.
        assert indexer.build_calls == 2

    def test_clear_unit_invokes_invalidate_on_mode_resolver(self) -> None:
        class _StubResolver:
            def __init__(self) -> None:
                self.invalidated = False

            def invalidate(self) -> None:
                self.invalidated = True

        captured: List[dict] = []
        opener = _make_chat_opener([], captured)
        stub = _StubResolver()
        mode = OllamaMode(
            client=OllamaChatClient(url_opener=opener),
            symbol_resolver=stub,
        )
        result = ClearCommand().execute(ReplContext(mode=mode))
        assert result.output == CLEAR_SUCCESS
        assert stub.invalidated is True

    def test_clear_without_resolver_still_succeeds(self) -> None:
        captured: List[dict] = []
        opener = _make_chat_opener([], captured)
        # symbol_resolver 미주입 — 기본값 None.
        mode = OllamaMode(client=OllamaChatClient(url_opener=opener))
        result = ClearCommand().execute(ReplContext(mode=mode))
        assert result.output == CLEAR_SUCCESS

    def test_clear_swallows_invalidate_exceptions(self) -> None:
        class _BrokenResolver:
            def invalidate(self) -> None:
                raise RuntimeError("boom")

        captured: List[dict] = []
        opener = _make_chat_opener([], captured)
        mode = OllamaMode(
            client=OllamaChatClient(url_opener=opener),
            symbol_resolver=_BrokenResolver(),
        )
        # 예외를 무시하고 정상 메시지를 반환해야 한다.
        result = ClearCommand().execute(ReplContext(mode=mode))
        assert result.output == CLEAR_SUCCESS


# ---------------------------------------------------------------------------
# 1 MB 초과 파일 인덱싱 제외 (실디스크 통합)
# ---------------------------------------------------------------------------


class TestOversizedFilesAreSkipped:
    def test_symbol_in_oversized_file_is_not_resolved(self, tmp_path: Path) -> None:
        from sicode.symbols.indexer import SymbolIndexer

        big_file = tmp_path / "big.py"
        # ``class Huge`` 정의 + 패딩으로 1 MB 초과 만든다.
        body = "class Huge:\n    pass\n" + ("# pad\n" * (1024 * 1024 // 6 + 10))
        big_file.write_text(body, encoding="utf-8")
        assert big_file.stat().st_size > 1024 * 1024

        indexer = SymbolIndexer(tmp_path)  # 기본 max_file_bytes = 1 MB
        resolver = SymbolResolver(indexer)
        result = resolver.find("Huge")
        assert result == []
