import unittest
from unittest.mock import patch

from backend.telegram.message_gate import is_lightweight_action, preflight_route
from backend.telegram import polling, recommender


class TelegramMessageGateTestCase(unittest.TestCase):
    def test_greeting_returns_boundary_intro(self):
        result = preflight_route("你好")
        self.assertEqual(result.action, "boundary_intro")
        self.assertTrue(is_lightweight_action(result.action))
        self.assertIn("A 股研究与模拟交易助手", result.reply)
        self.assertIn("不承诺收益", result.reply)

    def test_short_chat_returns_simple_reply(self):
        result = preflight_route("哈哈")
        self.assertEqual(result.action, "simple_reply")
        self.assertIn("A 股", result.reply)

    def test_stock_and_market_questions_go_to_agent_task(self):
        for text in ("京东方A怎么看", "推荐几只多头均线回踩的股票", "今天龙虎榜和北向资金如何", "我重仓603629要止损吗"):
            self.assertEqual(preflight_route(text).action, "agent_task", text)

    def test_multi_stock_ranking_questions_go_to_agent_task(self):
        with patch("backend.telegram.message_gate._stock_mentions", return_value=["002463.SZ", "600487.SH", "603986.SH", "002384.SZ"]):
            result = preflight_route("沪电股份 亨通光电 兆易创新 东山精密，留三个，保留哪三个？")
        self.assertEqual(result.action, "agent_task")

    def test_single_stock_keep_question_goes_to_agent_task(self):
        with patch("backend.telegram.message_gate._stock_mentions", return_value=["002463.SZ"]):
            result = preflight_route("沪电股份还能留吗")
        self.assertEqual(result.action, "agent_task")

    def test_known_commands_go_to_command_route(self):
        for text in ("/login", "/whoami", "/watch list", "/profile", "/memory", "/recommend 强势股", "/compare 600000.SH 600036.SH", "/intraday"):
            self.assertEqual(preflight_route(text).action, "command_route", text)

    def test_out_of_scope_gets_boundary_reply(self):
        result = preflight_route("帮我写一首关于夏天的诗")
        self.assertEqual(result.action, "out_of_scope")
        self.assertIn("超出我的工作范围", result.reply)

    def test_recommender_gate_returns_before_memory_chain(self):
        with patch.object(recommender, "record_message") as record:
            reply = recommender.handle_text_message("你好", chat_id="chat")
        self.assertIn("A 股研究与模拟交易助手", reply)
        record.assert_not_called()

    def test_polling_lightweight_reply_skips_progress(self):
        update = {
            "message": {
                "text": "你好",
                "chat": {"id": "chat"},
                "from": {"id": "user", "first_name": "u"},
            }
        }
        with patch.object(polling._TelegramProgress, "start") as start, \
             patch.object(polling, "send_rich_message", return_value={"ok": True}) as send:
            polling._handle_update(update)

        start.assert_not_called()
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
