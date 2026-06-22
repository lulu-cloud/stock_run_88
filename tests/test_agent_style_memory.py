import tempfile
import unittest
from unittest.mock import patch

from backend.evolution import memory
from backend.evolution.engine import format_evolution_prompt


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

    def test_agent_type_overrides_generic_style_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(memory, "ROOT_DIR", tmp):
                memory.seed_agent_style_memory(4, "自主决策Agent", {
                    "agent_type": "autonomous",
                    "style_prompt": "可以参考打板数据，但不要简单复制。",
                })
                snapshot = memory.read_memory(4)

        self.assertIn("全因子自主交易员", snapshot["trade_prefer"])
        self.assertNotIn("短线情绪/打板交易员", snapshot["trade_prefer"])

    def test_existing_style_anchor_can_be_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(memory, "ROOT_DIR", tmp):
                memory.seed_agent_style_memory(4, "自主决策Agent", {"agent_type": "chaser"})
                memory.seed_agent_style_memory(4, "自主决策Agent", {"agent_type": "autonomous"})
                snapshot = memory.read_memory(4)

        self.assertIn("全因子自主交易员", snapshot["trade_prefer"])
        self.assertNotIn("短线情绪/打板交易员", snapshot["trade_prefer"])

    def test_evolution_prompt_keeps_user_strategy_as_trusted_baseline(self):
        prompt = format_evolution_prompt({
            "memory_snapshot": {},
            "skills": [],
            "agent_config": {
                "agent_type": "user_style",
                "user_strategy_original": "Only trade confirmed breakouts.",
            },
        })

        self.assertIn("<trusted_user_strategy>", prompt)
        self.assertIn("Only trade confirmed breakouts.", prompt)
        self.assertNotIn("<untrusted_user_strategy>", prompt)

if __name__ == "__main__":
    unittest.main()
