"""sicode 실행 모드 패키지.

각 모드는 :class:`sicode.modes.base.BaseMode` 를 구현해야 한다. REPL은 모드의
구체 구현을 알 필요 없이 :meth:`BaseMode.handle` 만 호출한다 (DIP).
"""

from sicode.modes.base import BaseMode
from sicode.modes.ollama import OllamaClient, OllamaError, OllamaMode
from sicode.modes.simple import SimpleMode

__all__ = ["BaseMode", "OllamaClient", "OllamaError", "OllamaMode", "SimpleMode"]
