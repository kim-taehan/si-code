"""@토큰 → user message 본문 확장기(이슈 #17).

사용자 입력에서 ``@[A-Za-z_][A-Za-z0-9_]*`` 패턴 토큰을 추출하고, 매칭된
심볼 정의를 본문 끝에 코드 펜스 블록으로 append 한다.

설계 메모:
    - SRP: 본 모듈은 "user message 변환" 한 가지 책임만 갖는다. 인덱싱은
      :mod:`sicode.symbols.indexer`, 검색은 :mod:`sicode.symbols.resolver`.
    - DIP: :class:`SymbolExpander` 는 :class:`SymbolResolver` 추상에만 의존한다.
    - 출력 형식은 이슈 본문 명세를 따르며, 한 심볼은 최대
      :data:`DEFAULT_MAX_SYMBOL_LINES` 라인까지 첨부한다(초과 시
      ``... (truncated)`` 표기). 동일 심볼에 여러 매칭이 있으면 최대
      :data:`DEFAULT_MAX_MATCHES` 건만 첨부한다.
"""

from __future__ import annotations

import re
from typing import List, Optional

from sicode.symbols.resolver import SymbolResolver
from sicode.symbols.indexer import SymbolRecord


#: 한 심볼 토큰에 대해 첨부할 매칭 결과의 최대 개수.
DEFAULT_MAX_MATCHES: int = 3

#: 한 심볼 정의 본문의 최대 라인 수.
DEFAULT_MAX_SYMBOL_LINES: int = 150

#: ``@토큰`` 추출용 정규식. 토큰은 식별자 규칙(맨 앞 영문/언더스코어).
TOKEN_PATTERN: "re.Pattern[str]" = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)")


class SymbolExpander:
    """@토큰을 매칭된 심볼 코드로 확장하는 변환기.

    한 인스턴스는 (resolver, max_matches, max_symbol_lines) 한 묶음을 표현하며
    :meth:`expand` 는 입력 문자열을 받아 변환된 새 문자열을 반환한다(원본은 변경 X).
    """

    def __init__(
        self,
        resolver: SymbolResolver,
        *,
        max_matches: int = DEFAULT_MAX_MATCHES,
        max_symbol_lines: int = DEFAULT_MAX_SYMBOL_LINES,
    ) -> None:
        """확장기를 초기화한다.

        Args:
            resolver: 토큰 → 레코드 검색을 위임할 :class:`SymbolResolver`.
            max_matches: 한 토큰당 본문에 포함할 매칭 수의 상한.
            max_symbol_lines: 한 심볼 정의 본문의 라인 상한.

        Raises:
            ValueError: 한도 값이 1 미만일 때.
        """
        if max_matches < 1:
            raise ValueError("max_matches must be >= 1")
        if max_symbol_lines < 1:
            raise ValueError("max_symbol_lines must be >= 1")
        self._resolver: SymbolResolver = resolver
        self._max_matches: int = max_matches
        self._max_symbol_lines: int = max_symbol_lines

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
        """단일 토큰에 대해 매칭 결과 또는 안내 문구를 렌더링한다."""
        records = self._resolver.find(token)
        if not records:
            return f"(note: @{token} — no definition found in project)"

        attached = records[: self._max_matches]
        blocks = [
            _render_record_block(token, record, self._max_symbol_lines)
            for record in attached
        ]
        return "---\n" + "\n\n".join(blocks)


def _extract_tokens(text: str) -> List[str]:
    """입력 문자열에서 ``@토큰`` 을 등장 순서대로 추출(중복 제거)."""
    seen: "dict[str, None]" = {}
    for match in TOKEN_PATTERN.finditer(text):
        token = match.group(1)
        if token not in seen:
            seen[token] = None
    return list(seen.keys())


def _render_record_block(
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
) -> str:
    """모듈 수준 편의 함수: 일회성으로 :class:`SymbolExpander` 를 만들어 적용.

    싱글턴 resolver 가 없을 때 빠르게 변환할 수 있도록 제공한다. 프로덕션
    경로는 보통 :class:`SymbolExpander` 인스턴스를 한 번 만들어 재사용한다.
    """
    expander = SymbolExpander(
        resolver or SymbolResolver(),
        max_matches=max_matches,
        max_symbol_lines=max_symbol_lines,
    )
    return expander.expand(user_input)


__all__ = [
    "DEFAULT_MAX_MATCHES",
    "DEFAULT_MAX_SYMBOL_LINES",
    "SymbolExpander",
    "TOKEN_PATTERN",
    "expand_user_input",
]
