"""테스트 공용 픽스처/유틸.

REPL 및 CLI 테스트는 실제 Ollama 서버에 의존하지 않아야 하므로,
:class:`BaseMode` 의 가벼운 인메모리 구현체(:class:`EchoMode`)를 여기에 정의해
여러 테스트 모듈에서 재사용한다 (DIP, OCP).
"""

from __future__ import annotations

from typing import List

from sicode.modes.base import BaseMode


class EchoMode(BaseMode):
    """입력을 그대로 반환하는 테스트 전용 BaseMode 구현체.

    프로덕션 코드에는 존재하지 않고 테스트 더블 용도로만 사용된다.
    호출 인자를 ``calls`` 에 기록해 검증을 돕는다.
    """

    name = "echo"

    def __init__(self) -> None:
        self.calls: List[str] = []

    def handle(self, user_input: str) -> str:
        """입력 문자열을 그대로 돌려주고 호출 이력을 기록한다."""
        self.calls.append(user_input)
        return user_input
