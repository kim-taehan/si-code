"""``sicode.main._select_mode`` 의 모드 선택/우선순위 동작 테스트."""

from __future__ import annotations

import pytest

import sicode.main as main_module
from sicode.modes.ollama import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    OllamaClient,
    OllamaMode,
)
from sicode.modes.simple import SimpleMode


class TestSelectModeDefault:
    def test_no_args_returns_simple_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(main_module.ENV_OLLAMA_HOST, raising=False)
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)
        assert isinstance(main_module._select_mode([]), SimpleMode)

    def test_explicit_simple_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(main_module.ENV_OLLAMA_HOST, raising=False)
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)
        assert isinstance(
            main_module._select_mode(["--mode", "simple"]), SimpleMode
        )


class TestSelectModeOllama:
    def test_ollama_mode_uses_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(main_module.ENV_OLLAMA_HOST, raising=False)
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)

        mode = main_module._select_mode(["--mode", "ollama"])
        assert isinstance(mode, OllamaMode)
        client = mode._client  # type: ignore[attr-defined]
        assert isinstance(client, OllamaClient)
        assert client.host == DEFAULT_HOST
        assert client.model == DEFAULT_MODEL

    def test_env_var_overrides_default_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(main_module.ENV_OLLAMA_HOST, "http://example:9999")
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)

        mode = main_module._select_mode(["--mode", "ollama"])
        client = mode._client  # type: ignore[attr-defined]
        assert isinstance(client, OllamaClient)
        assert client.host == "http://example:9999"
        assert client.model == DEFAULT_MODEL

    def test_env_var_overrides_default_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(main_module.ENV_OLLAMA_HOST, raising=False)
        monkeypatch.setenv(main_module.ENV_OLLAMA_MODEL, "mistral")

        mode = main_module._select_mode(["--mode", "ollama"])
        client = mode._client  # type: ignore[attr-defined]
        assert client.model == "mistral"

    def test_cli_model_overrides_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(main_module.ENV_OLLAMA_MODEL, "mistral")

        mode = main_module._select_mode(
            ["--mode", "ollama", "--model", "llama3"]
        )
        client = mode._client  # type: ignore[attr-defined]
        assert client.model == "llama3"

    def test_cli_model_overrides_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)

        mode = main_module._select_mode(
            ["--mode", "ollama", "--model", "phi3"]
        )
        client = mode._client  # type: ignore[attr-defined]
        assert client.model == "phi3"

    def test_cli_does_not_accept_host_flag(self) -> None:
        # 보안 제약: 호스트 URL 은 환경 변수로만 지정 가능. CLI ``--host`` 등의
        # 옵션은 존재하지 않아야 한다 (argparse 가 SystemExit 으로 거부).
        with pytest.raises(SystemExit):
            main_module._select_mode(
                ["--mode", "ollama", "--host", "http://attacker"]
            )


class TestSelectModeUnknown:
    def test_unknown_mode_exits_via_argparse(self) -> None:
        with pytest.raises(SystemExit):
            main_module._select_mode(["--mode", "does-not-exist"])


class TestModeRegistry:
    def test_registry_contains_known_modes(self) -> None:
        assert "simple" in main_module.MODES
        assert "ollama" in main_module.MODES

    def test_registry_factories_are_callable(self) -> None:
        for factory in main_module.MODES.values():
            assert callable(factory)
