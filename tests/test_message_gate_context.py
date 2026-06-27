import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.telegram import recommender
from backend.telegram.message_gate import GateResult, preflight_route


class MessageGateContextTestCase(unittest.TestCase):
    def test_short_chat_with_recent_stock_context_goes_to_agent(self):
        with patch("backend.telegram.message_gate._has_recent_stock_context", return_value=True):
            result = preflight_route("ok", chat_id="chat", thread_id="topic")
        self.assertEqual(result.action, "agent_task")
        self.assertEqual(result.reason, "contextual_short_reply")

    def test_short_chat_without_context_stays_lightweight(self):
        with patch("backend.telegram.message_gate._has_recent_stock_context", return_value=False):
            result = preflight_route("ok", chat_id="chat", thread_id="topic")
        self.assertEqual(result.action, "simple_reply")

    def test_greeting_stays_lightweight_even_with_recent_context(self):
        greeting = "\u4f60\u597d"
        with patch("backend.telegram.message_gate._has_recent_stock_context", return_value=True) as recent:
            result = preflight_route(greeting, chat_id="chat", thread_id="topic")
        self.assertEqual(result.action, "boundary_intro")
        recent.assert_not_called()

    def test_recent_context_uses_same_thread_and_30_minute_window(self):
        now = datetime(2026, 6, 27, 8, 0, tzinfo=timezone.utc)
        recent_messages = [{
            "role": "assistant",
            "intent": "analyze",
            "content": "stock reply",
            "created_at": (now - timedelta(minutes=10)).isoformat(),
        }]
        with patch("backend.telegram.memory.get_recent_context", return_value=recent_messages) as get_recent:
            result = preflight_route("ok", chat_id="chat", thread_id="topic", now=now)
        self.assertEqual(result.action, "agent_task")
        get_recent.assert_called_once_with("chat", "topic", limit=6)

        recent_messages[0]["created_at"] = (now - timedelta(minutes=31)).isoformat()
        with patch("backend.telegram.memory.get_recent_context", return_value=recent_messages):
            result = preflight_route("ok", chat_id="chat", thread_id="topic", now=now)
        self.assertEqual(result.action, "simple_reply")

    def test_settle_commands_route_directly(self):
        self.assertEqual(preflight_route("/settle").action, "command_route")
        self.assertEqual(preflight_route("/settle 1").action, "command_route")

    def test_recommender_passes_scope_to_gate(self):
        with patch.object(
            recommender,
            "preflight_route",
            return_value=GateResult("simple_reply", "quick reply"),
        ) as gate, patch.object(recommender, "record_message") as record:
            reply = recommender.handle_text_message(
                "ok",
                chat_id="chat",
                user_id="user",
                thread_id="topic",
            )
        self.assertEqual(reply, "quick reply")
        gate.assert_called_once_with("ok", chat_id="chat", thread_id="topic")
        record.assert_not_called()

    def test_settle_command_parses_optional_agent_id(self):
        with patch.object(recommender, "apply_inferred_preferences", return_value={}), \
             patch(
                 "backend.telegram.manual_settlement.start_manual_settlement",
                 return_value="accepted",
             ) as start:
            reply = recommender._handle_text_message_inner(
                "/settle 7",
                chat_id="chat",
                username="name",
                user_id="user",
            )
        self.assertEqual(reply, "accepted")
        start.assert_called_once_with(
            chat_id="chat",
            user_id="user",
            username="name",
            agent_id=7,
        )


if __name__ == "__main__":
    unittest.main()
