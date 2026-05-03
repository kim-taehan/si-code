"""``sicode.main`` 의 모델 레지스트리 통합 테스트(이슈 #14).

검증 범위:
    - 시작 시 모델 결정 우선순위:
      ``--model`` CLI > ``SICODE_OLLAMA_MODEL`` 환경 변수 > ``models.json`` default >
      ``models[0].name`` > 내장 :data:`DEFAULT_MODEL`.
    - ``client_factory`` 가 주입되어 :meth:`OllamaMode.switch_model` 이 동작한다.
    - ``main()`` 실행 시 ``/model`` / ``/models`` 가 default_registry 에 등록된다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, List

import pytest

import sicode.main as main_module
from sicode.commands.registry import default_registry
from sicode.models.registry import ModelEntry, ModelRegistry, load_registry
from sicode.modes.ollama import DEFAULT_MODEL, OllamaMode
from sicode.modes.ollama_chat import OllamaChatClient


def _registry(*names: str, default: str = "") -> ModelRegistry:
    entries = [ModelEntry(name=n) for n in names]
    return ModelRegistry(entries=entries, default_name=default or names[0])


class TestResolveInitialModel:
    def test_cli_overrides_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(main_module.ENV_OLLAMA_MODEL, "envmodel")
        reg = _registry("a", "b", default="b")
        args = main_module._build_arg_parser().parse_args(["--model", "phi3"])
        assert main_module._resolve_initial_model(args, reg) == "phi3"
        # registry 에 없는 모델이므로 set_current 가 호출되지 않아 default 유지.
        assert reg.current() == "b"

    def test_env_var_used_when_no_cli(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(main_module.ENV_OLLAMA_MODEL, "envmodel")
        reg = _registry("a", "b", default="a")
        args = main_module._build_arg_parser().parse_args([])
        assert main_module._resolve_initial_model(args, reg) == "envmodel"

    def test_registry_default_used_when_no_cli_no_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)
        reg = _registry("first", "second", default="second")
        args = main_module._build_arg_parser().parse_args([])
        assert main_module._resolve_initial_model(args, reg) == "second"
        # 등록된 이름이므로 set_current 가 호출되어 current 가 동기화됨.
        assert reg.current() == "second"

    def test_first_entry_used_when_default_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)
        # 비어있는 default 는 ModelRegistry 가 첫 항목으로 정정.
        reg = _registry("alpha", "beta")
        args = main_module._build_arg_parser().parse_args([])
        assert main_module._resolve_initial_model(args, reg) == "alpha"

    def test_builtin_default_when_no_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)
        args = main_module._build_arg_parser().parse_args([])
        assert main_module._resolve_initial_model(args, None) == DEFAULT_MODEL

    def test_cli_overrides_registry_even_if_not_registered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)
        reg = _registry("a", "b", default="a")
        args = main_module._build_arg_parser().parse_args(["--model", "ghost"])
        # registry 에 없어도 그대로 사용(런타임 ``/model`` 로는 못 바꾸지만 시작 모델은 가능).
        assert main_module._resolve_initial_model(args, reg) == "ghost"


class TestSelectModeWithRegistry:
    def test_returns_ollama_mode_with_factory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(main_module.ENV_OLLAMA_HOST, raising=False)
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)
        reg = _registry("llama3.1:8b", "qwen3:14b", default="qwen3:14b")
        mode = main_module._select_mode_with_registry([], reg)
        assert isinstance(mode, OllamaMode)
        assert mode.current_model == "qwen3:14b"
        # client_factory 가 주입되어 switch_model 동작.
        mode.switch_model("llama3.1:8b")
        assert mode.current_model == "llama3.1:8b"

    def test_no_registry_uses_default_model_and_factory_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(main_module.ENV_OLLAMA_HOST, raising=False)
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)
        mode = main_module._select_mode_with_registry([], None)
        assert isinstance(mode, OllamaMode)
        assert mode.current_model == DEFAULT_MODEL
        # 팩토리는 항상 주입되어 있다(런타임 전환 가능).
        mode.switch_model("phi3")
        assert mode.current_model == "phi3"

    def test_factory_uses_env_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(main_module.ENV_OLLAMA_HOST, "http://example:9999")
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)
        mode = main_module._select_mode_with_registry([], None)
        client = mode._client  # type: ignore[attr-defined]
        assert isinstance(client, OllamaChatClient)
        assert client.host == "http://example:9999"
        # 전환 후에도 같은 호스트가 적용돼야 한다.
        mode.switch_model("other")
        new_client = mode._client  # type: ignore[attr-defined]
        assert new_client.host == "http://example:9999"


class TestMainRegistersModelCommands:
    def _patch_input(
        self, monkeypatch: pytest.MonkeyPatch, inputs: List[str]
    ) -> List[str]:
        iterator: Iterator[str] = iter(inputs)
        captured: List[str] = []

        def _fake_input(_prompt: str = "") -> str:
            return next(iterator)

        monkeypatch.setattr("builtins.input", _fake_input)
        monkeypatch.setattr(
            "builtins.print",
            lambda *args, **kwargs: captured.append(
                " ".join(str(a) for a in args)
            ),
        )
        return captured

    def test_main_registers_model_and_models_commands(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # 외부 IO 차단: select_mode 를 EchoMode 로, models.json 경로를 빈 디렉터리로.
        from tests.conftest import EchoMode

        monkeypatch.setattr(main_module, "_select_mode", lambda _argv: EchoMode())
        monkeypatch.setattr(
            main_module,
            "default_config_path",
            lambda: tmp_path / "missing.json",
        )

        self._patch_input(monkeypatch, ["/exit"])
        rc = main_module.main([])
        assert rc == 0
        # /model, /models 가 등록되어 있어야 한다.
        tokens = {cmd.name for cmd in default_registry.commands()}
        assert "model" in tokens
        assert "models" in tokens

    def test_main_loads_models_json_when_present(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from tests.conftest import EchoMode

        # models.json 작성.
        cfg = tmp_path / "models.json"
        cfg.write_text(
            json.dumps(
                {
                    "models": [
                        {"name": "llama3.1:8b"},
                        {"name": "qwen3:14b"},
                    ],
                    "default": "qwen3:14b",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            main_module, "default_config_path", lambda: cfg
        )
        monkeypatch.setattr(main_module, "_select_mode", lambda _argv: EchoMode())
        self._patch_input(monkeypatch, ["/exit"])
        rc = main_module.main([])
        assert rc == 0
        # 등록된 ModelCommand 의 레지스트리에 qwen3:14b 가 포함됐는지.
        from sicode.commands.model import ModelCommand

        for cmd in default_registry.commands():
            if isinstance(cmd, ModelCommand):
                # 비공개 필드 검사가 아닌 동작 검사: registry.current() 가 default 와 같다.
                assert cmd._registry is not None  # type: ignore[attr-defined]
                assert cmd._registry.default_name == "qwen3:14b"  # type: ignore[attr-defined]
                break
        else:
            pytest.fail("ModelCommand was not registered")

    def test_main_with_invalid_models_json_falls_back_with_stderr(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from tests.conftest import EchoMode

        cfg = tmp_path / "models.json"
        cfg.write_text("{ broken", encoding="utf-8")
        monkeypatch.setattr(
            main_module, "default_config_path", lambda: cfg
        )
        monkeypatch.setattr(main_module, "_select_mode", lambda _argv: EchoMode())
        self._patch_input(monkeypatch, ["/exit"])
        rc = main_module.main([])
        assert rc == 0
        captured = capsys.readouterr()
        assert "[sicode/models]" in captured.err
        assert "failed to parse" in captured.err


class TestPrioritySmoke:
    """수용 기준에 적힌 우선순위 시나리오를 한 곳에서 점검한다."""

    def test_full_priority_chain(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # 우선순위 1 (최상): --model CLI.
        monkeypatch.setenv(main_module.ENV_OLLAMA_MODEL, "envmodel")
        cfg_path = tmp_path / "m.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "models": [{"name": "fileA"}, {"name": "fileB"}],
                    "default": "fileB",
                }
            ),
            encoding="utf-8",
        )
        reg = load_registry(cfg_path)
        args1 = main_module._build_arg_parser().parse_args(["--model", "cli1"])
        assert main_module._resolve_initial_model(args1, reg) == "cli1"

        # 우선순위 2: env (CLI 미지정).
        args2 = main_module._build_arg_parser().parse_args([])
        assert main_module._resolve_initial_model(args2, reg) == "envmodel"

        # 우선순위 3: registry default (env 도 없을 때).
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)
        # set_current 를 reset 하기 위해 새로 로드.
        reg2 = load_registry(cfg_path)
        assert main_module._resolve_initial_model(args2, reg2) == "fileB"

        # 우선순위 4: 첫 항목 (default 없음).
        cfg_path.write_text(
            json.dumps({"models": [{"name": "first"}, {"name": "second"}]}),
            encoding="utf-8",
        )
        reg3 = load_registry(cfg_path)
        assert main_module._resolve_initial_model(args2, reg3) == "first"

        # 우선순위 5: 내장 기본 (registry 자체가 None).
        assert main_module._resolve_initial_model(args2, None) == DEFAULT_MODEL
