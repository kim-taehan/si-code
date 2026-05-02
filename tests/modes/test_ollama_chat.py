"""``OllamaChatClient`` 및 멀티턴 ``OllamaMode`` 단위 테스트.

실제 Ollama 서버에 의존하지 않도록 ``urlopen`` 호환 콜러블을 mock 으로 주입한다.
"""

from __future__ import annotations

import io
import json
import socket
from typing import Any, Callable, List, Optional
from urllib import error as urlerror

import pytest

from sicode.modes.conversation import Conversation
from sicode.modes.ollama import OllamaError, OllamaMode
from sicode.modes.ollama_chat import CHAT_PATH, OllamaChatClient


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeHTTPResponse:
    """``urlopen`` 의 컨텍스트 매니저 호환 응답."""

    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._buf.close()


def _make_opener(
    payload: dict,
    captured: Optional[List[dict]] = None,
) -> Callable[..., _FakeHTTPResponse]:
    def _opener(req: Any, timeout: float) -> _FakeHTTPResponse:
        if captured is not None:
            captured.append(
                {
                    "url": req.full_url,
                    "method": req.get_method(),
                    "headers": dict(req.header_items()),
                    "body": req.data,
                    "timeout": timeout,
                }
            )
        return _FakeHTTPResponse(json.dumps(payload).encode("utf-8"))

    return _opener


def _raising_opener(exc: BaseException) -> Callable[..., Any]:
    def _opener(_req: Any, timeout: float) -> Any:
        raise exc

    return _opener


def _scripted_opener(
    payloads: List[dict],
    captured: Optional[List[dict]] = None,
) -> Callable[..., _FakeHTTPResponse]:
    """호출 순서대로 다른 응답을 돌려주는 ``urlopen`` 더블."""
    iterator = iter(payloads)

    def _opener(req: Any, timeout: float) -> _FakeHTTPResponse:
        if captured is not None:
            captured.append(
                {
                    "url": req.full_url,
                    "body": req.data,
                    "timeout": timeout,
                }
            )
        return _FakeHTTPResponse(json.dumps(next(iterator)).encode("utf-8"))

    return _opener


# ---------------------------------------------------------------------------
# OllamaChatClient: HTTP 계약
# ---------------------------------------------------------------------------


class TestOllamaChatClientHappyPath:
    def test_chat_returns_message_content(self) -> None:
        opener = _make_opener(
            {"message": {"role": "assistant", "content": "hello"}}
        )
        client = OllamaChatClient(url_opener=opener)
        conv = Conversation()
        conv.add_user("hi")
        assert client.chat(conv) == "hello"

    def test_request_uses_chat_endpoint_and_messages_body(self) -> None:
        captured: List[dict] = []
        opener = _make_opener(
            {"message": {"role": "assistant", "content": "ok"}},
            captured=captured,
        )
        client = OllamaChatClient(
            host="http://localhost:11434",
            model="llama3",
            url_opener=opener,
        )
        conv = Conversation()
        conv.set_system("you are helpful")
        conv.add_user("hello there")
        client.chat(conv)

        assert len(captured) == 1
        rec = captured[0]
        assert rec["url"] == f"http://localhost:11434{CHAT_PATH}"
        assert rec["method"] == "POST"
        assert rec["headers"].get("Content-type") == "application/json"
        body = json.loads(rec["body"].decode("utf-8"))
        assert body == {
            "model": "llama3",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hello there"},
            ],
            "stream": False,
        }

    def test_does_not_mutate_conversation(self) -> None:
        opener = _make_opener({"message": {"role": "assistant", "content": "x"}})
        client = OllamaChatClient(url_opener=opener)
        conv = Conversation()
        conv.add_user("hi")
        before = conv.messages()
        client.chat(conv)
        after = conv.messages()
        # 클라이언트는 conversation 을 수정하지 않는다(append 책임은 OllamaMode).
        assert before == after

    def test_chat_path_constant(self) -> None:
        assert CHAT_PATH == "/api/chat"

    def test_host_with_trailing_slash_is_normalized(self) -> None:
        captured: List[dict] = []
        opener = _make_opener(
            {"message": {"role": "assistant", "content": "ok"}},
            captured=captured,
        )
        client = OllamaChatClient(
            host="http://localhost:11434/", url_opener=opener
        )
        conv = Conversation()
        conv.add_user("x")
        client.chat(conv)
        assert captured[0]["url"] == f"http://localhost:11434{CHAT_PATH}"

    def test_default_timeout_passed_to_opener(self) -> None:
        captured: List[dict] = []
        opener = _make_opener(
            {"message": {"role": "assistant", "content": "ok"}},
            captured=captured,
        )
        client = OllamaChatClient(url_opener=opener)
        conv = Conversation()
        conv.add_user("x")
        client.chat(conv)
        assert captured[0]["timeout"] == client.timeout


