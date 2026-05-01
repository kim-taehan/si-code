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


class TestSelectModeDefault:
    def test_no_args_returns_ollama_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``simple`` 모드 제거 후 ``--mode`` 인자 없이 호출하면 OllamaMode 가 선택된다.
        monkeypatch.delenv(main_module.ENV_OLLAMA_HOST, raising=False)
        monkeypatch.delenv(main_module.ENV_OLLAMA_MODEL, raising=False)
        assert isinstance(main_module._select_mode([]), OllamaMode)

    def test_simple_mode_choice_is_rejected(self) -> None:
        # ``simple`` 은 더 이상 유효한 모드 이름이 아니므로 argparse 가 SystemExit 한다.
        with pytest.raises(SystemExit):
            main_module._select_mode(["--mode", "simple"])


class TestDefaultModelValue:
    def test_default_model_is_llama3_1_8b(self) -> None:
        # 이슈 #5 수용 기준: 기본 모델은 ``llama3.1:8b`` 여야 한다.
        assert DEFAULT_MODEL == "llama3.1:8b"


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
        assert client.model == "llama3.1:8b"

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
            ["--mode", "ollama", "--model", "phi3"]
        )
        client = mode._client  # type: ignore[attr-defined]
        assert client.model == "phi3"

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
    def test_registry_contains_only_ollama(self) -> None:
        assert "ollama" in main_module.MODES
        assert "simple" not in main_module.MODES

    def test_registry_factories_are_callable(self) -> None:
        for factory in main_module.MODES.values():
            assert callable(factory)


class TestSimpleModeFullyRemoved:
    def test_importing_simple_mode_raises(self) -> None:
        # 이슈 #5 수용 기준: ``from sicode.modes import SimpleMode`` 는 ImportError.
        with pytest.raises(ImportError):
            from sicode.modes import SimpleMode  # noqa: F401

    def test_simple_mode_module_is_gone(self) -> None:
        # ``sicode.modes.simple`` 모듈 자체가 삭제되었으므로 import 가 실패해야 한다.
        with pytest.raises(ImportError):
            import sicode.modes.simple  # noqa: F401


class TestSimpleNotInModeChoices:
    def test_help_text_does_not_offer_simple(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # argparse 의 invalid choice 메시지에 가능한 선택지 목록이 포함된다.
        with pytest.raises(SystemExit):
            main_module._select_mode(["--mode", "simple"])
        err = capsys.readouterr().err
        assert "invalid choice: 'simple'" in err
        assert "ollama" in err
