"""sicode CLI 엔트리포인트.

``pyproject.toml`` 의 ``[project.scripts]`` 항목에서 ``sicode = "sicode.main:main"``
으로 등록되어, ``pip install -e .`` 후 셸에서 ``sicode`` 명령어로 호출된다.
"""

from __future__ import annotations

import argparse
import builtins
import os
import sys
from typing import Callable, Optional, Sequence

from sicode.modes.base import BaseMode
from sicode.modes.ollama import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    OllamaClient,
    OllamaMode,
)
from sicode.repl import run_repl


#: 환경 변수 이름들. 한 곳에 모아두어 테스트와 도큐먼트가 일관되게 참조한다.
ENV_OLLAMA_HOST: str = "SICODE_OLLAMA_HOST"
ENV_OLLAMA_MODEL: str = "SICODE_OLLAMA_MODEL"

#: ``--mode`` 의 기본값. 새 모드가 늘어나도 한 곳만 바꾸면 된다.
DEFAULT_MODE_NAME: str = "ollama"


def _build_ollama_mode(args: argparse.Namespace) -> BaseMode:
    """``--mode ollama`` 용 팩토리.

    호스트 URL 은 보안상 환경 변수(:data:`ENV_OLLAMA_HOST`) 로만 변경 가능하며 CLI 에서
    임의 URL 을 직접 지정할 수 없다. 모델 이름은 CLI > 환경 변수 > 기본값 순으로 적용된다.
    """
    host = os.environ.get(ENV_OLLAMA_HOST, DEFAULT_HOST)
    model = args.model or os.environ.get(ENV_OLLAMA_MODEL, DEFAULT_MODEL)
    client = OllamaClient(host=host, model=model)
    return OllamaMode(client=client)


#: 모드 이름 -> 모드 팩토리 매핑. 새 모드를 추가할 때 이 딕셔너리에 한 줄만 더하면 된다 (OCP).
MODES: "dict[str, Callable[[argparse.Namespace], BaseMode]]" = {
    "ollama": _build_ollama_mode,
}


def _build_arg_parser() -> argparse.ArgumentParser:
    """CLI argparse 파서를 만든다.

    - ``--mode`` : 사용할 모드 이름. 기본값은 :data:`DEFAULT_MODE_NAME`.
    - ``--model`` : Ollama 모드에서 사용할 모델 이름. ``SICODE_OLLAMA_MODEL`` 보다 우선.

    호스트 URL 옵션은 의도적으로 노출하지 않는다(보안 제약: 환경 변수 전용).
    """
    parser = argparse.ArgumentParser(
        prog="sicode",
        description="Claude Code 스타일의 sicode 대화형 CLI.",
    )
    parser.add_argument(
        "--mode",
        choices=sorted(MODES.keys()),
        default=DEFAULT_MODE_NAME,
        help=f"실행할 모드 (기본값: {DEFAULT_MODE_NAME}).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Ollama 모드에서 사용할 모델 이름. "
            f"미지정 시 환경 변수 {ENV_OLLAMA_MODEL} 또는 기본값({DEFAULT_MODEL})을 사용."
        ),
    )
    return parser


def _select_mode(argv: Sequence[str]) -> BaseMode:
    """CLI 인자에 따라 모드를 선택한다.

    모드 등록 레지스트리(:data:`MODES`)를 통해 새 모드 추가 시 본 함수 자체는 변경하지
    않아도 되도록 했다 (OCP).
    """
    parser = _build_arg_parser()
    args = parser.parse_args(list(argv))
    factory = MODES[args.mode]
    return factory(args)


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