# ---------------------------------------------------------------------------
# OllamaChatClient: error mapping
# ---------------------------------------------------------------------------


class TestOllamaChatClientErrors:
    def _make_client(self, opener: Callable[..., Any]) -> OllamaChatClient:
        return OllamaChatClient(url_opener=opener)

    def _run_with_user(self, client: OllamaChatClient) -> str:
        conv = Conversation()
        conv.add_user("hi")
        return client.chat(conv)

    def test_connection_refused_via_urlerror(self) -> None:
        client = self._make_client(
            _raising_opener(urlerror.URLError(ConnectionRefusedError("refused")))
        )
        with pytest.raises(OllamaError) as info:
            self._run_with_user(client)
        assert "refused connection" in str(info.value).lower()

    def test_raw_connection_refused(self) -> None:
        client = self._make_client(_raising_opener(ConnectionRefusedError("nope")))
        with pytest.raises(OllamaError):
            self._run_with_user(client)

    def test_timeout_via_urlerror(self) -> None:
        client = self._make_client(
            _raising_opener(urlerror.URLError(socket.timeout()))
        )
        with pytest.raises(OllamaError) as info:
            self._run_with_user(client)
        assert "timed out" in str(info.value).lower()

    def test_raw_socket_timeout(self) -> None:
        client = self._make_client(_raising_opener(socket.timeout()))
        with pytest.raises(OllamaError) as info:
            self._run_with_user(client)
        assert "timed out" in str(info.value).lower()

    def test_http_404_includes_status(self) -> None:
        http_error = urlerror.HTTPError(
            url="http://localhost:11434/api/chat",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error":"model not found"}'),
        )
        client = self._make_client(_raising_opener(http_error))
        with pytest.raises(OllamaError) as info:
            self._run_with_user(client)
        assert "404" in str(info.value)

    def test_http_500(self) -> None:
        http_error = urlerror.HTTPError(
            url="http://localhost:11434/api/chat",
            code=500,
            msg="Internal Server Error",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b""),
        )
        client = self._make_client(_raising_opener(http_error))
        with pytest.raises(OllamaError) as info:
            self._run_with_user(client)
        assert "500" in str(info.value)

    def test_invalid_json(self) -> None:
        def _opener(_req: Any, timeout: float) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(b"<<not json>>")

        with pytest.raises(OllamaError) as info:
            self._run_with_user(self._make_client(_opener))
        assert "json" in str(info.value).lower()

    def test_missing_message_field(self) -> None:
        def _opener(_req: Any, timeout: float) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(json.dumps({"foo": "bar"}).encode("utf-8"))

        with pytest.raises(OllamaError) as info:
            self._run_with_user(self._make_client(_opener))
        assert "message" in str(info.value).lower()

    def test_message_not_dict(self) -> None:
        def _opener(_req: Any, timeout: float) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(json.dumps({"message": "wrong"}).encode("utf-8"))

        with pytest.raises(OllamaError):
            self._run_with_user(self._make_client(_opener))

    def test_missing_content_field(self) -> None:
        def _opener(_req: Any, timeout: float) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(
                json.dumps({"message": {"role": "assistant"}}).encode("utf-8")
            )

        with pytest.raises(OllamaError) as info:
            self._run_with_user(self._make_client(_opener))
        assert "content" in str(info.value).lower()

    def test_non_string_content(self) -> None:
        def _opener(_req: Any, timeout: float) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(
                json.dumps(
                    {"message": {"role": "assistant", "content": 123}}
                ).encode("utf-8")
            )

        with pytest.raises(OllamaError):
            self._run_with_user(self._make_client(_opener))

    def test_top_level_not_object(self) -> None:
        def _opener(_req: Any, timeout: float) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(json.dumps([]).encode("utf-8"))

        with pytest.raises(OllamaError):
            self._run_with_user(self._make_client(_opener))


