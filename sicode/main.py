"""sicode CLI 엔트리포인트.

``pyproject.toml`` 의 ``[project.scripts]`` 항목에서 ``sicode = "sicode.main:main"``
으로 등록되어, ``pip install -e .`` 후 셸에서 ``sicode`` 명령어로 호출된다.
"""

from __future__ import annotations

import builtins
import sys
from typing import Optional, Sequence

from sicode.modes.base import BaseMode
from sicode.modes.simple import SimpleMode
from sicode.repl import run_repl


def _select_mode(_argv: Sequence[str]) -> BaseMode:
    """CLI 인자에 따라 모드를 선택한다.

    현재는 심플 모드만 지원하지만, 향후 ``--mode llm`` 등의 옵션을 추가할 때
    이 함수만 확장하면 된다 (OCP).
    """
    return SimpleMode()


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 엔트리포인트.

    Args:
        argv: 명령행 인자(프로그램 이름 제외). ``None`` 이면 ``sys.argv[1:]`` 사용.

    Returns:
        프로세스 종료 코드.
    """
    if argv is None:
        argv = sys.argv[1:]

    mode = _select_mode(argv)
    # ``builtins.input`` / ``builtins.print`` 를 호출 시점에 조회해서 전달한다.
    # 이렇게 해야 테스트에서 ``monkeypatch.setattr("builtins.input", ...)`` 같은
    # 패치가 정상적으로 적용된다.
    return run_repl(
        mode,
        input_fn=lambda prompt: builtins.input(prompt),
        output_fn=lambda line: builtins.print(line),
    )


if __name__ == "__main__":  # pragma: no cover - 직접 실행 진입점
    raise SystemExit(main())
