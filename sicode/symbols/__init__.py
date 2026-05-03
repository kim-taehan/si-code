"""@심볼 자동 확장 패키지(이슈 #17).

REPL 입력에서 ``@ClassName`` / ``@function_name`` 같은 토큰을 발견하면 작업
디렉토리의 Python 소스에서 해당 심볼의 정의 코드를 찾아 user message 본문
끝에 자동 첨부한다.

서브모듈 구성(SRP):
    - :mod:`sicode.symbols.indexer`: 작업 디렉토리를 한 번 스캔해 ``ClassDef`` /
      ``FunctionDef`` 노드를 인덱싱한다.
    - :mod:`sicode.symbols.resolver`: 인덱스를 캐시하고 토큰을 매칭(정확 일치 →
      대소문자 무시 부분 일치)으로 찾는 역할.
    - :mod:`sicode.symbols.expand`: 사용자 입력에서 ``@토큰`` 을 추출해 매칭된
      코드 블록을 본문 끝에 append 한다.
"""

from __future__ import annotations

from sicode.symbols.expand import (
    DEFAULT_MAX_MATCHES,
    DEFAULT_MAX_SYMBOL_LINES,
    SymbolExpander,
    expand_user_input,
)
from sicode.symbols.indexer import (
    DEFAULT_MAX_FILE_BYTES,
    SymbolIndexer,
    SymbolRecord,
)
from sicode.symbols.resolver import SymbolResolver

__all__ = [
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_MATCHES",
    "DEFAULT_MAX_SYMBOL_LINES",
    "SymbolExpander",
    "SymbolIndexer",
    "SymbolRecord",
    "SymbolResolver",
    "expand_user_input",
]
