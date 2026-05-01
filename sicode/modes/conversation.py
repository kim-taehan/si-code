"""대화 히스토리 관리 모듈.

멀티턴 대화에서 사용자/어시스턴트 메시지를 누적하고, 컨텍스트 윈도우 폭주를
방지하기 위해 가장 오래된 user+assistant 쌍을 자동으로 드롭하는 책임만을 진다.

설계 메모:
    - SRP: 본 모듈은 "메시지를 보관하고 정책에 따라 정리한다" 한 가지 책임만 갖는다.
      HTTP 호출(``OllamaChatClient``) / 모드 흐름(``OllamaMode``) / 슬래시 명령은
      모두 이 클래스의 작은 인터페이스(``add_user`` / ``add_assistant`` /
      ``set_system`` / ``clear`` / ``messages``)에만 의존한다 (DIP, ISP).
    - OCP: 새로운 드롭 정책(예: 토큰 수 기반)이 필요할 때 본 클래스를 상속하거나
      ``max_turns`` 파라미터에 의존하지 않는 다른 정책을 가진 형제 클래스를
      추가하면 된다. 호출부는 인터페이스만 사용하므로 무수정.
    - 불변성: ``messages()`` 는 깊은 복사 대신 얕은 리스트 복사를 반환한다.
      메시지 dict 자체는 호출자가 변경하지 않는다는 약한 계약을 갖는다.
"""

from __future__ import annotations

from typing import Dict, List, Optional


#: Ollama ``/api/chat`` 가 허용하는 역할 값.
ROLE_SYSTEM: str = "system"
ROLE_USER: str = "user"
ROLE_ASSISTANT: str = "assistant"

#: 기본 최대 턴 수. 한 턴은 user 1개 + assistant 1개 = 메시지 2개.
#: 환경 변수로 덮어쓸 수 있도록 ``OllamaMode`` 에서 외부 주입한다.
DEFAULT_MAX_TURNS: int = 20


#: 메시지 한 건의 형태.  Ollama API 가 요구하는 그대로의 dict 사용.
Message = Dict[str, str]


