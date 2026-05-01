"""슬래시 명령 레지스트리 및 디스패처.

요구사항(이슈 #7):
    - 모듈 수준 ``dict`` 기반 레지스트리.
    - 등록은 명시적 :func:`register` 호출만 허용 (임포트 부수효과 자동 등록 금지).
    - 미등록 명령은 ``"Unknown command: /foo. Type /help for available commands."`` 안내.
    - 테스트 격리용 :func:`reset` 또는 컨텍스트 매니저 제공.

SOLID 메모:
    - SRP: 레지스트리는 등록/조회/디스패치만 담당하고, 출력은 호출부(REPL) 가 담당한다.
    - OCP: 새 명령 추가 시 본 모듈을 수정하지 않고 :func:`register` 만 호출한다.
    - DIP: 디스패치는 :class:`SlashCommand` 추상에만 의존한다.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional

from sicode.commands.base import (
    CommandAction,
    CommandResult,
    ReplContext,
    SlashCommand,
)


class SlashCommandRegistry:
    """이름/별칭 -> :class:`SlashCommand` 매핑을 관리하는 레지스트리.

    같은 이름/별칭을 두 번 등록하면 :class:`ValueError` 를 발생시켜
    실수로 인한 덮어쓰기를 방지한다.
    """

    def __init__(self) -> None:
        # 이름/별칭 모두를 같은 dict 에 저장하되, ``_primary_names`` 로 정렬·표시용
        # 기본 이름 집합을 별도로 관리한다 (SRP: 두 책임을 명확히 구분).
        self._by_token: Dict[str, SlashCommand] = {}
        self._primary_names: List[str] = []

    def register(self, command: SlashCommand) -> None:
        """명령을 등록한다.

        Args:
            command: 등록할 :class:`SlashCommand` 인스턴스.

        Raises:
            ValueError: ``name`` 또는 ``aliases`` 가 비어 있거나 중복 등록 시.
        """
        if not command.name:
            raise ValueError("SlashCommand.name must be a non-empty string")
        tokens = (command.name, *command.aliases)
        for token in tokens:
            if not token:
                raise ValueError("SlashCommand alias entries must be non-empty")
            if token in self._by_token:
                raise ValueError(f"Slash command token already registered: /{token}")
        for token in tokens:
            self._by_token[token] = command
        self._primary_names.append(command.name)

    def unregister(self, name: str) -> None:
        """이름으로 명령을 제거한다. (테스트/런타임 수정용 보조 API)

        Args:
            name: 등록된 기본 이름.

        Raises:
            KeyError: 등록되지 않은 이름이면.
        """
        command = self._by_token.get(name)
        if command is None or command.name != name:
            raise KeyError(name)
        for token in (command.name, *command.aliases):
            self._by_token.pop(token, None)
        self._primary_names.remove(command.name)

    def reset(self) -> None:
        """레지스트리를 비운다 (테스트 격리 등에서 사용)."""
        self._by_token.clear()
        self._primary_names.clear()

    def get(self, token: str) -> Optional[SlashCommand]:
        """이름 또는 별칭으로 명령을 조회한다. 없으면 ``None``."""
        return self._by_token.get(token)

    def commands(self) -> List[SlashCommand]:
        """등록된 명령을 ``name`` 기준 알파벳 오름차순으로 반환한다.

        ``/help`` 출력 정렬에 사용된다.
        """
        unique: Dict[str, SlashCommand] = {}
        for name in self._primary_names:
            cmd = self._by_token.get(name)
            if cmd is not None:
                unique[name] = cmd
        return [unique[k] for k in sorted(unique.keys())]

    def __contains__(self, token: object) -> bool:  # pragma: no cover - 단순 위임
        return isinstance(token, str) and token in self._by_token

    def __len__(self) -> int:  # pragma: no cover - 단순 위임
        return len(self._primary_names)


#: 모듈 전역 기본 레지스트리. 테스트는 :func:`reset` 으로 격리한다.
default_registry: SlashCommandRegistry = SlashCommandRegistry()


def register(command: SlashCommand) -> None:
    """:data:`default_registry` 에 명령을 등록하는 모듈 수준 헬퍼."""
    default_registry.register(command)


def reset() -> None:
    """:data:`default_registry` 를 초기화하는 모듈 수준 헬퍼."""
    default_registry.reset()


@contextmanager
def temporary_registry() -> Iterator[SlashCommandRegistry]:
    """테스트에서 전역 레지스트리를 일시적으로 비운 뒤 복구한다.

    사용 예::

        with temporary_registry() as reg:
            reg.register(MyCommand())
            ...
    """
    snapshot_by_token = dict(default_registry._by_token)
    snapshot_primary = list(default_registry._primary_names)
    default_registry.reset()
    try:
        yield default_registry
    finally:
        default_registry._by_token = snapshot_by_token
        default_registry._primary_names = snapshot_primary


def parse_slash_input(line: str) -> Optional[str]:
    """입력 문자열을 슬래시 명령 토큰으로 변환한다.

    슬래시(``/``) 로 시작하지 않으면 ``None`` 을 반환한다. 토큰은 슬래시를 제거하고
    앞뒤 공백을 제거한 후 소문자로 정규화한다. (인자 파싱은 범위 외이므로 첫
    공백까지만 토큰으로 인정한다.)
    """
    stripped = line.strip()
    if not stripped.startswith("/"):
        return None
    body = stripped[1:].strip()
    if not body:
        return ""
    # 첫 공백 이전까지를 토큰으로 사용 (인자 파싱은 향후 작업).
    token = body.split(maxsplit=1)[0]
    return token.lower()


def dispatch_command(
    line: str,
    *,
    registry: Optional[SlashCommandRegistry] = None,
    context: Optional[ReplContext] = None,
) -> CommandResult:
    """슬래시 입력을 디스패치한다.

    호출 규약:
        - 호출 전에 ``line`` 이 ``/`` 로 시작하는지 확인했다고 가정한다.
        - 미등록 명령이거나 슬래시만 입력된 경우, 안내 문자열과 ``CONTINUE`` 를 반환한다.

    Args:
        line: 사용자 입력 한 줄.
        registry: 사용할 레지스트리. ``None`` 이면 :data:`default_registry`.
        context: 명령에 전달할 :class:`ReplContext`. ``None`` 이면 본 함수가
            레지스트리만 채워 생성한다.

    Returns:
        :class:`CommandResult`.
    """
    reg = registry or default_registry
    token = parse_slash_input(line)
    if token is None:
        # 호출자가 보장하지 않은 경우의 방어적 처리. 슬래시가 아니면
        # 그대로 CONTINUE 를 반환하며 출력은 비운다.
        return CommandResult(action=CommandAction.CONTINUE, output="")

    if token == "":
        # ``/`` 만 입력된 경우 - help 안내로 폴백한다.
        return CommandResult(
            action=CommandAction.CONTINUE,
            output="Unknown command: /. Type /help for available commands.",
        )

    command = reg.get(token)
    if command is None:
        return CommandResult(
            action=CommandAction.CONTINUE,
            output=f"Unknown command: /{token}. Type /help for available commands.",
        )

    ctx = context or ReplContext(registry=reg)
    return command.execute(ctx)


__all__ = [
    "SlashCommandRegistry",
    "default_registry",
    "dispatch_command",
    "parse_slash_input",
    "register",
    "reset",
    "temporary_registry",
]
