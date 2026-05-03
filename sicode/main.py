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

from sicode.commands import (
    ModelCommand,
    ModelsCommand,
    default_registry,
    register_default_commands,
)
from sicode.models.registry import (
    ModelRegistry,
    default_config_path,
    load_registry,
)
from sicode.modes.base import BaseMode
from sicode.modes.conversation import DEFAULT_MAX_TURNS
from sicode.modes.ollama import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    OllamaMode,
)
from sicode.modes.ollama_chat import OllamaChatClient
from sicode.repl import run_repl
from sicode.symbols import SymbolExpander, SymbolResolver


#: 환경 변수 이름들. 한 곳에 모아두어 테스트와 도큐먼트가 일관되게 참조한다.
ENV_OLLAMA_HOST: str = "SICODE_OLLAMA_HOST"
ENV_OLLAMA_MODEL: str = "SICODE_OLLAMA_MODEL"
ENV_OLLAMA_MAX_TURNS: str = "SICODE_OLLAMA_MAX_TURNS"

#: ``--mode`` 의 기본값. 새 모드가 늘어나도 한 곳만 바꾸면 된다.
DEFAULT_MODE_NAME: str = "ollama"


def _resolve_max_turns() -> int:
    """``SICODE_OLLAMA_MAX_TURNS`` 환경 변수에서 최대 턴 수를 읽는다.

    값이 없거나 정수로 해석할 수 없거나 1 미만이면 :data:`DEFAULT_MAX_TURNS`
    로 폴백한다(잘못된 환경 변수가 프로세스 기동을 막지 않게).
    """
    raw = os.environ.get(ENV_OLLAMA_MAX_TURNS)
    if raw is None:
        return DEFAULT_MAX_TURNS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_TURNS
    return value if value >= 1 else DEFAULT_MAX_TURNS


def _resolve_initial_model(
    args: argparse.Namespace, registry: Optional[ModelRegistry]
) -> str:
    """시작 시점의 활성 모델 이름을 결정한다.

    이슈 #14 우선순위 규칙:
        ``--model`` CLI > ``SICODE_OLLAMA_MODEL`` 환경 변수 > ``models.json`` 의
        ``default`` > ``models[0].name`` > 내장 :data:`DEFAULT_MODEL`.

    레지스트리가 주어졌고 우선순위 결과 모델이 등록되어 있으면 ``registry.set_current``
    로 동기화한다. 등록되어 있지 않으면 동기화는 하지 않고(런타임 ``/model`` 의
    "등록된 항목으로 제한" 정책을 위해 레지스트리는 본래 상태 유지) 시작 모델은
    그대로 사용한다.
    """
    if args.model:
        chosen = args.model
    elif os.environ.get(ENV_OLLAMA_MODEL):
        chosen = os.environ[ENV_OLLAMA_MODEL]
    elif registry is not None:
        chosen = registry.default_name
    else:
        chosen = DEFAULT_MODEL

    if registry is not None and registry.has(chosen):
        registry.set_current(chosen)
    return chosen


def _make_chat_client_factory(host: str) -> Callable[[str], OllamaChatClient]:
    """호스트가 캡처된 "모델 이름 -> chat 클라이언트" 팩토리를 만든다.

    DIP: ``OllamaMode`` 는 본 클로저에만 의존하며 호스트/타임아웃 디테일을 모른다.
    """
    def _factory(model_name: str) -> OllamaChatClient:
        return OllamaChatClient(host=host, model=model_name)
    return _factory


def _build_ollama_mode(
    args: argparse.Namespace, registry: Optional[ModelRegistry] = None
) -> BaseMode:
    """``--mode ollama`` 용 팩토리.

    호스트 URL 은 보안상 환경 변수(:data:`ENV_OLLAMA_HOST`) 로만 변경 가능하며 CLI 에서
    임의 URL 을 직접 지정할 수 없다. 모델 이름은 CLI > 환경 변수 > 모델 레지스트리
    default > 모델 레지스트리 첫 항목 > 내장 기본값 순으로 적용된다(이슈 #14).

    이슈 #11 부터는 멀티턴 대화 지원을 위해 ``OllamaChatClient`` (``/api/chat``)
    를 기본 클라이언트로 구성하고, ``OllamaMode`` 가 :class:`Conversation` 을
    내부에 보유하도록 한다. ``OllamaClient`` (``/api/generate``) 는 호환성 유지
    용도로 모듈에 그대로 남아 있다.

    이슈 #14 부터는 :meth:`OllamaMode.switch_model` 이 동작하도록 ``client_factory``
    를 함께 주입한다(기존 호출자는 ``registry=None`` 으로 호환).
    """
    host = os.environ.get(ENV_OLLAMA_HOST, DEFAULT_HOST)
    model = _resolve_initial_model(args, registry)
    client = OllamaChatClient(host=host, model=model)
    # 이슈 #17: ``@심볼 자동 확장`` 통합. resolver 와 expander 를 한 번 만들어
    # OllamaMode 에 주입한다(같은 인스턴스를 공유해야 ``/clear`` 의
    # ``invalidate()`` 가 다음 ``handle()`` 의 캐시 사용에 정확히 반영된다).
    symbol_resolver = SymbolResolver()
    symbol_expander = SymbolExpander(symbol_resolver)
    return OllamaMode(
        client=client,
        max_turns=_resolve_max_turns(),
        client_factory=_make_chat_client_factory(host),
        input_preprocessor=symbol_expander.expand,
        symbol_resolver=symbol_resolver,
    )


