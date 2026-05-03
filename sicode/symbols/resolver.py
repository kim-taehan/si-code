"""심볼 토큰 해석기(이슈 #17).

:class:`SymbolResolver` 는 :class:`SymbolIndexer` 가 만든 :class:`SymbolRecord`
목록을 in-memory 캐시로 보관하고, 토큰을 정확 일치 → 대소문자 무시 부분
일치 순서로 검색한다.

설계 메모:
    - SRP: 본 클래스는 "토큰 → 매칭 결과" 변환과 캐시 무효화만 책임진다.
      디스크 IO 와 사용자 입력 가공은 이웃 모듈이 담당.
    - DIP: :class:`SymbolIndexer` 추상에 의존하므로 테스트에서 다른 인덱서를
      주입할 수 있다(예: 미리 만들어 둔 in-memory 인덱서).
    - 상태 정책: 첫 :meth:`find` 호출 시 lazy 인덱싱을 1회 수행한다.
      :meth:`invalidate` 가 호출되면 다음 :meth:`find` 시 다시 인덱싱한다.
      ``/clear`` 슬래시 명령은 :meth:`invalidate` 만 호출한다.
"""

from __future__ import annotations

from typing import List, Optional, Protocol

from sicode.symbols.indexer import SymbolIndexer, SymbolRecord


class SymbolIndexerProtocol(Protocol):
    """인덱서가 지켜야 할 작은 계약(테스트에서 mock 교체 용)."""

    def build(self) -> List[SymbolRecord]:  # pragma: no cover - 프로토콜 정의
        ...


class SymbolResolver:
    """심볼 인덱스를 lazy 로 만들고 토큰 검색을 제공하는 캐시.

    인스턴스 단위로 캐시를 보관한다. 같은 REPL 세션이 같은 resolver 를 공유
    하면 인덱싱 비용이 한 번만 발생한다.
    """

    def __init__(self, indexer: Optional[SymbolIndexerProtocol] = None) -> None:
        """리졸버를 초기화한다.

        Args:
            indexer: 사용할 인덱서. ``None`` 이면 기본 :class:`SymbolIndexer`
                (``Path.cwd`` 루트, 기본 ignore/크기) 가 lazy 로 만들어진다.
        """
        self._indexer: Optional[SymbolIndexerProtocol] = indexer
        self._cache: Optional[List[SymbolRecord]] = None

    def invalidate(self) -> None:
        """캐시를 비운다. 다음 :meth:`find` 호출이 다시 인덱싱한다."""
        self._cache = None

    def all_records(self) -> List[SymbolRecord]:
        """현재 인덱스의 모든 레코드를 반환한다(없으면 lazy 인덱싱)."""
        return list(self._records())

    def find(self, token: str) -> List[SymbolRecord]:
        """토큰과 매칭되는 :class:`SymbolRecord` 리스트를 반환한다.

        매칭 정책(이슈 #17):
            1. 대소문자 구분 정확 일치가 한 건이라도 있으면 그 결과만 반환.
            2. 정확 일치가 없으면 대소문자 무시 부분 일치(``substring``) 결과를 반환.
            3. 어떤 매칭도 없으면 빈 리스트.

        Args:
            token: ``@`` 가 제거된 식별자(예: ``Conversation``).

        Returns:
            매칭된 레코드 리스트. 입력 토큰이 빈 문자열이면 빈 리스트.
        """
        if not token:
            return []

        records = self._records()
        exact: List[SymbolRecord] = [r for r in records if r.name == token]
        if exact:
            return exact

        lowered = token.lower()
        partial: List[SymbolRecord] = [
            r for r in records if lowered in r.name.lower()
        ]
        return partial

    # ------------------------------------------------------------------ helpers

    def _records(self) -> List[SymbolRecord]:
        """캐시가 있으면 재사용, 없으면 인덱서를 호출해 채운다."""
        if self._cache is None:
            indexer = self._indexer or SymbolIndexer()
            self._indexer = indexer
            self._cache = list(indexer.build())
        return self._cache


__all__ = [
    "SymbolIndexerProtocol",
    "SymbolResolver",
]
