import unittest

from backend.logs.report_generator import _trade_reason_for_report


class ReportGeneratorTestCase(unittest.TestCase):
    def test_old_pending_order_reason_gets_context_note(self):
        reason = _trade_reason_for_report(
            {
                "order_trade_date": "20260618",
                "reason": "中天科技成本48.78，现价56.55。",
            },
            "20260622",
        )
        self.assertIn("旧预操作单说明", reason)
        self.assertIn("来自20260618决策上下文", reason)
        self.assertIn("按20260622当日行情撮合", reason)
        self.assertIn("现价56.55", reason)

    def test_same_day_order_reason_is_unchanged(self):
        reason = _trade_reason_for_report(
            {"order_trade_date": "20260622", "reason": "现价59.63。"},
            "20260622",
        )
        self.assertEqual(reason, "现价59.63。")


if __name__ == "__main__":
    unittest.main()