#: 모드 이름 -> 모드 팩토리 매핑. 새 모드를 추가할 때 이 딕셔너리에 한 줄만 더하면 된다 (OCP).
#: 팩토리 시그니처는 ``(args, registry) -> BaseMode`` 다. ``registry`` 는 모델
#: 레지스트리(이슈 #14) 이며, 모델 개념이 없는 모드는 ``_`` 로 무시하면 된다.
MODES: "dict[str, Callable[[argparse.Namespace, Optional[ModelRegistry]], BaseMode]]" = {
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
    """CLI 인자에 따라 모드를 선택한다(레지스트리 없이 구성).

    모드 등록 레지스트리(:data:`MODES`)를 통해 새 모드 추가 시 본 함수 자체는 변경하지
    않아도 되도록 했다 (OCP).

    이슈 #14 의 모델 레지스트리는 :func:`_select_mode_with_registry` 가 별도로
    처리한다. 본 함수는 기존 호출자 / 단위 테스트(monkeypatch) 호환을 위해
    인자 1개로 유지한다.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(list(argv))
    factory = MODES[args.mode]
    return factory(args, None)


def _select_mode_with_registry(
    argv: Sequence[str], registry: Optional[ModelRegistry]
) -> BaseMode:
    """``--mode`` 분기 후 모델 레지스트리를 함께 주입해 모드를 만든다.

    이슈 #14 의 시작 시 우선순위(``--model`` > env > ``models.json`` default >
    ``models[0].name`` > 내장 기본) 적용을 위해 레지스트리를 팩토리에 흘려준다.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(list(argv))
    factory = MODES[args.mode]
    return factory(args, registry)


#: 모듈 로드 시점의 :func:`_select_mode` 참조. ``main()`` 이 monkeypatch 여부를
#: 판정해 레지스트리 주입 경로를 선택한다(테스트 호환을 위해).
_ORIGINAL_SELECT_MODE: "Callable[[Sequence[str]], BaseMode]" = _select_mode


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 엔트리포인트.

    Args:
        argv: 명령행 인자(프로그램 이름 제외). ``None`` 이면 ``sys.argv[1:]`` 사용.

    Returns:
        프로세스 종료 코드.
    """
    if argv is None:
        argv = sys.argv[1:]

    # 이슈 #14: 모델 레지스트리를 시작 시 1회 로드한다(파일 부재는 정상 흐름,
    # 파싱 오류는 stderr 경고 후 폴백). 본 호출은 외부 서비스 의존이 없으므로
    # 환경에 무관하게 동작한다.
    model_registry = load_registry(default_config_path())

    # 단위 테스트가 ``_select_mode`` 를 monkeypatch 하는 흐름을 깨지 않도록,
    # patch 여부에 따라 레지스트리 주입 경로를 분기한다. 정상 실행 경로에서는
    # 항상 :func:`_select_mode_with_registry` 가 호출된다.
    if _select_mode is _ORIGINAL_SELECT_MODE:
        mode = _select_mode_with_registry(argv, model_registry)
    else:
        mode = _select_mode(argv)
    # 슬래시 명령 기본 셋(``/exit``, ``/quit``, ``/help``)을 명시적으로 등록한다.
    # 이미 등록된 상태(같은 프로세스에서 main 두 번 호출 등)이면 무시한다.
    if not default_registry.commands():
        register_default_commands()
        # 이슈 #14: ``/model`` / ``/models`` 는 모델 레지스트리 의존이 명시적이므로
        # 별도 등록 단계로 분리해 다른 명령과 책임 경계를 분명히 한다 (SRP).
        # 본 단계는 REPL 본문(repl.py) 변경 없이 동작한다 (OCP).
        default_registry.register(ModelCommand(model_registry=model_registry))
        default_registry.register(ModelsCommand(model_registry=model_registry))
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
