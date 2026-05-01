"""프로젝트 디렉토리 정적 스캐너.

요구사항(이슈 #9):
    - ``Path.cwd()`` 를 루트로 하여 디렉토리 트리를 최대 4단계 깊이까지 수집한다.
    - ``.git/``, ``__pycache__/``, ``*.pyc``, ``.env``, ``*.pem``, ``id_rsa*`` 등
      보안·노이즈 패턴은 스캔에서 제외한다.
    - 1 MB 초과 파일 및 텍스트로 판별되지 않는 바이너리 파일은 내용을 읽지 않고
      메타데이터(경로, 크기) 만 기록한다.
    - ``pyproject.toml``, ``package.json``, ``README.md``, ``Makefile``, ``*.toml``,
      ``requirements*.txt`` 등 프로젝트 메타데이터 파일은 내용을 읽어 요약한다.

설계 메모:
    - SRP: 본 모듈은 파일 시스템에서 정보 수집만 담당한다. 마크다운 렌더링은
      :mod:`sicode.init.renderer` 가, 명령 처리/저장은 :mod:`sicode.init.command`
      가 담당한다.
    - OCP: 깊이/패턴/크기 한도는 모두 함수 인자로 주입할 수 있다. 기본값은 모듈 상수
      (``DEFAULT_*``) 로 노출한다.
    - 외부 의존성 없이 표준 라이브러리(``pathlib``, ``fnmatch``, ``os``) 만 사용한다.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


#: 기본 최대 깊이. 루트(0) 포함하여 자식 디렉토리는 4단계까지 내려간다.
DEFAULT_MAX_DEPTH: int = 4

#: 1 MB. 초과 파일은 내용 없이 메타데이터만 남긴다.
DEFAULT_MAX_FILE_BYTES: int = 1024 * 1024

#: 파일/디렉토리 이름에 적용하는 무시 패턴(``fnmatch`` 스타일).
#:
#: 디렉토리 이름과 파일 이름 양쪽에 적용된다. 보안 관련(``.env``, ``*.pem``,
#: ``id_rsa*``) 항목은 절대 읽히지 않도록 가장 먼저 검사한다.
DEFAULT_IGNORE_PATTERNS: Tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    "*.egg-info",
    # 환경/시크릿 (보수적 디폴트). 이름 패턴은 ``fnmatch`` 스타일이며 디렉토리·
    # 파일 양쪽에 적용된다. 정확한 이름 외에 ``*`` 와일드카드로 변형까지 차단.
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.crt",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa*",  # id_rsa, id_rsa.pub, id_rsa_legacy 등 모든 변형
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "secrets.*",
    "secret.*",
    "credentials*",
    "*.netrc",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "*.aws",
    ".aws",
    ".DS_Store",
)

#: 프로젝트 메타데이터로 인식할 파일 이름 패턴(``fnmatch`` 스타일).
#:
#: 매칭된 파일은 본문을 읽어 마크다운 요약 섹션에 포함한다.
DEFAULT_METADATA_PATTERNS: Tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "README.md",
    "README.rst",
    "Makefile",
    "*.toml",
    "requirements*.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Cargo.toml",
    "go.mod",
)

#: 메타데이터 파일에서 읽어들일 최대 바이트(렌더 시 잘려도 무방하도록 보수적 한도).
DEFAULT_METADATA_MAX_BYTES: int = 64 * 1024


@dataclass(frozen=True)
class FileEntry:
    """파일 항목.

    Attributes:
        path: 루트로부터의 상대 경로(POSIX 스타일).
        size_bytes: 바이트 크기. ``stat`` 실패 시 ``0`` 으로 기록될 수 있다.
        is_binary: 텍스트로 판별되지 않은 경우 ``True``.
        is_oversize: ``DEFAULT_MAX_FILE_BYTES`` 초과 시 ``True``.
    """

    path: str
    size_bytes: int
    is_binary: bool = False
    is_oversize: bool = False


@dataclass(frozen=True)
class DirectoryEntry:
    """디렉토리 트리 노드.

    Attributes:
        path: 루트로부터의 상대 경로(POSIX 스타일). 루트 자신은 ``"."``.
        depth: 루트(0) 기준 깊이.
        directories: 직속 하위 디렉토리.
        files: 직속 하위 파일.
        truncated: 깊이 한도를 만나 이 디렉토리의 내용을 더 이상 탐색하지 않은 경우 ``True``.
    """

    path: str
    depth: int
    directories: Tuple["DirectoryEntry", ...] = field(default_factory=tuple)
    files: Tuple[FileEntry, ...] = field(default_factory=tuple)
    truncated: bool = False


@dataclass(frozen=True)
class MetadataFile:
    """프로젝트 메타데이터 파일의 본문 스냅샷.

    Attributes:
        path: 루트로부터의 상대 경로(POSIX 스타일).
        content: 텍스트 본문. 길이가 길면 ``truncated=True`` 와 함께 잘릴 수 있다.
        truncated: ``DEFAULT_METADATA_MAX_BYTES`` 초과로 잘린 경우 ``True``.
    """

    path: str
    content: str
    truncated: bool = False


@dataclass(frozen=True)
class ProjectSnapshot:
    """정적 스캔 결과 전체를 표현하는 불변 데이터.

    Attributes:
        root: 스캔 루트의 절대 경로 문자열.
        tree: 루트 디렉토리 노드.
        metadata_files: 본문이 함께 수집된 프로젝트 메타데이터 파일 목록.
        max_depth: 적용된 최대 깊이.
        max_file_bytes: 본문 수집 임계 크기(바이트).
    """

    root: str
    tree: DirectoryEntry
    metadata_files: Tuple[MetadataFile, ...] = field(default_factory=tuple)
    max_depth: int = DEFAULT_MAX_DEPTH
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _matches_any(name: str, patterns: Iterable[str]) -> bool:
    """이름이 ``fnmatch`` 패턴 중 하나에라도 매칭되는지 검사."""
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def _is_probably_binary(path: Path, sample_size: int = 4096) -> bool:
    """파일의 앞부분을 읽어 NULL 바이트가 포함되어 있으면 바이너리로 판단한다.

    표준 라이브러리만 사용하므로 ``mimetypes`` 보다 간단·확실한 휴리스틱을 채택했다.
    심볼릭 링크 대상이 사라졌거나 권한 문제로 읽지 못하는 경우는 안전하게 ``True``
    (바이너리 취급) 로 간주해 본문 수집을 건너뛴다.
    """
    try:
        with path.open("rb") as fh:
            chunk = fh.read(sample_size)
    except OSError:
        return True
    if not chunk:
        return False
    return b"\x00" in chunk


def _safe_size(path: Path) -> int:
    """``stat`` 실패 시 ``0`` 을 돌려주는 안전 헬퍼."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read_text_clipped(path: Path, max_bytes: int) -> Tuple[str, bool]:
    """텍스트 파일을 최대 ``max_bytes`` 만큼만 읽는다.

    Returns:
        (본문, ``truncated`` 여부) 튜플. 권한/디코드 실패 시 ``("", False)`` 를 반환한다.
    """
    try:
        with path.open("rb") as fh:
            data = fh.read(max_bytes + 1)
    except OSError:
        return "", False

    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - 매우 드문 경로
            return "", False
    return text, truncated


