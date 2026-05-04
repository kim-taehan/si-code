"""Python 심볼 / 파일 인덱서(이슈 #17, #24).

본 모듈은 두 종류의 인덱서를 제공한다.

- :class:`SymbolIndexer` (이슈 #17): 작업 디렉토리 트리를 walk 하며 ``*.py``
  파일을 ``ast`` 로 파싱해 ``ClassDef`` / ``FunctionDef`` /
  ``AsyncFunctionDef`` 노드를 :class:`SymbolRecord` 로 수집한다.
- :class:`FileIndexer` (이슈 #24): 같은 트리를 walk 하며 모든 비-바이너리·
  비-시크릿·비-과대 파일을 :class:`FileRecord` 로 수집한다. ``@README``,
  ``@sicode/repl.py`` 같은 파일 토큰 자동완성/expand 의 데이터 소스다.
- :class:`CompositeIndexer` (이슈 #24): 두 인덱서를 묶어
  :class:`IndexerProtocol` 한 번의 ``build()`` 로 통합 레코드를 돌려준다.

설계 메모:
    - SRP: 각 인덱서는 "디렉토리 → 자기 종류의 레코드 리스트" 변환만 담당한다.
      캐시·검색·첨부는 :mod:`resolver` / :mod:`expand` 가 책임진다.
    - DIP: 각 인덱서는 ``Path`` 와 디스크 IO 만 알고, 무시 패턴 판정은 외부
      에서 주입받은 함수에 위임한다(테스트에서 교체 가능). 기본값은
      :data:`sicode.init.scanner.DEFAULT_IGNORE_PATTERNS` 한 곳에서만 정의된
      보안 패턴을 재사용한다.
    - ISP: :class:`IndexerProtocol` 은 ``build() -> Iterable[record]`` 한
      메서드만 요구한다. 콘크리트 인덱서는 자기 record 타입을 돌려주기만 하면
      교체 가능하다.
    - 외부 의존성 없이 표준 라이브러리(``ast``, ``pathlib``, ``os``,
      ``fnmatch``) 만 사용한다.
"""

from __future__ import annotations

import ast
import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Protocol, Sequence, Tuple, Union

from sicode.init.scanner import DEFAULT_IGNORE_PATTERNS

#: 1 MB. 이슈 #17 정책에 따라 초과 파일은 인덱싱하지 않는다.
DEFAULT_MAX_FILE_BYTES: int = 1024 * 1024


@dataclass(frozen=True)
class SymbolRecord:
    """인덱싱된 단일 심볼.

    Attributes:
        name: 심볼 이름(``ClassDef.name`` / ``FunctionDef.name``).
        kind: ``"class"`` 또는 ``"function"`` (async 함수도 ``"function"``).
        rel_path: 작업 루트 기준 상대 경로(POSIX 스타일).
        start_line: 정의 시작 라인 번호(1-base).
        end_line: 정의 끝 라인 번호(1-base, 포함).
        source: 원본 파일에서 슬라이스한 정의 본문.
    """

    name: str
    kind: str
    rel_path: str
    start_line: int
    end_line: int
    source: str


#: 무시 패턴 매칭 함수 시그니처. 디렉토리/파일 이름 한 건을 받고 무시 여부 반환.
IgnoreMatcher = Callable[[str], bool]


def _default_ignore_matcher() -> IgnoreMatcher:
    """:data:`DEFAULT_IGNORE_PATTERNS` 기반 기본 매처를 만든다.

    SRP: 패턴 정의 자체는 :mod:`sicode.init.scanner` 의 단일 출처에 둔다.
    """
    patterns: Tuple[str, ...] = tuple(DEFAULT_IGNORE_PATTERNS)

    def _match(name: str) -> bool:
        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    return _match


