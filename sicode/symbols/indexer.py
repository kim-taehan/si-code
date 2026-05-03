"""Python 심볼 인덱서(이슈 #17).

작업 디렉토리 트리를 한 번 walk 하면서 ``*.py`` 파일을 ``ast`` 로 파싱하고
``ClassDef`` / ``FunctionDef`` / ``AsyncFunctionDef`` 노드의 위치 정보와 원본
소스 슬라이스를 :class:`SymbolRecord` 로 수집한다.

설계 메모:
    - SRP: 본 모듈은 "디렉토리 → SymbolRecord 리스트" 변환만 담당한다. 캐시,
      검색, 사용자 입력 가공은 다른 모듈이 책임진다.
    - DIP: :class:`SymbolIndexer` 는 ``Path`` 와 디스크 IO 만 알고, 무시 패턴
      판정은 외부에서 주입받은 함수에 위임한다(테스트에서 교체 가능). 기본값은
      :func:`sicode.init.scanner._matches_any` 와 ``DEFAULT_IGNORE_PATTERNS``
      를 재사용해 "한 곳에만 보안 패턴이 정의되어 있다" 는 단일 출처 원칙을 유지.
    - 외부 의존성 없이 표준 라이브러리(``ast``, ``pathlib``, ``os``, ``fnmatch``)
      만 사용한다.
"""

from __future__ import annotations

import ast
import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

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


__all__ = [
    "DEFAULT_MAX_FILE_BYTES",
    "IgnoreMatcher",
    "SymbolIndexer",
    "SymbolRecord",
    "iter_python_files",
]
