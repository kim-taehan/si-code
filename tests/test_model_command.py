"""``/model`` 및 ``/models`` 슬래시 명령 단위/통합 테스트.

검증 범위:
    - ``/model`` (인자 없음): 다음 모델로 순환 전환.
    - ``/model <name>``: 등록된 이름은 즉시 전환, 미등록은 거부 + 안내.
    - ``/models``: 등록 목록과 현재 모델 표시.
    - 모델 전환 시 ``OllamaMode`` 가 새 :class:`OllamaChatClient` (model 변경)
      를 사용한다.
    - 모델 전환 후 :class:`Conversation` 히스토리가 그대로 유지된다.
    - 미등록 이름은 거부되고 현재 모델/사용 가능 목록이 출력된다.
    - REPL 통합: 슬래시 명령 등록만으로 동작(REPL 본문 무수정).
    - 생성자 미주입 시 :attr:`ReplContext.model_registry` 폴백 동작.
"""

from __future__ import annotations

import io
import json
from typing import Any, Callable, List, Optional

import pytest

from sicode.commands import (
    ModelCommand,
    ModelsCommand,
    register_default_commands,
)
from sicode.commands.base import CommandAction, ReplContext
from sicode.commands.model import (
    HISTORY_PRESERVED_NOTICE,
    NOT_CONFIGURED_MESSAGE,
    SUCCESS_PREFIX,
    SWITCH_UNSUPPORTED_MESSAGE,
)
from sicode.commands.registry import (
    SlashCommandRegistry,
    default_registry,
    dispatch_command,
)
from sicode.models.registry import ModelEntry, ModelRegistry
from sicode.modes.base import BaseMode
from sicode.modes.conversation import Conversation
from sicode.modes.ollama import OllamaMode
from sicode.modes.ollama_chat import OllamaChatClient
from sicode.repl import run_repl_with_inputs


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _make_registry(*names: str, default: Optional[str] = None) -> ModelRegistry:
    entries = [ModelEntry(name=n, description=f"desc-{n}") for n in names]
    return ModelRegistry(entries=entries, default_name=default or names[0])


class _FakeMode(BaseMode):
    """``switch_model`` 만 지원하는 가벼운 가짜 모드 (unit test 용)."""

    name = "fake"

    def __init__(self) -> None:
        self.switched_to: List[str] = []
        self.conversation = Conversation()

    def handle(self, user_input: str) -> str:  # pragma: no cover - 미사용
        return user_input

    def switch_model(self, name: str) -> None:
        self.switched_to.append(name)


class _ModeWithoutSwitch(BaseMode):
    name = "noswitch"

    def handle(self, user_input: str) -> str:  # pragma: no cover - 미사용
        return user_input


# ---------------------------------------------------------------------------
# /model unit tests
# ---------------------------------------------------------------------------


