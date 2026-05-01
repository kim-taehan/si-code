"""OllamaMode / OllamaClient 단위 테스트.

실제 Ollama 서버 없이 동작하도록 ``urlopen`` 호환 콜러블을 mock 으로 주입한다.
"""

from __future__ import annotations

import io
import json
import socket
from typing import Any, Callable, List, Optional
from urllib import error as urlerror

import pytest

from sicode.modes.base import BaseMode
from sicode.modes.ollama import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    GENERATE_PATH,
    OllamaClient,
    OllamaError,
    OllamaMode,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeHTTPResponse:
    """`urlopen` 의 컨텍스트 매니저 호환 응답 객체."""

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
    """주어진 JSON 페이로드를 반환하는 가짜 ``urlopen`` 을 만든다.

    호출 인자(요청 객체, timeout)는 ``captured`` 리스트에 기록된다.
    """

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


# ---------------------------------------------------------------------------
# OllamaClient
# ---------------------------------------------------------------------------


class TestOllamaClientHappyPath:
    def test_returns_response_field_text(self) -> None:
        opener = _make_opener({"response": "hi there", "done": True})
        client = OllamaClient(url_opener=opener)
        assert client("ping") == "hi there"

    def test_request_url_method_headers_and_body(self) -> None:
        captured: List[dict] = []
        opener = _make_opener(
            {"response": "ok"}, captured=captured
        )
        client = OllamaClient(
            host="http://localhost:11434",
            model="llama3",
            url_opener=opener,
        )
        client("hello")

        assert len(captured) == 1
        rec = captured[0]
        assert rec["url"] == f"http://localhost:11434{GENERATE_PATH}"
        assert rec["method"] == "POST"
        # urllib 은 헤더 키를 Title-Case 로 정규화한다.
        assert rec["headers"].get("Content-type") == "application/json"
        body = json.loads(rec["body"].decode("utf-8"))
        assert body == {"model": "llama3", "prompt": "hello", "stream": False}

    def test_default_timeout_is_passed_to_opener(self) -> None:
        captured: List[dict] = []
        opener = _make_opener({"response": "ok"}, captured=captured)
        client = OllamaClient(url_opener=opener)
        client("x")
        assert captured[0]["timeout"] == DEFAULT_TIMEOUT_SECONDS

    def test_custom_timeout_is_passed_to_opener(self) -> None:
        captured: List[dict] = []
        opener = _make_opener({"response": "ok"}, captured=captured)
        client = OllamaClient(timeout=5.0, url_opener=opener)
        client("x")
        assert captured[0]["timeout"] == 5.0

    def test_host_with_trailing_slash_is_normalized(self) -> None:
        captured: List[dict] = []
        opener = _make_opener({"response": "ok"}, captured=captured)
        client = OllamaClient(
            host="http://localhost:11434/", url_opener=opener
        )
        client("x")
        assert captured[0]["url"] == f"http://localhost:11434{GENERATE_PATH}"

    def test_host_and_model_properties(self) -> None:
        client = OllamaClient(
            host="http://example.com:9999",
            model="mistral",
            timeout=10.0,
            url_opener=_make_opener({"response": "ok"}),
        )
        assert client.host == "http://example.com:9999"
        assert client.model == "mistral"
        assert client.timeout == 10.0


