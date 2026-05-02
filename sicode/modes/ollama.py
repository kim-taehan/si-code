"""Ollama 로컬 LLM 연동 모드.

`Ollama <https://ollama.com>`_ 가 로컬에서 실행 중일 때, ``POST /api/generate``
엔드포인트로 비스트리밍 요청을 보내 응답 텍스트를 그대로 사용자에게 돌려준다.

설계 메모:
    - HTTP 통신은 :class:`OllamaClient` 로 분리하고, :class:`OllamaMode` 는 클라이언트
      추상에만 의존한다 (DIP). 테스트에서 mock 클라이언트를 주입할 수 있다.
    - 외부 라이브러리 없이 표준 라이브러리 ``urllib.request`` 만 사용한다.
    - 다양한 네트워크/HTTP 오류는 :class:`OllamaError` 로 정규화하여 모드 레이어가
      사용자에게 일관된 메시지를 보여줄 수 있게 한다 (SRP — 클라이언트는 통신과
      에러 매핑, 모드는 사용자 메시지 포맷팅을 담당).
"""

from __future__ import annotations

import json
import socket
from typing import TYPE_CHECKING, Callable, Optional, Protocol, Union
from urllib import error as urlerror
from urllib import request as urlrequest

from sicode.modes.base import BaseMode
from sicode.modes.conversation import Conversation, DEFAULT_MAX_TURNS

if TYPE_CHECKING:  # pragma: no cover - 타입 힌트 전용 (런타임 순환 회피)
    from sicode.modes.ollama_chat import OllamaChatClient


#: 기본 Ollama 호스트. ``SICODE_OLLAMA_HOST`` 로 덮어쓸 수 있다.
DEFAULT_HOST: str = "http://localhost:11434"

#: 기본 모델 이름. ``SICODE_OLLAMA_MODEL`` 또는 CLI ``--model`` 로 덮어쓸 수 있다.
DEFAULT_MODEL: str = "llama3.1:8b"

#: 단일 요청에 대한 타임아웃(초).
DEFAULT_TIMEOUT_SECONDS: float = 30.0

#: ``/api/generate`` 엔드포인트 경로.
GENERATE_PATH: str = "/api/generate"


class OllamaError(Exception):
    """Ollama 통신 중 발생한 사용자에게 보여줄 만한 오류.

    네트워크/HTTP 예외를 :class:`OllamaMode` 가 한 곳에서 처리할 수 있도록 정규화한다.
    """


class OllamaClientProtocol(Protocol):
    """Ollama HTTP 호출을 추상화한 프로토콜.

    한 가지 일(프롬프트 -> 응답 텍스트)만 책임지는 작은 인터페이스로, 테스트에서 mock
    교체가 단순하다 (ISP).
    """

    def __call__(self, prompt: str) -> str:  # pragma: no cover - 프로토콜 정의
        ...


#: ``urlopen`` 시그니처와 호환되는 최소 타입(테스트에서 주입 가능).
UrlOpener = Callable[..., object]


class OllamaClient:
    """``POST /api/generate`` 를 호출하는 비스트리밍 HTTP 클라이언트.

    한 인스턴스는 (host, model, timeout) 한 묶음을 표현한다. 호출할 때마다 새로운
    프롬프트를 받아 응답 텍스트를 반환한다.
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

    def __call__(self, prompt: str) -> str:
        """프롬프트를 전송하고 응답 텍스트를 반환한다.

        Args:
            prompt: 모델에게 전달할 사용자 입력.

        Returns:
            모델이 생성한 텍스트(응답 JSON의 ``response`` 필드).

        Raises:
            OllamaError: 연결 거부, 타임아웃, HTTP 4xx/5xx 또는 응답 파싱 실패 시.
        """
        url = f"{self._host}{GENERATE_PATH}"
        body = json.dumps(
            {"model": self._model, "prompt": prompt, "stream": False}
        ).encode("utf-8")
        req = urlrequest.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            response = self._url_opener(req, timeout=self._timeout)
        except urlerror.HTTPError as exc:  # 4xx/5xx (모델 미존재 포함)
            detail = _safe_read(exc)
            raise OllamaError(
                f"Ollama HTTP {exc.code} error: {exc.reason}"
                + (f" ({detail})" if detail else "")
            ) from exc
        except urlerror.URLError as exc:
            reason = exc.reason
            # ``URLError.reason`` 은 문자열일 수도, 다른 예외일 수도 있다.
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
        except ConnectionRefusedError as exc:  # 직접 raw 소켓 거부
            raise OllamaError(
                f"Ollama server refused connection at {self._host}"
            ) from exc
        except socket.timeout as exc:  # urlopen 이 직접 timeout 을 던지는 경우
            raise OllamaError(
                f"Ollama request timed out after {self._timeout:.0f}s"
            ) from exc

        try:
            with response:  # type: ignore[attr-defined]
                raw = response.read()  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - 매우 드문 IO 실패
            raise OllamaError(f"Failed to read Ollama response: {exc}") from exc

        return _extract_response_text(raw)


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


def _extract_response_text(raw: bytes) -> str:
    """Ollama 응답 JSON에서 ``response`` 필드를 꺼낸다.

    Raises:
        OllamaError: JSON 파싱 실패 또는 ``response`` 필드 부재.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OllamaError(f"Invalid JSON from Ollama: {exc}") from exc

    if not isinstance(payload, dict) or "response" not in payload:
        raise OllamaError(
            "Unexpected Ollama response shape: missing 'response' field"
        )

    response_text = payload["response"]
    if not isinstance(response_text, str):
        raise OllamaError(
            "Unexpected Ollama response shape: 'response' is not a string"
        )
    return response_text


