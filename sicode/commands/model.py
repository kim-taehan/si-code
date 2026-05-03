"""``/model`` 슬래시 명령: 런타임 모델 전환.

요구사항(이슈 #14):
    - 인자 없음(``/model``): 다음 등록 모델로 순환 전환.
    - 인자 있음(``/model <name>``): 지정 모델로 즉시 전환. 등록되지 않은 이름이면
      거부하고 현재 모델 + 사용 가능한 모델 목록을 안내한다.
    - 전환 후에도 기존 :class:`Conversation` (대화 히스토리) 은 유지한다.
      "이전 대화 맥락이 유지됩니다." 문구를 출력한다.
    - REPL 본문(``repl.py``) 은 수정하지 않는다 (OCP).

설계 메모:
    - DIP: 본 명령은 ``ModelRegistry`` 와 ``mode.switch_model(name)`` 추상에만
      의존한다. 모드 구체 클래스(``OllamaMode``)나 HTTP 클라이언트(``OllamaChatClient``)
      를 직접 참조하지 않는다.
    - SRP: "유효성 검증 + 레지스트리 갱신 + 모드에 위임 + 안내 문구" 만 담당.
    - 생성자 주입(``model_registry``) 이 우선이며, 미주입 시 :attr:`ReplContext.model_registry`
      로 폴백한다. 둘 다 없으면 안내 후 종료(LSP 위배 없음).
"""

from __future__ import annotations

from typing import Optional

from sicode.commands.base import CommandResult, ReplContext, SlashCommand
from sicode.models.registry import ModelRegistry


#: 모델 전환 성공 시 출력 prefix. 뒤에 모델 이름이 이어진다.
SUCCESS_PREFIX: str = "모델을 전환했습니다:"

#: 전환 후 첨부되는 히스토리 보존 안내 문구. 이슈 #14 본문 명시 문구.
HISTORY_PRESERVED_NOTICE: str = "이전 대화 맥락이 유지됩니다."

#: 모델 레지스트리/모드가 구성되지 않았을 때 안내 메시지.
NOT_CONFIGURED_MESSAGE: str = (
    "모델 레지스트리가 구성되지 않아 /model 명령을 사용할 수 없습니다."
)

#: 모드가 ``switch_model`` 을 지원하지 않을 때 안내 메시지.
SWITCH_UNSUPPORTED_MESSAGE: str = (
    "현재 모드는 런타임 모델 전환을 지원하지 않습니다."
)


def _format_models_listing(registry: ModelRegistry) -> str:
    """현재 모델 + 등록 모델 목록을 사람이 읽기 좋은 형태로 만든다."""
    lines = [f"현재 모델: {registry.current()}", "사용 가능한 모델:"]
    for entry in registry.entries:
        marker = "*" if entry.name == registry.current() else " "
        if entry.description:
            lines.append(f"  {marker} {entry.name} - {entry.description}")
        else:
            lines.append(f"  {marker} {entry.name}")
    return "\n".join(lines)


class ModelCommand(SlashCommand):
    """런타임 모델 전환 슬래시 명령.

    Attributes:
        name: ``"model"``.
        aliases: 없음. 별도 단축 명령은 두지 않는다(혼동 방지).
        description: ``/help`` 출력용 한 줄 설명.
    """

    name: str = "model"
    aliases: "tuple[str, ...]" = ()
    description: str = (
        "Switch the active model. /model cycles to the next; "
        "/model <name> selects by name."
    )

    def __init__(self, model_registry: Optional[ModelRegistry] = None) -> None:
        """명령을 초기화한다.

        Args:
            model_registry: 사용할 :class:`ModelRegistry`. ``None`` 이면 매 호출 시
                :attr:`ReplContext.model_registry` 를 폴백으로 사용한다.
        """
        self._registry = model_registry

    def _resolve_registry(self, context: ReplContext) -> Optional[ModelRegistry]:
        return self._registry or context.model_registry

    def execute(self, context: ReplContext) -> CommandResult:
        registry = self._resolve_registry(context)
        if registry is None:
            return CommandResult.cont(NOT_CONFIGURED_MESSAGE)

        argument = context.argument.strip()
        if argument == "":
            target = registry.cycle_next()
        else:
            entry = registry.get(argument)
            if entry is None:
                # 미등록 이름은 거부하되 사용자가 다음 행동을 결정할 수 있도록
                # 현재 모델과 전체 목록을 함께 안내한다.
                lines = [
                    f"등록되지 않은 모델: {argument}",
                    _format_models_listing(registry),
                ]
                return CommandResult.cont("\n".join(lines))
            target = registry.select(argument)

        # 모드에 위임. 모드가 ``switch_model`` 을 지원하지 않으면 레지스트리 상태를
        # 롤백하지 않고 안내만 한다(레지스트리는 "사용자가 의도한 활성 모델"의
        # 단일 진실 공급원이며, 다음 클라이언트 재구성 시 자연스럽게 적용된다).
        mode = context.mode
        switch = getattr(mode, "switch_model", None)
        if not callable(switch):
            return CommandResult.cont(SWITCH_UNSUPPORTED_MESSAGE)
        try:
            switch(target.name)
        except Exception as exc:  # pragma: no cover - 예외 안내 경로
            return CommandResult.cont(f"모델 전환 실패: {exc}")

        return CommandResult.cont(
            f"{SUCCESS_PREFIX} {target.name}\n{HISTORY_PRESERVED_NOTICE}"
        )


__all__ = [
    "HISTORY_PRESERVED_NOTICE",
    "ModelCommand",
    "NOT_CONFIGURED_MESSAGE",
    "SUCCESS_PREFIX",
    "SWITCH_UNSUPPORTED_MESSAGE",
]