class TestModelCommandUnit:
    def test_no_argument_cycles_to_next(self) -> None:
        reg = _make_registry("a", "b", "c")
        assert reg.current() == "a"
        mode = _FakeMode()
        result = ModelCommand(model_registry=reg).execute(
            ReplContext(mode=mode, argument="")
        )
        assert result.action is CommandAction.CONTINUE
        assert reg.current() == "b"
        assert mode.switched_to == ["b"]
        assert SUCCESS_PREFIX in result.output
        assert "b" in result.output
        assert HISTORY_PRESERVED_NOTICE in result.output

    def test_no_argument_wraps_around(self) -> None:
        reg = _make_registry("a", "b")
        mode = _FakeMode()
        cmd = ModelCommand(model_registry=reg)
        cmd.execute(ReplContext(mode=mode, argument=""))  # a -> b
        cmd.execute(ReplContext(mode=mode, argument=""))  # b -> a
        assert reg.current() == "a"
        assert mode.switched_to == ["b", "a"]

    def test_named_argument_selects_model(self) -> None:
        reg = _make_registry("a", "b", "c")
        mode = _FakeMode()
        result = ModelCommand(model_registry=reg).execute(
            ReplContext(mode=mode, argument="c")
        )
        assert reg.current() == "c"
        assert mode.switched_to == ["c"]
        assert "c" in result.output

    def test_unknown_name_is_rejected(self) -> None:
        reg = _make_registry("a", "b")
        mode = _FakeMode()
        result = ModelCommand(model_registry=reg).execute(
            ReplContext(mode=mode, argument="ghost")
        )
        # 거부되어야 한다(switch_model 호출되지 않음, 현재 모델 변동 없음).
        assert mode.switched_to == []
        assert reg.current() == "a"
        assert "등록되지 않은 모델" in result.output
        assert "ghost" in result.output
        # 현재 모델과 목록이 안내에 포함되어야 한다.
        assert "현재 모델" in result.output
        assert "/" not in result.output.split("등록되지")[0]  # 잡음 없음
        assert "a" in result.output and "b" in result.output

    def test_no_registry_returns_not_configured(self) -> None:
        # 생성자 미주입 + 컨텍스트 미주입.
        mode = _FakeMode()
        result = ModelCommand().execute(ReplContext(mode=mode, argument=""))
        assert result.output == NOT_CONFIGURED_MESSAGE
        assert mode.switched_to == []

    def test_context_registry_used_when_no_constructor_injection(self) -> None:
        reg = _make_registry("a", "b")
        mode = _FakeMode()
        cmd = ModelCommand()  # 생성자 주입 없음
        result = cmd.execute(
            ReplContext(mode=mode, argument="b", model_registry=reg)
        )
        assert reg.current() == "b"
        assert mode.switched_to == ["b"]
        assert SUCCESS_PREFIX in result.output

    def test_constructor_registry_takes_precedence_over_context(self) -> None:
        # 생성자가 주입되면 컨텍스트는 무시된다.
        ctor_reg = _make_registry("ctor1", "ctor2")
        ctx_reg = _make_registry("ctx1", "ctx2")
        mode = _FakeMode()
        cmd = ModelCommand(model_registry=ctor_reg)
        cmd.execute(
            ReplContext(mode=mode, argument="ctor2", model_registry=ctx_reg)
        )
        assert ctor_reg.current() == "ctor2"
        assert ctx_reg.current() == "ctx1"  # 변동 없음
        assert mode.switched_to == ["ctor2"]

    def test_mode_without_switch_returns_unsupported_message(self) -> None:
        reg = _make_registry("a", "b")
        mode = _ModeWithoutSwitch()
        result = ModelCommand(model_registry=reg).execute(
            ReplContext(mode=mode, argument="b")
        )
        assert result.output == SWITCH_UNSUPPORTED_MESSAGE

    def test_action_is_continue(self) -> None:
        reg = _make_registry("a", "b")
        mode = _FakeMode()
        result = ModelCommand(model_registry=reg).execute(
            ReplContext(mode=mode, argument="")
        )
        assert result.action is CommandAction.CONTINUE


# ---------------------------------------------------------------------------
# /models unit tests
# ---------------------------------------------------------------------------


class TestModelsCommandUnit:
    def test_lists_models_with_current_marker(self) -> None:
        reg = _make_registry("a", "b", "c")
        reg.set_current("b")
        result = ModelsCommand(model_registry=reg).execute(
            ReplContext(argument="")
        )
        assert result.action is CommandAction.CONTINUE
        out = result.output
        assert "현재 모델: b" in out
        # b 라인은 ``*`` 마커, 다른 라인은 공백 마커.
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("* b"):
                break
        else:
            pytest.fail(f"current marker '* b' not in output:\n{out}")
        assert "a" in out and "c" in out
        # description 포함.
        assert "desc-a" in out and "desc-b" in out

    def test_lists_models_without_description(self) -> None:
        reg = ModelRegistry(
            entries=[ModelEntry(name="only")], default_name="only"
        )
        result = ModelsCommand(model_registry=reg).execute(ReplContext())
        assert "only" in result.output
        # description 이 없으므로 ``-`` 구분자가 없어야 한다.
        for line in result.output.splitlines():
            if "only" in line:
                assert " - " not in line

    def test_no_registry_returns_not_configured(self) -> None:
        result = ModelsCommand().execute(ReplContext())
        assert result.output == NOT_CONFIGURED_MESSAGE

    def test_context_registry_fallback(self) -> None:
        reg = _make_registry("a", "b")
        result = ModelsCommand().execute(ReplContext(model_registry=reg))
        assert "현재 모델" in result.output


# ---------------------------------------------------------------------------
# Dispatcher integration
# ---------------------------------------------------------------------------