def _relpath(path: Path, root: Path) -> str:
    """루트 기준 상대 경로(POSIX 스타일). 루트 자체는 ``"."``."""
    rel = os.path.relpath(str(path), str(root))
    if rel == ".":
        return "."
    return rel.replace(os.sep, "/")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_project(
    root: Optional[Path] = None,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ignore_patterns: Iterable[str] = DEFAULT_IGNORE_PATTERNS,
    metadata_patterns: Iterable[str] = DEFAULT_METADATA_PATTERNS,
    metadata_max_bytes: int = DEFAULT_METADATA_MAX_BYTES,
) -> ProjectSnapshot:
    """루트 디렉토리를 스캔해 :class:`ProjectSnapshot` 을 반환한다.

    Args:
        root: 스캔 루트. ``None`` 이면 :func:`Path.cwd` 사용.
        max_depth: 루트(0) 기준 최대 깊이. ``4`` 면 루트의 4단계 자식까지 내려간다.
            한도 + 1 깊이의 디렉토리는 노드 자체는 트리에 등장하지만 자식 없이
            ``truncated=True`` sentinel 로만 노출되어 "여기서 잘렸음" 을 표시한다.
            한도 + 2 이상 깊이의 노드는 일절 트리에 포함되지 않는다.
        max_file_bytes: 본문 수집 임계 크기. 초과 파일은 메타데이터만 기록한다.
        ignore_patterns: 무시할 이름 패턴(``fnmatch``). 디렉토리·파일 모두에 적용.
        metadata_patterns: 본문을 읽어 요약 섹션에 포함할 파일 이름 패턴.
        metadata_max_bytes: 메타데이터 파일 본문 수집 한도(바이트).

    Returns:
        스냅샷.
    """
    if root is None:
        root = Path.cwd()
    root = root.resolve()
    ignore_tuple = tuple(ignore_patterns)
    metadata_tuple = tuple(metadata_patterns)

    metadata_files: List[MetadataFile] = []
    tree = _scan_directory(
        path=root,
        root=root,
        depth=0,
        max_depth=max_depth,
        max_file_bytes=max_file_bytes,
        ignore_patterns=ignore_tuple,
        metadata_patterns=metadata_tuple,
        metadata_max_bytes=metadata_max_bytes,
        metadata_sink=metadata_files,
    )
    metadata_files.sort(key=lambda m: m.path)
    return ProjectSnapshot(
        root=str(root),
        tree=tree,
        metadata_files=tuple(metadata_files),
        max_depth=max_depth,
        max_file_bytes=max_file_bytes,
    )