class TestOllamaClientErrors:
    def test_connection_refused_raises_ollama_error(self) -> None:
        opener = _raising_opener(
            urlerror.URLError(ConnectionRefusedError("refused"))
        )
        client = OllamaClient(url_opener=opener)
        with pytest.raises(OllamaError) as exc_info:
            client("hi")
        assert "refused connection" in str(exc_info.value).lower()

    def test_raw_connection_refused_raises_ollama_error(self) -> None:
        opener = _raising_opener(ConnectionRefusedError("refused"))
        client = OllamaClient(url_opener=opener)
        with pytest.raises(OllamaError):
            client("hi")

    def test_timeout_via_urlerror_raises_ollama_error(self) -> None:
        opener = _raising_opener(urlerror.URLError(socket.timeout()))
        client = OllamaClient(url_opener=opener)
        with pytest.raises(OllamaError) as exc_info:
            client("hi")
        assert "timed out" in str(exc_info.value).lower()

    def test_raw_socket_timeout_raises_ollama_error(self) -> None:
        opener = _raising_opener(socket.timeout())
        client = OllamaClient(url_opener=opener)
        with pytest.raises(OllamaError) as exc_info:
            client("hi")
        assert "timed out" in str(exc_info.value).lower()

    def test_http_error_404_includes_status(self) -> None:
        body = io.BytesIO(b'{"error":"model not found"}')
        http_error = urlerror.HTTPError(
            url="http://localhost:11434/api/generate",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=body,
        )
        opener = _raising_opener(http_error)
        client = OllamaClient(url_opener=opener)
        with pytest.raises(OllamaError) as exc_info:
            client("hi")
        assert "404" in str(exc_info.value)

    def test_http_error_500(self) -> None:
        http_error = urlerror.HTTPError(
            url="http://localhost:11434/api/generate",
            code=500,
            msg="Internal Server Error",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b""),
        )
        opener = _raising_opener(http_error)
        client = OllamaClient(url_opener=opener)
        with pytest.raises(OllamaError) as exc_info:
            client("hi")
        assert "500" in str(exc_info.value)

    def test_invalid_json_raises_ollama_error(self) -> None:
        def _opener(_req: Any, timeout: float) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(b"<<not json>>")

        client = OllamaClient(url_opener=_opener)
        with pytest.raises(OllamaError) as exc_info:
            client("hi")
        assert "json" in str(exc_info.value).lower()

    def test_missing_response_field_raises_ollama_error(self) -> None:
        def _opener(_req: Any, timeout: float) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(json.dumps({"foo": "bar"}).encode("utf-8"))

        client = OllamaClient(url_opener=_opener)
        with pytest.raises(OllamaError) as exc_info:
            client("hi")
        assert "response" in str(exc_info.value).lower()

    def test_non_string_response_field_raises_ollama_error(self) -> None:
        def _opener(_req: Any, timeout: float) -> _FakeHTTPResponse:
            return _FakeHTTPResponse(json.dumps({"response": 123}).encode("utf-8"))

        client = OllamaClient(url_opener=_opener)
        with pytest.raises(OllamaError):
            client("hi")

    def test_generic_url_error_raises_ollama_error(self) -> None:
        opener = _raising_opener(urlerror.URLError("dns failed"))
        client = OllamaClient(url_opener=opener)
        with pytest.raises(OllamaError) as exc_info:
            client("hi")
        assert "connection failed" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# OllamaMode
# ---------------------------------------------------------------------------


class TestOllamaMode:
    def test_is_a_basemode(self) -> None:
        # LSP: BaseMode 자리에 그대로 대입 가능해야 한다.
        mode = OllamaMode(client=lambda prompt: "x")
        assert isinstance(mode, BaseMode)

    def test_name_attribute(self) -> None:
        assert OllamaMode(client=lambda p: "x").name == "ollama"

    def test_handle_returns_client_response(self) -> None:
        mode = OllamaMode(client=lambda prompt: f"echo:{prompt}")
        assert mode.handle("hello") == "echo:hello"

    def test_handle_passes_prompt_to_client(self) -> None:
        seen: List[str] = []

        def _client(prompt: str) -> str:
            seen.append(prompt)
            return "ok"

        OllamaMode(client=_client).handle("hi there")
        assert seen == ["hi there"]

    def test_handle_returns_user_message_on_ollama_error(self) -> None:
        def _client(prompt: str) -> str:
            raise OllamaError("server is down")

        out = OllamaMode(client=_client).handle("hello")
        assert "[ollama]" in out
        assert "server is down" in out

    def test_handle_does_not_swallow_unexpected_exceptions(self) -> None:
        # OllamaError 가 아닌 예외는 그대로 전파되어야 (REPL 의 종료 경로로 가지 않게)
        # 한다. 이는 클라이언트 구현 버그를 숨기지 않기 위한 의도적 동작.
        def _client(prompt: str) -> str:
            raise RuntimeError("bug")

        with pytest.raises(RuntimeError):
            OllamaMode(client=_client).handle("hello")


# ---------------------------------------------------------------------------
# OllamaClient + OllamaMode 통합 (mock urlopen 까지 포함)
# ---------------------------------------------------------------------------


class TestOllamaClientWithMode:
    def test_end_to_end_with_mocked_urlopen(self) -> None:
        opener = _make_opener({"response": "hello, world"})
        client = OllamaClient(url_opener=opener)
        mode = OllamaMode(client=client)
        assert mode.handle("hi") == "hello, world"

    def test_end_to_end_error_keeps_repl_alive(self) -> None:
        # REPL 은 OllamaError 를 잡아 메시지로 변환한 응답을 받는다.
        opener = _raising_opener(
            urlerror.URLError(ConnectionRefusedError("refused"))
        )
        client = OllamaClient(url_opener=opener)
        mode = OllamaMode(client=client)
        result = mode.handle("hi")
        assert result.startswith("[ollama]")
        assert "refused" in result.lower()


# ---------------------------------------------------------------------------
# 모듈 상수 (회귀 방지)
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_default_host(self) -> None:
        assert DEFAULT_HOST == "http://localhost:11434"

    def test_default_model(self) -> None:
        # 이슈 #5: 사용자 로컬 환경에 실재하는 모델로 기본값 변경.
        assert DEFAULT_MODEL == "llama3.1:8b"

    def test_default_timeout_is_30_seconds(self) -> None:
        assert DEFAULT_TIMEOUT_SECONDS == 30.0

    def test_generate_path(self) -> None:
        assert GENERATE_PATH == "/api/generate"