def _safe_size(path: Path) -> int:
    """``stat`` 실패 시 ``0`` 을 돌려주는 안전 헬퍼."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read_text(path: Path) -> Optional[str]:
    """UTF-8 텍스트 파일을 읽는다. 권한/디코드 실패 시 ``None``."""
    try:
        with path.open("rb") as fh:
            data = fh.read()
    except OSError:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # ASCII 외 인코딩 파일은 인덱싱 대상에서 제외한다(파싱 실패 가능성).
        return None


def _slice_source(lines: List[str], start_line: int, end_line: int) -> str:
    """1-base 라인 번호로 원본을 슬라이스한다(끝 포함)."""
    if start_line < 1:
        start_line = 1
    if end_line < start_line:
        end_line = start_line
    # ``lines`` 는 splitlines(keepends=True) 결과여서 그대로 join 하면 원본이 복원된다.
    return "".join(lines[start_line - 1 : end_line])


def _relpath(path: Path, root: Path) -> str:
    """루트 기준 상대 경로(POSIX 스타일)."""
    rel = os.path.relpath(str(path), str(root))
    return rel.replace(os.sep, "/")


class SymbolIndexer:
    """디렉토리 트리에서 Python 심볼 정의를 수집하는 인덱서.

    한 인스턴스는 (root, ignore_matcher, max_file_bytes) 한 묶음을 표현한다.
    :meth:`build` 는 매 호출마다 디스크를 새로 walk 한다(캐싱은 :class:`SymbolResolver`
    의 책임).
    """

    def __init__(
        self,
        root: Optional[Path] = None,
        *,
        ignore_matcher: Optional[IgnoreMatcher] = None,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        """인덱서를 초기화한다.

        Args:
            root: 인덱싱 루트. ``None`` 이면 :func:`Path.cwd`.
            ignore_matcher: 디렉토리/파일 이름을 받고 무시 여부를 반환하는 함수.
                ``None`` 이면 :data:`DEFAULT_IGNORE_PATTERNS` 기반 기본 매처를
                사용한다(보안 시크릿 패턴 포함).
            max_file_bytes: 단일 파일 최대 크기. 초과 파일은 인덱싱하지 않는다.
        """
        self._root: Path = (root or Path.cwd()).resolve()
        self._ignore: IgnoreMatcher = ignore_matcher or _default_ignore_matcher()
        self._max_file_bytes: int = max_file_bytes

    @property
    def root(self) -> Path:
        """인덱싱 루트 경로(절대)."""
        return self._root

    def build(self) -> List[SymbolRecord]:
        """루트를 한 번 walk 해 :class:`SymbolRecord` 리스트를 만든다.

        Returns:
            인덱싱된 모든 심볼. 호출자가 후처리(정렬/필터)하기 좋도록 그대로
            반환한다. 같은 파일에서 정의 순서대로, 파일 간에는 walk 순서대로
            기록된다.
        """
        records: List[SymbolRecord] = []
        self._walk(self._root, records)
        return records

    # ------------------------------------------------------------------ helpers

    def _walk(self, directory: Path, sink: List[SymbolRecord]) -> None:
        """단일 디렉토리를 재귀적으로 처리한다."""
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return

        for child in children:
            name = child.name
            if self._ignore(name):
                continue
            if child.is_symlink():
                # 심볼릭 링크는 따라가지 않는다(루프/외부 노출 방지).
                continue
            if child.is_dir():
                self._walk(child, sink)
                continue
            if child.is_file() and name.endswith(".py"):
                self._index_file(child, sink)

    def _index_file(self, path: Path, sink: List[SymbolRecord]) -> None:
        """단일 ``*.py`` 파일을 인덱싱해 ``sink`` 에 누적."""
        size = _safe_size(path)
        if size > self._max_file_bytes:
            return

        text = _read_text(path)
        if text is None:
            return

        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            # 깨진 Python 파일은 조용히 건너뛴다.
            return

        rel = _relpath(path, self._root)
        # 원본 슬라이싱을 위해 keepends=True 로 라인 분할.
        lines = text.splitlines(keepends=True)

        for node in ast.walk(tree):
            kind = _node_kind(node)
            if kind is None:
                continue
            start_line = getattr(node, "lineno", None)
            end_line = getattr(node, "end_lineno", None)
            if not isinstance(start_line, int):
                continue
            if not isinstance(end_line, int):
                end_line = start_line
            source = _slice_source(lines, start_line, end_line)
            sink.append(
                SymbolRecord(
                    name=node.name,  # type: ignore[attr-defined]
                    kind=kind,
                    rel_path=rel,
                    start_line=start_line,
                    end_line=end_line,
                    source=source,
                )
            )


def _node_kind(node: ast.AST) -> Optional[str]:
    """AST 노드가 인덱싱 대상이면 종류 문자열을 반환한다."""
    if isinstance(node, ast.ClassDef):
        return "class"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "function"
    return None


def iter_python_files(
    root: Path,
    *,
    ignore_matcher: Optional[IgnoreMatcher] = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> Iterable[Path]:
    """디버깅/테스트 편의용: 인덱싱 대상으로 인정되는 ``*.py`` 경로 이터레이터."""
    matcher = ignore_matcher or _default_ignore_matcher()

    def _walk(directory: Path) -> Iterable[Path]:
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for child in children:
            if matcher(child.name):
                continue
            if child.is_symlink():
                continue
            if child.is_dir():
                yield from _walk(child)
            elif child.is_file() and child.name.endswith(".py"):
                if _safe_size(child) <= max_file_bytes:
                    yield child

    yield from _walk(root)


# ---------------------------------------------------------------------------
# File indexer (issue #24)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileRecord:
    """인덱싱된 단일 파일.

    ``@파일명`` 자동완성/expand 의 데이터 소스(이슈 #24).

    Attributes:
        name: 파일의 마지막 segment(``README.md``, ``repl.py`` 등).
        rel_path: 인덱싱 루트 기준 POSIX 상대 경로.
        kind: 항상 ``"file"``. :class:`SymbolRecord` 와의 식별 차단용.
        start_line: 항상 ``1`` (이슈 본문 정책). expand 시 본문은 1번 라인부터
            시작한다.
        snippet: 본문 미리보기. 인덱싱 시점에는 메모리 부담을 피하기 위해
            기본적으로 빈 문자열이다. 본문 첨부는 :class:`SymbolExpander` 가
            expand 시점에 디스크에서 다시 읽는다(저용량 인덱스 유지).
        size_bytes: 파일 크기. ``stat`` 실패 시 ``0``.
        is_binary: NULL 바이트 휴리스틱으로 바이너리로 판정된 경우 ``True``.
        is_oversize: ``max_file_bytes`` 초과 시 ``True``.
    """

    name: str
    rel_path: str
    kind: str = "file"
    start_line: int = 1
    snippet: str = ""
    size_bytes: int = 0
    is_binary: bool = False
    is_oversize: bool = False


#: :class:`SymbolIndexer` / :class:`FileIndexer` 가 돌려주는 통합 레코드 타입.
IndexedRecord = Union[SymbolRecord, FileRecord]


def _is_probably_binary(path: Path, sample_size: int = 1024) -> bool:
    """파일 앞부분의 NULL 바이트 존재 여부로 바이너리 추정(이슈 #24).

    :func:`sicode.init.scanner._is_probably_binary` 와 동일한 휴리스틱이지만,
    의존 방향을 단순하게 유지하기 위해 패턴만 재구현했다(상수
    ``DEFAULT_IGNORE_PATTERNS`` 만 import). 권한 문제 등으로 읽기 실패 시
    안전하게 바이너리로 간주해 본문 수집을 건너뛴다.
    """
    try:
        with path.open("rb") as fh:
            chunk = fh.read(sample_size)
    except OSError:
        return True
    if not chunk:
        return False
    return b"\x00" in chunk


class FileIndexer:
    """디렉토리 트리에서 일반 파일을 :class:`FileRecord` 로 수집하는 인덱서.

    수집 정책(이슈 #24):
        - 이름 기반 무시 패턴(:data:`DEFAULT_IGNORE_PATTERNS`) 에 매칭되는 파일·
          디렉토리는 제외한다(시크릿/`.git/`/`__pycache__/` 등).
        - 심볼릭 링크는 따라가지 않는다(루프 / 외부 노출 방지).
        - 크기/바이너리 판정 결과는 :class:`FileRecord` 의 플래그로 기록되지만
          레코드 자체는 인덱스에 포함된다(자동완성 후보로 노출하기 위함).
          본문 첨부는 expander 가 같은 플래그를 다시 검사해 결정한다.
        - ``snippet`` 은 인덱싱 시점에 본문을 읽지 않는다(저용량 인덱스).
    """

    def __init__(
        self,
        root: Optional[Path] = None,
        *,
        ignore_matcher: Optional[IgnoreMatcher] = None,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        """인덱서를 초기화한다.

        Args:
            root: 인덱싱 루트. ``None`` 이면 :func:`Path.cwd`.
            ignore_matcher: 디렉토리/파일 이름을 받고 무시 여부를 반환하는 함수.
                ``None`` 이면 :data:`DEFAULT_IGNORE_PATTERNS` 기반 기본 매처.
            max_file_bytes: ``is_oversize`` 플래그 임계값. 초과해도 인덱스에는
                포함되지만 본문은 첨부되지 않는다.
        """
        self._root: Path = (root or Path.cwd()).resolve()
        self._ignore: IgnoreMatcher = ignore_matcher or _default_ignore_matcher()
        self._max_file_bytes: int = max_file_bytes

    @property
    def root(self) -> Path:
        """인덱싱 루트 경로(절대)."""
        return self._root

    def build(self) -> List[FileRecord]:
        """루트를 한 번 walk 해 :class:`FileRecord` 리스트를 만든다."""
        records: List[FileRecord] = []
        self._walk(self._root, records)
        return records

    # ------------------------------------------------------------------ helpers

    def _walk(self, directory: Path, sink: List[FileRecord]) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return

        for child in children:
            name = child.name
            if self._ignore(name):
                continue
            if child.is_symlink():
                continue
            if child.is_dir():
                self._walk(child, sink)
                continue
            if child.is_file():
                self._index_file(child, sink)

    def _index_file(self, path: Path, sink: List[FileRecord]) -> None:
        size = _safe_size(path)
        is_oversize = size > self._max_file_bytes
        # 바이너리 판정은 oversize 가 아닐 때만 시도(거대한 파일을 굳이 1KB 읽을
        # 필요는 없지만, 안전하게 oversize 면 메타만 남기고 바이너리 판정 생략).
        is_binary = False if is_oversize else _is_probably_binary(path)
        rel = _relpath(path, self._root)
        sink.append(
            FileRecord(
                name=path.name,
                rel_path=rel,
                kind="file",
                start_line=1,
                snippet="",
                size_bytes=size,
                is_binary=is_binary,
                is_oversize=is_oversize,
            )
        )


# ---------------------------------------------------------------------------
# Composite indexer (issue #24)
# ---------------------------------------------------------------------------


class _IndexerLike(Protocol):
    """:class:`CompositeIndexer` 가 받아들이는 최소 인덱서 계약(ISP).

    ``build()`` 한 메서드만 요구한다. :class:`SymbolIndexer`,
    :class:`FileIndexer`, 또는 임의의 stub 인덱서가 모두 본 프로토콜을
    만족한다.
    """

    def build(self) -> "List[IndexedRecord]":  # pragma: no cover - 프로토콜 정의
        ...


class CompositeIndexer:
    """여러 :class:`_IndexerLike` 결과를 하나의 ``build()`` 로 통합한다.

    SRP/DIP: 본 클래스는 "여러 인덱서의 결과 합치기" 한 가지 책임만 갖고,
    각 인덱서는 자기 종류의 레코드 수집만 담당한다.
    :class:`SymbolResolver` 는 :class:`_IndexerLike` 추상에만 의존하므로
    :class:`SymbolIndexer` 단독, :class:`CompositeIndexer` 로 묶인 두 인덱서,
    또는 테스트용 stub 등 어떤 것이든 주입할 수 있다.
    """

    def __init__(
        self,
        indexers: Sequence[_IndexerLike],
    ) -> None:
        """여러 인덱서를 묶는다.

        Args:
            indexers: ``build()`` 메서드를 가진 객체의 시퀀스. 호출 순서대로
                결과가 이어 붙는다.
        """
        self._indexers: Tuple[_IndexerLike, ...] = tuple(indexers)

    def build(self) -> List[IndexedRecord]:
        """모든 인덱서를 호출해 결과를 이어 붙인다.

        Returns:
            각 인덱서가 돌려준 레코드를 호출 순서대로 이어 붙인 리스트.
        """
        merged: List[IndexedRecord] = []
        for indexer in self._indexers:
            merged.extend(indexer.build())
        return merged


def make_default_composite_indexer(
    root: Optional[Path] = None,
    *,
    ignore_matcher: Optional[IgnoreMatcher] = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> CompositeIndexer:
    """기본 :class:`SymbolIndexer` + :class:`FileIndexer` 묶음을 만든다.

    :class:`SymbolResolver` 가 ``indexer=None`` 으로 초기화될 때 lazy 로 호출
    하는 팩토리. 같은 (root, ignore_matcher, max_file_bytes) 설정을 두 인덱서
    가 공유하도록 보장한다.
    """
    symbol_indexer = SymbolIndexer(
        root,
        ignore_matcher=ignore_matcher,
        max_file_bytes=max_file_bytes,
    )
    file_indexer = FileIndexer(
        root,
        ignore_matcher=ignore_matcher,
        max_file_bytes=max_file_bytes,
    )
    return CompositeIndexer([symbol_indexer, file_indexer])


__all__ = [
    "DEFAULT_MAX_FILE_BYTES",
    "CompositeIndexer",
    "FileIndexer",
    "FileRecord",
    "IgnoreMatcher",
    "IndexedRecord",
    "SymbolIndexer",
    "SymbolRecord",
    "iter_python_files",
    "make_default_composite_indexer",
]
