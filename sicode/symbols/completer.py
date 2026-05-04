"""readline 기반 ``@심볼`` / ``/명령`` 자동완성기(이슈 #20).

REPL 입력 도중 ``Tab`` 키를 누르면 readline 이 본 모듈의
:class:`SymbolCompleter` 콜백을 ``state=0, 1, 2, ...`` 순으로 호출한다. 콜백은
``state`` 마다 다음 후보 문자열을 반환하고, 더 이상 후보가 없으면 ``None`` 을
반환한다. ``readline`` 의 호출 규약은 다음과 같다::

    completer(text: str, state: int) -> Optional[str]

설계 메모:
    - SRP: 본 모듈은 "텍스트 prefix → 후보 문자열 리스트" 변환과 readline 초기화
      두 가지 좁은 책임만 갖는다. 후보 데이터 자체(심볼 인덱스, 명령 레지스트리)
      는 외부 객체에서 받는다.
    - DIP: :class:`SymbolCompleter` 는 :class:`SymbolResolverProtocol`,
      :class:`SlashRegistryProtocol` 추상에만 의존한다. 테스트는 작은 fake 객체를
      주입해 readline 없이 콜백을 검증할 수 있다.
    - ISP: 외부에 강요하는 메서드가 ``all_records()`` / ``commands()`` 두 개로
      쪼개져 있어 큰 인터페이스 한 덩어리에 엮이지 않는다.
    - 안전성: ``import readline`` 이 실패하는 환경(예: Windows 기본 파이썬,
      pyreadline3 미설치)에서도 :func:`setup_readline_completer` 는 조용히 리턴해
      REPL 본체가 동작 가능한 상태를 유지한다(graceful fallback).

수용 기준 매핑(이슈 #20):
    - ``@`` prefix 후보: :meth:`SymbolCompleter._complete_symbol`.
    - ``/`` prefix 후보: :meth:`SymbolCompleter._complete_slash`.
    - 매치 0건일 때 ``state=0`` 에 ``None`` 반환: :meth:`SymbolCompleter.__call__`.
    - 후보 상한 20건: :data:`MAX_SYMBOL_CANDIDATES`.
    - readline 미존재 환경에서 예외 없이 노옵: :func:`setup_readline_completer`.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Protocol

from sicode.symbols.indexer import SymbolRecord


#: 한 번의 ``@`` prefix 자동완성에서 노출할 최대 후보 수.
#: 이슈 #20 정책: 인덱스에 심볼이 더 많아도 20건만 반환한다.
MAX_SYMBOL_CANDIDATES: int = 20

#: ``readline.set_completer_delims`` 에 설정할 구분자 문자열.
#: 기본 delims 에는 ``@`` 가 포함돼 있어 ``@Symbol`` 입력 도중 Tab 을 누르면
#: ``@`` 가 잘려 나간 ``Symbol`` 만 ``text`` 로 전달된다. 본 정책은 공백/탭/
#: 개행만 토큰 경계로 보고, ``@`` 와 ``/`` 는 토큰 본체로 유지한다.
COMPLETER_DELIMS: str = " \t\n"


class SymbolResolverProtocol(Protocol):
    """후보 산출에 필요한 :class:`SymbolResolver` 의 최소 계약(ISP)."""

    def all_records(self) -> List[SymbolRecord]:  # pragma: no cover - 프로토콜
        ...


class SlashRegistryProtocol(Protocol):
    """슬래시 명령 후보 산출에 필요한 레지스트리의 최소 계약(ISP).

    실제 :class:`sicode.commands.registry.SlashCommandRegistry` 는
    ``commands() -> List[SlashCommand]`` 를 제공한다. 본 프로토콜은 그중에서도
    ``cmd.name`` 만 사용한다.
    """

    def commands(self) -> "List[_NamedCommand]":  # pragma: no cover - 프로토콜
        ...


class _NamedCommand(Protocol):
    """슬래시 명령 후보에 사용할 최소 속성(``name``)만 요구하는 프로토콜."""

    name: str


class SymbolCompleter:
    """``@심볼`` 과 ``/명령`` 자동완성을 제공하는 readline 콜백.

    한 인스턴스는 ``(resolver, registry)`` 쌍 한 묶음을 표현한다. ``state=0``
    일 때 ``text`` 의 prefix 분기에 따라 후보 리스트를 1회 계산해 캐시하고,
    같은 ``text`` 에 대한 후속 ``state>=1`` 호출에서는 캐시된 리스트의 다음
    인덱스를 반환한다. ``text`` 가 바뀌면 캐시가 자연스럽게 재계산된다.
    """

    def __init__(
        self,
        resolver: SymbolResolverProtocol,
        registry: Optional[SlashRegistryProtocol] = None,
    ) -> None:
        """완성기를 초기화한다.

        Args:
            resolver: ``@`` 후보 산출에 사용할 심볼 리졸버. 본 객체는 ``all_records()``
                만 호출되며, 캐시/invalidate 정책은 :class:`SymbolResolver` 본체가
                책임진다.
            registry: ``/`` 후보 산출에 사용할 슬래시 명령 레지스트리. ``None`` 이면
                슬래시 자동완성은 후보 0건이 된다.
        """
        self._resolver: SymbolResolverProtocol = resolver
        self._registry: Optional[SlashRegistryProtocol] = registry
        # readline 콜백은 같은 ``text`` 로 ``state=0,1,2,...`` 를 순차 호출한다.
        # 이를 위해 (text, candidates) 한 쌍을 캐시한다.
        self._cached_text: Optional[str] = None
        self._cached_candidates: List[str] = []

    # ------------------------------------------------------------------ readline

    def __call__(self, text: str, state: int) -> Optional[str]:
        """readline 콜백 시그니처.

        Args:
            text: 현재 토큰(공백으로 분할된 마지막 단어). delims 를 공백 한정으로
                재설정해 두면 ``@Symbol`` 또는 ``/cmd`` 가 통째로 들어온다.
            state: 0 부터 시작해 ``None`` 반환 시까지 1 씩 증가하며 호출된다.

        Returns:
            ``state`` 번째 후보 문자열. 후보가 더 없으면 ``None``.
        """
        if state == 0:
            self._cached_text = text
            self._cached_candidates = self._build_candidates(text)

        # 다른 ``text`` 에 대한 ``state>=1`` 호출은 비정상 흐름이지만, 방어적으로
        # 빈 리스트로 취급해 ``None`` 을 돌려준다.
        if text != self._cached_text:
            return None

        if state < 0 or state >= len(self._cached_candidates):
            return None
        return self._cached_candidates[state]

    # ------------------------------------------------------------------ helpers

    def _build_candidates(self, text: str) -> List[str]:
        """``text`` 의 prefix 에 따라 후보 리스트를 만든다.

        - ``@...`` : 심볼 prefix 일치(대소문자 구분, 알파벳 오름차순, 최대 20).
        - ``/...`` : 슬래시 명령 prefix 일치(알파벳 오름차순).
        - 그 외   : 빈 리스트(자동완성 비활성).
        """
        if text.startswith("@"):
            return self._complete_symbol(text[1:])
        if text.startswith("/"):
            return self._complete_slash(text[1:])
        return []

    def _complete_symbol(self, prefix: str) -> List[str]:
        """심볼 이름 prefix 일치 후보를 ``@Name`` 형태로 반환한다.

        매칭 정책(이슈 #20):
            - 대소문자 구분 prefix 일치.
            - 같은 이름 중복 제거(파일이 달라도 이름이 같으면 후보 하나).
            - 알파벳 오름차순 정렬.
            - 최대 :data:`MAX_SYMBOL_CANDIDATES` 건.

        ``prefix`` 가 빈 문자열이면 ``@`` 만 입력된 상태이므로 인덱스 전체에서
        앞 20건을 보여 준다(이미 정렬·중복 제거된 결과).
        """
        records = self._resolver.all_records()
        seen: "dict[str, None]" = {}
        for record in records:
            name = record.name
            if not name.startswith(prefix):
                continue
            if name in seen:
                continue
            seen[name] = None
        names = sorted(seen.keys())
        limited = names[:MAX_SYMBOL_CANDIDATES]
        return [f"@{name}" for name in limited]

    def _complete_slash(self, prefix: str) -> List[str]:
        """슬래시 명령 이름 prefix 일치 후보를 ``/name`` 형태로 반환한다.

        ``SlashCommandRegistry.commands()`` 결과는 이미 알파벳 오름차순이지만,
        본 메서드는 입력 정렬에 의존하지 않고 자체 정렬해 안정성을 보장한다.
        """
        if self._registry is None:
            return []
        names: List[str] = []
        for cmd in self._registry.commands():
            cmd_name = getattr(cmd, "name", "")
            if not cmd_name:
                continue
            if cmd_name.startswith(prefix):
                names.append(cmd_name)
        names.sort()
        return [f"/{name}" for name in names]


def setup_readline_completer(
    completer: SymbolCompleter,
    registry: Optional[SlashRegistryProtocol] = None,
) -> bool:
    """readline 에 ``completer`` 를 등록하고 Tab 자동완성을 활성화한다.

    Args:
        completer: 등록할 :class:`SymbolCompleter` 인스턴스.
        registry: 본 함수는 슬래시 자동완성을 위해 별도로 레지스트리를 사용하지
            않는다(``completer`` 가 이미 보유). 인자는 호출 측 대칭성/추후 확장
            용으로 받아 두며, ``None`` 또는 임의 객체를 넘겨도 동작에 영향 없다.

    Returns:
        readline 가 사용 가능해 정상 등록된 경우 ``True``, 환경에 readline 모듈이
        없거나 초기화 단계에서 예외가 난 경우 ``False``. 어느 쪽이든 예외는
        밖으로 던지지 않는다(graceful fallback — REPL 본체는 계속 살아 있다).

    Notes:
        - ``readline.set_completer_delims`` 를 :data:`COMPLETER_DELIMS` 로 재설정
          한다. 기본값에는 ``@`` 가 포함돼 ``@Symbol`` 의 ``@`` 가 잘려 나가므로
          본 옵션은 필수다.
        - ``readline.parse_and_bind("tab: complete")`` 로 Tab 키에 자동완성을
          바인딩한다. macOS 의 libedit 호환 readline 도 같은 문자열을 인식한다.
    """
    try:
        import readline  # type: ignore[import-not-found]
    except Exception:
        return False

    try:
        readline.set_completer(completer)
        readline.set_completer_delims(COMPLETER_DELIMS)
        readline.parse_and_bind("tab: complete")
    except Exception:
        # 일부 환경(특히 stub readline)에서 set_completer 가 NotImplementedError
        # 등을 던질 수 있다. 본 경로는 REPL 시작을 막지 않는다.
        return False
    return True


#: 외부에서 ``setup_readline_completer`` 의 본문 변경 없이 readline 모듈을
#: 교체하고 싶을 때 사용하는 임포트 hook 자리. 현재는 노출하지 않는다.
_ImportHook = Callable[[], object]


__all__ = [
    "COMPLETER_DELIMS",
    "MAX_SYMBOL_CANDIDATES",
    "SlashRegistryProtocol",
    "SymbolCompleter",
    "SymbolResolverProtocol",
    "setup_readline_completer",
]
