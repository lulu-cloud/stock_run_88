import tempfile
import unittest
from unittest.mock import patch

from backend.evolution import memory


class AgentStyleMemoryTestCase(unittest.TestCase):
    def test_user_style_agent_gets_distinct_preference_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(memory, "ROOT_DIR", tmp):
                result = memory.seed_agent_style_memory(6, "xulu", {
                    "agent_type": "user_style",
                    "preferred_strategies": ["ma_bullish_pullback", "momentum"],
                    "user_strategy_original": "只做自己股票池，右侧确认后分批进攻。",
                })
                snapshot = memory.read_memory(6)

        self.assertTrue(result["changed"])
        self.assertIn("Agent风格锚点: 用户风格交易员", snapshot["trade_prefer"])
        self.assertIn("右侧确认后分批进攻", snapshot["trade_prefer"])

    def test_chaser_agent_gets_board_quality_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(memory, "ROOT_DIR", tmp):
                memory.seed_agent_style_memory(3, "追高打板Agent", {
                    "agent_type": "chaser",
                    "style_prompt": "追高打板情绪猎手",
                })
                snapshot = memory.read_memory(3)

        self.assertIn("短线情绪/打板交易员", snapshot["trade_prefer"])
        self.assertIn("封板质量", snapshot["trade_prefer"])


if __name__ == "__main__":
    unittest.main()
