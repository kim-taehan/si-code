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
from typing import Callable, Optional, Protocol
from urllib import error as urlerror
from urllib import request as urlrequest

from sicode.modes.base import BaseMode


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
    - HTTP 호출은 :class:`OllamaClientProtocol` 추상으로 위임한다 (DIP).
    """

    name = "ollama"

    def __init__(self, client: OllamaClientProtocol) -> None:
        """모드를 초기화한다.

        Args:
            client: 프롬프트를 받아 응답 텍스트를 반환하는 콜러블/객체.
        """
        self._client = client

    def handle(self, user_input: str) -> str:
        """사용자 입력을 Ollama 로 보내고 응답을 반환한다.

        오류 시 REPL 을 종료시키지 않기 위해 :class:`OllamaError` 를 잡아 사용자에게
        보여줄 메시지로 변환한다.
        """
        try:
            return self._client(user_input)
        except OllamaError as exc:
            return f"[ollama] {exc}"