class TestDispatcherWithModelCommand:
    def test_dispatch_routes_to_model(self) -> None:
        reg = SlashCommandRegistry()
        mreg = _make_registry("a", "b")
        cmd = ModelCommand(model_registry=mreg)
        reg.register(cmd)
        mode = _FakeMode()
        result = dispatch_command("/model b", registry=reg, mode=mode)
        assert mreg.current() == "b"
        assert mode.switched_to == ["b"]
        assert "b" in result.output

    def test_dispatch_routes_to_models(self) -> None:
        reg = SlashCommandRegistry()
        mreg = _make_registry("a", "b")
        reg.register(ModelsCommand(model_registry=mreg))
        result = dispatch_command("/models", registry=reg)
        assert "현재 모델" in result.output


# ---------------------------------------------------------------------------
# OllamaMode.switch_model integration
# ---------------------------------------------------------------------------


def _capture_chat_opener(
    payload_seq: List[dict], captured: List[dict]
) -> Callable[..., Any]:
    iterator = iter(payload_seq)

    class _Resp:
        def __init__(self, data: bytes) -> None:
            self._buf = io.BytesIO(data)

        def read(self) -> bytes:
            return self._buf.read()

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *exc: Any) -> None:
            self._buf.close()

    def _opener(req: Any, timeout: float) -> _Resp:
        captured.append({"url": req.full_url, "body": req.data})
        return _Resp(json.dumps(next(iterator)).encode("utf-8"))

    return _opener


class TestOllamaModeSwitchModel:
    def test_switch_model_swaps_client_and_uses_new_model(self) -> None:
        captured: List[dict] = []
        # 두 응답을 순서대로 돌려준다(첫 호출 = 모델1, 두 번째 = 모델2 검증).
        opener = _capture_chat_opener(
            [
                {"message": {"role": "assistant", "content": "from-1"}},
                {"message": {"role": "assistant", "content": "from-2"}},
            ],
            captured,
        )

        def factory(name: str) -> OllamaChatClient:
            return OllamaChatClient(model=name, url_opener=opener)

        initial_client = factory("model-1")
        mode = OllamaMode(client=initial_client, client_factory=factory)

        # 첫 메시지 - model-1.
        mode.handle("hi")
        assert mode.current_model == "model-1"
        body1 = json.loads(captured[0]["body"].decode("utf-8"))
        assert body1["model"] == "model-1"

        # 모델 전환.
        mode.switch_model("model-2")
        assert mode.current_model == "model-2"

        # 두 번째 메시지 - model-2 로 전송.
        mode.handle("again")
        body2 = json.loads(captured[1]["body"].decode("utf-8"))
        assert body2["model"] == "model-2"

    def test_switch_model_preserves_conversation_history(self) -> None:
        captured: List[dict] = []
        opener = _capture_chat_opener(
            [
                {"message": {"role": "assistant", "content": "r1"}},
                {"message": {"role": "assistant", "content": "r2"}},
            ],
            captured,
        )

        def factory(name: str) -> OllamaChatClient:
            return OllamaChatClient(model=name, url_opener=opener)

        mode = OllamaMode(client=factory("m1"), client_factory=factory)
        mode.handle("first user message")
        # 첫 턴이 conversation 에 누적되었는지 확인.
        msgs_before = mode.conversation.messages()
        assert any(m.get("content") == "first user message" for m in msgs_before)

        # 모델 전환 — 같은 conversation 인스턴스 사용 확인.
        same_conversation = mode.conversation
        mode.switch_model("m2")
        assert mode.conversation is same_conversation
        assert mode.conversation.messages() == msgs_before  # 변동 없음

        # 다음 호출이 전체 히스토리를 새 모델로 전송하는지.
        mode.handle("second")
        body2 = json.loads(captured[1]["body"].decode("utf-8"))
        assert body2["model"] == "m2"
        # 새 호출의 메시지 시퀀스 안에 첫 턴이 들어 있어야 한다.
        contents = [m["content"] for m in body2["messages"]]
        assert "first user message" in contents
        assert "r1" in contents
        assert "second" in contents

    def test_switch_model_without_factory_raises(self) -> None:
        client = OllamaChatClient(model="m")
        mode = OllamaMode(client=client)  # factory 없음
        with pytest.raises(RuntimeError):
            mode.switch_model("other")

    def test_current_model_property_reads_from_client(self) -> None:
        client = OllamaChatClient(model="x")
        mode = OllamaMode(client=client)
        assert mode.current_model == "x"


# ---------------------------------------------------------------------------
# REPL integration
# ---------------------------------------------------------------------------


