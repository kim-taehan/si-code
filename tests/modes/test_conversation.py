"""``sicode.modes.conversation.Conversation`` 단위 테스트.

검증 범위:
    - 추가/조회/초기화의 기본 동작.
    - max_turns 초과 시 가장 오래된 user+assistant 쌍 드롭.
    - system 메시지는 드롭 대상이 아니며 ``set_system`` 으로 교체된다.
    - ``add_assistant`` 호출 전 user 가 없으면 RuntimeError.
    - ``messages()`` 가 dict 복사를 반환해 외부 변경이 내부 상태에 영향을 주지 않는다.
"""

from __future__ import annotations

import pytest

from sicode.modes.conversation import (
    DEFAULT_MAX_TURNS,
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_USER,
    Conversation,
)


class TestConversationBasics:
    def test_default_max_turns_is_20(self) -> None:
        assert DEFAULT_MAX_TURNS == 20

    def test_new_conversation_has_no_messages(self) -> None:
        conv = Conversation()
        assert conv.messages() == []
        assert conv.system_message is None
        assert conv.max_turns == DEFAULT_MAX_TURNS

    def test_invalid_max_turns_raises(self) -> None:
        with pytest.raises(ValueError):
            Conversation(max_turns=0)
        with pytest.raises(ValueError):
            Conversation(max_turns=-3)

    def test_add_user_then_assistant_round_trip(self) -> None:
        conv = Conversation()
        conv.add_user("hi")
        conv.add_assistant("hello there")
        assert conv.messages() == [
            {"role": ROLE_USER, "content": "hi"},
            {"role": ROLE_ASSISTANT, "content": "hello there"},
        ]

    def test_add_user_alone_appears_as_pending(self) -> None:
        conv = Conversation()
        conv.add_user("incomplete")
        # pending user 도 messages 스냅샷에 포함된다(채팅 클라이언트 호출용).
        assert conv.messages() == [{"role": ROLE_USER, "content": "incomplete"}]

    def test_assistant_without_user_raises(self) -> None:
        conv = Conversation()
        with pytest.raises(RuntimeError):
            conv.add_assistant("orphan")

    def test_consecutive_add_user_replaces_pending(self) -> None:
        conv = Conversation()
        conv.add_user("first attempt")
        conv.add_user("second attempt")
        # 새 pending 만 남아야 한다.
        assert conv.messages() == [
            {"role": ROLE_USER, "content": "second attempt"}
        ]

    def test_clear_resets_history_including_system(self) -> None:
        conv = Conversation()
        conv.set_system("you are helpful")
        conv.add_user("hi")
        conv.add_assistant("hello")
        conv.add_user("pending")

        conv.clear()
        assert conv.messages() == []
        assert conv.system_message is None

    def test_messages_returns_a_copy(self) -> None:
        """반환된 리스트/dict 의 변경이 내부 상태에 영향을 주지 않아야 한다."""
        conv = Conversation()
        conv.add_user("hi")
        conv.add_assistant("hello")
        snap = conv.messages()
        snap.append({"role": "x", "content": "y"})
        snap[0]["content"] = "MUTATED"
        # 내부 상태는 변하지 않아야 한다.
        again = conv.messages()
        assert len(again) == 2
        assert again[0]["content"] == "hi"

    def test_discard_pending_user_returns_true_and_removes(self) -> None:
        conv = Conversation()
        conv.add_user("oops")
        assert conv.discard_pending_user() is True
        assert conv.messages() == []

    def test_discard_pending_user_returns_false_when_idle(self) -> None:
        conv = Conversation()
        assert conv.discard_pending_user() is False


class TestSystemMessage:
    def test_set_system_appears_first_in_messages(self) -> None:
        conv = Conversation()
        conv.set_system("you are helpful")
        conv.add_user("hi")
        conv.add_assistant("hello")
        msgs = conv.messages()
        assert msgs[0] == {"role": ROLE_SYSTEM, "content": "you are helpful"}
        assert msgs[1]["role"] == ROLE_USER
        assert msgs[2]["role"] == ROLE_ASSISTANT

    def test_set_system_replaces_previous_system(self) -> None:
        conv = Conversation()
        conv.set_system("v1")
        conv.set_system("v2")
        assert conv.system_message == "v2"
        assert conv.messages() == [{"role": ROLE_SYSTEM, "content": "v2"}]

    def test_system_message_property_reflects_state(self) -> None:
        conv = Conversation()
        assert conv.system_message is None
        conv.set_system("hello")
        assert conv.system_message == "hello"
        conv.clear()
        assert conv.system_message is None


class TestMaxTurnsDropPolicy:
    def test_does_not_drop_below_max_turns(self) -> None:
        conv = Conversation(max_turns=3)
        for i in range(3):
            conv.add_user(f"u{i}")
            conv.add_assistant(f"a{i}")
        msgs = conv.messages()
        assert len(msgs) == 6
        assert [m["content"] for m in msgs] == ["u0", "a0", "u1", "a1", "u2", "a2"]

    def test_drops_oldest_pair_when_exceeding(self) -> None:
        conv = Conversation(max_turns=2)
        for i in range(5):
            conv.add_user(f"u{i}")
            conv.add_assistant(f"a{i}")
        msgs = conv.messages()
        # max_turns=2 → 최신 두 쌍(u3,a3,u4,a4)만 유지.
        assert len(msgs) == 4
        assert [m["content"] for m in msgs] == ["u3", "a3", "u4", "a4"]

    def test_default_20_turns_drops_oldest_after_overflow(self) -> None:
        conv = Conversation()  # max_turns=20
        for i in range(25):
            conv.add_user(f"u{i}")
            conv.add_assistant(f"a{i}")
        msgs = conv.messages()
        # 메시지 수는 40개를 초과하지 않는다.
        assert len(msgs) == 40
        # 가장 오래된 user 는 u5 (앞의 5쌍이 드롭됨).
        assert msgs[0]["content"] == "u5"
        assert msgs[-1]["content"] == "a24"

    def test_system_message_not_counted_in_drop_policy(self) -> None:
        conv = Conversation(max_turns=2)
        conv.set_system("persistent")
        for i in range(5):
            conv.add_user(f"u{i}")
            conv.add_assistant(f"a{i}")
        msgs = conv.messages()
        assert msgs[0] == {"role": ROLE_SYSTEM, "content": "persistent"}
        # system 1개 + 최신 2쌍 4개 = 5개.
        assert len(msgs) == 5
        assert [m["content"] for m in msgs[1:]] == ["u3", "a3", "u4", "a4"]

    def test_pending_user_not_subject_to_drop_until_paired(self) -> None:
        conv = Conversation(max_turns=2)
        for i in range(2):
            conv.add_user(f"u{i}")
            conv.add_assistant(f"a{i}")
        # 한 쌍 더 시작 — 아직 pending. 드롭은 add_assistant 시점에 일어난다.
        conv.add_user("u2")
        msgs_before = conv.messages()
        assert msgs_before[-1] == {"role": ROLE_USER, "content": "u2"}
        # 드롭은 add_assistant 시점에 발생: u0/a0 가 빠지고 u1/a1, u2/a2 만 남는다.
        conv.add_assistant("a2")
        msgs_after = conv.messages()
        assert [m["content"] for m in msgs_after] == [
            "u1",
            "a1",
            "u2",
            "a2",
        ]
