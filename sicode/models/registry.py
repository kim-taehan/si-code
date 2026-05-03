"""모델 레지스트리 및 ``models.json`` 로더.

요구사항(이슈 #14):
    - JSON 파일(`~/.config/sicode/models.json`) 에서 모델 목록과 기본값을 로드한다.
    - 파일 부재 시 내장 기본 모델 한 개로 폴백한다(경고 출력 없음).
    - JSON 파싱 오류/스키마 오류 시 stderr 에 메시지를 남기고 내장 기본으로 폴백.
    - 이름 검증(``[A-Za-z0-9:.\\-_/]+``) 에 실패한 항목은 건너뛰고 stderr 경고.
    - 모델 순환 전환(다음 모델로 이동), 이름으로 직접 선택, 미등록 이름 거부.

설계 메모:
    - SRP: 본 모듈은 "모델 목록 보관과 현재 활성 모델 추적" 만 책임진다.
      클라이언트 생성, 슬래시 명령 출력 포맷, REPL 흐름은 모두 외부.
    - DIP: 슬래시 명령은 본 모듈의 ``ModelRegistry`` 추상에만 의존한다.
      파일 IO 는 ``load_registry`` 함수로 분리되어 테스트가 쉽다(파일 경로/스트림
      주입 가능).
    - OCP: 새로운 로딩 소스(예: 원격 API) 가 필요해지면 별도 로더 함수를 추가하면
      되며, ``ModelRegistry`` 자체는 변경 없이 재사용 가능하다.
    - Py3.8 호환: ``from __future__ import annotations`` + ``Optional`` /
      ``List`` / ``Dict`` 사용. 데이터클래스는 frozen 불가(``current`` 가 변동).
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple


#: 기본 모델 이름. ``models.json`` 이 없거나 비어 있을 때 폴백으로 사용한다.
#: ``sicode.modes.ollama.DEFAULT_MODEL`` 과 동기화 유지(이슈 #14 본문 참조).
BUILTIN_DEFAULT_MODEL: str = "llama3.1:8b"

#: 모델 이름 허용 패턴. Ollama 모델 명명 규칙(예: ``qwen3:14b``, ``llama3.1:8b``)
#: 과 호환되며 셸 메타문자(``;``, ``&``, ``|`` 등) 를 사전 차단한다.
MODEL_NAME_PATTERN: re.Pattern = re.compile(r"^[A-Za-z0-9:.\-_/]+$")

#: 기본 설정 파일 경로(XDG Base Directory). 환경 변수 ``XDG_CONFIG_HOME`` 이
#: 설정되어 있으면 그 아래로 폴더를 잡는다(없으면 ``~/.config``).
def default_config_path() -> Path:
    """``models.json`` 의 표준 위치를 반환한다.

    XDG Base Directory 사양에 따라 ``$XDG_CONFIG_HOME/sicode/models.json`` 을
    우선하고, 미설정 시 ``~/.config/sicode/models.json`` 으로 폴백한다.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "sicode" / "models.json"
    return Path.home() / ".config" / "sicode" / "models.json"


#: 호환성을 위한 모듈 수준 상수(테스트/외부 참조용). 호출 시점이 아닌
#: 임포트 시점의 환경을 반영하므로, 동적 경로가 필요한 호출자는
#: :func:`default_config_path` 를 직접 사용하라.
DEFAULT_CONFIG_PATH: Path = default_config_path()


@dataclass(frozen=True)
class ModelEntry:
    """등록된 한 모델의 메타데이터.

    Attributes:
        name: 모델 이름(예: ``llama3.1:8b``). 필수, 패턴 검증 통과한 값만 보관.
        description: 사용자에게 보여줄 한 줄 설명. 선택, 미지정 시 빈 문자열.
    """

    name: str
    description: str = ""


def _is_valid_name(name: Any) -> bool:
    """모델 이름이 :data:`MODEL_NAME_PATTERN` 에 맞는지 검증한다."""
    return isinstance(name, str) and bool(MODEL_NAME_PATTERN.match(name))


