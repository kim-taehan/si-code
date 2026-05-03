"""``sicode.models.registry`` 단위 테스트.

검증 범위:
    - JSON 파일 로드(정상/부재/파싱 오류/스키마 오류).
    - 모델 이름 패턴 검증, 잘못된 항목 스킵 + stderr 경고.
    - ``default`` 우선 / 미지정 시 첫 항목 폴백 / 미존재 default 폴백.
    - :class:`ModelRegistry` 의 ``select`` / ``cycle_next`` / ``set_current`` 동작.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

from sicode.models.registry import (
    BUILTIN_DEFAULT_MODEL,
    MODEL_NAME_PATTERN,
    ModelEntry,
    ModelRegistry,
    default_config_path,
    load_registry,
)


# ---------------------------------------------------------------------------
# Pattern
# ---------------------------------------------------------------------------


class TestNamePattern:
    @pytest.mark.parametrize(
        "name",
        [
            "llama3.1:8b",
            "qwen3:14b",
            "mistral",
            "user/model",
            "abc-123_4",
            "Model.With.Dots",
        ],
    )
    def test_accepts_valid(self, name: str) -> None:
        assert MODEL_NAME_PATTERN.match(name) is not None

    @pytest.mark.parametrize(
        "name",
        [
            "rm -rf /",
            "evil; ls",
            "pipe|cmd",
            "amp&cmd",
            "back`tick",
            "$var",
            "with space",
            "",
            "tab\there",
            "newline\nhere",
        ],
    )
    def test_rejects_invalid(self, name: str) -> None:
        assert MODEL_NAME_PATTERN.match(name) is None


# ---------------------------------------------------------------------------
# ModelRegistry behavior
# ---------------------------------------------------------------------------


def _entries(*names: str) -> List[ModelEntry]:
    return [ModelEntry(name=n) for n in names]


class TestModelRegistry:
    def test_post_init_sets_current_to_default(self) -> None:
        reg = ModelRegistry(entries=_entries("a", "b"), default_name="b")
        assert reg.current() == "b"
        assert reg.default_name == "b"

    def test_default_falls_back_to_first_when_unknown(self) -> None:
        reg = ModelRegistry(entries=_entries("a", "b"), default_name="z")
        assert reg.default_name == "a"
        assert reg.current() == "a"

    def test_empty_entries_raises(self) -> None:
        with pytest.raises(ValueError):
            ModelRegistry(entries=[], default_name="x")

    def test_has_and_get(self) -> None:
        reg = ModelRegistry(entries=_entries("a", "b"), default_name="a")
        assert reg.has("a") and reg.has("b")
        assert not reg.has("c")
        assert reg.get("a") == ModelEntry(name="a")
        assert reg.get("c") is None

    def test_select_known_name_changes_current(self) -> None:
        reg = ModelRegistry(entries=_entries("a", "b"), default_name="a")
        entry = reg.select("b")
        assert entry.name == "b"
        assert reg.current() == "b"

    def test_select_unknown_raises(self) -> None:
        reg = ModelRegistry(entries=_entries("a"), default_name="a")
        with pytest.raises(ValueError):
            reg.select("nope")

    def test_cycle_next_wraps_around(self) -> None:
        reg = ModelRegistry(entries=_entries("a", "b", "c"), default_name="a")
        # a -> b -> c -> a
        assert reg.cycle_next().name == "b"
        assert reg.cycle_next().name == "c"
        assert reg.cycle_next().name == "a"

    def test_cycle_next_single_entry(self) -> None:
        reg = ModelRegistry(entries=_entries("only"), default_name="only")
        assert reg.cycle_next().name == "only"
        assert reg.current() == "only"

    def test_cycle_next_when_current_unknown_starts_at_first(self) -> None:
        reg = ModelRegistry(entries=_entries("a", "b"), default_name="a")
        # 직접 current 를 강제로 변경하는 건 비공개 API 지만, 이론적 안전 동작 확인.
        reg.current_name = "ghost"
        assert reg.cycle_next().name == "a"

    def test_set_current_unknown_raises(self) -> None:
        reg = ModelRegistry(entries=_entries("a"), default_name="a")
        with pytest.raises(ValueError):
            reg.set_current("ghost")

    def test_set_current_does_not_change_default(self) -> None:
        reg = ModelRegistry(entries=_entries("a", "b"), default_name="a")
        reg.set_current("b")
        assert reg.default_name == "a"
        assert reg.current() == "b"

    def test_names_preserves_order(self) -> None:
        reg = ModelRegistry(entries=_entries("c", "a", "b"), default_name="c")
        assert reg.names() == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# default_config_path
# ---------------------------------------------------------------------------


class TestDefaultConfigPath:
    def test_uses_xdg_config_home_when_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = default_config_path()
        assert path == tmp_path / "sicode" / "models.json"

    def test_falls_back_to_home_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # type: ignore[arg-type]
        # ``classmethod`` 패치는 환경에 따라 다르게 동작할 수 있으므로 일반 패치로 대체.
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        path = default_config_path()
        assert path == tmp_path / ".config" / "sicode" / "models.json"


# ---------------------------------------------------------------------------
# load_registry
# ---------------------------------------------------------------------------


class TestLoadRegistryFileMissing:
    def test_missing_file_returns_builtin_without_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "nope.json"
        warns: List[str] = []
        reg = load_registry(target, warn=warns.append)
        assert reg.entries == [
            ModelEntry(name=BUILTIN_DEFAULT_MODEL, description="Built-in default")
        ]
        assert reg.default_name == BUILTIN_DEFAULT_MODEL
        assert warns == []  # 부재는 경고 없음
        # capsys: stderr 도 비어 있어야 한다(우리는 warn 콜백을 가로채지만,
        # 파일 부재 경로 자체가 아무 경로로도 출력하지 않음).
        captured = capsys.readouterr()
        assert captured.err == ""


class TestLoadRegistryHappyPath:
    def test_loads_models_and_default(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text(
            json.dumps(
                {
                    "models": [
                        {"name": "llama3.1:8b", "description": "기본"},
                        {"name": "qwen3:14b", "description": "고성능"},
                    ],
                    "default": "qwen3:14b",
                }
            ),
            encoding="utf-8",
        )
        reg = load_registry(path)
        assert reg.names() == ["llama3.1:8b", "qwen3:14b"]
        assert reg.default_name == "qwen3:14b"
        assert reg.current() == "qwen3:14b"
        # description 이 정확히 보존됐는지.
        e = reg.get("llama3.1:8b")
        assert e is not None and e.description == "기본"

    def test_missing_default_falls_back_to_first(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text(
            json.dumps({"models": [{"name": "a"}, {"name": "b"}]}),
            encoding="utf-8",
        )
        reg = load_registry(path)
        assert reg.default_name == "a"

    def test_optional_description_defaults_to_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text(
            json.dumps({"models": [{"name": "only"}]}), encoding="utf-8"
        )
        reg = load_registry(path)
        e = reg.get("only")
        assert e is not None and e.description == ""


class TestLoadRegistryErrors:
    def test_invalid_json_falls_back_with_warning(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text("{ not json", encoding="utf-8")
        warns: List[str] = []
        reg = load_registry(path, warn=warns.append)
        assert reg.entries[0].name == BUILTIN_DEFAULT_MODEL
        assert any("failed to parse" in w for w in warns)

    def test_top_level_not_object_falls_back(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text(json.dumps(["just", "a", "list"]), encoding="utf-8")
        warns: List[str] = []
        reg = load_registry(path, warn=warns.append)
        assert reg.entries[0].name == BUILTIN_DEFAULT_MODEL
        assert any("top-level JSON" in w for w in warns)

    def test_models_not_array_falls_back(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text(
            json.dumps({"models": {"name": "single"}}), encoding="utf-8"
        )
        warns: List[str] = []
        reg = load_registry(path, warn=warns.append)
        assert reg.entries[0].name == BUILTIN_DEFAULT_MODEL
        assert any("must be a JSON array" in w for w in warns)

    def test_invalid_name_is_skipped_with_warning(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text(
            json.dumps(
                {
                    "models": [
                        {"name": "good"},
                        {"name": "bad; rm -rf"},
                        {"name": "ok2"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        warns: List[str] = []
        reg = load_registry(path, warn=warns.append)
        assert reg.names() == ["good", "ok2"]
        assert any("invalid name" in w for w in warns)

    def test_all_invalid_falls_back_to_builtin(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text(
            json.dumps({"models": [{"name": "with space"}, {"foo": "bar"}]}),
            encoding="utf-8",
        )
        warns: List[str] = []
        reg = load_registry(path, warn=warns.append)
        assert reg.entries[0].name == BUILTIN_DEFAULT_MODEL
        assert any("no valid model entries" in w for w in warns)

    def test_unknown_default_warns_and_uses_first(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text(
            json.dumps(
                {
                    "models": [{"name": "a"}, {"name": "b"}],
                    "default": "z",
                }
            ),
            encoding="utf-8",
        )
        warns: List[str] = []
        reg = load_registry(path, warn=warns.append)
        assert reg.default_name == "a"
        assert any("not in models" in w for w in warns)

    def test_duplicate_names_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text(
            json.dumps(
                {"models": [{"name": "a"}, {"name": "a"}, {"name": "b"}]}
            ),
            encoding="utf-8",
        )
        warns: List[str] = []
        reg = load_registry(path, warn=warns.append)
        assert reg.names() == ["a", "b"]
        assert any("duplicate" in w for w in warns)

    def test_non_dict_item_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text(
            json.dumps({"models": ["llama3.1:8b", {"name": "a"}]}),
            encoding="utf-8",
        )
        warns: List[str] = []
        reg = load_registry(path, warn=warns.append)
        assert reg.names() == ["a"]
        assert any("not an object" in w for w in warns)

    def test_default_warn_writes_to_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "models.json"
        path.write_text("not json", encoding="utf-8")
        load_registry(path)  # warn 미주입 -> 기본 stderr 경로
        captured = capsys.readouterr()
        assert "[sicode/models]" in captured.err
        assert "failed to parse" in captured.err

    def test_non_string_description_coerced_to_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text(
            json.dumps(
                {"models": [{"name": "a", "description": 123}]}
            ),
            encoding="utf-8",
        )
        reg = load_registry(path)
        e = reg.get("a")
        assert e is not None and e.description == ""
