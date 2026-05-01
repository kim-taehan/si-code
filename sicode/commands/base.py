"""슬래시 명령 추상 인터페이스 및 결과 타입.

설계 원칙:
    - REPL 루프와 명령 구현은 :class:`SlashCommand` 추상에만 의존한다 (DIP).
    - 작은 인터페이스(``name``/``aliases``/``description``/``execute``) 만 강제해
      에코, 종료, 도움말 등 다양한 명령을 가볍게 추가할 수 있다 (ISP, SRP).
    - 명령은 부작용으로 출력하지 않고, 출력 문자열을 반환값으로만 전달한다.
      이렇게 분리하면 테스트 시 ``output_fn`` 캡처만으로 검증할 수 있다 (SRP).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - 타입 힌트 전용 임포트(런타임 순환 참조 회피)
    from sicode.commands.registry import SlashCommandRegistry


class CommandAction(Enum):
    """명령 실행 후 REPL이 취해야 할 동작."""

    #: REPL 루프를 계속 진행한다.
    CONTINUE = "continue"
    #: REPL 루프를 종료한다.
    EXIT = "exit"


@dataclass(frozen=True)
class CommandResult:
    """슬래시 명령 실행 결과.

    Attributes:
        action: 다음 REPL 동작(:class:`CommandAction`).
        output: 출력할 문자열. 빈 문자열이면 출력하지 않는다.
    """

    action: CommandAction
    output: str = ""

    @classmethod
    def cont(cls, output: str = "") -> "CommandResult":
        """``CONTINUE`` 결과를 만든다 (가독성 헬퍼)."""
        return cls(action=CommandAction.CONTINUE, output=output)

    @classmethod
    def exit_(cls, output: str = "") -> "CommandResult":
        """``EXIT`` 결과를 만든다 (가독성 헬퍼). ``exit`` 가 키워드라 ``exit_``)."""
        return cls(action=CommandAction.EXIT, output=output)


@dataclass(frozen=True)
class ReplContext:
    """슬래시 명령에 노출되는 REPL 컨텍스트.

    명령이 필요로 하는 최소 정보만 노출해 결합도를 낮춘다 (ISP).
    현재는 ``/help`` 가 등록 명령 목록을 조회하는 용도로만 사용한다.

    Attributes:
        registry: 슬래시 명령 레지스트리. ``None`` 이면 명령은 레지스트리 정보를
            사용하지 않는다고 가정한다.
    """

    registry: Optional["SlashCommandRegistry"] = None


class SlashCommand(ABC):
    """모든 슬래시 명령이 구현해야 하는 추상 인터페이스.

    Attributes:
        name: 슬래시 없이 비교되는 기본 이름(예: ``"exit"``).
        aliases: 동일 동작에 매핑되는 별칭 목록(예: ``["quit"]``).
        description: ``/help`` 출력에 사용되는 한 줄 설명.
    """

    name: str = ""
    aliases: "tuple[str, ...]" = ()
    description: str = ""

    @abstractmethod
    def execute(self, context: ReplContext) -> CommandResult:
        """명령을 실행하고 :class:`CommandResult` 를 반환한다.

        Args:
            context: REPL 컨텍스트. 명령은 필요한 정보만 사용한다.

        Returns:
            REPL 의 후속 동작과 출력 문자열.
        """
        raise NotImplementedError


__all__ = [
    "CommandAction",
    "CommandResult",
    "ReplContext",
    "SlashCommand",
]
