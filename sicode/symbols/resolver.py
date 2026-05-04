"""심볼 / 파일 토큰 해석기(이슈 #17, #24).

:class:`SymbolResolver` 는 :class:`IndexerProtocol` 결과(:class:`SymbolRecord`
또는 :class:`FileRecord`) 를 in-memory 캐시로 보관하고, 토큰을 두 종류
모두에 대해 검색한다.

설계 메모:
    - SRP: 본 클래스는 "토큰 → 매칭 결과" 변환과 캐시 무효화만 책임진다.
      디스크 IO 와 사용자 입력 가공은 이웃 모듈이 담당.
    - DIP: 인덱서는 ``build() -> List[record]`` 한 메서드만 요구하는 작은
      프로토콜에 의존하므로, :class:`SymbolIndexer` / :class:`FileIndexer` /
      :class:`CompositeIndexer` 또는 임의의 stub 어느 것이든 주입 가능.
    - ISP: 외부에는 검색 메서드(``find`` / ``find_files``)와 조회 메서드
      (``all_records`` / ``all_symbols`` / ``all_files``)를 분리해 노출한다.
      자동완성기는 ``all_*`` 만, expander 는 ``find*`` 만 사용한다.
    - 상태 정책: 첫 검색 호출 시 lazy 인덱싱을 1회 수행한다.
      :meth:`invalidate` 가 호출되면 다음 검색 시 다시 인덱싱한다.
      ``/clear`` 슬래시 명령은 :meth:`invalidate` 만 호출한다.
"""

from __future__ import annotations

from typing import List, Optional, Protocol

from sicode.symbols.indexer import (
    FileRecord,
    IndexedRecord,
    SymbolRecord,
    make_default_composite_indexer,
)


class SymbolIndexerProtocol(Protocol):
    """인덱서가 지켜야 할 작은 계약(테스트에서 mock 교체 용).

    이름은 역사적 호환을 위해 ``Symbol-`` 접두사를 유지하지만, 본 프로토콜은
    :class:`SymbolRecord` / :class:`FileRecord` 어느 쪽이든 ``build()`` 가
    돌려주는 모든 레코드 타입을 받는다. 이름 변경은 외부 API 깨짐을 막기 위해
    하지 않는다.
    """

    def build(self) -> "List[IndexedRecord]":  # pragma: no cover - 프로토콜 정의
        ...


