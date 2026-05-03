"""모델 레지스트리 패키지.

JSON 파일(`~/.config/sicode/models.json`) 에서 사용 가능한 모델 목록을
선언하고, 런타임에 ``/model`` / ``/models`` 슬래시 명령으로 활성 모델을
전환하기 위한 자료구조를 제공한다.

본 패키지는 HTTP 통신 / REPL 흐름과 무관하게 "모델 이름 보관 및 전환 정책"
한 가지 책임만을 갖는다 (SRP).
"""

from __future__ import annotations

from sicode.models.registry import (
    DEFAULT_CONFIG_PATH,
    MODEL_NAME_PATTERN,
    ModelEntry,
    ModelRegistry,
    default_config_path,
    load_registry,
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "MODEL_NAME_PATTERN",
    "ModelEntry",
    "ModelRegistry",
    "default_config_path",
    "load_registry",
]
