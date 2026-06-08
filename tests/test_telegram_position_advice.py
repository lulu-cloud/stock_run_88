import unittest
from unittest.mock import patch

from backend.telegram import recommender


class TelegramPositionAdviceTestCase(unittest.TestCase):
    def test_detects_position_advice_not_stock_selection(self):
        text = "我有700股，均价220，亏损已达28000，之前有近70000的盈利，是都一键清仓割肉止损！？"

        self.assertTrue(recommender._is_position_advice_query(text))
        self.assertFalse(recommender._is_stock_selection_query(text))
        self.assertFalse(recommender._requires_recommendations(text, "recommend"))

    def test_formats_position_advice_with_cost_context(self):
        text = "我有700股，均价220，亏损已达28000，之前有近70000的盈利，是都一键清仓割肉止损！？"
        tech = {
            "ok": True,
            "trade_date": "20260605",
            "close": 180.38,
            "pct_chg": -10.0,
            "pct_20": -16.7,
            "pct_60": 42.0,
            "summary": "跌破MA5/10/20，短期空头排列",
            "ma": {"ma5": 195.0, "ma10": 205.0, "ma20": 210.0, "ma60": 121.0},
        }

        with patch.object(recommender, "_resolve_context_stock", return_value="603629.SH"), \
             patch.object(recommender, "build_technical_snapshot", return_value=tech), \
             patch.object(recommender, "lookup_stock_name", return_value="利通电子"), \
             patch.object(recommender, "build_memory_prompt", return_value={
                 "short_term_messages": [],
                 "long_term_memories": [],
                 "session_summary": {},
             }), \
             patch.object(recommender, "_record_interest_quietly"):
            reply = recommender._format_position_advice(text, "chat", "user")

        self.assertIn("603629.SH 利通电子 持仓处置分析", reply)
        self.assertIn("持仓 700 股，均价 220.00", reply)
        self.assertIn("估算浮亏约 27,734 元", reply)
        self.assertIn("一键清仓是合理选项之一", reply)
        self.assertIn("赚过钱不能作为继续扛单的理由", reply)

    def test_position_advice_runs_before_feedback_detection(self):
        text = "我亏了28000，重仓利通电子，要不要一键清仓割肉？"

        with patch.object(recommender, "_format_position_advice", return_value="position-advice") as advice, \
             patch.object(recommender, "_record_text_feedback", return_value=True) as feedback:
            reply = recommender._handle_text_message_inner(text, chat_id="", username="")

        self.assertEqual(reply, "position-advice")
        advice.assert_called_once()
        feedback.assert_not_called()

    def test_emotional_risk_help_is_not_feedback(self):
        text = "想赚钱想疯了，咋办，给我点建议？给我点冷水。"

        self.assertTrue(recommender._is_emotional_risk_help_query(text))
        self.assertEqual(recommender._detect_text_feedback_type(text), "")

        with patch.object(recommender, "_record_text_feedback", return_value=True) as feedback:
            reply = recommender._handle_text_message_inner(text, chat_id="chat", username="lili")

        self.assertIn("先停手", reply)
        self.assertIn("高风险信号", reply)
        feedback.assert_not_called()

    def test_feedback_requires_recommendation_context(self):
        self.assertEqual(recommender._detect_text_feedback_type("想赚钱想疯了，咋办"), "")
        self.assertEqual(recommender._detect_text_feedback_type("按你推荐买了，赚钱了"), "positive")
        self.assertEqual(recommender._detect_text_feedback_type("你刚才推荐的票跌了"), "negative")
        self.assertEqual(recommender._detect_text_feedback_type("你推荐太激进了"), "risk_too_high")
        self.assertEqual(recommender._detect_text_feedback_type("你太保守了，机会少"), "")
        self.assertEqual(recommender._detect_text_feedback_type("按你建议太保守了，机会少"), "risk_too_low")


if __name__ == "__main__":
    unittest.main()
