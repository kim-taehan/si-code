"""``/models`` 슬래시 명령: 등록된 모델 목록과 현재 활성 모델 조회.

요구사항(이슈 #14):
    - 등록된 모델 전체 목록과 현재 활성 모델을 출력한다.
    - REPL 본문 무수정 (OCP).

설계 메모:
    - SRP: 본 명령은 "모델 레지스트리 -> 사람이 읽을 수 있는 텍스트" 변환만 담당.
    - DIP: ``ModelRegistry`` 추상에만 의존.
    - ``ModelCommand`` 와 출력 포맷 헬퍼(:func:`_format_models_listing`) 를 공유해
      "현재 모델" 마킹 일관성을 유지한다.
"""

from __future__ import annotations

from typing import Optional

from sicode.commands.base import CommandResult, ReplContext, SlashCommand
from sicode.commands.model import (
    NOT_CONFIGURED_MESSAGE,
    _format_models_listing,
)
from sicode.models.registry import ModelRegistry


class ModelsCommand(SlashCommand):
    """등록된 모델 목록과 현재 모델을 출력하는 슬래시 명령."""

    name: str = "models"
    aliases: "tuple[str, ...]" = ()
    description: str = "List registered models and the active model."

    def __init__(self, model_registry: Optional[ModelRegistry] = None) -> None:
        """명령을 초기화한다.

        Args:
            model_registry: 사용할 :class:`ModelRegistry`. ``None`` 이면 매 호출 시
                :attr:`ReplContext.model_registry` 를 폴백으로 사용한다.
        """
        self._registry = model_registry

    def execute(self, context: ReplContext) -> CommandResult:
        registry = self._registry or context.model_registry
        if registry is None:
            return CommandResult.cont(NOT_CONFIGURED_MESSAGE)
        return CommandResult.cont(_format_models_listing(registry))


__all__ = ["ModelsCommand"]
