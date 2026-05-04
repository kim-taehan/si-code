"""@토큰 → user message 본문 확장기(이슈 #17, #24).

사용자 입력에서 ``@`` 로 시작하는 토큰을 추출하고, 매칭된 심볼 정의 또는
파일 본문을 본문 끝에 코드 펜스 블록으로 append 한다.

설계 메모:
    - SRP: 본 모듈은 "user message 변환" 한 가지 책임만 갖는다. 인덱싱은
      :mod:`sicode.symbols.indexer`, 검색은 :mod:`sicode.symbols.resolver`.
    - DIP: :class:`SymbolExpander` 는 :class:`SymbolResolver` 추상에만 의존한다.
    - 이슈 #24 부터는 토큰 패턴이 두 가지를 모두 받는다:
        1. 식별자 토큰: ``@ClassName`` / ``@function_name`` (기존 동작 유지).
        2. 파일 토큰: ``@README.md``, ``@sicode/repl.py`` (점·슬래시 포함).
      파일 매칭이 우선이며, 파일이 매칭되지 않은 식별자 토큰은 심볼 매칭으로
      떨어진다(파일도 심볼도 매칭되지 않으면 안내 문구).
    - 한 심볼은 :data:`DEFAULT_MAX_SYMBOL_LINES` 라인까지, 한 파일은
      :data:`DEFAULT_MAX_FILE_LINES` 라인까지 첨부한다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from sicode.symbols.indexer import (
    DEFAULT_MAX_FILE_BYTES,
    FileRecord,
    SymbolRecord,
)
from sicode.symbols.resolver import SymbolResolver


#: 한 심볼 토큰에 대해 첨부할 매칭 결과의 최대 개수.
DEFAULT_MAX_MATCHES: int = 3

#: 한 심볼 정의 본문의 최대 라인 수.
DEFAULT_MAX_SYMBOL_LINES: int = 150

#: 한 파일 본문의 최대 라인 수(이슈 #24).
DEFAULT_MAX_FILE_LINES: int = 200

#: ``@토큰`` 추출용 정규식.
#:
#: 이슈 #17 의 식별자 패턴(``@ClassName``)과 이슈 #24 의 파일 토큰
#: (``@README.md``, ``@sicode/repl.py``) 을 함께 받는다. 시작 문자는 영문/언더
#: 스코어/숫자 모두 허용(파일명이 숫자로 시작할 수도 있으므로). 본문에는 점·
#: 슬래시·하이픈도 허용한다. 매칭이 인덱스에 없는 임의 문자열까지 잡지
#: 않도록 끝은 알파벳/숫자/언더스코어로 마감되도록 정규식을 좁힌다.
TOKEN_PATTERN: "re.Pattern[str]" = re.compile(
    r"@([A-Za-z0-9_][A-Za-z0-9_./\-]*[A-Za-z0-9_]|[A-Za-z0-9_])"
)


class SymbolExpander:
    """@토큰을 매칭된 심볼/파일 코드로 확장하는 변환기.

    한 인스턴스는 (resolver, max_matches, max_symbol_lines, max_file_lines,
    file_root) 한 묶음을 표현하며 :meth:`expand` 는 입력 문자열을 받아 변환된
    새 문자열을 반환한다(원본은 변경 X).
    """

    def __init__(
        self,
        resolver: SymbolResolver,
        *,
        max_matches: int = DEFAULT_MAX_MATCHES,
        max_symbol_lines: int = DEFAULT_MAX_SYMBOL_LINES,
        max_file_lines: int = DEFAULT_MAX_FILE_LINES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        file_root: Optional[Path] = None,
    ) -> None:
        """확장기를 초기화한다.

        Args:
            resolver: 토큰 → 레코드 검색을 위임할 :class:`SymbolResolver`.
            max_matches: 한 토큰당 본문에 포함할 매칭 수의 상한.
            max_symbol_lines: 한 심볼 정의 본문의 라인 상한.
            max_file_lines: 한 파일 본문의 라인 상한(이슈 #24, 기본 200).
            max_file_bytes: 본문을 읽을 파일 크기 상한. 초과 파일은 메타데이터만
                첨부한다(이슈 #24, 기본 1 MB).
            file_root: 파일 본문을 읽을 루트. ``None`` 이면 :func:`Path.cwd`.
                ``rel_path`` 를 본 루트에 결합해 본문을 읽는다.

        Raises:
            ValueError: 한도 값이 1 미만일 때.
        """
        if max_matches < 1:
            raise ValueError("max_matches must be >= 1")
        if max_symbol_lines < 1:
            raise ValueError("max_symbol_lines must be >= 1")
        if max_file_lines < 1:
            raise ValueError("max_file_lines must be >= 1")
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be >= 1")
        self._resolver: SymbolResolver = resolver
        self._max_matches: int = max_matches
        self._max_symbol_lines: int = max_symbol_lines
        self._max_file_lines: int = max_file_lines
        self._max_file_bytes: int = max_file_bytes
        self._file_root: Path = (file_root or Path.cwd()).resolve()

    def expand(self, user_input: str) -> str:
        """``@토큰`` 을 추출해 매칭된 코드 블록을 본문 끝에 append 한다.

        Args:
            user_input: 사용자 원본 입력.

        Returns:
            토큰이 없으면 입력을 그대로, 있으면 코드 펜스 블록(또는 안내 문구)
            가 append 된 새 문자열.
        """
        if not user_input or "@" not in user_input:
            return user_input

        tokens = _extract_tokens(user_input)
        if not tokens:
            return user_input

        sections: List[str] = []
        for token in tokens:
            section = self._render_token(token)
            if section:
                sections.append(section)

        if not sections:
            return user_input

        return user_input + "\n\n" + "\n\n".join(sections)

    # ------------------------------------------------------------------ helpers

    def _render_token(self, token: str) -> str:
        """단일 토큰에 대해 매칭 결과 또는 안내 문구를 렌더링한다.

        매칭 우선순위(이슈 #24): 파일 → 심볼 → 안내. 같은 토큰이 파일과 심볼
        양쪽에서 매칭되더라도 파일 본문이 더 직접적인 컨텍스트라는 정책을
        취해 파일을 먼저 첨부한다.
        """
        files = self._resolver.find_files(token)
        if files:
            attached = files[: self._max_matches]
            blocks = [self._render_file_block(token, record) for record in attached]
            return "---\n" + "\n\n".join(blocks)

        symbols = self._resolver.find(token)
        if symbols:
            attached_symbols = symbols[: self._max_matches]
            symbol_blocks = [
                _render_symbol_block(token, record, self._max_symbol_lines)
                for record in attached_symbols
            ]
            return "---\n" + "\n\n".join(symbol_blocks)

        return f"(note: @{token} — no definition found in project)"

    def _render_file_block(self, token: str, record: FileRecord) -> str:
        """단일 :class:`FileRecord` 를 코드 펜스 블록으로 렌더링한다.

        본문 첨부 정책(이슈 #24):
            - ``is_oversize=True`` 이거나 ``is_binary=True`` 이면 메타데이터만
              안내 라인으로 첨부한다.
            - 그 외에는 디스크에서 본문을 읽어 :data:`DEFAULT_MAX_FILE_LINES`
              까지 첨부하고, 초과 시 ``[truncated]`` 라인을 붙인다.
            - 디스크 읽기 실패는 메타데이터 폴백.
        """
        if record.is_binary:
            return (
                f"Referenced file: `@{token}` ({record.rel_path}, "
                f"{record.size_bytes} bytes, binary — content omitted)"
            )
        if record.is_oversize:
            return (
                f"Referenced file: `@{token}` ({record.rel_path}, "
                f"{record.size_bytes} bytes, exceeds 1 MB — content omitted)"
            )

        body, truncated = self._read_file_body(record)
        if body is None:
            return (
                f"Referenced file: `@{token}` ({record.rel_path}, "
                f"{record.size_bytes} bytes — content unreadable)"
            )

        fence_lines = [
            f"Referenced file: `@{token}` ({record.rel_path})",
            "```",
            body.rstrip("\n"),
        ]
        if truncated:
            fence_lines.append("[truncated]")
        fence_lines.append("```")
        return "\n".join(fence_lines)

    def _read_file_body(self, record: FileRecord) -> "Tuple[Optional[str], bool]":
        """파일 본문을 최대 ``max_file_lines`` 까지 읽는다.

        Returns:
            ``(본문, truncated)`` 튜플. 권한/디코드 실패 시 ``(None, False)``.
        """
        # ``rel_path`` 는 인덱서가 만든 POSIX 경로다. ``file_root`` 와 결합해
        # OS 네이티브 경로로 풀어 디스크에서 다시 읽는다.
        target = self._file_root.joinpath(*record.rel_path.split("/"))
        # ``file_root`` 바깥으로의 탈출(예: 인덱서가 다른 root 였던 경우) 차단.
        try:
            resolved = target.resolve()
        except OSError:
            return None, False
        try:
            resolved.relative_to(self._file_root)
        except ValueError:
            return None, False

        try:
            with resolved.open("rb") as fh:
                data = fh.read(self._max_file_bytes + 1)
        except OSError:
            return None, False

        # 인덱싱 시점 이후 파일이 커져 max_file_bytes 를 넘긴 경우 메타데이터
        # 폴백(상위 호출자가 처리한다).
        if len(data) > self._max_file_bytes:
            return None, False

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return None, False

        lines = text.splitlines()
        if len(lines) <= self._max_file_lines:
            return text, False
        clipped = "\n".join(lines[: self._max_file_lines])
        return clipped, True


def _extract_tokens(text: str) -> List[str]:
    """입력 문자열에서 ``@토큰`` 을 등장 순서대로 추출(중복 제거)."""
    seen: "dict[str, None]" = {}
    for match in TOKEN_PATTERN.finditer(text):
        token = match.group(1)
        if token not in seen:
            seen[token] = None
    return list(seen.keys())


def _render_symbol_block(
    token: str, record: "SymbolRecord", max_lines: int
) -> str:
    """단일 :class:`SymbolRecord` 를 본문에 첨부할 텍스트 블록으로 렌더한다.

    형식::

        Referenced symbol: `@SymbolName` (relative/path/to/file.py:42)
        ```python
        class SymbolName:
            ...
        ```
    """
    body, truncated = _clip_lines(record.source, max_lines)
    fence_lines = [
        f"Referenced symbol: `@{token}` ({record.rel_path}:{record.start_line})",
        "```python",
        body.rstrip("\n"),
    ]
    if truncated:
        fence_lines.append("... (truncated)")
    fence_lines.append("```")
    return "\n".join(fence_lines)


def _clip_lines(source: str, max_lines: int) -> "tuple[str, bool]":
    """원본을 최대 ``max_lines`` 라인으로 자르고 truncated 여부를 함께 반환."""
    lines = source.splitlines()
    if len(lines) <= max_lines:
        return source, False
    clipped = "\n".join(lines[:max_lines])
    return clipped, True


def expand_user_input(
    user_input: str,
    resolver: Optional[SymbolResolver] = None,
    *,
    max_matches: int = DEFAULT_MAX_MATCHES,
    max_symbol_lines: int = DEFAULT_MAX_SYMBOL_LINES,
    max_file_lines: int = DEFAULT_MAX_FILE_LINES,
) -> str:
    """모듈 수준 편의 함수: 일회성으로 :class:`SymbolExpander` 를 만들어 적용.

    싱글턴 resolver 가 없을 때 빠르게 변환할 수 있도록 제공한다. 프로덕션
    경로는 보통 :class:`SymbolExpander` 인스턴스를 한 번 만들어 재사용한다.
    """
    expander = SymbolExpander(
        resolver or SymbolResolver(),
        max_matches=max_matches,
        max_symbol_lines=max_symbol_lines,
        max_file_lines=max_file_lines,
    )
    return expander.expand(user_input)


__all__ = [
    "DEFAULT_MAX_FILE_LINES",
    "DEFAULT_MAX_MATCHES",
    "DEFAULT_MAX_SYMBOL_LINES",
    "SymbolExpander",
    "TOKEN_PATTERN",
    "expand_user_input",
]
