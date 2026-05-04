"""@심볼 / @파일 자동 확장 패키지(이슈 #17, #24).

REPL 입력에서 ``@ClassName`` / ``@function_name`` / ``@README.md`` /
``@sicode/repl.py`` 같은 토큰을 발견하면 작업 디렉토리의 Python 소스 또는 파일
본문을 user message 끝에 자동 첨부한다.

서브모듈 구성(SRP):
    - :mod:`sicode.symbols.indexer`: 작업 디렉토리를 한 번 스캔해 ``ClassDef`` /
      ``FunctionDef`` 노드(:class:`SymbolRecord`) 와 일반 파일
      (:class:`FileRecord`) 을 인덱싱한다. :class:`CompositeIndexer` 가 두
      인덱서를 묶는다.
    - :mod:`sicode.symbols.resolver`: 인덱스를 캐시하고 토큰을 매칭(정확 일치 →
      대소문자 무시 부분 일치)으로 찾는 역할. 심볼/파일 검색 메서드를 분리해
      제공한다(``find`` / ``find_files``).
    - :mod:`sicode.symbols.expand`: 사용자 입력에서 ``@토큰`` 을 추출해 매칭된
      코드 블록 또는 파일 본문을 본문 끝에 append 한다.
    - :mod:`sicode.symbols.completer`: readline Tab 자동완성 콜백.
"""

from __future__ import annotations

from sicode.symbols.completer import (
    MAX_CANDIDATES,
    MAX_FILE_CANDIDATES,
    MAX_SYMBOL_CANDIDATES,
    SymbolCompleter,
    setup_readline_completer,
)
from sicode.symbols.expand import (
    DEFAULT_MAX_FILE_LINES,
    DEFAULT_MAX_MATCHES,
    DEFAULT_MAX_SYMBOL_LINES,
    SymbolExpander,
    expand_user_input,
)
from sicode.symbols.indexer import (
    DEFAULT_MAX_FILE_BYTES,
    CompositeIndexer,
    FileIndexer,
    FileRecord,
    SymbolIndexer,
    SymbolRecord,
    make_default_composite_indexer,
)
from sicode.symbols.resolver import SymbolResolver

__all__ = [
    "CompositeIndexer",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_FILE_LINES",
    "DEFAULT_MAX_MATCHES",
    "DEFAULT_MAX_SYMBOL_LINES",
    "FileIndexer",
    "FileRecord",
    "MAX_CANDIDATES",
    "MAX_FILE_CANDIDATES",
    "MAX_SYMBOL_CANDIDATES",
    "SymbolCompleter",
    "SymbolExpander",
    "SymbolIndexer",
    "SymbolRecord",
    "SymbolResolver",
    "expand_user_input",
    "make_default_composite_indexer",
    "setup_readline_completer",
]