# ---------------------------------------------------------------------------
# OllamaMode 멀티턴 통합
# ---------------------------------------------------------------------------


class TestOllamaModeMultiTurn:
    def test_chat_client_enables_multi_turn(self) -> None:
        captured: List[dict] = []
        opener = _scripted_opener(
            [
                {"message": {"role": "assistant", "content": "first reply"}},
                {"message": {"role": "assistant", "content": "second reply"}},
            ],
            captured=captured,
        )
        mode = OllamaMode(client=OllamaChatClient(url_opener=opener))

        assert mode.handle("first message") == "first reply"
        assert mode.handle("second message") == "second reply"

        # 두 번째 요청의 messages 배열에 첫 턴이 포함되어야 한다(이슈 #11 수용 기준).
        second_body = json.loads(captured[1]["body"].decode("utf-8"))
        assert second_body["messages"] == [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "first reply"},
            {"role": "user", "content": "second message"},
        ]

    def test_supports_multi_turn_property(self) -> None:
        chat = OllamaChatClient(
            url_opener=_make_opener(
                {"message": {"role": "assistant", "content": "x"}}
            )
        )
        assert OllamaMode(client=chat).supports_multi_turn is True
        # legacy callable 은 multi-turn 미지원
        assert OllamaMode(client=lambda p: "x").supports_multi_turn is False

    def test_legacy_callable_does_not_use_history(self) -> None:
        captured: List[str] = []

        def _client(prompt: str) -> str:
            captured.append(prompt)
            return f"reply:{prompt}"

        mode = OllamaMode(client=_client)
        mode.handle("a")
        mode.handle("b")
        # 단일 턴 클라이언트는 매번 사용자 입력만 받는다(히스토리 없음).
        assert captured == ["a", "b"]
        # conversation 은 비어 있어야 한다(legacy 경로는 conversation 미사용).
        assert mode.conversation.messages() == []

    def test_chat_client_failure_rolls_back_pending_user(self) -> None:
        """클라이언트 호출 실패 시 사용자 메시지가 누적되지 않아야 한다."""

        class _Fail:
            def chat(self, conversation: Conversation) -> str:
                raise OllamaError("boom")

        mode = OllamaMode(client=_Fail())
        result = mode.handle("hi")
        assert result.startswith("[ollama]")
        assert "boom" in result
        # 실패 후 history 는 깨끗해야 한다(다음 시도가 누적되지 않게).
        assert mode.conversation.messages() == []

    def test_chat_client_records_assistant_response(self) -> None:
        opener = _make_opener(
            {"message": {"role": "assistant", "content": "stored"}}
        )
        mode = OllamaMode(client=OllamaChatClient(url_opener=opener))
        mode.handle("first")
        msgs = mode.conversation.messages()
        assert msgs == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "stored"},
        ]

    def test_max_turns_enforced_via_constructor(self) -> None:
        """``max_turns`` 인자가 Conversation 으로 전달되어 드롭이 일어난다."""

        class _Echo:
            def chat(self, conversation: Conversation) -> str:
                # 사용자 메시지를 그대로 반복.
                last_user = next(
                    m for m in reversed(conversation.messages()) if m["role"] == "user"
                )
                return f"echo:{last_user['content']}"

        mode = OllamaMode(client=_Echo(), max_turns=2)
        for i in range(5):
            mode.handle(f"u{i}")
        msgs = mode.conversation.messages()
        # 최신 2쌍만 유지 → 4개 메시지.
        assert len(msgs) == 4
        assert msgs[0]["content"] == "u3"

    def test_unexpected_exception_propagates_in_chat_path(self) -> None:
        class _Bug:
            def chat(self, conversation: Conversation) -> str:
                raise RuntimeError("bug")

        with pytest.raises(RuntimeError):
            OllamaMode(client=_Bug()).handle("hi")
