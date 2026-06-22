import unittest

from backend.llm.json_repair import extract_json_object
from backend.llm.prompt_safety import trusted_strategy_block


class JsonRepairTestCase(unittest.TestCase):
    def test_extracts_json_from_noisy_reply(self):
        raw = """I will explain first:
```json
{"strategy":"momentum","params":{"lookback_days":15,},"max_results":5}
```
done.
"""

        data = extract_json_object(raw)

        self.assertEqual(data["strategy"], "momentum")
        self.assertEqual(data["params"]["lookback_days"], 15)
        self.assertEqual(data["max_results"], 5)

    def test_extracts_prefaced_strategy_json(self):
        raw = 'parsed result: {"strategy":"ma_bullish","params":{},"max_results":3,"use_custom":false}'

        data = extract_json_object(raw)

        self.assertEqual(data["strategy"], "ma_bullish")
        self.assertEqual(data["max_results"], 3)
        self.assertFalse(data["use_custom"])

    def test_extracts_trade_plan_json_after_text(self):
        raw = """
analysis complete.
{"market_analysis":"risk-on","selected_stocks":[],"orders":[],"risk_assessment":"light"}
"""

        data = extract_json_object(raw)

        self.assertEqual(data["market_analysis"], "risk-on")
        self.assertEqual(data["orders"], [])

    def test_trusted_strategy_block_marks_user_strategy_as_baseline(self):
        block = trusted_strategy_block(
            "user_strategy",
            "Only trade confirmed breakouts.",
        )

        self.assertIn("<trusted_user_strategy>", block)
        self.assertIn("Only trade confirmed breakouts", block)
        self.assertIn("</trusted_user_strategy>", block)


if __name__ == "__main__":
    unittest.main()
