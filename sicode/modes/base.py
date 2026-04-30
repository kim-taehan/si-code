"""모드 추상 인터페이스.

REPL은 구체 모드 구현이 아닌 :class:`BaseMode` 추상에 의존한다 (DIP).
새로운 모드 추가 시 REPL 코드를 수정하지 않고 ``BaseMode`` 만 구현하면 된다 (OCP).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseMode(ABC):
    """모든 모드가 구현해야 하는 추상 인터페이스.

    인터페이스를 작게 유지해 다양한 모드(에코, LLM, 디버그 등)가 공통의 작은
    계약만 만족하도록 한다 (ISP).
    """

    #: 모드를 식별하는 사람이 읽기 좋은 이름. 환영 메시지에 사용된다.
    name: str = "base"

    @abstractmethod
    def handle(self, user_input: str) -> str:
        """사용자 입력 한 줄을 받아서 응답 문자열을 반환한다.

        Args:
            user_input: 사용자가 입력한 한 줄(개행 제외).

        Returns:
            REPL이 출력할 응답 문자열. 빈 문자열을 반환하면 출력하지 않는다.
        """
        raise NotImplementedError