class OllamaMode(BaseMode):
    """Ollama 로컬 서버를 호출해 응답을 돌려주는 모드.

    - REPL 코드는 :class:`BaseMode` 추상에만 의존하므로 본 모드 추가로 인한 REPL
      변경은 없다 (OCP).
    - HTTP 호출은 :class:`OllamaClientProtocol` 또는 chat 클라이언트(``chat`` 메서드를
      갖는 객체) 추상으로 위임한다 (DIP).

    동작 모드:
        본 클래스는 두 가지 클라이언트를 모두 지원한다.

        1. **Single-turn (legacy)**: ``client`` 가 ``Callable[[str], str]`` 처럼
           ``__call__(prompt)`` 만 가진 객체일 때. 호출마다 독립적으로 프롬프트를
           보내며 대화 히스토리를 사용하지 않는다 (기존 동작 유지).
        2. **Multi-turn (chat)**: ``client`` 가 ``chat(conversation)`` 메서드를
           갖는 객체(예: :class:`OllamaChatClient`) 일 때. 내부 :class:`Conversation`
           에 사용자/어시스턴트 메시지를 누적해 매 호출마다 함께 전송한다.

        멀티턴 활성화는 단순 duck-typing 으로 판정한다 (``hasattr(client, "chat")``).
        이렇게 분기하는 이유는 LSP 위배 없이 두 다른 클라이언트 형태를 한 모드 안에서
        매끄럽게 지원하면서도, 각 클라이언트가 본인의 단일 책임만을 갖도록 유지하기 위함.
    """

    name = "ollama"

    def __init__(
        self,
        client: Union[OllamaClientProtocol, "OllamaChatClient"],
        *,
        conversation: Optional[Conversation] = None,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> None:
        """모드를 초기화한다.

        Args:
            client: 단일 프롬프트 콜러블(``__call__(prompt) -> str``) 또는 ``chat``
                메서드를 가진 멀티턴 클라이언트(``chat(conversation) -> str``).
            conversation: 멀티턴 모드에서 사용할 :class:`Conversation` 인스턴스.
                ``None`` 이면 ``max_turns`` 로 새 인스턴스를 생성한다. single-turn
                클라이언트와 함께 쓰면 본 인스턴스는 사용되지 않는다.
            max_turns: ``conversation`` 미지정 시 새 :class:`Conversation` 의
                최대 턴 수.
        """
        self._client = client
        self._conversation = conversation or Conversation(max_turns=max_turns)
        self._is_chat_client = hasattr(client, "chat")

    @property
    def conversation(self) -> Conversation:
        """대화 히스토리 핸들. ``/clear`` / ``/system`` 슬래시 명령이 사용한다."""
        return self._conversation

    @property
    def supports_multi_turn(self) -> bool:
        """현재 클라이언트가 멀티턴(chat) 모드를 지원하는지 여부."""
        return self._is_chat_client

    def handle(self, user_input: str) -> str:
        """사용자 입력을 Ollama 로 보내고 응답을 반환한다.

        오류 시 REPL 을 종료시키지 않기 위해 :class:`OllamaError` 를 잡아 사용자에게
        보여줄 메시지로 변환한다. 멀티턴 모드에서는 사용자 메시지를 :class:`Conversation`
        에 추가 → 클라이언트 호출 → 어시스턴트 응답을 히스토리에 저장하는 흐름으로
        동작한다. 클라이언트 호출이 실패하면 사용자 메시지가 히스토리에 누적되지
        않도록 롤백한다(같은 입력으로 재시도하기 쉬워진다).
        """
        if self._is_chat_client:
            return self._handle_chat(user_input)
        return self._handle_legacy(user_input)

    # ------------------------------------------------------------------ helpers

    def _handle_legacy(self, user_input: str) -> str:
        """단일 프롬프트 호출(호환 경로). 히스토리를 사용하지 않는다."""
        try:
            return self._client(user_input)  # type: ignore[operator]
        except OllamaError as exc:
            return f"[ollama] {exc}"

    def _handle_chat(self, user_input: str) -> str:
        """멀티턴 chat 호출. 성공 시 어시스턴트 응답을 히스토리에 저장한다."""
        self._conversation.add_user(user_input)
        try:
            reply = self._client.chat(self._conversation)  # type: ignore[union-attr]
        except OllamaError as exc:
            # 클라이언트 호출 실패 시 pending user 메시지를 폐기해 다음 호출에
            # 누적되지 않도록 한다(같은 입력으로 재시도 시 중복 누적 방지).
            self._conversation.discard_pending_user()
            return f"[ollama] {exc}"
        self._conversation.add_assistant(reply)
        return reply