def _opener_for(payloads: List[dict], captured: List[dict]) -> Callable[..., Any]:
    return _capture_chat_opener(payloads, captured)


class TestReplIntegrationModelSwitching:
    def _build_mode(
        self, captured: List[dict], replies: List[str]
    ) -> OllamaMode:
        opener = _opener_for(
            [{"message": {"role": "assistant", "content": r}} for r in replies],
            captured,
        )

        def factory(name: str) -> OllamaChatClient:
            return OllamaChatClient(model=name, url_opener=opener)

        return OllamaMode(client=factory("llama3.1:8b"), client_factory=factory)

    def test_slash_model_named_switches_for_next_request(self) -> None:
        register_default_commands()
        reg = _make_registry("llama3.1:8b", "qwen3:14b")
        default_registry.register(ModelCommand(model_registry=reg))
        default_registry.register(ModelsCommand(model_registry=reg))

        captured: List[dict] = []
        mode = self._build_mode(captured, ["pong"])
        outputs = run_repl_with_inputs(
            mode, ["/model qwen3:14b", "ping", "/exit"]
        )
        # 전환 안내가 출력되었는지.
        assert any("qwen3:14b" in o and SUCCESS_PREFIX in o for o in outputs)
        # 다음 채팅이 qwen3:14b 로 전송됐는지.
        assert len(captured) == 1
        body = json.loads(captured[0]["body"].decode("utf-8"))
        assert body["model"] == "qwen3:14b"

    def test_slash_model_cycles_through_registered(self) -> None:
        register_default_commands()
        reg = _make_registry("a", "b", "c")
        default_registry.register(ModelCommand(model_registry=reg))
        default_registry.register(ModelsCommand(model_registry=reg))

        captured: List[dict] = []
        mode = self._build_mode(captured, [])  # 채팅 없음
        run_repl_with_inputs(mode, ["/model", "/model", "/model", "/exit"])
        # a -> b -> c -> a (마지막 이후 첫 모델 복귀).
        assert reg.current() == "a"

    def test_slash_model_unknown_keeps_current_and_warns(self) -> None:
        register_default_commands()
        reg = _make_registry("a", "b")
        default_registry.register(ModelCommand(model_registry=reg))

        captured: List[dict] = []
        mode = self._build_mode(captured, [])
        outputs = run_repl_with_inputs(
            mode, ["/model unknown-model", "/exit"]
        )
        joined = "\n".join(outputs)
        assert "등록되지 않은 모델" in joined
        assert "unknown-model" in joined
        assert "현재 모델" in joined
        assert reg.current() == "a"  # 변동 없음

    def test_slash_models_lists_registered(self) -> None:
        register_default_commands()
        reg = _make_registry("alpha", "beta")
        default_registry.register(ModelCommand(model_registry=reg))
        default_registry.register(ModelsCommand(model_registry=reg))
        captured: List[dict] = []
        mode = self._build_mode(captured, [])
        outputs = run_repl_with_inputs(mode, ["/models", "/exit"])
        joined = "\n".join(outputs)
        assert "alpha" in joined and "beta" in joined
        assert "현재 모델" in joined

    def test_help_lists_model_and_models(self) -> None:
        register_default_commands()
        reg = _make_registry("a")
        default_registry.register(ModelCommand(model_registry=reg))
        default_registry.register(ModelsCommand(model_registry=reg))
        captured: List[dict] = []
        mode = self._build_mode(captured, [])
        outputs = run_repl_with_inputs(mode, ["/help", "/exit"])
        joined = "\n".join(outputs)
        assert "/model" in joined
        assert "/models" in joined

    def test_history_preserved_across_switch_in_repl(self) -> None:
        """REPL 시나리오: chat -> /model -> chat 에서 첫 턴이 두 번째 요청에 포함."""
        register_default_commands()
        reg = _make_registry("m1", "m2")
        default_registry.register(ModelCommand(model_registry=reg))

        captured: List[dict] = []
        mode = self._build_mode(captured, ["resp-1", "resp-2"])
        run_repl_with_inputs(
            mode, ["hello there", "/model m2", "follow up", "/exit"]
        )
        # 두 번째 chat 호출에 첫 턴이 포함되어 있어야 한다.
        body2 = json.loads(captured[1]["body"].decode("utf-8"))
        assert body2["model"] == "m2"
        contents = [m["content"] for m in body2["messages"]]
        assert "hello there" in contents
        assert "resp-1" in contents
        assert "follow up" in contents