@dataclass
class ModelRegistry:
    """등록된 모델 목록과 현재 활성 모델을 관리한다.

    인스턴스는 :func:`load_registry` 또는 직접 생성으로 만든다. 명령 클래스는
    본 추상에만 의존한다 (DIP).

    상태:
        - ``entries``: 등록 순서를 보존한 모델 엔트리 리스트.
        - ``current_name``: 현재 활성 모델 이름. ``select`` / ``cycle_next`` /
          런타임 외부 설정으로 변경된다.
        - ``default_name``: 시작 시 우선순위에 따라 결정된 기본 모델 이름.
          런타임 전환은 본 값을 변경하지 않는다(재시작 시 원래 우선순위 복귀).

    한 항목이라도 ``entries`` 에 들어 있어야 정상 사용 가능하다. 빈 레지스트리는
    구성 오류이므로 :meth:`__post_init__` 에서 ``ValueError`` 를 던진다.
    """

    entries: List[ModelEntry]
    default_name: str
    current_name: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("ModelRegistry requires at least one entry")
        # ``default_name`` 이 entries 에 없으면 첫 항목으로 정정.
        if not self.has(self.default_name):
            self.default_name = self.entries[0].name
        # 시작 시점의 활성 모델은 default_name 으로 동기화.
        self.current_name = self.default_name

    # ------------------------------------------------------------------ accessors

    def has(self, name: str) -> bool:
        """등록된 모델 이름인지 확인한다."""
        return any(e.name == name for e in self.entries)

    def names(self) -> List[str]:
        """등록 순서를 유지한 이름 리스트를 반환한다."""
        return [e.name for e in self.entries]

    def current(self) -> str:
        """현재 활성 모델 이름."""
        return self.current_name

    def get(self, name: str) -> Optional[ModelEntry]:
        """이름으로 엔트리를 조회한다. 없으면 ``None``."""
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    # ------------------------------------------------------------------ mutators

    def set_current(self, name: str) -> None:
        """현재 활성 모델을 ``name`` 으로 설정한다(시작 시 우선순위 적용용).

        Raises:
            ValueError: ``name`` 이 등록되지 않은 경우.
        """
        if not self.has(name):
            raise ValueError(f"Unknown model: {name}")
        self.current_name = name

    def select(self, name: str) -> ModelEntry:
        """``name`` 으로 즉시 전환한다.

        Args:
            name: 전환할 모델 이름.

        Returns:
            전환된 :class:`ModelEntry`.

        Raises:
            ValueError: 등록되지 않은 이름이면.
        """
        entry = self.get(name)
        if entry is None:
            raise ValueError(f"Unknown model: {name}")
        self.current_name = entry.name
        return entry

    def cycle_next(self) -> ModelEntry:
        """등록 순서대로 다음 모델로 순환 전환한다.

        마지막 항목 이후에는 첫 번째 항목으로 돌아온다. 항목이 1개뿐이면
        같은 항목을 그대로 반환한다.
        """
        names = self.names()
        try:
            idx = names.index(self.current_name)
        except ValueError:
            # 현재 이름이 목록에서 사라진 경우(이론상 발생하지 않음): 첫 항목으로 정정.
            idx = -1
        nxt = self.entries[(idx + 1) % len(self.entries)]
        self.current_name = nxt.name
        return nxt


# ---------------------------------------------------------------------------
# 로더
# ---------------------------------------------------------------------------


def _builtin_registry() -> ModelRegistry:
    """파일 부재/오류 시 사용할 1-항목 내장 폴백 레지스트리."""
    return ModelRegistry(
        entries=[ModelEntry(name=BUILTIN_DEFAULT_MODEL, description="Built-in default")],
        default_name=BUILTIN_DEFAULT_MODEL,
    )


def _warn(message: str) -> None:
    """stderr 에 한 줄 경고를 출력한다(테스트는 capsys 로 검증).

    ``print`` 가 아닌 ``sys.stderr.write`` 를 직접 호출하는 이유: 호출자가
    ``builtins.print`` 를 monkeypatch 하더라도 진단 출력은 그대로 stderr 로
    흐르도록 보장하기 위함(REPL 본문이 ``builtins.print`` 를 캡처하는 케이스
    회피).
    """
    sys.stderr.write(f"[sicode/models] {message}\n")


