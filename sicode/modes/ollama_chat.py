"""Ollama ``POST /api/chat`` 기반 멀티턴 HTTP 클라이언트.

기존 :class:`sicode.modes.ollama.OllamaClient` (``/api/generate`` 단일 프롬프트
기반) 와 별도로 신설한 클라이언트로, ``messages`` 배열을 통한 대화 히스토리 전달을
담당한다.

설계 메모:
    - SRP: 본 클래스는 "Conversation 스냅샷 -> assistant content 문자열" 변환만
      담당한다. 히스토리 관리는 :class:`Conversation` 이, 모드 흐름은
      :class:`OllamaMode` 가 책임진다.
    - DIP: ``urlopen`` 호환 콜러블을 주입받아 테스트에서 mock 으로 교체한다.
    - OCP: 기존 ``OllamaClient`` 와 인터페이스가 다르므로 별도 클래스로 분리했다.
      두 클라이언트는 ``Protocol`` / 추상 클래스를 공유하지 않으며, 각자의 책임
      경계가 다르다 (단일 프롬프트 vs. 멀티 메시지).
    - 에러 정책: 연결 거부 / 타임아웃 / HTTP 4xx·5xx / JSON 파싱 실패 / 응답
      형식 오류 모두 :class:`OllamaError` 로 정규화. ``OllamaClient`` 와 동일한
      어휘를 사용해 사용자가 모드 차이를 의식하지 않게 한다.
"""

from __future__ import annotations

import json
import socket
from typing import Callable, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

from sicode.modes.conversation import Conversation
from sicode.modes.ollama import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    OllamaError,
)


#: ``/api/chat`` 엔드포인트 경로.
CHAT_PATH: str = "/api/chat"


#: ``urlopen`` 시그니처와 호환되는 최소 타입(테스트에서 주입 가능).
UrlOpener = Callable[..., object]


class OllamaChatClient:
    """``POST /api/chat`` 을 호출하는 비스트리밍 멀티턴 HTTP 클라이언트.

    한 인스턴스는 (host, model, timeout) 한 묶음을 표현한다. ``chat`` 호출 시
    :class:`Conversation` 의 messages 스냅샷을 보내고, 응답의
    ``message.content`` 를 반환한다.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        *,
        url_opener: Optional[UrlOpener] = None,
    ) -> None:
        """클라이언트를 초기화한다.

        Args:
            host: Ollama 서버 베이스 URL. 끝에 슬래시가 있어도 정규화한다.
            model: 사용할 모델 이름.
            timeout: 단일 요청 타임아웃(초).
            url_opener: ``urllib.request.urlopen`` 호환 함수. 테스트에서 주입한다.
                ``None`` 이면 표준 ``urlopen`` 을 사용한다 (DIP).
        """
        self._host = host.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._url_opener: UrlOpener = url_opener or urlrequest.urlopen

    @property
    def host(self) -> str:
        """현재 설정된 호스트 URL."""
        return self._host

    @property
    def model(self) -> str:
        """현재 설정된 모델 이름."""
        return self._model

    @property
    def timeout(self) -> float:
        """단일 요청 타임아웃(초)."""
        return self._timeout

    def chat(self, conversation: Conversation) -> str:
        """대화 스냅샷을 전송하고 어시스턴트 응답 텍스트를 반환한다.

        Args:
            conversation: 보낼 메시지 시퀀스를 보유한 :class:`Conversation`.
                본 메서드는 conversation 을 변경하지 않는다(읽기 전용).

        Returns:
            모델이 생성한 어시스턴트 메시지 본문(``message.content``).

        Raises:
            OllamaError: 연결 거부, 타임아웃, HTTP 4xx/5xx, JSON 파싱 실패,
                응답 형식 오류 시.
        """
        messages = conversation.messages()
        url = f"{self._host}{CHAT_PATH}"
        body = json.dumps(
            {"model": self._model, "messages": messages, "stream": False}
        ).encode("utf-8")
        req = urlrequest.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            response = self._url_opener(req, timeout=self._timeout)
        except urlerror.HTTPError as exc:  # 4xx/5xx
            detail = _safe_read(exc)
            raise OllamaError(
                f"Ollama HTTP {exc.code} error: {exc.reason}"
                + (f" ({detail})" if detail else "")
            ) from exc
        except urlerror.URLError as exc:
            reason = exc.reason
            if isinstance(reason, socket.timeout):
                raise OllamaError(
                    f"Ollama request timed out after {self._timeout:.0f}s"
                ) from exc
            if isinstance(reason, ConnectionRefusedError):
                raise OllamaError(
                    f"Ollama server refused connection at {self._host}"
                ) from exc
            raise OllamaError(
                f"Ollama connection failed: {reason}"
            ) from exc
        except ConnectionRefusedError as exc:
            raise OllamaError(
                f"Ollama server refused connection at {self._host}"
            ) from exc
        except socket.timeout as exc:
            raise OllamaError(
                f"Ollama request timed out after {self._timeout:.0f}s"
            ) from exc

        try:
            with response:  # type: ignore[attr-defined]
                raw = response.read()  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - 매우 드문 IO 실패
            raise OllamaError(f"Failed to read Ollama response: {exc}") from exc

        return _extract_chat_message(raw)


def _safe_read(exc: urlerror.HTTPError) -> str:
    """HTTPError 의 본문을 가능한 만큼 안전하게 문자열로 변환한다."""
    try:
        data = exc.read()
    except Exception:  # pragma: no cover - 본문이 이미 소진된 경우
        return ""
    if not data:
        return ""
    try:
        return data.decode("utf-8", errors="replace").strip()
    except Exception:  # pragma: no cover
        return ""


def _extract_chat_message(raw: bytes) -> str:
    """``/api/chat`` 응답 JSON에서 ``message.content`` 필드를 꺼낸다.

    Raises:
        OllamaError: JSON 파싱 실패, ``message`` 부재 또는 형 오류,
            ``content`` 부재 또는 비-문자열 시.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OllamaError(f"Invalid JSON from Ollama: {exc}") from exc

    if not isinstance(payload, dict):
        raise OllamaError(
            "Unexpected Ollama response shape: top-level value is not an object"
        )

    message = payload.get("message")
    if not isinstance(message, dict):
        raise OllamaError(
            "Unexpected Ollama response shape: missing or invalid 'message' field"
        )

    content = message.get("content")
    if not isinstance(content, str):
        raise OllamaError(
            "Unexpected Ollama response shape: 'message.content' is not a string"
        )
    return content


__all__ = [
    "CHAT_PATH",
    "OllamaChatClient",
]
