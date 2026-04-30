"""심플 모드: 사용자 입력을 그대로 에코한다.

LLM 호출 없이 단순히 입력을 출력으로 돌려준다. 향후 다른 모드(예: LLM 연동) 추가
시에도 본 모듈은 변경되지 않는다 (SRP).
"""

from __future__ import annotations

from sicode.modes.base import BaseMode


class SimpleMode(BaseMode):
    """입력을 그대로 반환하는 가장 단순한 모드."""

    name = "simple"

    def handle(self, user_input: str) -> str:
        """입력 문자열을 변경 없이 반환한다."""
        return user_input