class SymbolResolver:
    """심볼/파일 인덱스를 lazy 로 만들고 토큰 검색을 제공하는 캐시.

    인스턴스 단위로 캐시를 보관한다. 같은 REPL 세션이 같은 resolver 를 공유
    하면 인덱싱 비용이 한 번만 발생한다(이슈 #17). 이슈 #24 부터는 같은 캐시
    안에 :class:`SymbolRecord` 와 :class:`FileRecord` 가 함께 보관되며, 두
    종류 모두 ``/clear`` 한 번으로 무효화된다.
    """

    def __init__(self, indexer: Optional[SymbolIndexerProtocol] = None) -> None:
        """리졸버를 초기화한다.

        Args:
            indexer: 사용할 인덱서. ``None`` 이면 :func:`make_default_composite_indexer`
                로 ``Path.cwd`` 기반 (SymbolIndexer + FileIndexer) 묶음이 lazy
                로 만들어진다(이슈 #24).
        """
        self._indexer: Optional[SymbolIndexerProtocol] = indexer
        self._cache: Optional[List[IndexedRecord]] = None

    def invalidate(self) -> None:
        """캐시를 비운다. 다음 검색 호출이 다시 인덱싱한다.

        이슈 #24: 단일 캐시 안에 심볼·파일 레코드가 함께 들어 있으므로
        ``/clear`` 한 번으로 두 종류 모두 무효화된다.
        """
        self._cache = None

    def all_records(self) -> List[IndexedRecord]:
        """현재 인덱스의 모든 레코드를 반환한다(없으면 lazy 인덱싱).

        ``SymbolCompleter`` 가 후보 산출에 사용하는 진입점.
        """
        return list(self._records())

    def all_symbols(self) -> List[SymbolRecord]:
        """심볼 종류의 레코드만 필터링해 반환한다(이슈 #24).

        기존 호출자(``SymbolCompleter._complete_symbol`` 같은 일부 경로) 가
        심볼만 보고 싶을 때 사용한다.
        """
        return [r for r in self._records() if isinstance(r, SymbolRecord)]

    def all_files(self) -> List[FileRecord]:
        """파일 종류의 레코드만 필터링해 반환한다(이슈 #24)."""
        return [r for r in self._records() if isinstance(r, FileRecord)]

    def find(self, token: str) -> List[SymbolRecord]:
        """토큰과 매칭되는 :class:`SymbolRecord` 리스트를 반환한다.

        매칭 정책(이슈 #17, 호환 유지):
            1. 대소문자 구분 정확 일치가 한 건이라도 있으면 그 결과만 반환.
            2. 정확 일치가 없으면 대소문자 무시 부분 일치(``substring``) 결과를 반환.
            3. 어떤 매칭도 없으면 빈 리스트.

        Args:
            token: ``@`` 가 제거된 식별자(예: ``Conversation``).

        Returns:
            매칭된 심볼 레코드 리스트. 입력 토큰이 빈 문자열이면 빈 리스트.
        """
        if not token:
            return []

        records = self.all_symbols()
        exact: List[SymbolRecord] = [r for r in records if r.name == token]
        if exact:
            return exact

        lowered = token.lower()
        partial: List[SymbolRecord] = [
            r for r in records if lowered in r.name.lower()
        ]
        return partial

    def find_files(self, token: str) -> List[FileRecord]:
        """토큰과 매칭되는 :class:`FileRecord` 리스트를 반환한다(이슈 #24).

        매칭 정책:
            1. 대소문자 구분 정확 일치(상대 경로 ``rel_path`` 또는 파일 이름
               ``name``) 가 있으면 그 결과만 반환.
            2. 확장자 생략 보정: ``token`` 에 ``"."`` 이 없으면
               ``f"{token}.*"`` 패턴으로 ``name`` 을 ``fnmatch`` 매칭(``@README``
               → ``README.md``).
            3. 위 둘 다 없으면 대소문자 무시 segment prefix 매칭. ``rel_path``
               를 ``"/"`` 로 분할해 마지막 segment 가 prefix 매치이면 후보.
               전체 ``rel_path`` 가 prefix 매치인 경우도 포함(``sicode/r`` →
               ``sicode/repl.py``).

        Args:
            token: ``@`` 가 제거된 파일 토큰(``README.md``, ``sicode/repl.py`` 등).

        Returns:
            매칭된 파일 레코드 리스트. 빈 토큰이면 빈 리스트.
        """
        if not token:
            return []

        records = self.all_files()

        # 1. 대소문자 구분 정확 일치(rel_path 또는 name).
        exact: List[FileRecord] = [
            r for r in records if r.rel_path == token or r.name == token
        ]
        if exact:
            return exact

        # 2. 확장자 생략 보정: ``@README`` → ``README.md`` 류.
        if "." not in token and "/" not in token:
            import fnmatch as _fn

            ext_match: List[FileRecord] = [
                r for r in records if _fn.fnmatch(r.name, f"{token}.*")
            ]
            if ext_match:
                return ext_match

        # 3. 대소문자 무시 segment / 경로 prefix 매칭.
        lowered = token.lower()
        partial: List[FileRecord] = []
        for record in records:
            rel_lower = record.rel_path.lower()
            name_lower = record.name.lower()
            # 전체 경로 prefix.
            if rel_lower.startswith(lowered):
                partial.append(record)
                continue
            # 마지막 segment prefix.
            if name_lower.startswith(lowered):
                partial.append(record)
                continue
        return partial

    # ------------------------------------------------------------------ helpers

    def _records(self) -> List[IndexedRecord]:
        """캐시가 있으면 재사용, 없으면 인덱서를 호출해 채운다."""
        if self._cache is None:
            indexer = self._indexer or make_default_composite_indexer()
            self._indexer = indexer
            self._cache = list(indexer.build())
        return self._cache


__all__ = [
    "SymbolIndexerProtocol",
    "SymbolResolver",
]