class Conversation:
    """user/assistant/system 메시지 히스토리를 관리한다.

    - ``add_user`` / ``add_assistant`` 로 턴을 누적한다.
    - 누적 후 (user, assistant) 쌍 수가 ``max_turns`` 를 넘으면 가장 오래된 쌍부터
      한 쌍씩 제거한다. ``set_system`` 으로 설정한 system 메시지는 드롭 대상이
      아니다(토큰을 덜 먹는다는 가정 하의 단순 정책).
    - ``messages()`` 는 ``[system?, user, assistant, user, assistant, ...]`` 순서로
      외부에 노출할 스냅샷을 반환한다.
    """

    def __init__(self, *, max_turns: int = DEFAULT_MAX_TURNS) -> None:
        """대화 히스토리를 초기화한다.

        Args:
            max_turns: 보관할 user/assistant 쌍의 최대 개수. 1 이상이어야 한다.

        Raises:
            ValueError: ``max_turns`` 가 1 미만일 때.
        """
        if max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        self._max_turns: int = max_turns
        self._system: Optional[Message] = None
        # user/assistant 쌍을 한 묶음으로 보관하면 드롭 정책이 단순해진다.
        # 진행 중인(어시스턴트 미응답) 사용자 메시지는 ``_pending_user`` 에 임시로 둔다.
        self._turns: List["_Turn"] = []
        self._pending_user: Optional[Message] = None

    # ------------------------------------------------------------------ accessors

    @property
    def max_turns(self) -> int:
        """현재 설정된 최대 턴 수."""
        return self._max_turns

    @property
    def system_message(self) -> Optional[str]:
        """현재 설정된 system 메시지 본문. 없으면 ``None``."""
        return None if self._system is None else self._system["content"]

    def messages(self) -> List[Message]:
        """현재 보관 중인 메시지 시퀀스를 새로운 리스트로 반환한다.

        반환 순서:
            ``[system?] + [user, assistant, ...] + [pending_user?]``
        """
        out: List[Message] = []
        if self._system is not None:
            out.append(dict(self._system))
        for turn in self._turns:
            out.append(dict(turn.user))
            if turn.assistant is not None:
                out.append(dict(turn.assistant))
        if self._pending_user is not None:
            out.append(dict(self._pending_user))
        return out

    # ------------------------------------------------------------------ mutators

    def set_system(self, content: str) -> None:
        """system 메시지를 설정한다(기존 system 메시지 교체).

        Args:
            content: system 메시지 본문. 비어 있어도 호출은 허용하지만, 보통
                상위 슬래시 명령에서 빈 본문을 사전 차단한다.
        """
        self._system = {"role": ROLE_SYSTEM, "content": content}

    def add_user(self, content: str) -> None:
        """사용자 메시지를 추가한다.

        직전에 ``add_user`` 를 호출하고 ``add_assistant`` 가 아직 오지 않은 상태에서
        다시 호출되면 이전의 미완성 사용자 메시지는 폐기되고 새 메시지로 교체된다
        (사용자가 같은 턴에서 다시 보내는 경우의 보수적 처리).
        """
        self._pending_user = {"role": ROLE_USER, "content": content}

    def add_assistant(self, content: str) -> None:
        """어시스턴트 응답을 추가해 직전 사용자 메시지와 한 턴을 완성한다.

        ``add_user`` 호출 없이 호출되면 어시스턴트만 단독으로 추가하지 않는다 —
        대화 모델 상 의미가 없으므로 ``RuntimeError`` 를 던진다.

        턴 완성 후 ``max_turns`` 초과 시 가장 오래된 쌍부터 드롭한다.
        """
        if self._pending_user is None:
            raise RuntimeError(
                "add_assistant called without a preceding add_user; "
                "the conversation has no pending user turn to pair with."
            )
        turn = _Turn(
            user=self._pending_user,
            assistant={"role": ROLE_ASSISTANT, "content": content},
        )
        self._turns.append(turn)
        self._pending_user = None
        self._enforce_max_turns()

    def clear(self) -> None:
        """대화 히스토리를 초기화한다.

        system 메시지는 의도적으로 함께 제거한다. ``/clear`` 의 사용자 기대(완전
        초기화)에 부합하기 때문이다. system 만 유지하고 싶다면 ``/system <text>``
        를 다시 호출하면 된다.
        """
        self._system = None
        self._turns.clear()
        self._pending_user = None

    def discard_pending_user(self) -> bool:
        """미완성(pending) 사용자 메시지를 폐기한다.

        ``add_user`` 를 호출했지만 ``add_assistant`` 가 도착하기 전에 클라이언트
        호출이 실패한 경우, 호출자가 본 메서드로 pending 상태를 정리해 다음
        시도가 깨끗한 상태에서 시작하게 한다.

        Returns:
            폐기된 pending 메시지가 있었으면 ``True``, 없었으면 ``False``.
        """
        if self._pending_user is None:
            return False
        self._pending_user = None
        return True

    # ------------------------------------------------------------------ internal

    def _enforce_max_turns(self) -> None:
        """``max_turns`` 를 초과한 경우 가장 오래된 (user, assistant) 쌍부터 드롭."""
        while len(self._turns) > self._max_turns:
            self._turns.pop(0)


class _Turn:
    """user/assistant 한 쌍을 묶은 내부 자료구조.

    외부에 노출하지 않는 구현 디테일이다 (모듈 사적 사용).
    """

    __slots__ = ("user", "assistant")

    def __init__(self, *, user: Message, assistant: Optional[Message]) -> None:
        self.user: Message = user
        self.assistant: Optional[Message] = assistant


__all__ = [
    "Conversation",
    "DEFAULT_MAX_TURNS",
    "Message",
    "ROLE_ASSISTANT",
    "ROLE_SYSTEM",
    "ROLE_USER",
]