def _parse_entries(raw: Any) -> Tuple[List[ModelEntry], List[str]]:
    """``models`` 배열을 :class:`ModelEntry` 리스트로 정규화한다.

    Returns:
        (validated_entries, warnings).  ``warnings`` 는 호출자가 stderr 로
        흘릴 메시지 리스트.
    """
    warnings: List[str] = []
    entries: List[ModelEntry] = []
    if not isinstance(raw, list):
        warnings.append("`models` must be a JSON array; using built-in fallback")
        return entries, warnings

    seen: set = set()
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            warnings.append(f"models[{idx}] is not an object; skipping")
            continue
        name = item.get("name")
        if not _is_valid_name(name):
            warnings.append(
                f"models[{idx}] has invalid name {name!r}; skipping"
            )
            continue
        if name in seen:
            warnings.append(f"models[{idx}] duplicate name {name!r}; skipping")
            continue
        description = item.get("description", "")
        if not isinstance(description, str):
            description = ""
        entries.append(ModelEntry(name=name, description=description))
        seen.add(name)
    return entries, warnings


def _registry_from_payload(payload: Any) -> Tuple[Optional[ModelRegistry], List[str]]:
    """파싱된 JSON payload 로부터 :class:`ModelRegistry` 를 만든다.

    실패 시 ``(None, warnings)`` 를 반환한다(빈 entries 는 폴백 신호).
    """
    warnings: List[str] = []
    if not isinstance(payload, dict):
        warnings.append("top-level JSON must be an object; using built-in fallback")
        return None, warnings

    entries, entry_warnings = _parse_entries(payload.get("models"))
    warnings.extend(entry_warnings)
    if not entries:
        warnings.append("no valid model entries; using built-in fallback")
        return None, warnings

    raw_default = payload.get("default")
    if isinstance(raw_default, str) and any(e.name == raw_default for e in entries):
        default_name = raw_default
    else:
        if raw_default is not None and not (
            isinstance(raw_default, str) and any(e.name == raw_default for e in entries)
        ):
            warnings.append(
                f"`default` {raw_default!r} not in models; using first entry"
            )
        default_name = entries[0].name

    return ModelRegistry(entries=entries, default_name=default_name), warnings


def load_registry(
    path: Optional[Path] = None,
    *,
    warn: Optional[Any] = None,
) -> ModelRegistry:
    """``models.json`` 을 읽어 :class:`ModelRegistry` 를 만든다.

    파일이 없거나 읽기/파싱/검증에 실패하면 내장 기본 레지스트리로 폴백한다.
    파일 부재는 경고 없이 폴백, 그 외(파싱 실패, 스키마 오류, 항목 검증 실패)는
    stderr 경고를 출력한다.

    Args:
        path: 읽어올 파일 경로. ``None`` 이면 :func:`default_config_path`.
        warn: 경고 출력 콜러블(주입 가능, 기본은 stderr 출력). 테스트에서
            검증을 단순화하기 위해 주입 가능하게 했다 (DIP).

    Returns:
        검증된 :class:`ModelRegistry`. 호출자는 항상 비-None 결과를 받는다.
    """
    target = path if path is not None else default_config_path()
    warner = warn if warn is not None else _warn

    if not target.exists():
        # 파일 부재는 정상 흐름 — 경고 없음.
        return _builtin_registry()

    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        warner(f"failed to read {target}: {exc}; using built-in fallback")
        return _builtin_registry()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        warner(f"failed to parse {target}: {exc}; using built-in fallback")
        return _builtin_registry()

    registry, warnings = _registry_from_payload(payload)
    for msg in warnings:
        warner(msg)
    if registry is None:
        return _builtin_registry()
    return registry


__all__ = [
    "BUILTIN_DEFAULT_MODEL",
    "DEFAULT_CONFIG_PATH",
    "MODEL_NAME_PATTERN",
    "ModelEntry",
    "ModelRegistry",
    "default_config_path",
    "load_registry",
]