def _scan_directory(
    *,
    path: Path,
    root: Path,
    depth: int,
    max_depth: int,
    max_file_bytes: int,
    ignore_patterns: Tuple[str, ...],
    metadata_patterns: Tuple[str, ...],
    metadata_max_bytes: int,
    metadata_sink: List[MetadataFile],
) -> DirectoryEntry:
    """단일 디렉토리를 스캔해 :class:`DirectoryEntry` 를 만든다."""
    rel = _relpath(path, root)

    # 깊이 한도 초과 디렉토리는 노드만 생성하고 내려가지 않는다.
    if depth > max_depth:
        return DirectoryEntry(
            path=rel,
            depth=depth,
            directories=(),
            files=(),
            truncated=True,
        )

    try:
        children = sorted(path.iterdir(), key=lambda p: p.name)
    except OSError:
        return DirectoryEntry(path=rel, depth=depth, truncated=False)

    sub_dirs: List[DirectoryEntry] = []
    files: List[FileEntry] = []
    for child in children:
        name = child.name
        # 이름 기반 무시 패턴은 디렉토리/파일 모두에 동일 적용한다.
        if _matches_any(name, ignore_patterns):
            continue

        if child.is_symlink():
            # 심볼릭 링크는 따라가지 않는다(루프/외부 노출 방지).
            # 파일 메타데이터만 기록한다.
            files.append(
                FileEntry(
                    path=_relpath(child, root),
                    size_bytes=_safe_size(child),
                    is_binary=True,
                    is_oversize=False,
                )
            )
            continue

        if child.is_dir():
            sub_dirs.append(
                _scan_directory(
                    path=child,
                    root=root,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_file_bytes=max_file_bytes,
                    ignore_patterns=ignore_patterns,
                    metadata_patterns=metadata_patterns,
                    metadata_max_bytes=metadata_max_bytes,
                    metadata_sink=metadata_sink,
                )
            )
            continue

        if child.is_file():
            size = _safe_size(child)
            is_oversize = size > max_file_bytes
            is_binary = False if is_oversize else _is_probably_binary(child)
            files.append(
                FileEntry(
                    path=_relpath(child, root),
                    size_bytes=size,
                    is_binary=is_binary,
                    is_oversize=is_oversize,
                )
            )
            # 메타데이터 후보는 무시 패턴을 통과하고 크기/바이너리 제약을 만족한 경우에만 읽는다.
            if (
                not is_oversize
                and not is_binary
                and _matches_any(name, metadata_patterns)
            ):
                content, truncated = _read_text_clipped(child, metadata_max_bytes)
                if content:
                    metadata_sink.append(
                        MetadataFile(
                            path=_relpath(child, root),
                            content=content,
                            truncated=truncated,
                        )
                    )

    return DirectoryEntry(
        path=rel,
        depth=depth,
        directories=tuple(sub_dirs),
        files=tuple(files),
        truncated=False,
    )


__all__ = [
    "DEFAULT_IGNORE_PATTERNS",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_METADATA_MAX_BYTES",
    "DEFAULT_METADATA_PATTERNS",
    "DirectoryEntry",
    "FileEntry",
    "MetadataFile",
    "ProjectSnapshot",
    "scan_project",
]
